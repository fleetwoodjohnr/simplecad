"""Locate a visible blend back to the one source edge that authored it."""

from __future__ import annotations

from dataclasses import replace

from .errors import CadError
from .naming import SubShapeRef, fingerprint, resolve, similarity


def _face_similarity(left, right):
    """Compare faces without treating a reversed normal as a different face."""
    made = fingerprint(left, "face")
    picked = fingerprint(right, "face")
    score = similarity(made, picked)
    if made.direction is not None:
        score = max(score, similarity(replace(
            made, direction=tuple(-axis for axis in made.direction),
        ), picked))
    return score


def inspect_edge_finish(document, body_name: str, pick_state: dict) -> dict:
    """Run in the geometry worker, where OCCT blend builders are crash-isolated."""
    from .rebuild import Rebuilder
    from .naming import sub_shapes
    from ..kernel.operations import EdgeFinishFeature, _faces_at_edge, edge_finish_specs
    from OCP.TopoDS import TopoDS

    body = document.body(body_name)
    if body is None or body.shape is None:
        raise CadError("The selected object is unavailable.")
    reference = SubShapeRef.from_dict(pick_state)
    selected = resolve(reference, body.shape)
    targets = ([selected] if reference.kind == "face" else
               _faces_at_edge(body.shape, TopoDS.Edge_s(selected)))
    if not targets:
        raise CadError("Select a fillet or chamfer face or one of its boundary edges.")

    graph = document.prepare_graph()
    head = graph.latest.get(body_name)
    candidates = set(graph.writers.get(head.object_id, ())) if head else set()
    builder = document._rebuilder or Rebuilder(document)
    matches = []
    for original in reversed(document.features):
        if original.id not in candidates or original.type_name not in {
            "fillet", "chamfer", "edge_finish",
        } or original.suppressed:
            continue
        specs = edge_finish_specs(original)
        if not specs:
            continue
        editable = EdgeFinishFeature(
            feature_id=original.id, name=original.name,
            inputs={"body": original.inputs["body"], "edge_specs": specs},
            outputs=list(original.outputs),
        )
        editable.bindings = dict(original.bindings)
        editable.output_ids = dict(original.output_ids)
        try:
            context = builder.context_for(editable, edit_id=original.id)
            _shape, generated = editable.build_with_generated(context)
            # Follow the actual result's topology through later rigid moves.
            # A moved face has a new location but keeps the same TShape.
            actual = builder._get(original.id).get(original.outputs[0])
            actual_faces = sub_shapes(actual, "face") if actual is not None else []
        except BaseException:
            continue
        for index, shapes in enumerate(generated):
            for face in shapes:
                authored = [candidate for candidate in actual_faces
                            if _face_similarity(face, candidate) >= 0.995]
                for target in targets:
                    if face.IsSame(target) or face.IsPartner(target):
                        matches.append((original, index, 1.0))
                        break
                    try:
                        score = _face_similarity(face, target)
                    except BaseException:
                        score = 0.0
                    if score < 0.995 and any(candidate.IsPartner(target) for candidate in authored):
                        score = 1.0
                    if score >= 0.995:
                        matches.append((original, index, score))
                        break
    unique = {(feature.id, index): (feature, index, score)
              for feature, index, score in matches}
    if len(unique) != 1:
        raise CadError(
            "This blend cannot be linked to one source edge unambiguously.",
            suggestion="Select its feature in History to inspect it, or pick another blend face.",
        )
    feature, index, _score = next(iter(unique.values()))
    return {"feature_id": feature.id, "edge_index": index,
            "feature": feature.to_dict(), "body": body_name}
