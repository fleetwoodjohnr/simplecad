"""Reusable clip geometry, layouts, and multi-output previews."""

from __future__ import annotations

import pytest

from simplecad.core.document import BodyRef, Document
from simplecad.core.geometry_service import build_preview_result, serialise_shape
from simplecad.core.naming import fingerprint, make_ref, sub_shapes
from simplecad.core.project import load, save
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.clips import ClipJointFeature, layout_points
from simplecad.kernel.occ import is_valid, volume
from simplecad.kernel.primitives import BoxFeature


def _face(shape, upward: bool):
    matches = []
    for face in sub_shapes(shape, "face"):
        data = fingerprint(face, "face")
        wanted = data.direction and data.direction[2] * (1 if upward else -1) > 0.99
        if data.geometry == "plane" and wanted:
            matches.append((data.center[2], face))
    return (max if upward else min)(matches, key=lambda item: item[0])[1]


def _document_and_feature(anchors, connector_names):
    document = Document("Clip test")
    moving = document.add_feature(BoxFeature(
        inputs={"width": 30, "depth": 20, "height": 12, "x": 50},
        outputs=["Moving"],
    ))
    target = document.add_feature(BoxFeature(
        inputs={"width": 30, "depth": 20, "height": 12}, outputs=["Target"]
    ))
    rebuilder = Rebuilder(document)
    assert rebuilder.rebuild().ok
    moving_shape = document.bodies["Moving"].shape
    target_shape = document.bodies["Target"].shape
    feature = ClipJointFeature(
        inputs={
            "body_a": BodyRef("Moving"), "body_b": BodyRef("Target"),
            "face_a": make_ref(
                moving_shape, _face(moving_shape, False), moving.id, body="Moving"
            ),
            "face_b": make_ref(
                target_shape, _face(target_shape, True), target.id, body="Target"
            ),
            "layout": "manual", "anchors": anchors,
            "size": "medium", "material": "petg",
            "retention": "standard", "clearance": 0.15,
            "connector_names": connector_names,
        },
        outputs=["Moving", "Target", *connector_names],
    )
    return document, rebuilder, feature


def test_manual_row_and_grid_layouts_are_uniform():
    assert layout_points("manual", [(1, 2), (3, 4)]) == [(1, 2), (3, 4)]
    assert layout_points("row", [(0, 0), (10, 0)], count=3) == [
        (0, 0), (5, 0), (10, 0),
    ]
    assert layout_points(
        "row", [(0, 0), (0, 20)], count=3,
        spacing_mode="fixed", spacing=2.5,
    ) == [(0, 0), (0, 2.5), (0, 5)]
    assert layout_points("grid", [(0, 0), (10, 20)], rows=2, columns=3) == [
        (0, 0), (5, 0), (10, 0), (0, 20), (5, 20), (10, 20),
    ]


def test_clip_joint_aligns_parts_cuts_two_sockets_and_emits_connectors():
    document, rebuilder, feature = _document_and_feature(
        [(-5, 0), (5, 0)], ["Clip", "Clip2"]
    )
    before = volume(document.bodies["Target"].shape)
    document.add_feature(feature)
    report = rebuilder.rebuild()
    assert report.ok, report.summary()
    assert set(document.bodies) == {"Moving", "Target", "Clip", "Clip2"}
    assert volume(document.bodies["Moving"].shape) < before
    assert volume(document.bodies["Target"].shape) < before
    assert all(is_valid(document.bodies[name].shape) for name in document.bodies)


def test_multi_output_preview_contains_both_parts_and_every_clip():
    document, _rebuilder, feature = _document_and_feature([(0, 0)], ["Clip"])
    result = build_preview_result(document, feature.to_dict())
    assert result["error"] is None
    assert result["shape"] is not None
    # Compound volume is the sum of two socketed parts and the independent clip.
    assert volume(result["shape"]) > 10_000


def test_clip_too_close_to_an_edge_is_rejected_before_cutting():
    document, rebuilder, feature = _document_and_feature([(14.5, 0)], ["Clip"])
    document.add_feature(feature)
    report = rebuilder.rebuild()
    assert not report.ok
    assert "does not fit" in str(report.failures[feature.id])


def test_calibrated_press_fit_may_use_a_small_negative_clearance():
    document, rebuilder, feature = _document_and_feature([(0, 0)], ["Clip"])
    feature.inputs["clearance"] = -0.05
    document.add_feature(feature)
    report = rebuilder.rebuild()
    assert report.ok, report.summary()
    assert is_valid(document.bodies["Clip"].shape)


def test_more_than_64_grid_positions_is_rejected():
    with pytest.raises(Exception, match="at most 64"):
        layout_points("grid", [(0, 0), (10, 10)], rows=9, columns=8)


def test_clip_joint_survives_save_and_rebuilds_all_three_linked_parts(tmp_path):
    document, rebuilder, feature = _document_and_feature([(0, 0)], ["Clip"])
    document.add_feature(feature)
    assert rebuilder.rebuild().ok

    path = save(document, str(tmp_path / "clipped.scad3"))
    reopened, cached = load(path)
    assert cached
    assert set(reopened.bodies) == {"Moving", "Target", "Clip"}

    joint = next(item for item in reopened.features if item.type_name == "clip_joint")
    assert joint.outputs == ["Moving", "Target", "Clip"]
    before = {
        name: serialise_shape(reopened.bodies[name].shape)
        for name in joint.outputs
    }

    # Moving the connector location must rebuild the connector and both socketed
    # parents from the same persistent feature rather than baking three unrelated
    # snapshots into the project.
    joint.inputs["anchors"] = [[3.0, 0.0]]
    reopened_builder = Rebuilder(reopened)
    reopened_builder.invalidate({joint.id})
    report = reopened_builder.rebuild()

    assert report.ok, report.summary()
    assert set(reopened.bodies) == {"Moving", "Target", "Clip"}
    assert all(is_valid(reopened.bodies[name].shape) for name in joint.outputs)
    assert all(
        serialise_shape(reopened.bodies[name].shape) != before[name]
        for name in joint.outputs
    )
