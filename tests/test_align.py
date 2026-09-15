"""Align / Stack / Concentric.

These are checked geometrically rather than by eye: after the solved transform
is applied, the faces really are touching, the axes really are collinear, and
the parts do not overlap.
"""

from __future__ import annotations

import math

import pytest

from simplecad.core.naming import sub_shapes
from simplecad.kernel.align import planar_frame, reference_frame, solve, suggest, support_face
from simplecad.kernel.detect import analyse_cylinder, analyse_plane
from simplecad.kernel.occ import bounding_box, transformed, volume


def box(dx, dy, dz, at=(0.0, 0.0, 0.0)):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    return BRepPrimAPI_MakeBox(gp_Pnt(*at), dx, dy, dz).Shape()


def cylinder(radius, height, at=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0)):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    return BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(*at), gp_Dir(*direction)), radius, height
    ).Shape()


def plate_with_bore(size=40.0, thickness=10.0, radius=5.0, at=(0.0, 0.0, 0.0)):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    plate = BRepPrimAPI_MakeBox(gp_Pnt(*at), size, size, thickness).Shape()
    bore = BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(at[0] + size / 2, at[1] + size / 2, at[2] - 1), gp_Dir(0, 0, 1)),
        radius, thickness + 2,
    ).Shape()
    cut = BRepAlgoAPI_Cut(plate, bore)
    cut.Build()
    return cut.Shape()


def face_with_normal(shape, normal, at_height=None):
    """The planar face whose outward normal matches, optionally at a height."""
    for face in sub_shapes(shape, "face"):
        info = analyse_plane(face)
        if info is None:
            continue
        if all(abs(a - b) < 1e-6 for a, b in zip(info.normal, normal)):
            if at_height is None or abs(info.center[2] - at_height) < 1e-6:
                return face
    raise AssertionError(f"no face with normal {normal}")


def only_cylinder(shape):
    for face in sub_shapes(shape, "face"):
        info = analyse_cylinder(face)
        if info is not None:
            return face
    raise AssertionError("no cylindrical face")


# ----------------------------------------------------------------------
def test_suggests_stack_for_two_flat_faces():
    a, b = box(20, 20, 10), box(30, 30, 10, at=(100, 0, 0))
    assert suggest(face_with_normal(a, (0, 0, -1)), face_with_normal(b, (0, 0, 1))) == "stack"


def test_suggests_concentric_for_two_round_faces():
    shaft = cylinder(4.9, 20, at=(100, 0, 0))
    plate = plate_with_bore(radius=5.0)
    assert suggest(only_cylinder(shaft), only_cylinder(plate)) == "concentric"


def test_stack_puts_two_faces_in_contact():
    """The headline case: select a face on each part, press Stack."""
    moving = box(20, 20, 10, at=(80, 55, 33))       # deliberately somewhere odd
    target = box(40, 40, 12)

    moving_face = face_with_normal(moving, (0, 0, -1))   # its underside
    target_face = face_with_normal(target, (0, 0, 1), at_height=12.0)

    result = solve(moving_face, target_face)
    assert result.operation == "stack"
    placed = transformed(moving, result.transform)

    low, high = bounding_box(placed)
    assert low[2] == pytest.approx(12.0, abs=1e-6), "must sit on the target's top face"
    # And centred on that face, since face centres are brought together.
    assert (low[0] + high[0]) / 2 == pytest.approx(20.0, abs=1e-6)
    assert (low[1] + high[1]) / 2 == pytest.approx(20.0, abs=1e-6)


def test_stack_rotates_a_part_that_starts_the_wrong_way_up():
    moving = box(20, 20, 10)
    target = box(40, 40, 12)
    # Use the moving part's *top* face: it must be turned over to make contact.
    moving_face = face_with_normal(moving, (0, 0, 1), at_height=10.0)
    target_face = face_with_normal(target, (0, 0, 1), at_height=12.0)

    placed = transformed(moving, solve(moving_face, target_face).transform)
    low, high = bounding_box(placed)
    assert low[2] == pytest.approx(12.0, abs=1e-6)
    assert high[2] == pytest.approx(22.0, abs=1e-6)


def test_stack_onto_a_vertical_face_works_the_same_way():
    moving = box(10, 10, 10, at=(200, 200, 200))
    target = box(40, 40, 40)
    moving_face = face_with_normal(moving, (0, 0, -1))
    target_face = face_with_normal(target, (1, 0, 0))    # the +X side

    placed = transformed(moving, solve(moving_face, target_face).transform)
    low, high = bounding_box(placed)
    assert low[0] == pytest.approx(40.0, abs=1e-6), "must sit against the +X face"
    assert (low[1] + high[1]) / 2 == pytest.approx(20.0, abs=1e-6)
    assert (low[2] + high[2]) / 2 == pytest.approx(20.0, abs=1e-6)


def test_stack_offset_leaves_a_gap():
    moving = box(20, 20, 10, at=(70, 70, 70))
    target = box(40, 40, 12)
    result = solve(
        face_with_normal(moving, (0, 0, -1)),
        face_with_normal(target, (0, 0, 1), at_height=12.0),
        offset=2.0,
    )
    low, _high = bounding_box(transformed(moving, result.transform))
    assert low[2] == pytest.approx(14.0, abs=1e-6)


def test_signed_reference_offsets_match_left_right_up_down_labels():
    moving = box(10, 10, 5, at=(100, 100, 30))
    target = box(40, 40, 4)
    moving_face = face_with_normal(moving, (0, 0, -1))
    target_face = face_with_normal(target, (0, 0, 1), at_height=4)
    frame = reference_frame(target_face)

    for u, v in ((-21, 0), (21, 0), (0, -21), (0, 21)):
        result = solve(
            moving_face, target_face, operation="stack",
            moving_anchor="center", target_anchor="center", u=u, v=v,
        )
        placed = transformed(moving, result.transform)
        centre = tuple(sum(pair) / 2 for pair in zip(*bounding_box(placed)))
        relative = tuple(centre[i] - frame.center[i] for i in range(3))
        assert sum(relative[i] * frame.x_axis[i] for i in range(3)) == pytest.approx(u)
        assert sum(relative[i] * frame.y_axis[i] for i in range(3)) == pytest.approx(v)


def test_signed_reference_offsets_stay_correct_on_a_rotated_face():
    moving = box(8, 8, 3, at=(100, 100, 100))
    target = box(4, 40, 40)
    moving_face = face_with_normal(moving, (0, 0, -1))
    target_face = face_with_normal(target, (1, 0, 0))
    frame = reference_frame(target_face)
    placed = transformed(moving, solve(
        moving_face, target_face,
        moving_anchor="center", target_anchor="center", u=-7, v=9,
    ).transform)
    centre = tuple(sum(pair) / 2 for pair in zip(*bounding_box(placed)))
    # The moving box centre is also its selected-face centre plus half its
    # thickness along the target normal, so in-plane coordinates are exact.
    relative = tuple(centre[i] - frame.center[i] for i in range(3))
    assert sum(relative[i] * frame.x_axis[i] for i in range(3)) == pytest.approx(-7)
    assert sum(relative[i] * frame.y_axis[i] for i in range(3)) == pytest.approx(9)


def test_stacked_parts_do_not_overlap():
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common

    moving = box(20, 20, 10, at=(60, 20, 5))
    target = box(40, 40, 12)
    placed = transformed(
        moving,
        solve(
            face_with_normal(moving, (0, 0, -1)),
            face_with_normal(target, (0, 0, 1), at_height=12.0),
        ).transform,
    )
    common = BRepAlgoAPI_Common(placed, target)
    common.Build()
    assert abs(volume(common.Shape())) < 1e-6


def test_concentric_drops_a_shaft_into_its_hole():
    """Select shaft, select hole, press Concentric -- the spec's example."""
    shaft = cylinder(4.9, 30, at=(150, -60, 12))
    plate = plate_with_bore(size=40, thickness=10, radius=5.0)

    result = solve(only_cylinder(shaft), only_cylinder(plate))
    assert result.operation == "concentric"
    placed = transformed(shaft, result.transform)

    info = analyse_cylinder(only_cylinder(placed))
    # The shaft axis must now run through the bore's axis at (20, 20).
    assert info.origin[0] == pytest.approx(20.0, abs=1e-6)
    assert info.origin[1] == pytest.approx(20.0, abs=1e-6)
    assert info.direction == pytest.approx((0.0, 0.0, 1.0))


def test_concentric_reorients_a_shaft_lying_on_another_axis():
    shaft = cylinder(4.9, 30, at=(0, 0, 0), direction=(1, 0, 0))   # along X
    plate = plate_with_bore(radius=5.0)

    placed = transformed(shaft, solve(only_cylinder(shaft), only_cylinder(plate)).transform)
    info = analyse_cylinder(only_cylinder(placed))
    assert info.direction == pytest.approx((0.0, 0.0, 1.0)), "must be turned upright"
    assert info.origin[0] == pytest.approx(20.0, abs=1e-6)
    assert info.origin[1] == pytest.approx(20.0, abs=1e-6)


def test_concentric_offset_slides_along_the_axis():
    shaft = cylinder(4.9, 30, at=(150, -60, 12))
    plate = plate_with_bore(radius=5.0)
    placed = transformed(
        shaft, solve(only_cylinder(shaft), only_cylinder(plate), offset=3.0).transform
    )
    info = analyse_cylinder(only_cylinder(placed))
    assert info.origin[2] == pytest.approx(analyse_cylinder(only_cylinder(plate)).origin[2] + 3.0, abs=1e-6)


def test_mixing_a_flat_and_a_round_face_reports_a_useful_error():
    from simplecad.core.errors import CadError

    flat = face_with_normal(box(20, 20, 10), (0, 0, 1), at_height=10.0)
    round_face = only_cylinder(cylinder(5, 20, at=(100, 0, 0)))
    with pytest.raises(CadError) as caught:
        solve(round_face, flat, operation="concentric")
    assert "cylindrical" in str(caught.value).lower()


def test_stack_places_a_face_center_from_the_target_left_and_bottom_edges():
    moving = box(10, 10, 10, at=(100, 100, 100))
    target = box(80, 60, 12)
    moving_face = face_with_normal(moving, (0, 0, -1))
    target_face = face_with_normal(target, (0, 0, 1), at_height=12.0)
    frame = planar_frame(target_face)

    result = solve(
        moving_face,
        target_face,
        operation="stack",
        x=28.0,
        y=25.0,
        x_anchor="left",
        y_anchor="bottom",
    )
    placed_face = transformed(moving_face, result.transform)
    center = analyse_plane(placed_face).center
    expected = frame.point(frame.x_bounds[0] + 28.0, frame.y_bounds[0] + 25.0)
    assert center == pytest.approx(expected, abs=1e-6)


def test_right_and_top_anchor_offsets_point_inward():
    moving = box(10, 10, 10, at=(100, 100, 100))
    target = box(80, 60, 12)
    moving_face = face_with_normal(moving, (0, 0, -1))
    target_face = face_with_normal(target, (0, 0, 1), at_height=12.0)
    frame = planar_frame(target_face)

    placed_face = transformed(
        moving_face,
        solve(
            moving_face,
            target_face,
            x=8.0,
            y=5.0,
            x_anchor="right",
            y_anchor="top",
        ).transform,
    )
    expected = frame.point(frame.x_bounds[1] - 8.0, frame.y_bounds[1] - 5.0)
    assert analyse_plane(placed_face).center == pytest.approx(expected, abs=1e-6)


def test_whole_body_support_inference_chooses_the_cylinder_cap_nearest_the_target():
    target = box(60, 60, 12)
    target_face = face_with_normal(target, (0, 0, 1), at_height=12.0)
    moving = cylinder(8, 30, at=(100, 100, 70))
    inferred = support_face(moving, target_face)
    info = analyse_plane(inferred)
    assert info is not None
    assert info.center[2] == pytest.approx(70.0, abs=1e-6)


def test_align_feature_persists_face_local_anchor_inputs():
    from simplecad.core.document import BodyRef, Document
    from simplecad.core.naming import make_ref
    from simplecad.core.rebuild import Rebuilder
    from simplecad.kernel.operations import AlignFeature
    from simplecad.kernel.primitives import BoxFeature

    document = Document("Placed")
    target_feature = document.add_feature(
        BoxFeature(inputs={"width": 80, "depth": 60, "height": 12}, outputs=["Base"])
    )
    moving_feature = document.add_feature(
        BoxFeature(
            inputs={"width": 10, "depth": 10, "height": 10, "x": 100},
            outputs=["Post"],
        )
    )
    builder = Rebuilder(document)
    assert builder.rebuild().ok
    target_face = face_with_normal(document.bodies["Base"].shape, (0, 0, 1), 12)
    moving_face = face_with_normal(document.bodies["Post"].shape, (0, 0, -1))
    frame = planar_frame(target_face)
    document.add_feature(
        AlignFeature(
            inputs={
                "body": BodyRef("Post"),
                "moving_face": make_ref(
                    document.bodies["Post"].shape, moving_face,
                    moving_feature.id, body="Post",
                ),
                "target_face": make_ref(
                    document.bodies["Base"].shape, target_face,
                    target_feature.id, body="Base",
                ),
                "operation": "stack",
                "x_anchor": "left",
                "y_anchor": "bottom",
                "x": 28,
                "y": 25,
            },
            outputs=["Post"],
        )
    )
    assert builder.rebuild().ok
    placed_face = face_with_normal(document.bodies["Post"].shape, (0, 0, -1))
    expected = frame.point(frame.x_bounds[0] + 28, frame.y_bounds[0] + 25)
    assert analyse_plane(placed_face).center == pytest.approx(expected, abs=1e-5)
