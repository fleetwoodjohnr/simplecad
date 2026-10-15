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


# ----------------------------------------------------------------------
# Edge-gap frames: the camera picks an axis, never an angle
# ----------------------------------------------------------------------
def test_the_view_frame_uses_the_faces_own_axes_whatever_the_camera():
    """Otherwise "the left edge" is a diagonal, and the saved basis is a tilt.

    A frame taken straight from the projected camera vectors sits at whatever
    angle the user happened to be looking from -- one model here stores 13.3
    degrees. Gaps typed against an edge are then not measured to that edge, and
    the four dot products written into the file describe a direction OCCT is
    free to re-author under them.
    """
    from simplecad.kernel.align import frame_basis, view_frame

    target = box(80, 60, 12)
    target_face = face_with_normal(target, (0, 0, 1), at_height=12.0)
    base = planar_frame(target_face)
    axes = {
        tuple(round(v, 9) for v in axis)
        for axis in (
            base.x_axis, tuple(-v for v in base.x_axis),
            base.y_axis, tuple(-v for v in base.y_axis),
        )
    }

    cameras = (
        ((0, 0, -1), (0, 1, 0)),                  # straight down
        ((0, 0, -1), (1, 0, 0)),                  # straight down, turned 90
        ((-0.577, -0.577, -0.577), (0, 0, 1)),    # iso: up is along the normal
        ((0, -1, -0.02), (0, 0, 1)),              # nearly edge on
    )
    for direction, up in cameras:
        frame = view_frame(target_face, direction, up)
        assert tuple(round(v, 9) for v in frame.x_axis) in axes
        assert tuple(round(v, 9) for v in frame.y_axis) in axes
        assert set(frame_basis(target_face, frame)) <= {0.0, 1.0, -1.0}
        # U right, V up and the outward normal stay right-handed.
        cross = (
            frame.x_axis[1] * frame.y_axis[2] - frame.x_axis[2] * frame.y_axis[1],
            frame.x_axis[2] * frame.y_axis[0] - frame.x_axis[0] * frame.y_axis[2],
            frame.x_axis[0] * frame.y_axis[1] - frame.x_axis[1] * frame.y_axis[0],
        )
        assert sum(a * b for a, b in zip(cross, frame.normal)) == pytest.approx(1.0)


def test_a_legacy_tilted_basis_still_rebuilds_where_it_was_authored():
    """Saved placements must not move because the convention improved."""
    from simplecad.kernel.align import basis_is_axis_aligned, frame_from_basis

    target = box(80, 60, 12)
    target_face = face_with_normal(target, (0, 0, 1), at_height=12.0)
    base = planar_frame(target_face)
    tilted = [0.9730015907360747, 0.23079840646128483,
              -0.2307984064612848, 0.9730015907360746]
    assert not basis_is_axis_aligned(tilted)

    frame = frame_from_basis(target_face, tilted)

    def combine(a, b):
        return tuple(
            base.x_axis[i] * a + base.y_axis[i] * b for i in range(3)
        )

    assert frame.x_axis == pytest.approx(combine(tilted[0], tilted[1]), abs=1e-12)
    assert frame.y_axis == pytest.approx(combine(tilted[2], tilted[3]), abs=1e-12)


def test_a_re_authored_plane_does_not_take_the_placement_with_it():
    """The stored basis names a face axis; a rebuild may renumber those.

    ``planar_frame`` reads the plane's authored X direction, which is whatever
    OCCT produced. When a rebuild re-authors it, the same four numbers point
    somewhere else -- and the part that was placed against an edge quietly
    moves. The world direction saved beside them settles it.
    """
    from simplecad.kernel.align import frame_from_basis

    target = box(80, 60, 12)
    target_face = face_with_normal(target, (0, 0, 1), at_height=12.0)
    base = planar_frame(target_face)
    swapped = [0.0, 1.0, -1.0, 0.0]       # as if X and Y had been renumbered

    restored = frame_from_basis(
        target_face, swapped,
        world_u=base.x_axis, world_normal=base.normal,
    )

    assert restored.x_axis == pytest.approx(base.x_axis, abs=1e-12)
    assert frame_from_basis(target_face, swapped).x_axis == pytest.approx(
        base.y_axis, abs=1e-12
    )


# ----------------------------------------------------------------------
# Faces made by modelling operations, rotation, flush contact
# ----------------------------------------------------------------------
def _rotated(shape, axis, degrees, pivot=(0.0, 0.0, 0.0)):
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

    transform = gp_Trsf()
    transform.SetRotation(gp_Ax1(gp_Pnt(*pivot), gp_Dir(*axis)), math.radians(degrees))
    return transformed(shape, transform)


def _flat_box(shape):
    return [value for corner in bounding_box(shape) for value in corner]


def _distance(first, second):
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    return BRepExtrema_DistShapeShape(first, second).Value()


def _image(transform, vector):
    from OCP.gp import gp_Vec

    result = gp_Vec(*vector)
    result.Transform(transform)
    return (result.X(), result.Y(), result.Z())


def _assert_flush(moving_face, target_face, transform, moving_body, target_body):
    """Opposed normals, coincident centroids, zero gap, no overlap."""
    from simplecad.kernel.align import interference_volume

    placed = analyse_plane(transformed(moving_face, transform))
    target = analyse_plane(target_face)
    assert placed.normal == pytest.approx(tuple(-n for n in target.normal), abs=1e-7)
    assert placed.center == pytest.approx(target.center, abs=1e-6)
    placed_body = transformed(moving_body, transform)
    assert _distance(placed_body, target_body) == pytest.approx(0.0, abs=1e-6)
    assert interference_volume(placed_body, target_body) == pytest.approx(0.0, abs=1e-6)
    assert volume(placed_body) == pytest.approx(volume(moving_body))


def _split_half_on_a_base():
    from simplecad.core.document import BodyRef, Document
    from simplecad.core.rebuild import Rebuilder
    from simplecad.kernel.primitives import BoxFeature, CylinderFeature
    from simplecad.kernel.split import SplitFeature

    document = Document("Stack a half")
    document.add_feature(BoxFeature(
        inputs={"width": 40, "depth": 30, "height": 12}, outputs=["Base"],
    ))
    document.add_feature(CylinderFeature(
        inputs={"radius": "10", "height": "30", "x": 100}, outputs=["Cylinder"],
    ))
    document.add_feature(SplitFeature(
        inputs={"body": BodyRef("Cylinder"), "normal": [1, 0, 0], "origin": [100, 0, 0],
                "position": 0.0, "names": ["Half", "Other half"]},
        outputs=["Half", "Other half"],
    ))
    rebuilder = Rebuilder(document)
    assert rebuilder.rebuild().ok
    return document, rebuilder


def _align_feature(document, moving, moving_face, target, target_face, **inputs):
    from simplecad.core.document import BodyRef
    from simplecad.core.naming import make_ref
    from simplecad.kernel.operations import AlignFeature

    return document.add_feature(AlignFeature(
        inputs={
            "body": BodyRef(moving),
            "moving_face": make_ref(document.body(moving).shape, moving_face,
                                    document.producer_of(moving).id, body=moving),
            "target_face": make_ref(document.body(target).shape, target_face,
                                    document.producer_of(target).id, body=target),
            "operation": "stack", "moving_anchor": "center", "target_anchor": "center",
            "u": "0", "v": "0", "offset": "0", "flip": False, "rotation": "0",
            **inputs,
        },
        outputs=[moving],
    ))


def test_a_split_halfs_cut_face_stacks_flush_and_centred_on_a_box(tmp_path):
    from simplecad.core.project import load, save
    from simplecad.core.rebuild import Rebuilder

    document, rebuilder = _split_half_on_a_base()
    half = document.body("Half").shape
    base = document.body("Base").shape
    cut_face = face_with_normal(half, (1, 0, 0))
    top = face_with_normal(base, (0, 0, 1), 12)
    _assert_flush(cut_face, top, solve(cut_face, top).transform, half, base)

    _align_feature(document, "Half", cut_face, "Base", top)
    report = rebuilder.rebuild()
    assert report.ok, report.summary()
    assert not report.warnings
    placed = document.body("Half").shape
    seated = face_with_normal(placed, (0, 0, -1))
    assert analyse_plane(seated).center == pytest.approx((20, 15, 12), abs=1e-6)
    assert _distance(placed, base) == pytest.approx(0.0, abs=1e-6)
    assert volume(placed) == pytest.approx(volume(half))
    low, high = bounding_box(placed)
    assert sorted(h - lo for lo, h in zip(low, high)) == pytest.approx([10, 20, 30], abs=1e-5)
    # Separate bodies, the target untouched.
    assert document.body("Base").shape is base
    assert sorted(document.bodies) == ["Base", "Half", "Other half"]

    reopened, _cached = load(save(document, str(tmp_path / "stacked")))
    assert Rebuilder(reopened).rebuild(force=True).ok
    assert _flat_box(reopened.body("Half").shape) == pytest.approx(_flat_box(placed), abs=1e-6)

    # Editing the moving part re-seats it; the base stays as it was.
    next(f for f in reopened.features if f.type_name == "cylinder").inputs["radius"] = "12"
    assert Rebuilder(reopened).rebuild(force=True).ok
    assert bounding_box(reopened.body("Half").shape)[0][2] == pytest.approx(12, abs=1e-6)
    assert _flat_box(reopened.body("Base").shape) == pytest.approx(_flat_box(base), abs=1e-6)


def test_stack_between_tilted_faces_of_different_sizes():
    target_body = _rotated(box(40, 30, 12), (1, 1, 0.3), 37)
    moving_body = _rotated(box(10, 6, 8, at=(80, 0, 0)), (0.2, -1, 1), 113, (80, 0, 0))
    target_face = next(f for f in sub_shapes(target_body, "face") if analyse_plane(f))
    for moving_face in [f for f in sub_shapes(moving_body, "face") if analyse_plane(f)]:
        _assert_flush(moving_face, target_face, solve(moving_face, target_face).transform,
                      moving_body, target_body)


def test_irregular_boolean_faces_meet_at_their_area_centroids():
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

    def cut(first, second):
        builder = BRepAlgoAPI_Cut(first, second)
        builder.Build()
        return builder.Shape()

    # An L-shaped base and a notched block: neither face is a rectangle.
    base = cut(box(40, 30, 10), box(20, 15, 20, at=(20, 15, -5)))
    block = cut(box(12, 12, 6, at=(100, 0, 0)), cylinder(4, 20, at=(112, 12, -5)))
    top = face_with_normal(base, (0, 0, 1), 10)
    bottom = face_with_normal(block, (0, 0, -1))
    assert analyse_plane(top).center[0] != pytest.approx(20)  # not the bounding-box middle
    _assert_flush(bottom, top, solve(bottom, top).transform, block, base)


def test_numeric_offsets_and_rotation_about_the_normal():
    target_body = box(40, 30, 10)
    moving_body = box(12, 4, 6, at=(100, 0, 0))
    top = face_with_normal(target_body, (0, 0, 1), 10)
    bottom = face_with_normal(moving_body, (0, 0, -1))
    frame = reference_frame(top)

    def placed(rotation, **values):
        return solve(bottom, top, moving_anchor="center", target_anchor="center",
                     rotation=rotation, **values).transform

    still = placed(0)
    long_edge = _image(still, (1, 0, 0))
    for angle in (90, 180, 37.5, -90):
        turned = placed(angle)
        # The face stays seated and centred; only the spin changes.
        _assert_flush(bottom, top, turned, moving_body, target_body)
        edge = _image(turned, (1, 0, 0))
        assert sum(a * b for a, b in zip(edge, long_edge)) == pytest.approx(math.cos(math.radians(angle)))
        # Positive is anticlockwise seen from above the target face.
        cross_z = long_edge[0] * edge[1] - long_edge[1] * edge[0]
        assert cross_z == pytest.approx(math.sin(math.radians(angle)), abs=1e-9)

    shifted = placed(90, u=5, v=-3, offset=2)
    center = analyse_plane(transformed(bottom, shifted)).center
    expected = frame.point(5, -3, 2)
    assert center == pytest.approx(expected, abs=1e-6)
    assert _distance(transformed(moving_body, shifted), target_body) == pytest.approx(2.0, abs=1e-6)


def test_rotation_round_trips_through_an_align_feature():
    document, rebuilder = _split_half_on_a_base()
    half = document.body("Half").shape
    cut_face = face_with_normal(half, (1, 0, 0))
    top = face_with_normal(document.body("Base").shape, (0, 0, 1), 12)
    _align_feature(document, "Half", cut_face, "Base", top, rotation="90 deg")
    assert rebuilder.rebuild().ok
    low, high = bounding_box(document.body("Half").shape)
    # A quarter turn swaps the 20 mm and 30 mm footprint, still centred.
    assert (high[0] - low[0], high[1] - low[1]) == pytest.approx((20, 30), abs=1e-5)
    assert ((low[0] + high[0]) / 2, (low[1] + high[1]) / 2) == pytest.approx((20, 15), abs=1e-5)


def test_stacking_is_deterministic_from_any_starting_orientation():
    target_body = box(40, 30, 10)
    top = face_with_normal(target_body, (0, 0, 1), 10)
    for axis, angle in (((1, 0, 0), 0), ((1, 0, 0), 90), ((0, 1, 0), 180), ((1, 2, 3), 71)):
        moving_body = _rotated(box(8, 6, 4, at=(100, 0, 0)), axis, angle, (100, 0, 0))
        moving_face = max((f for f in sub_shapes(moving_body, "face") if analyse_plane(f)),
                          key=lambda f: analyse_plane(f).area)
        first = solve(moving_face, top).transform
        _assert_flush(moving_face, top, first, moving_body, target_body)
        again = solve(moving_face, top).transform
        assert [first.Value(r, c) for r in (1, 2, 3) for c in (1, 2, 3, 4)] == pytest.approx(
            [again.Value(r, c) for r in (1, 2, 3) for c in (1, 2, 3, 4)])


def test_a_recessed_mounting_face_warns_instead_of_silently_overlapping():
    from simplecad.core.document import BodyRef, Document
    from simplecad.core.rebuild import Rebuilder
    from simplecad.kernel.operations import BooleanFeature
    from simplecad.kernel.primitives import BoxFeature

    document = Document("Step")
    document.add_feature(BoxFeature(inputs={"width": 40, "depth": 30, "height": 12}, outputs=["Base"]))
    document.add_feature(BoxFeature(
        inputs={"width": 20, "depth": 20, "height": 20, "x": 100}, outputs=["Step"]))
    document.add_feature(BoxFeature(
        inputs={"width": 10, "depth": 30, "height": 20, "x": 110, "y": -5, "z": 10},
        outputs=["Cutter"]))
    document.add_feature(BooleanFeature(
        inputs={"body": BodyRef("Step"), "tools": [BodyRef("Cutter")], "operation": "cut"},
        outputs=["Step"]))
    rebuilder = Rebuilder(document)
    assert rebuilder.rebuild().ok
    tread = face_with_normal(document.body("Step").shape, (0, 0, 1), 10)
    top = face_with_normal(document.body("Base").shape, (0, 0, 1), 12)
    align = _align_feature(document, "Step", tread, "Base", top)

    report = rebuilder.rebuild()
    assert report.ok
    assert any("overlaps the target" in warning for warning in report.warnings)

    # A deliberate interference fit is not second-guessed.
    align.inputs["offset"] = "-1"
    report = Rebuilder(document).rebuild(force=True)
    assert report.ok and not report.warnings
