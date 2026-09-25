"""Face-relative placement and printable face-pattern geometry."""

from __future__ import annotations

import json

import pytest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from simplecad.core.document import BodyRef, Document
from simplecad.core.geometry_service import build_preview_result
from simplecad.core.naming import make_ref, sub_shapes
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.align import (
    edge_gaps_from_shape, frame_basis, frame_from_basis, solve, view_frame,
)
from simplecad.kernel.detect import analyse_cylinder, analyse_plane
from simplecad.kernel.face_texture import (
    PACKAGES, FaceTextureFeature, _cylindrical_tools, layout_for_face,
    mapping_for_face,
)
from simplecad.kernel.occ import built_shape, is_valid, transformed, volume
from simplecad.kernel.operations import AlignFeature
from simplecad.kernel.primitives import BoxFeature, CylinderFeature, TubeFeature
from simplecad.kernel.text import TextFeature


def _top(shape):
    return max(
        (face for face in sub_shapes(shape, "face")
         if (info := analyse_plane(face)) is not None and info.normal[2] > 0.9),
        key=lambda face: analyse_plane(face).area,
    )


def test_edge_placement_uses_object_outline_and_survives_target_resize():
    document = Document()
    target_feature = document.add_feature(BoxFeature(
        inputs={"width": "target_width", "depth": 60, "height": 5}, outputs=["Target"]
    ))
    moving_feature = document.add_feature(BoxFeature(
        inputs={"width": 10, "depth": 10, "height": 5, "x": 100}, outputs=["Moving"]
    ))
    document.parameters.set("target_width", "80")
    rebuilder = Rebuilder(document)
    assert rebuilder.rebuild().ok
    target = document.body("Target").shape
    moving = document.body("Moving").shape
    target_face = _top(target)
    moving_face = next(
        face for face in sub_shapes(moving, "face")
        if (info := analyse_plane(face)) is not None and info.normal[2] < -0.9
    )
    frame = view_frame(target_face, (0, 0, -1), (0, 1, 0))
    assert frame.x_axis[0] > 0.9 and frame.y_axis[1] > 0.9
    assert frame_from_basis(target_face, frame_basis(target_face, frame)).x_axis == pytest.approx(frame.x_axis)

    result = solve(
        moving_face, target_face, moving_body=moving, placement_mode="edge_gap",
        horizontal="right", vertical="top", horizontal_gap=25, vertical_gap=15,
        target_frame=frame,
    )
    assert edge_gaps_from_shape(frame, transformed(moving, result.transform), "right", "top") == pytest.approx((25, 15))

    document.add_feature(AlignFeature(inputs={
        "body": BodyRef("Moving"),
        "moving_face": make_ref(moving, moving_face, moving_feature.id, body="Moving"),
        "target_face": make_ref(target, target_face, target_feature.id, body="Target"),
        "operation": "stack", "placement_mode": "edge_gap",
        "horizontal": "right", "vertical": "top",
        "horizontal_gap": "25", "vertical_gap": "15",
        "target_basis": frame_basis(target_face, frame),
    }, outputs=["Moving"]))
    assert rebuilder.rebuild().ok
    document.parameters.set("target_width", "100")
    rebuilder.invalidate_parameter("target_width")
    assert rebuilder.rebuild().ok
    new_frame = frame_from_basis(_top(document.body("Target").shape), frame_basis(target_face, frame))
    assert edge_gaps_from_shape(new_frame, document.body("Moving").shape, "right", "top") == pytest.approx((25, 15), abs=1e-5)


def test_all_builtin_patterns_fit_a_flat_face_and_respect_a_hole():
    plate = BRepPrimAPI_MakeBox(30, 30, 4).Shape()
    drill = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(15, 15, -1), gp_Dir(0, 0, 1)), 3, 6).Shape()
    plate = built_shape(BRepAlgoAPI_Cut(plate, drill), "hole")
    face = _top(plate)
    assert len(PACKAGES["builtin.v1"]) == 16
    for pattern, definition in PACKAGES["builtin.v1"].items():
        layout = layout_for_face(face, {
            "pattern": pattern, "mode": definition.default_mode,
            "size": 4, "spacing": 3, "depth": 0.5,
            "edge_clearance": 2, "hole_clearance": 2,
        })
        assert layout.motifs, pattern
        holes = [source for kind, source, _buffered in layout.protected if kind == "hole"]
        assert holes, pattern
        for motif in layout.motifs:
            assert motif.distance(holes[0]) >= 2 - 1e-3, pattern


def test_flat_texture_is_solid_editable_and_serializable():
    document = Document()
    box = document.add_feature(BoxFeature(
        inputs={"width": "plate_width", "depth": 15, "height": 3}, outputs=["Plate"]
    ))
    document.parameters.set("plate_width", "15")
    rebuilder = Rebuilder(document)
    assert rebuilder.rebuild().ok
    shape = document.body("Plate").shape
    feature = document.add_feature(FaceTextureFeature(inputs={
        "body": BodyRef("Plate"),
        "face": make_ref(shape, _top(shape), box.id, body="Plate"),
        "pattern": "horizontal_lines", "mode": "raised",
        "size": "5", "spacing": "5", "depth": "0.4", "edge_clearance": "1",
    }, outputs=["Plate"]))
    assert rebuilder.rebuild().ok
    before = volume(document.body("Plate").shape)
    assert before > 15 * 15 * 3
    assert is_valid(document.body("Plate").shape)

    preview_state = feature.to_dict()
    preview_state["inputs"]["depth"] = "0.8"
    preview_state["__edit_feature_id"] = feature.id
    preview = build_preview_result(document, preview_state)
    assert preview["error"] is None
    assert "base_face" in preview["parts"]
    assert volume(preview["shape"]) > before
    assert volume(document.body("Plate").shape) == pytest.approx(before)

    guide_state = feature.to_dict()
    guide_state["__edit_feature_id"] = feature.id
    guide_state["__base_face_only"] = True
    guide = build_preview_result(document, guide_state)
    assert guide["error"] is None
    assert guide["shape"] is None
    assert "base_face" in guide["parts"]
    assert volume(document.body("Plate").shape) == pytest.approx(before)

    restored = Document.from_dict(json.loads(json.dumps(document.to_dict())))
    restored.parameters.set("plate_width", "18")
    assert Rebuilder(restored).rebuild().ok
    assert is_valid(restored.body("Plate").shape)


def test_rotated_engraved_text_gets_its_own_clearance():
    document = Document()
    box = document.add_feature(BoxFeature(
        inputs={"width": 60, "depth": 30, "height": 10}, outputs=["Plate"]
    ))
    rebuilder = Rebuilder(document)
    assert rebuilder.rebuild().ok
    shape = document.body("Plate").shape
    document.add_feature(TextFeature(inputs={
        "body": BodyRef("Plate"),
        "face": make_ref(shape, _top(shape), box.id, body="Plate"),
        "text": "A", "font_family": "sans-serif", "mode": "engraved",
        "text_height": 8, "depth": 1, "rotation": 32,
    }, outputs=["Plate"]))
    assert rebuilder.rebuild().ok
    layout = layout_for_face(_top(document.body("Plate").shape), {
        "pattern": "horizontal_lines", "size": 4, "spacing": 5,
        "edge_clearance": 2, "text_clearance": 3, "other_clearance": 0,
    }, document, "Plate")
    text_regions = [source for kind, source, _buffered in layout.protected if kind == "text"]
    assert text_regions
    assert all(motif.distance(region) >= 3 - 1e-3 for motif in layout.motifs for region in text_regions)


@pytest.mark.parametrize("internal,mode", [(False, "raised"), (True, "recessed")])
def test_wrapped_texture_on_both_sides_of_tube(internal, mode):
    document = Document()
    tube = document.add_feature(TubeFeature(
        inputs={"outer_radius": 7, "inner_radius": 5, "height": 8}, outputs=["Tube"]
    ))
    rebuilder = Rebuilder(document)
    assert rebuilder.rebuild().ok
    shape = document.body("Tube").shape
    face = next(
        face for face in sub_shapes(shape, "face")
        if (info := analyse_cylinder(face)) is not None and info.internal == internal
    )
    original = volume(shape)
    document.add_feature(FaceTextureFeature(inputs={
        "body": BodyRef("Tube"),
        "face": make_ref(shape, face, tube.id, body="Tube"),
        "pattern": "horizontal_lines", "mode": mode,
        "size": 5, "spacing": 5, "depth": 0.5, "edge_clearance": 1,
    }, outputs=["Tube"]))
    report = rebuilder.rebuild()
    assert report.ok, report.summary()
    result = document.body("Tube").shape
    assert is_valid(result)
    assert volume(result) > original if mode == "raised" else volume(result) < original


def test_texture_wraps_a_partial_cylindrical_face():
    cylinder = BRepPrimAPI_MakeCylinder(8, 12).Shape()
    clip = BRepPrimAPI_MakeBox(gp_Pnt(0, -9, -1), 9, 18, 14).Shape()
    section = built_shape(BRepAlgoAPI_Common(cylinder, clip), "curved section")
    face = next(face for face in sub_shapes(section, "face") if analyse_cylinder(face) is not None)
    mapping = mapping_for_face(face)
    assert mapping.period is None
    layout = layout_for_face(face, {
        "pattern": "hexagon", "size": 5, "spacing": 8, "depth": 0.3,
        "edge_clearance": 1,
    }, mapping=mapping)
    tools = _cylindrical_tools(layout, 0.3, "raised", section)
    result = built_shape(BRepAlgoAPI_Fuse(section, tools), "partial cylinder texture")
    assert is_valid(result)
    assert volume(result) > volume(section)


@pytest.mark.slow
def test_vent_pattern_cuts_through_a_curved_tube_wall():
    document = Document()
    tube = document.add_feature(TubeFeature(
        inputs={"outer_radius": 7, "inner_radius": 5, "height": 8}, outputs=["Tube"]
    ))
    rebuilder = Rebuilder(document)
    assert rebuilder.rebuild().ok
    shape = document.body("Tube").shape
    face = next(
        face for face in sub_shapes(shape, "face")
        if (info := analyse_cylinder(face)) is not None and not info.internal
    )
    before = volume(shape)
    document.add_feature(FaceTextureFeature(inputs={
        "body": BodyRef("Tube"),
        "face": make_ref(shape, face, tube.id, body="Tube"),
        "pattern": "vent", "mode": "through",
        "size": 5, "spacing": 5, "edge_clearance": 1,
    }, outputs=["Tube"]))
    report = rebuilder.rebuild()
    assert report.ok, report.summary()
    assert is_valid(document.body("Tube").shape)
    assert volume(document.body("Tube").shape) < before
