"""Undo and redo.

Built on the fact that a document already serialises losslessly -- the project
format depends on it and it is tested. So a snapshot is just
``Document.to_dict()``, and undo is restoring one.

The subtlety is cost. Restoring naively would mean rebuilding every feature,
which for a model containing threads is seconds. Instead the snapshot is applied
*into* the existing document and the rebuild cache is invalidated only for the
features that actually differ, so undoing a fillet re-runs the fillet and
nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Snapshot:
    """A document state, with the name of the action that produced it."""

    label: str
    data: dict


class History:
    """Undo/redo stacks over document snapshots."""

    def __init__(self, document, limit: int = 80) -> None:
        self.document = document
        self.limit = limit
        self._undo: list[Snapshot] = []
        self._redo: list[Snapshot] = []

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
        self._undo.append(Snapshot(label, self.document.to_dict()))
        if len(self._undo) > self.limit:
            self._undo.pop(0)
        self._redo.clear()

    def reset(self, document=None) -> None:
        if document is not None:
            self.document = document
        self._undo.clear()
        self._redo.clear()

    # -- queries ---------------------------------------------------------
    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str:
        return self._undo[-1].label if self._undo else ""

    @property
    def redo_label(self) -> str:
        return self._redo[-1].label if self._redo else ""

    # -- moving ----------------------------------------------------------
    def undo(self) -> set[str] | None:
        """Step back. Returns the feature ids whose results are now stale."""
        if not self._undo:
            return None
        target = self._undo.pop()
        self._redo.append(Snapshot(target.label, self.document.to_dict()))
        return self._apply(target)

    def redo(self) -> set[str] | None:
        if not self._redo:
            return None
        target = self._redo.pop()
        self._undo.append(Snapshot(target.label, self.document.to_dict()))
        return self._apply(target)

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

        # Apply in place so the caller's Rebuilder keeps its cache.
        document.title = restored.title
        document.parameters = restored.parameters
        document.metadata = restored.metadata
        document.features = restored.features
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
        return stale
