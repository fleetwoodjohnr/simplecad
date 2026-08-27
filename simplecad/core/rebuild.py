"""Dependency-driven rebuild.

Two rules shape this module:

* **Only rebuild what changed.** Editing a parameter marks the features that
  reference it dirty, then everything downstream of those. Unrelated bodies are
  never recomputed.
* **A failed feature must never cost you your model.** Each feature is built
  inside a guard; on failure the feature is marked and skipped, the last valid
  shape for its outputs is retained, and the rebuild carries on. One bad fillet
  radius does not empty the viewport, and it certainly does not crash the app.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from .document import Body, BuildContext, Document, Feature, FeatureState
from .errors import CadError, ReferenceLost, translate

log = logging.getLogger("simplecad.rebuild")


@dataclass
class RebuildReport:
    """What happened, in terms the UI can show without interpretation."""

    rebuilt: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failures: dict[str, CadError] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    duration: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.failures

    def summary(self) -> str:
        if self.ok:
            return f"Rebuilt {len(self.rebuilt)} feature(s) in {self.duration * 1000:.0f} ms"
        first = next(iter(self.failures.values()))
        return str(first)


class Rebuilder:
    """Rebuilds a document, reusing results for anything that did not change."""

    def __init__(self, document: Document) -> None:
        self.document = document
        #: feature id -> {body name: shape} from the last successful build.
        self._cache: dict[str, dict[str, object]] = {}

    def invalidate(self, feature_ids: set[str] | None = None) -> None:
        """Drop cached results so those features rebuild next time."""
        if feature_ids is None:
            self._cache.clear()
            return
        for feature_id in self.document.dependents_of(set(feature_ids)):
            self._cache.pop(feature_id, None)

    def invalidate_parameter(self, name: str) -> None:
        """Mark everything that reads *name* (and its dependents) dirty."""
        self.invalidate(self.document.features_using_parameter(name))

    def rebuild(self, force: bool = False, on_feature=None) -> RebuildReport:
        """Rebuild the document.

        *on_feature* is called as ``on_feature(feature, index, total)`` before
        each feature is executed. The UI uses it to report progress and let the
        window repaint between features -- a single feature is atomic and can
        take seconds, but a rebuild of many features need not look frozen
        throughout. Kept as a plain callback so ``core`` stays free of Qt.
        """
        started = time.perf_counter()
        report = RebuildReport()
        document = self.document
        if force:
            self._cache.clear()

        context = BuildContext(document)
        live_bodies: dict[str, object] = {}
        produced_by: dict[str, str] = {}
        consumed: set[str] = set()

        order = document.topological_order()
        for position, feature in enumerate(order):
            if on_feature is not None:
                on_feature(feature, position, len(order))

            if feature.suppressed:
                feature.state = FeatureState.SUPPRESSED
                feature.message = ""
                report.skipped.append(feature.id)
                continue

            context.bodies = dict(live_bodies)
            cached = self._cache.get(feature.id)
            if cached is not None:
                outputs = cached
            else:
                outputs = self._execute(feature, context, report)
                if outputs is None:
                    # Keep the previous geometry for this feature's outputs so
                    # the model on screen stays whole.
                    report.skipped.append(feature.id)
                    continue
                self._cache[feature.id] = outputs
                report.rebuilt.append(feature.id)

            for name in feature.consumed_bodies():
                if name not in outputs:
                    live_bodies.pop(name, None)
                    produced_by.pop(name, None)
                    consumed.add(name)
            for name, shape in outputs.items():
                live_bodies[name] = shape
                produced_by[name] = feature.id
                consumed.discard(name)

        self._sync_bodies(live_bodies, produced_by, consumed)
        report.warnings.extend(context.warnings)
        report.duration = time.perf_counter() - started
        return report

    def _execute(
        self, feature: Feature, context: BuildContext, report: RebuildReport
    ) -> dict[str, object] | None:
        # Cleared up front, not on success: features set an informational
        # message during execute ("M12 pair, normal clearance"), and clearing it
        # afterwards would throw away exactly what the timeline wants to show.
        feature.message = ""
        try:
            outputs = feature.execute(context)
        except ReferenceLost as exc:
            # Distinct from a hard failure: the geometry is fine, we just lost
            # track of which face or edge the user picked.
            feature.state = FeatureState.NEEDS_ATTENTION
            feature.message = str(exc)
            report.failures[feature.id] = exc
            log.info("%s needs attention: %s", feature.name, exc)
            return None
        except BaseException as exc:  # noqa: BLE001 - OCCT raises non-Exceptions
            error = translate(exc, feature.label.lower())
            feature.state = FeatureState.FAILED
            feature.message = str(error)
            report.failures[feature.id] = error
            log.info("%s failed: %s", feature.name, error.detail or error)
            return None

        if not outputs:
            feature.state = FeatureState.FAILED
            feature.message = "This feature produced no geometry."
            report.failures[feature.id] = CadError(feature.message)
            return None

        feature.state = FeatureState.OK
        # Applied after the empty check above: a feature that produced nothing
        # has failed, but one whose outputs were all deleted is merely inert.
        outputs = feature.keep(outputs)
        if not feature.outputs:
            feature.outputs = list(outputs)
        return outputs

    #: Outputs whose names start with this are intermediate results -- a solved
    #: sketch, say -- passed to downstream features but never shown as bodies.
    INTERNAL_PREFIX = "__"

    def _sync_bodies(
        self, shapes: dict[str, object], produced_by: dict[str, str],
        consumed: set[str] | None = None,
    ) -> None:
        """Update the document's bodies, preserving per-body view settings."""
        document = self.document
        for name in consumed or ():
            document.bodies.pop(name, None)
        shapes = {
            name: shape for name, shape in shapes.items()
            if not name.startswith(self.INTERNAL_PREFIX)
        }
        for name, shape in shapes.items():
            body = document.bodies.get(name)
            if body is None:
                body = Body(name=name)
                document.bodies[name] = body
            body.shape = shape
            body.producer = produced_by.get(name, body.producer)
        # Bodies whose producing feature is gone should not linger.
        for name in list(document.bodies):
            if name not in shapes and document.producer_of(name) is None:
                del document.bodies[name]
