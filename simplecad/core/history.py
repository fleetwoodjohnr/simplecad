"""Bounded undo/redo using immutable, reversible entity patches.

The legacy record-before-mutation API remains safe: the one pending snapshot
is finalized at the next action, undo, or save. Retained actions contain only
entity differences, never aliases into a mutable document.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import json
from .changes import make_patch, apply_patch


@dataclass(frozen=True)
class Snapshot:
    """A document state, with the name of the action that produced it."""

    label: str
    data: dict
    cache_keys: tuple[str, ...] = ()
    cache_by_feature: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Action:
    label: str
    patch_json: str
    cache_keys: tuple[str, ...] = ()


def _restore_recipe(feature, source) -> None:
    """Give *feature* the recorded recipe of *source*, keeping its identity."""
    for key in ("name", "inputs", "outputs", "output_ids", "bindings", "suppressed", "dropped"):
        value = getattr(source, key)
        if getattr(feature, key) != value:
            setattr(feature, key, value)


class History:
    """Undo/redo stacks over document snapshots."""

    def __init__(self, document, limit: int = 1000) -> None:
        self.document = document
        self.limit = limit
        self._undo: list[Action] = []
        self._redo: list[Action] = []
        self._pending: Snapshot | None = None
        document._history = self
        self.restore(getattr(document, "_history_data", {}))

    # -- recording -------------------------------------------------------
    def record(self, label: str) -> None:
        """Snapshot the current state. Call once, *before* mutating.

        There is deliberately no matching "commit" call. An earlier version had
        one, and any mutation that forgot it silently corrupted the stack --
        undo would jump back past changes rather than stepping over them. One
        call that captures the state about to be replaced cannot be half-used.

        *label* names the action about to happen, so the UI can offer
        "Undo Fillet" rather than a bare "Undo".
        """
        current = self.document.to_dict()
        self._finish_pending(current)
        signatures = dict(getattr(self.document, "_geometry_signatures", {}))
        self._pending = Snapshot(label, current,
                                 tuple(signatures.values()), signatures)
        self._redo.clear()

    def record_data(self, label, before):
        self._finish_pending(before)
        self._undo.append(Action(label, json.dumps(make_patch(self.document.to_dict(), before)),
                                 tuple(getattr(self.document, "_geometry_signatures", {}).values())))
        self._undo[:] = self._undo[-self.limit:]
        self._redo.clear()

    def _finish_pending(self, current=None):
        if self._pending is None:
            return
        before, self._pending = self._pending, None
        current = self.document.to_dict() if current is None else current
        patch = make_patch(current, before.data)
        affected = set(patch.get("collections", {}).get("features", {}).get("set", {}))
        affected.update(patch.get("collections", {}).get("features", {}).get("remove", []))
        parameter_change = patch.get("collections", {}).get("parameters", {})
        for name in set(parameter_change.get("set", {})) | set(parameter_change.get("remove", [])):
            affected.update(self.document.features_using_parameter(name))
        affected = self.document.dependents_of(affected) if affected else set()
        keys = tuple(before.cache_by_feature[fid] for fid in affected
                     if fid in before.cache_by_feature)
        self._undo.append(Action(before.label, json.dumps(patch), keys))
        self._undo[:] = self._undo[-self.limit:]

    def cache_keys(self):
        """Result-cache keys the retained history can still restore.

        Asked on every rebuild request and reply; the union over up to
        ``limit`` actions is recomputed only when a stack actually changed.
        Actions are immutable, so the stacks' ends identify their contents.
        """
        stamp = (self._pending, len(self._undo), len(self._redo),
                 self._undo[0] if self._undo else None,
                 self._undo[-1] if self._undo else None,
                 self._redo[-1] if self._redo else None)
        cached = getattr(self, "_keys_cache", None)
        if cached is not None and all(a is b or (type(a) is int and a == b)
                                         for a, b in zip(cached[0], stamp)):
            return set(cached[1])
        keys = set(self._pending.cache_keys) if self._pending else set()
        for action in self._undo + self._redo:
            keys.update(action.cache_keys)
        self._keys_cache = (stamp, frozenset(keys))
        return keys

    def to_dict(self):
        self._finish_pending()
        return {"version": 1, "limit": self.limit,
                "undo": [{"label": a.label, "patch": json.loads(a.patch_json), "cache_keys": list(a.cache_keys)} for a in self._undo],
                "redo": [{"label": a.label, "patch": json.loads(a.patch_json), "cache_keys": list(a.cache_keys)} for a in self._redo]}

    def restore(self, data):
        self._pending = None
        self._undo = [Action(a["label"], json.dumps(a["patch"]), tuple(a.get("cache_keys", []))) for a in data.get("undo", [])][-self.limit:]
        self._redo = [Action(a["label"], json.dumps(a["patch"]), tuple(a.get("cache_keys", []))) for a in data.get("redo", [])][-self.limit:]

    def reset(self, document=None):
        if document is not None:
            self.document = document
        self.document._history = self
        self.restore(getattr(self.document, "_history_data", {}))

    # -- queries ---------------------------------------------------------
    @property
    def can_undo(self) -> bool:
        return bool(self._undo) or self._pending is not None

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str:
        return self._pending.label if self._pending else (self._undo[-1].label if self._undo else "")

    @property
    def redo_label(self) -> str:
        return self._redo[-1].label if self._redo else ""

    # -- moving ----------------------------------------------------------
    def _move(self, source, destination):
        self._finish_pending()
        if not source:
            return None
        action = source.pop()
        patch = json.loads(action.patch_json)
        inverse = self._inverse_patch(patch)
        current_signatures = dict(getattr(self.document, "_geometry_signatures", {}))
        try:
            stale = self._apply_entity_patch(patch)
        except BaseException:
            source.append(action)
            raise
        destination.append(Action(action.label, json.dumps(inverse),
                                  tuple(current_signatures[fid] for fid in stale
                                        if fid in current_signatures)))
        return stale

    def undo(self):
        return self._move(self._undo, self._redo)

    def redo(self):
        return self._move(self._redo, self._undo)

    def _inverse_patch(self, patch):
        """Read only the entities named by a patch, not the whole document."""
        document = self.document
        collections = {
            "features": (document.features, "id"),
            "bodies": (document.bodies.values(), "id"),
            "groups": (document.groups.values(), "id"),
            "parameters": (document.parameters, "name"),
        }
        inverse = {"values": {}, "collections": {}}
        values = {
            "title": document.title, "counters": document._counters,
            "retired_ids": sorted(document._retired_ids), "metadata": document.metadata,
            "assets": sorted(document.active_asset_keys()),
        }
        for key in patch.get("values", {}):
            inverse["values"][key] = json.loads(json.dumps(values.get(key)))
        for key, change in patch.get("keyed", {}).items():
            mapping = values.get(key) or {}
            touched = set(change.get("set", {})) | set(change.get("remove", []))
            inverse.setdefault("keyed", {})[key] = {
                "set": {k: json.loads(json.dumps(mapping[k])) for k in touched if k in mapping},
                "remove": [k for k in touched if k not in mapping],
            }
        for key, change in patch.get("collections", {}).items():
            items, identity = collections[key]
            current = {getattr(item, identity): item for item in items}
            touched = set(change.get("set", {})) | set(change.get("remove", []))
            inverse["collections"][key] = {
                "set": {ident: current[ident].to_dict() for ident in touched if ident in current},
                "remove": [ident for ident in touched if ident not in current],
                "order": list(current) if change.get("order") is not None else None,
            }
        return inverse

    def _apply_entity_patch(self, patch):
        """Restore changed entities in place, retaining unchanged shapes/cache."""
        from .document import Body, Feature, Group
        from .params import ParameterSet

        document = self.document
        old_graph = document.prepare_graph()
        feature_change = patch.get("collections", {}).get("features")
        parameter_change = patch.get("collections", {}).get("parameters")
        stale = set()
        if feature_change:
            current = {f.id: f for f in document.features}
            structural = False
            for ident in feature_change.get("remove", []):
                if ident in current:
                    stale.add(ident)
                    del current[ident]
                    structural = True
            for ident, state in feature_change.get("set", {}).items():
                previous = current.get(ident)
                if previous is None or previous.to_dict() != state:
                    stale.add(ident)
                    replacement = Feature.from_dict(state)
                    if previous is not None and type(previous) is type(replacement):
                        # Restore in place. The graph is then re-indexed only
                        # if this feature's connections changed -- undoing a
                        # dimension or a move re-derives nothing else.
                        _restore_recipe(previous, replacement)
                    else:
                        current[ident] = replacement
                        structural = True
            existing_order = [f.id for f in document.features]
            order = feature_change.get("order") or existing_order
            if structural or order != existing_order:
                document.features = [current[ident] for ident in order if ident in current]
                for ident, feature in current.items():
                    if ident not in order:
                        document.features.append(feature)
        if parameter_change:
            current = {p.name: p.to_dict() for p in document.parameters}
            names = set(parameter_change.get("set", {})) | set(parameter_change.get("remove", []))
            for name in names:
                if current.get(name) != parameter_change.get("set", {}).get(name):
                    stale.update(old_graph.parameter_dependents(name, document.parameters))
            for name in parameter_change.get("remove", []):
                current.pop(name, None)
            current.update(parameter_change.get("set", {}))
            order = parameter_change.get("order") or list(current)
            document.parameters = ParameterSet.from_list([current[n] for n in order if n in current])
        for key, value in patch.get("values", {}).items():
            if key == "title":
                document.title = value
            elif key == "counters":
                document._counters = dict(value or {})
            elif key == "retired_ids":
                document._retired_ids = set(value or ())
            elif key == "metadata":
                document.metadata = value or {}
            elif key == "assets":
                document.assets = {name: document._all_assets[name] for name in value or ()
                                   if name in document._all_assets}
        counters = patch.get("keyed", {}).get("counters")
        if counters:
            for name in counters.get("remove", []):
                document._counters.pop(name, None)
            document._counters.update(counters.get("set", {}))
        for key, cls in (("bodies", Body), ("groups", Group)):
            change = patch.get("collections", {}).get(key)
            if not change:
                continue
            original = document.bodies.values() if key == "bodies" else document.groups.values()
            current = {item.id: item for item in original}
            for ident in change.get("remove", []):
                current.pop(ident, None)
            for ident, state in change.get("set", {}).items():
                item = current.get(ident)
                if item is None:
                    item = cls(name=state["name"], id=ident)
                    current[ident] = item
                for attribute in ("name", "visible", "color", "producer", "metadata") if key == "bodies" else ("name", "members", "visible", "expanded"):
                    if attribute in state:
                        setattr(item, attribute, state[attribute])
            order = change.get("order") or list(current)
            ordered = [current[ident] for ident in order if ident in current]
            ordered.extend(item for ident, item in current.items() if ident not in order)
            setattr(document, key, {item.name: item for item in ordered})
        document.prepare_graph()
        if parameter_change:
            for name in set(parameter_change.get("set", {})) | set(parameter_change.get("remove", [])):
                stale.update(document.features_using_parameter(name))
        stale |= old_graph.dependents(stale) | document.dependents_of(stale)
        document._pending_changes.update(stale)
        document._signature_dirty.update(stale)
        document.revision += 1
        return stale

    # -- internals -------------------------------------------------------
    def _apply(self, snapshot: Snapshot) -> set[str]:
        """Restore *snapshot* in place, returning ids that must be rebuilt."""
        from .document import Document, Feature
        from .params import ParameterSet

        document = self.document
        before = {f.id: f.to_dict() for f in document.features}
        before_params = {p["name"]: p["expression"]
                         for p in document.parameters.to_list()}

        restored = Document.from_dict(snapshot.data)
        after = {f.id: f.to_dict() for f in restored.features}
        after_params = {p["name"]: p["expression"]
                        for p in restored.parameters.to_list()}

        # Anything added, removed or edited has to be rebuilt.
        stale = {
            fid for fid in set(before) | set(after)
            if before.get(fid) != after.get(fid)
        }
        # A parameter change dirties whatever reads it.
        for name in set(before_params) | set(after_params):
            if before_params.get(name) != after_params.get(name):
                stale |= restored.features_using_parameter(name)

        # Removing a modifier rebinds later body inputs to an earlier writer.
        # Capture both graphs before replacing the feature list, or redo of a
        # removal can reuse a downstream result that still contains the modifier.
        stale |= document.dependents_of(stale) | restored.dependents_of(stale)

        # Apply in place so the caller's Rebuilder keeps its cache.
        document.title = restored.title
        document.parameters = restored.parameters
        document.metadata = restored.metadata
        document.assets = {name: document._all_assets[name] for name in snapshot.data.get("assets", [])
                           if name in document._all_assets}
        existing_features = {f.id: f for f in document.features}
        document.features = [existing_features[f.id] if before.get(f.id) == after.get(f.id) else f
                             for f in restored.features]
        for name, value in restored._counters.items():
            document._counters[name] = max(value, document._counters.get(name, 1))
        document.revision += 1
        # Groups are document state rather than feature state, so they have to
        # be restored explicitly -- otherwise undoing a Group would put the
        # geometry back and leave the grouping behind.
        document.groups = restored.groups

        keep = {name for f in restored.features for name in f.outputs}
        for name in list(document.bodies):
            if name not in keep:
                del document.bodies[name]
        for name, body in restored.bodies.items():
            existing = document.bodies.get(name)
            if existing is None:
                document.bodies[name] = body
            else:
                existing.visible = body.visible
                existing.color = body.color
                existing.metadata = body.metadata
                existing.id = body.id
                existing.producer = body.producer
        document.prepare_graph()
        document._pending_changes.update(stale)
        return stale
