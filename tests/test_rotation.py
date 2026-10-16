"""Rotation that does what was asked: ring angles, snapping, order, pivots."""

from __future__ import annotations

import math

import pytest
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

from simplecad.core.document import BodyRef, Document
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.occ import bounding_box, volume
from simplecad.kernel.operations import MoveFeature, move_transform
from simplecad.kernel.primitives import BoxFeature, CylinderFeature
from simplecad.kernel.split import SplitFeature
from simplecad.ui.viewport.gizmo import RotationSnapState, ring_angle

AXES = ((1, 0, 0), (0, 1, 0), (0, 0, 1))


@pytest.mark.parametrize("axis", AXES)
@pytest.mark.parametrize("degrees", [3.0, 45.0, 89.0, 91.0, 120.0, 150.0, 179.0, -100.0, -179.0])
def test_ring_angle_reads_every_axis_past_a_quarter_turn(axis, degrees):
    """Euler decomposition confined Y to +/-90: 120 degrees came back as 60."""
    transform = gp_Trsf()
    transform.SetRotation(gp_Ax1(gp_Pnt(12, -4, 7), gp_Dir(*axis)), math.radians(degrees))
    assert ring_angle(transform, axis) == pytest.approx(degrees, abs=1e-9)


def test_a_ring_dragged_past_180_keeps_turning():
    state = RotationSnapState(step=90.0)
    angles = [state.update(ring, bypass=True)[0] for ring in (60, 120, 170, -170, -120)]
    assert angles == pytest.approx([60, 120, 170, 190, 240])


@pytest.mark.parametrize("small", [1.0, 3.0, 6.0, -4.0])
def test_a_small_drag_is_not_snapped_back_to_nothing(small):
    """Zero used to be a detent, so any drag under 8 degrees committed nothing."""
    angle, snapped = RotationSnapState(step=90.0).update(small)
    assert angle == pytest.approx(small) and not snapped


def test_detents_follow_the_configured_step():
    state = RotationSnapState(step=15.0)
    assert state.update(29.0) == pytest.approx((30.0, True))
    state = RotationSnapState(step=15.0)
    assert state.update(37.0) == pytest.approx((37.0, False))


def test_ctrl_rounds_to_whole_degrees():
    assert RotationSnapState().update(12.4, fine=True) == pytest.approx((12.0, True))


def _point(transform, xyz):
    p = gp_Pnt(*xyz).Transformed(transform)
    return (p.X(), p.Y(), p.Z())


def test_xyz_order_turns_x_first_then_y_then_z():
    transform = move_transform((0, 0, 0), (90, 90, 0), (0, 0, 0), "xyz")
    # X 90 takes +Y to +Z; then Y 90 takes +Z to +X.
    assert _point(transform, (0, 1, 0)) == pytest.approx((1, 0, 0), abs=1e-9)


def test_legacy_moves_keep_their_saved_composition():
    """No ``order`` key: Z first, exactly as projects saved before were built."""
    legacy = move_transform((0, 0, 0), (90, 90, 0), (0, 0, 0))
    expected = gp_Trsf()
    rx, ry = gp_Trsf(), gp_Trsf()
    rx.SetRotation(gp_Ax1(gp_Pnt(), gp_Dir(1, 0, 0)), math.pi / 2)
    ry.SetRotation(gp_Ax1(gp_Pnt(), gp_Dir(0, 1, 0)), math.pi / 2)
    expected = rx.Multiplied(ry)
    assert _point(legacy, (0, 1, 0)) == pytest.approx(_point(expected, (0, 1, 0)), abs=1e-9)
    assert _point(legacy, (0, 1, 0)) != pytest.approx((1, 0, 0), abs=1e-6)


def _split_half() -> Document:
    document = Document("Rotate")
    document.add_feature(CylinderFeature(inputs={"radius": 10, "height": 30}, outputs=["Cylinder"]))
    document.add_feature(SplitFeature(inputs={
        "body": BodyRef("Cylinder"), "normal": [1, 0, 0], "origin": [0, 0, 0],
        "position": 0.0, "names": ["Cylinder A", "Cylinder B"],
    }, outputs=["Cylinder A", "Cylinder B"]))
    assert Rebuilder(document).rebuild().ok
    return document


@pytest.mark.parametrize("key", ["rx", "ry", "rz"])
def test_a_split_half_turns_120_degrees_about_every_axis_and_back(key):
    document = _split_half()
    rebuilder = Rebuilder(document)
    start = bounding_box(document.body("Cylinder A").shape)
    pivot = [0.0, 0.0, 15.0]
    for angle in (120, -120):
        document.add_feature(MoveFeature(inputs={
            "body": BodyRef("Cylinder A"), "dx": 0, "dy": 0, "dz": 0, key: angle,
            "pivot": pivot, "order": "xyz",
        }, outputs=["Cylinder A"]))
        report = rebuilder.rebuild()
        assert report.ok, report.summary()
    end = bounding_box(document.body("Cylinder A").shape)
    # A stored pivot makes +t then -t an exact round trip, even for a part
    # whose bounding-box centre moves as it turns.
    assert end[0] == pytest.approx(start[0], abs=1e-6)
    assert end[1] == pytest.approx(start[1], abs=1e-6)
    assert bounding_box(document.body("Cylinder B").shape)[0][0] == pytest.approx(0.0, abs=1e-6)


def test_rotating_several_bodies_about_a_shared_pivot_keeps_their_layout():
    document = Document("Pair")
    document.add_feature(BoxFeature(inputs={"width": 10, "depth": 10, "height": 10}, outputs=["A"]))
    document.add_feature(BoxFeature(inputs={"width": 10, "depth": 10, "height": 10, "x": 30},
                                    outputs=["B"]))
    rebuilder = Rebuilder(document)
    assert rebuilder.rebuild().ok
    pivot = [20.0, 5.0, 5.0]
    for name in ("A", "B"):
        document.add_feature(MoveFeature(inputs={
            "body": BodyRef(name), "dx": 0, "dy": 0, "dz": 0, "rz": 180,
            "pivot": pivot, "order": "xyz",
        }, outputs=[name]))
    assert rebuilder.rebuild().ok
    a_low, _ = bounding_box(document.body("A").shape)
    b_low, _ = bounding_box(document.body("B").shape)
    # 180 degrees about x=20 swaps the two ends of the pair.
    assert a_low[0] == pytest.approx(30.0, abs=1e-6)
    assert b_low[0] == pytest.approx(0.0, abs=1e-6)
    assert volume(document.body("A").shape) == pytest.approx(1000.0)


def test_nudge_axes_snap_to_one_world_axis_each():
    from simplecad.ui.main_window import _world_axes

    s2, s3, s6 = math.sqrt(2), math.sqrt(3), math.sqrt(6)
    iso = _world_axes((1 / s2, -1 / s2, 0), (-1 / s6, -1 / s6, 2 / s6), (-1 / s3, -1 / s3, -1 / s3))
    assert iso == ((1, 0, 0), (0, 0, 1), (0, -1, 0))
    assert sorted(map(abs, (i for axis in iso for i in axis))) == [0] * 6 + [1] * 3
