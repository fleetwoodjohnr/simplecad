"""The modelling operations that ship in the contextual toolbar.

Every one of these is one click away in the UI, so each needs to be known to
work -- especially Shell, which leans on ``MakeThickSolidByJoin``, among OCCT's
more temperamental calls.
"""

from __future__ import annotations

import math

import pytest

from simplecad.core.document import BodyRef, Document
from simplecad.core.naming import fingerprint, make_ref, sub_shapes
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.detect import analyse_cylinder, cylindrical_faces
from simplecad.kernel.occ import bounding_box, is_valid, volume
from simplecad.kernel.operations import (
    BooleanFeature, ChamferFeature, HoleFeature, MoveFeature, PushPullFeature,
    ShellFeature,
)
from simplecad.kernel.primitives import BoxFeature, CylinderFeature


@pytest.fixture
def doc():
    return Document("Ops")


def build(document):
    report = Rebuilder(document).rebuild()
    assert report.ok, report.summary()
    return report


def upward_face(shape):
    faces = [(f, fingerprint(f, "face")) for f in sub_shapes(shape, "face")]
    up = [(f, p) for f, p in faces
          if p.geometry == "plane" and p.direction and p.direction[2] > 0.99]
    return max(up, key=lambda item: item[1].center[2])[0]


def plate(document, name="Plate", **overrides):
    inputs = {"width": 40, "depth": 40, "height": 10}
    inputs.update(overrides)
    return document.add_feature(BoxFeature(inputs=inputs, outputs=[name]))


# ----------------------------------------------------------------------
def test_shell_hollows_a_box_and_leaves_the_wall(doc):
    feature = plate(doc, height=20)
    build(doc)
    solid = volume(doc.bodies["Plate"].shape)

    doc.add_feature(
        ShellFeature(
            inputs={
                "body": BodyRef("Plate"),
                "faces": [make_ref(
                    doc.bodies["Plate"].shape,
                    upward_face(doc.bodies["Plate"].shape),
                    feature.id, body="Plate",
                )],
                "thickness": 2,
            },
            outputs=["Plate"],
        )
    )
    build(doc)
    shelled = doc.bodies["Plate"].shape
    assert is_valid(shelled)
    assert volume(shelled) < solid, "shelling must remove the interior"
    # Outer size is unchanged: the wall goes inward.
    low, high = bounding_box(shelled)
    assert (high[0] - low[0]) == pytest.approx(40.0, abs=1e-6)
    assert (high[2] - low[2]) == pytest.approx(20.0, abs=1e-6)
    # A 40x40x20 box with 2 mm walls, open on top.
    expected = 40 * 40 * 20 - 36 * 36 * 18
    assert volume(shelled) == pytest.approx(expected, rel=0.02)


def test_shell_refuses_a_wall_thicker_than_the_body(doc):
    """Opposite walls meeting in the middle is a friendly error, not a crash.

    MakeThickSolidByJoin fails opaquely there, so the guard has to catch it
    first and say what would work.
    """
    feature = plate(doc, height=20)
    build(doc)

    doc.add_feature(
        ShellFeature(
            inputs={
                "body": BodyRef("Plate"),
                "faces": [make_ref(
                    doc.bodies["Plate"].shape,
                    upward_face(doc.bodies["Plate"].shape),
                    feature.id, body="Plate",
                )],
                "thickness": 12,   # the box is only 20 mm through
            },
            outputs=["Plate"],
        )
    )
    report = Rebuilder(doc).rebuild()
    assert not report.ok
    assert "too thick" in report.summary().lower()
    # The limit is named, so the message is actionable.
    assert "10.00 mm" in report.summary()


def test_push_pull_refuses_to_cut_straight_through(doc):
    """Pushing a face in further than the body is deep must be refused.

    OCCT reports that as a *successful* cut that returns nothing, so without a
    guard the user gets a body that is in the tree and not in the model.
    """
    feature = plate(doc)                       # 40 x 40 x 10
    build(doc)
    face_ref = make_ref(
        doc.bodies["Plate"].shape,
        upward_face(doc.bodies["Plate"].shape),
        feature.id, body="Plate",
    )
    doc.add_feature(
        PushPullFeature(
            inputs={"body": BodyRef("Plate"), "face": face_ref, "distance": -14},
            outputs=["Plate"],
        )
    )
    report = Rebuilder(doc).rebuild()
    assert not report.ok
    assert "through this body" in report.summary()


def test_push_pull_cuts_a_face_inward(doc):
    """The negative direction, which nothing exercised before."""
    feature = plate(doc)                       # 40 x 40 x 10
    build(doc)
    face_ref = make_ref(
        doc.bodies["Plate"].shape,
        upward_face(doc.bodies["Plate"].shape),
        feature.id, body="Plate",
    )
    doc.add_feature(
        PushPullFeature(
            inputs={"body": BodyRef("Plate"), "face": face_ref, "distance": -4},
            outputs=["Plate"],
        )
    )
    build(doc)
    shape = doc.bodies["Plate"].shape
    assert is_valid(shape)
    low, high = bounding_box(shape)
    assert (high[2] - low[2]) == pytest.approx(6.0, abs=1e-6)
    assert volume(shape) == pytest.approx(40 * 40 * 6, rel=1e-6)


def test_chamfer_removes_material_from_the_selected_edges(doc):
    feature = plate(doc)
    build(doc)
    before = volume(doc.bodies["Plate"].shape)

    shape = doc.bodies["Plate"].shape
    vertical = [
        e for e in sub_shapes(shape, "edge")
        if (p := fingerprint(e, "edge")).geometry == "line"
        and p.direction and abs(p.direction[2]) > 0.99
    ]
    doc.add_feature(
        ChamferFeature(
            inputs={
                "body": BodyRef("Plate"),
                "edges": [make_ref(shape, e, feature.id, body="Plate") for e in vertical],
                "distance": 3,
            },
            outputs=["Plate"],
        )
    )
    build(doc)
    after = doc.bodies["Plate"].shape
    assert is_valid(after)
    # Four corners, each losing a right triangle 3x3 through the full height.
    assert volume(after) == pytest.approx(before - 4 * 0.5 * 3 * 3 * 10, rel=1e-6)


@pytest.mark.parametrize(
    "operation,expected",
    [("join", 40 * 40 * 10 + math.pi * 25 * 30 - math.pi * 25 * 10),
     ("cut", 40 * 40 * 10 - math.pi * 25 * 10),
     ("intersect", math.pi * 25 * 10)],
)
def test_booleans_produce_the_expected_volume(doc, operation, expected):
    plate(doc)
    doc.add_feature(
        CylinderFeature(
            inputs={"radius": 5, "height": 30, "x": 20, "y": 20}, outputs=["Tool"]
        )
    )
    build(doc)
    doc.add_feature(
        BooleanFeature(
            inputs={"body": BodyRef("Plate"), "tool": BodyRef("Tool"),
                    "operation": operation},
            outputs=["Plate"],
        )
    )
    build(doc)
    assert is_valid(doc.bodies["Plate"].shape)
    assert volume(doc.bodies["Plate"].shape) == pytest.approx(expected, rel=1e-3)


def test_move_translates_by_the_requested_amount(doc):
    plate(doc)
    build(doc)
    doc.add_feature(
        MoveFeature(
            inputs={"body": BodyRef("Plate"), "dx": 10, "dy": -5, "dz": 2},
            outputs=["Plate"],
        )
    )
    build(doc)
    low, _high = bounding_box(doc.bodies["Plate"].shape)
    assert low == pytest.approx((10.0, -5.0, 2.0), abs=1e-6)


def test_move_rotates_about_the_body_not_the_world_origin(doc):
    """Rotating a part parked far from the origin must not fling it away."""
    plate(doc, width=20, depth=10, height=10, x=150)
    build(doc)
    before = bounding_box(doc.bodies["Plate"].shape)
    centre_before = [(a + b) / 2 for a, b in zip(*before)]

    doc.add_feature(
        MoveFeature(inputs={"body": BodyRef("Plate"), "rz": 90}, outputs=["Plate"])
    )
    build(doc)
    after = bounding_box(doc.bodies["Plate"].shape)
    centre_after = [(a + b) / 2 for a, b in zip(*after)]

    assert centre_after == pytest.approx(centre_before, abs=1e-6), (
        "the body must spin in place"
    )
    # A 20x10 footprint turned 90 degrees becomes 10x20.
    assert (after[1][0] - after[0][0]) == pytest.approx(10.0, abs=1e-6)
    assert (after[1][1] - after[0][1]) == pytest.approx(20.0, abs=1e-6)


# ----------------------------------------------------------------------
# Hole styles
# ----------------------------------------------------------------------
def _hole(doc, feature, **inputs):
    base = {
        "body": BodyRef("Plate"),
        "face": make_ref(
            doc.bodies["Plate"].shape, upward_face(doc.bodies["Plate"].shape),
            feature.id, body="Plate",
        ),
        "diameter": 6,
        "position": (20.0, 20.0, 10.0),
        "depth_mode": "through",
    }
    base.update(inputs)
    doc.add_feature(HoleFeature(inputs=base, outputs=["Plate"]))


def test_simple_through_hole(doc):
    feature = plate(doc)
    build(doc)
    before = volume(doc.bodies["Plate"].shape)
    _hole(doc, feature)
    build(doc)
    assert volume(doc.bodies["Plate"].shape) == pytest.approx(
        before - math.pi * 9 * 10, rel=1e-3
    )
    assert cylindrical_faces(doc.bodies["Plate"].shape)[0][1].internal


def test_blind_hole_stops_at_the_requested_depth(doc):
    feature = plate(doc)
    build(doc)
    _hole(doc, feature, depth_mode="blind", depth=4)
    build(doc)
    bore = cylindrical_faces(doc.bodies["Plate"].shape)[0][1]
    assert bore.length == pytest.approx(4.0, abs=1e-6)


def test_counterbore_produces_two_diameters(doc):
    feature = plate(doc)
    build(doc)
    _hole(doc, feature, style="counterbore",
          counterbore_diameter=12, counterbore_depth=4)
    build(doc)
    bores = sorted(
        i.diameter for _f, i in cylindrical_faces(doc.bodies["Plate"].shape)
    )
    assert bores == pytest.approx([6.0, 12.0], abs=1e-6)
    assert is_valid(doc.bodies["Plate"].shape)


def test_countersink_adds_a_conical_mouth(doc):
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cone
    from OCP.TopoDS import TopoDS

    feature = plate(doc)
    build(doc)
    _hole(doc, feature, style="countersink", countersink_diameter=12,
          countersink_angle=90)
    build(doc)
    shape = doc.bodies["Plate"].shape
    cones = [
        f for f in sub_shapes(shape, "face")
        if BRepAdaptor_Surface(TopoDS.Face_s(f)).GetType() == GeomAbs_Cone
    ]
    assert len(cones) == 1
    assert is_valid(shape)


@pytest.mark.slow
def test_threaded_through_hole_is_actually_threaded(doc):
    """The bug this test exists for: the thread must span the material.

    A through hole is drilled with a tool long enough to clear the bounding box.
    Threading *that* length asks for tens of turns through empty space, the
    sweep fails, and the user silently receives a plain bore.
    """
    feature = plate(doc, height=6)
    build(doc)
    plain = volume(doc.bodies["Plate"].shape)

    _hole(doc, feature, style="threaded", position=(20.0, 20.0, 6.0))
    report = build(doc)

    assert not report.warnings, f"thread degraded: {report.warnings}"
    shape = doc.bodies["Plate"].shape
    assert is_valid(shape)

    # Nut metal protrudes into the bore, so a threaded hole leaves *more*
    # material than a plain one of the same diameter.
    plain_bore = plain - math.pi * 9 * 6
    assert volume(shape) > plain_bore + 1.0
    low, high = bounding_box(shape)
    # Tolerance is 0.05 mm, a quarter of a layer height: tight enough to catch a
    # thread running off into space, loose enough not to trip on the optimal
    # bounding box's own numerical slop.
    assert (high[2] - low[2]) == pytest.approx(6.0, abs=0.05), (
        "the thread must not extend past the material"
    )
    assert doc.features[-1].inputs["designation"] == "M6"
