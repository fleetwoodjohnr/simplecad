"""Demand-driven feature evaluation with persistent per-output checkpoints.

Every input resolves to an explicit producer/object pair. Final shapes are
published independently; failure never publishes an earlier intermediate as a
replacement for a previously finished body.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import time

from .document import Body, BuildContext, Document, FeatureState
from .errors import CadError, ReferenceLost, translate
from .result_store import ResultStore, signatures

log = logging.getLogger("simplecad.rebuild")


@dataclass
class RebuildReport:
    rebuilt: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    sealed: list[str] = field(default_factory=list)
    failures: dict[str, CadError] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    duration: float = 0.0
    visited: int = 0
    cache_hits: int = 0
    changed_bodies: list[str] = field(default_factory=list)

    @property
    def ok(self):
        return not self.failures

    def summary(self):
        if self.failures:
            return str(next(iter(self.failures.values())))
        return f"Rebuilt {len(self.rebuilt)} feature(s) in {self.duration * 1000:.0f} ms"


class Rebuilder:
    INTERNAL_PREFIX = "__"

    def __init__(self, document: Document):
        self.document = document
        document._rebuilder = self
        if document._geometry_store is None:
            document._geometry_store = ResultStore()
        self.store = document._geometry_store
        self._cache = {}  # Current evaluation only; retained for diagnostics.
        self._signatures = {}
        self._invalid = set()
        self._baseline_ids = set()
        self._report = RebuildReport()
        self._graph = None
        self._on_feature = None
        self._evaluating = set()
        self._failed = set()
        self._last_graph = None
        self._previewing = False

    def _prepare(self, force=False, on_feature=None):
        self._graph = self.document.prepare_graph()
        if force:
            self.document._signature_dirty.update(self._graph.features)
        self._invalid.intersection_update(self._graph.features)
        self._signatures = signatures(self.document, self._graph)
        self._report = RebuildReport()
        self._on_feature = on_feature
        self._planned = sum(fid in self._invalid or sig not in self.store.entries
                            for fid, sig in self._signatures.items())
        self._cache = {}
        self._evaluating = set()
        self._failed = set()
        if force:
            self._invalid.update(self._graph.features)
            self._baseline_ids.clear()
        self._adopt_fragments()

    def adopt_baseline(self, bodies=None):
        """Adopt final outputs individually, never seal an entire timeline.

        This is also the v1 migration path. Editing one legacy branch may need
        to hydrate its ancestors once, but other finished branches stay cached.
        """
        self._prepare()
        if bodies is None:
            bodies = {n: b.shape for n, b in self.document.bodies.items() if b.shape is not None}
        if not bodies or set(bodies) != set(self.document.bodies):
            return False
        outputs = {}
        for name, shape in bodies.items():
            ref = self._graph.latest.get(name)
            if ref is None or shape is None:
                return False
            outputs.setdefault(ref.feature_id, {})[name] = shape
        for fid, shapes in outputs.items():
            feature = self._graph.features[fid]
            # A partial multi-output result must not masquerade as complete.
            if set(shapes) == set(feature.outputs) - set(feature.dropped):
                self.store.put(feature, self._signatures[fid], shapes)
        self._baseline_ids = set(self._graph.features)
        return True

    def drop_baseline(self):
        self._invalid.update(self._baseline_ids)
        self._baseline_ids.clear()

    @property
    def baseline_size(self):
        return len(self._baseline_ids)

    def seed_result(self, feature, outputs, message=""):
        graph = self.document.prepare_graph()
        feature.message = message
        # A preview may add outputs dynamically (for example, Combine with
        # "Keep tool bodies"). Give them the same stable IDs that a normal
        # rebuild assigns before writing the preview into the result cache.
        from .graph import output_id
        for name in outputs:
            if name not in feature.output_ids:
                source = feature.bindings.get(name)
                feature.output_ids[name] = source.object_id if source else output_id(feature.id, name)
        self.store.put(feature, signatures(self.document, graph)[feature.id], outputs)
        self._invalid.discard(feature.id)

    def invalidate(self, feature_ids=None):
        graph = self.document.prepare_graph()
        ids = set(graph.features) if feature_ids is None else set(feature_ids)
        dirty = graph.dependents(ids)
        if self._last_graph is not None:
            dirty |= self._last_graph.dependents(ids)
        self._invalid.update(dirty)
        self.document._signature_dirty.update(dirty)
        self._baseline_ids.difference_update(dirty)

    def invalidate_parameter(self, name):
        # Signatures include the entire parameter closure. No need to discard
        # a reusable result from a previous Undo/Redo revision.
        self._baseline_ids.difference_update(self.document.features_using_parameter(name))

    def _adopt_fragments(self):
        """Migrate old clipboard checkpoints into the common result store."""
        import base64
        from .geometry_service import deserialise_shape
        retained = []
        for checkpoint in getattr(self.document, "_geometry_fragments", []):
            states = checkpoint["features"]
            if not states:
                continue
            if any((f := self._graph.features.get(state["id"])) is None
                   or any(f.to_dict().get(k) != v for k, v in state.items()
                          if k not in {"bindings", "output_ids"}) for state in states):
                continue
            params = {p["name"]: p for p in self.document.parameters.to_list()}
            if any(params.get(p["name"]) != p for p in checkpoint["parameters"]):
                continue
            grouped = {}
            for name, blob in checkpoint["shapes"].items():
                # Find the last cloned producer, excluding the placement tail.
                feature = next((self._graph.features[s["id"]] for s in reversed(states)
                                if name in self._graph.features[s["id"]].outputs), None)
                if feature is not None and self._signatures[feature.id] not in self.store.entries:
                    grouped.setdefault(feature.id, {})[name] = deserialise_shape(base64.b64decode(blob))
            for fid, shapes in grouped.items():
                feature = self._graph.features[fid]
                if (set(shapes) == set(feature.outputs) - set(feature.dropped)
                        and all(v is not None for v in shapes.values())):
                    self.store.put(feature, self._signatures[fid], shapes)
            retained.append(checkpoint)
        self.document._geometry_fragments = retained

    def _input_context(self, feature):
        context = BuildContext(self.document)
        context.feature_id = feature.id
        for name, ref in feature.bindings.items():
            source = self._graph.features.get(ref.feature_id)
            if source is None:
                raise ReferenceLost(f"The object '{name}' this feature needs was deleted.",
                                    suggestion="Undo the deletion or re-select its input.")
            outputs = self._get(source.id)
            source_name = next((n for n, oid in source.output_ids.items() if oid == ref.object_id), None)
            if source_name is None or source_name not in outputs:
                raise ReferenceLost(f"The output '{name}' this feature needs is unavailable.")
            context.bodies[name] = outputs[source_name]
        return context

    def context_for(self, feature, *, edit_id=None):
        """An isolated preview context at the exact input version of a feature."""
        self._prepare()
        self._previewing = True
        if edit_id:
            existing = self._graph.features.get(edit_id)
            if existing is None:
                raise ReferenceLost("The texture feature being edited no longer exists.")
            feature.bindings = dict(existing.bindings)
            feature.output_ids = dict(existing.output_ids)
        else:
            from .graph import input_names
            for name in input_names(feature):
                if name not in feature.bindings:
                    ref = self._graph.latest.get(name)
                    if ref is None:
                        raise ReferenceLost(f"The object '{name}' is unavailable.")
                    feature.bindings[name] = ref
        try:
            context = self._input_context(feature)
            context.feature_id = edit_id or feature.id
            return context
        finally:
            self._previewing = False

    def _get(self, fid):
        # Schedule uncached ancestors iteratively. Deep histories must not be
        # limited by Python's call stack, and a cached head needs no traversal.
        pending = [(fid, False)]
        scheduled = set()
        while pending:
            current, ready = pending.pop()
            if current in self._cache or current in self._failed:
                continue
            if ready or (current not in self._invalid
                         and self._signatures[current] in self.store.entries):
                try:
                    self._evaluate(current)
                except CadError:
                    pass
                continue
            if current in scheduled:
                continue
            scheduled.add(current)
            pending.append((current, True))
            feature = self._graph.features[current]
            for ref in reversed(list(feature.bindings.values())):
                if ref.feature_id in self._graph.features:
                    pending.append((ref.feature_id, False))
        return self._evaluate(fid)

    def _evaluate(self, fid):
        if fid in self._failed:
            raise ReferenceLost("An earlier feature failed; keeping the last valid model.")
        if fid in self._cache:
            return self._cache[fid]
        feature = self._graph.features[fid]
        if self._previewing:
            from .document import Feature
            feature = Feature.from_dict(feature.to_dict())
        self._report.visited += 1
        if fid in self._evaluating:
            raise ReferenceLost("A feature refers back to itself.")
        self._evaluating.add(fid)
        try:
            if fid not in self._invalid:
                cached = self.store.get(feature, self._signatures[fid])
                if cached is not None:
                    self._report.cache_hits += 1
                    self._report.sealed.append(fid)
                    feature.state = FeatureState.SUPPRESSED if feature.suppressed else FeatureState.OK
                    self._cache[fid] = cached
                    return cached
            context = self._input_context(feature)
            if feature.suppressed:
                outputs = {n: context.bodies[n] for n in feature.outputs if n in context.bodies}
                feature.state = FeatureState.SUPPRESSED
                self._report.skipped.append(fid)
            else:
                if self._on_feature:
                    self._on_feature(feature, len(self._report.rebuilt), max(1, self._planned))
                feature.message = ""
                outputs = feature.execute(context)
                if not outputs:
                    raise CadError("This feature produced no geometry.")
                feature.state = FeatureState.OK
                self._report.rebuilt.append(fid)
            outputs = feature.keep(outputs)
            from .graph import output_id
            for name in outputs:
                if name not in feature.output_ids:
                    source = feature.bindings.get(name)
                    feature.output_ids[name] = source.object_id if source else output_id(fid, name)
            self._report.warnings.extend(context.warnings)
            self._cache[fid] = outputs
            self.store.put(feature, self._signatures[fid], outputs)
            self._invalid.discard(fid)
            return outputs
        except BaseException as exc:
            error = exc if isinstance(exc, CadError) else translate(exc, feature.label.lower())
            feature.state = FeatureState.NEEDS_ATTENTION if isinstance(error, ReferenceLost) else FeatureState.FAILED
            feature.message = str(error)
            self._report.failures[fid] = error
            self._failed.add(fid)
            self._report.skipped.append(fid)
            raise error
        finally:
            self._evaluating.discard(fid)

    def rebuild(self, force=False, on_feature=None):
        start = time.perf_counter()
        stamp = (self.document.revision, self.document.parameters.to_list())
        if not force and not self._invalid and getattr(self, "_last_stamp", None) == stamp:
            return RebuildReport(failures=dict(self._report.failures),
                                 duration=time.perf_counter() - start)
        try:
            self._prepare(force, on_feature)
        except CadError as exc:
            report = RebuildReport(failures={"graph": exc})
            report.duration = time.perf_counter() - start
            return report
        graph, document = self._graph, self.document
        previous = {b.id: b for b in document.bodies.values()}
        previous_geometry = {b.id: (b.shape, b.producer, b.name) for b in document.bodies.values()}
        consumed_ids = {feature.bindings[name].object_id for feature in graph.order if not feature.suppressed
                        for name in feature.consumed_bodies() if name in feature.bindings}
        final = {}
        # Demand final outputs only. A valid saved final result avoids executing
        # its ancestors; editing it hydrates only that dependency branch.
        for oid, ref in graph.heads.items():
            name = graph.names[oid]
            if name.startswith(self.INTERNAL_PREFIX) or oid in consumed_ids:
                continue
            feature = graph.features[ref.feature_id]
            if name in feature.dropped:
                continue
            try:
                outputs = self._get(feature.id)
                shape = outputs.get(name)
                if shape is None:
                    continue
                body = previous.get(oid) or Body(name=name, id=oid)
                if body.shape is not shape or body.name != name:
                    self._report.changed_bodies.append(name)
                body.name, body.shape, body.producer = name, shape, feature.id
                final[oid] = body
            except CadError:
                if oid in previous:
                    final[oid] = previous[oid]
        # Internal-only documents still need to solve sketches/constructions.
        for oid, ref in graph.heads.items():
            if not graph.names[oid].startswith(self.INTERNAL_PREFIX):
                continue
            try:
                self._get(ref.feature_id)
            except CadError:
                pass
        protected = graph.dependents(self._failed)
        for feature in graph.order:
            if feature.suppressed:
                continue
            # A cached final modifier can conceal its consumed predecessors.
            # Consumption is an explicit graph property, not a name lookup.
            blocked = feature.id in protected or feature.id in self._failed
            for name in feature.consumed_bodies():
                ref = feature.bindings.get(name)
                if ref is None:
                    continue
                if blocked:
                    if ref.object_id in previous:
                        final[ref.object_id] = previous[ref.object_id]
                    else:
                        final.pop(ref.object_id, None)
                else:
                    final.pop(ref.object_id, None)
        # Preserve failed chains atomically, including outputs of multi-body
        # operations and every downstream modifier.
        protected = graph.dependents(self._failed)
        for fid in protected:
            feature = graph.features.get(fid)
            if feature is None:
                continue
            if fid not in self._failed:
                feature.state = FeatureState.NEEDS_ATTENTION
                feature.message = "An earlier feature failed; keeping the last valid model."
            for oid in feature.output_ids.values():
                if oid in previous:
                    body = previous[oid]
                    body.shape, body.producer, body.name = previous_geometry[oid]
                    final[oid] = body
                else:
                    final.pop(oid, None)
        old_names = set(document.bodies)
        document.bodies = {body.name: body for body in final.values()}
        self._report.changed_bodies.extend(sorted(old_names - set(document.bodies)))
        self._report.duration = time.perf_counter() - start
        self._last_graph = graph
        document._pending_changes.clear()
        # Kernel-selected defaults become part of the accepted recipe. Re-key
        # its results without executing or serializing the geometry again.
        actual = signatures(document)
        for fid, outputs in self._cache.items():
            old, new = self._signatures.get(fid), actual.get(fid)
            if new and old != new and old in self.store.entries:
                self.store.entries[new] = dict(self.store.entries[old])
                if old in self.store.decoded:
                    values, size = self.store.decoded[old]
                    self.store._remember(new, values, size)
        self._signatures = actual
        document._geometry_signatures = {fid: sig for fid, sig in actual.items() if fid not in protected}
        history = getattr(document, "_history", None)
        retained = set(actual.values()) | set(getattr(document, "_retained_cache_keys", ()))
        if history is not None:
            retained.update(history.cache_keys())
        else:
            for stack in ("undo", "redo"):
                for action in document._history_data.get(stack, []):
                    retained.update(action.get("cache_keys", []))
        self.store.prune(retained)
        self._cache.clear()
        self._last_stamp = (document.revision, document.parameters.to_list())
        return self._report
