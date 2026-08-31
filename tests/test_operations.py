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
from simplecad.core.errors import CadError
from simplecad.kernel.operations import (
    BooleanFeature, ChamferFeature, HoleFeature, MoveFeature, PushPullFeature,
    RoundPushPullFeature, ShellFeature,
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


def test_a_cut_consumes_the_tool_body(doc):
    """Subtract is not "put the cutter inside the part and leave it there".

    Without this the cutter stays in the tree and in the viewport, occupying
    the space it just removed -- which is not what subtract means in any other
    CAD package.
    """
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
                    "operation": "cut"},
            outputs=["Plate"],
        )
    )
    build(doc)
    assert "Tool" not in doc.bodies
    assert "Plate" in doc.bodies


def test_keep_tool_leaves_the_cutter_in_the_document(doc):
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
                    "operation": "cut", "keep_tool": True},
            outputs=["Plate"],
        )
    )
    build(doc)
    assert "Tool" in doc.bodies
    assert volume(doc.bodies["Tool"].shape) == pytest.approx(
        math.pi * 25 * 30, rel=1e-3
    )


def test_a_cut_takes_several_tools_at_once(doc):
    """Selecting three bodies and pressing Subtract has to mean all of them."""
    plate(doc)
    for index, x in enumerate((10, 20, 30), start=1):
        doc.add_feature(
            CylinderFeature(
                inputs={"radius": 3, "height": 30, "x": x, "y": 20},
                outputs=[f"Tool {index}"],
            )
        )
    build(doc)
    before = volume(doc.bodies["Plate"].shape)
    doc.add_feature(
        BooleanFeature(
            inputs={
                "body": BodyRef("Plate"),
                "tools": [BodyRef("Tool 1"), BodyRef("Tool 2"), BodyRef("Tool 3")],
                "operation": "cut",
            },
            outputs=["Plate"],
        )
    )
    build(doc)
    assert is_valid(doc.bodies["Plate"].shape)
    assert volume(doc.bodies["Plate"].shape) == pytest.approx(
        before - 3 * math.pi * 9 * 10, rel=1e-3
    )
    for index in (1, 2, 3):
        assert f"Tool {index}" not in doc.bodies


def test_a_combine_with_no_tool_says_so(doc):
    plate(doc)
    build(doc)
    doc.add_feature(
        BooleanFeature(
            inputs={"body": BodyRef("Plate"), "operation": "cut"},
            outputs=["Plate"],
        )
    )
    report = Rebuilder(doc).rebuild()
    assert not report.ok
    assert "second body" in report.summary()


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
    # P6, not M6: the printable coarse series leads, because an ISO tooth at a
    # 1 mm pitch is 0.31 mm deep -- under what a 0.4 mm nozzle can resolve.
    assert doc.features[-1].inputs["designation"] == "P6"


@pytest.mark.slow
def test_a_threaded_hole_honours_the_size_it_was_given(doc):
    """The panel now offers a size, so the kernel has to use the one chosen.

    Left to itself a ⌀6 hole gets P6, whose printed tooth is 0.75 mm deep. Asked
    for M6 it must get M6 -- 0.31 mm, shallow enough that the panel warns about
    it, and the warning has to reach the feature rather than a hint line the next
    click wipes.
    """
    feature = plate(doc, height=6)
    build(doc)
    _hole(
        doc, feature, style="threaded", position=(20.0, 20.0, 6.0),
        diameter=6, designation="M6",
    )
    report = build(doc)
    assert doc.features[-1].inputs["designation"] == "M6"
    assert any("M6" in w for w in report.warnings), report.warnings
    assert "M6" in (doc.features[-1].message or ""), (
        "the warning must survive on the feature, not only in the hint line"
    )

    shape = doc.bodies["Plate"].shape
    assert is_valid(shape)
    crests = sorted(
        round(info.diameter, 2) for face in sub_shapes(shape, "face")
        if (info := analyse_cylinder(face)) is not None and info.internal
    )
    assert any(d < 5.95 for d in crests), (
        f"nothing protrudes into the bore, so there is no thread: {crests}"
    )
    # M6's tooth is 0.31 mm, so the crest sits near 5.4 -- not at P6's 4.7.
    assert min(crests) > 5.0, f"that is a P6 thread, not the M6 asked for: {crests}"


@pytest.mark.slow
def test_a_threaded_hole_finds_its_own_bore_among_others_the_same_size(doc):
    """Matched by diameter and axis alone, the longest bore won.

    Two ⌀6 holes through the same plate are indistinguishable that way, so the
    thread could land in the one drilled ten minutes ago and the new hole came
    out plain.
    """
    feature = plate(doc, height=6)
    build(doc)
    _hole(doc, feature, position=(10.0, 10.0, 6.0))        # a plain one first
    build(doc)
    _hole(
        doc, feature, style="threaded", position=(30.0, 30.0, 6.0),
        designation="P6",
    )
    build(doc)

    shape = doc.bodies["Plate"].shape
    threaded = [
        info for face in sub_shapes(shape, "face")
        if (info := analyse_cylinder(face)) is not None
        and info.internal and info.diameter < 5.9
    ]
    assert threaded, "the second hole was left plain"
    # Every crest belongs to the hole that asked for the thread, not the other.
    for info in threaded:
        assert info.origin[0] == pytest.approx(30.0, abs=0.5), info.origin
        assert info.origin[1] == pytest.approx(30.0, abs=0.5), info.origin


# ----------------------------------------------------------------------
# Pull, on round things
# ----------------------------------------------------------------------
def outer_round_face(shape):
    """The widest shaft face -- the outside of a cylinder or a tube."""
    return max(
        (f for f, i in cylindrical_faces(shape) if not analyse_cylinder(f).internal),
        key=lambda f: analyse_cylinder(f).radius,
    )


def bore_face(shape):
    return max(
        (f for f, i in cylindrical_faces(shape) if analyse_cylinder(f).internal),
        key=lambda f: analyse_cylinder(f).radius,
    )


def tube(document, name="Tube", outer=10.0, bore=6.0, height=20.0):
    """A cylinder with a coaxial hole through it, built as two features."""
    outer_feature = document.add_feature(
        CylinderFeature(
            inputs={"radius": outer, "height": height}, outputs=[name]
        )
    )
    document.add_feature(
        CylinderFeature(
            inputs={"radius": bore, "height": height}, outputs=["Bore"]
        )
    )
    document.add_feature(
        BooleanFeature(
            inputs={
                "body": BodyRef(name), "tools": [BodyRef("Bore")],
                "operation": "cut",
            },
            outputs=[name],
        )
    )
    return outer_feature


def resize(document, name, face, feature_id, delta):
    document.add_feature(
        RoundPushPullFeature(
            inputs={
                "body": BodyRef(name),
                "face": make_ref(
                    document.bodies[name].shape, face, feature_id, body=name
                ),
                "delta": delta,
            },
            outputs=[name],
        )
    )


def test_a_shaft_can_be_made_thinner(doc):
    """The request this exists for: drag the side of a cylinder inward."""
    feature = doc.add_feature(
        CylinderFeature(inputs={"radius": 10.0, "height": 30.0}, outputs=["Post"])
    )
    build(doc)
    before = doc.bodies["Post"].shape
    resize(doc, "Post", outer_round_face(before), feature.id, -2.5)
    build(doc)

    shape = doc.bodies["Post"].shape
    assert is_valid(shape)
    assert analyse_cylinder(outer_round_face(shape)).radius == pytest.approx(7.5)
    # Only the radius moved: the part is no shorter than it was.
    low, high = bounding_box(shape)
    assert (high[2] - low[2]) == pytest.approx(30.0, abs=1e-6)
    assert volume(shape) == pytest.approx(math.pi * 7.5 ** 2 * 30.0, rel=1e-4)


def test_a_shaft_can_be_made_fatter(doc):
    feature = doc.add_feature(
        CylinderFeature(inputs={"radius": 6.0, "height": 20.0}, outputs=["Post"])
    )
    build(doc)
    resize(doc, "Post", outer_round_face(doc.bodies["Post"].shape), feature.id, 1.5)
    build(doc)
    shape = doc.bodies["Post"].shape
    assert analyse_cylinder(outer_round_face(shape)).radius == pytest.approx(7.5)
    assert volume(shape) == pytest.approx(math.pi * 7.5 ** 2 * 20.0, rel=1e-4)


def test_thinning_a_tube_keeps_its_bore(doc):
    """The reason the tool is an annulus and not a cylinder.

    Turning a tube down with a solid cylinder would fill the bore on the way
    past, which is the difference between making a tube thinner and ruining it.
    """
    feature = tube(doc, outer=10.0, bore=6.0, height=20.0)
    build(doc)
    resize(doc, "Tube", outer_round_face(doc.bodies["Tube"].shape), feature.id, -2.0)
    build(doc)

    shape = doc.bodies["Tube"].shape
    assert is_valid(shape)
    assert analyse_cylinder(outer_round_face(shape)).radius == pytest.approx(8.0)
    assert analyse_cylinder(bore_face(shape)).radius == pytest.approx(6.0)
    assert volume(shape) == pytest.approx(
        math.pi * (8.0 ** 2 - 6.0 ** 2) * 20.0, rel=1e-4
    )


def test_a_bore_can_be_opened_out(doc):
    feature = tube(doc, outer=10.0, bore=5.0, height=20.0)
    build(doc)
    # Negative takes material away, whichever face it is: on a hole that means
    # a wider hole.
    resize(doc, "Tube", bore_face(doc.bodies["Tube"].shape), feature.id, -1.5)
    build(doc)

    shape = doc.bodies["Tube"].shape
    assert analyse_cylinder(bore_face(shape)).radius == pytest.approx(6.5)
    assert analyse_cylinder(outer_round_face(shape)).radius == pytest.approx(10.0)


def test_thinning_past_the_bore_is_refused_with_the_limit(doc):
    feature = tube(doc, outer=10.0, bore=8.0, height=20.0)
    build(doc)
    resize(doc, "Tube", outer_round_face(doc.bodies["Tube"].shape), feature.id, -3.0)

    report = Rebuilder(doc).rebuild()
    assert not report.ok
    assert "break through" in report.summary()
    # And it names the size that would work, rather than only saying no.
    assert "16.1" in report.summary()


def test_resizing_to_nothing_is_refused(doc):
    feature = doc.add_feature(
        CylinderFeature(inputs={"radius": 5.0, "height": 10.0}, outputs=["Post"])
    )
    build(doc)
    resize(doc, "Post", outer_round_face(doc.bodies["Post"].shape), feature.id, -5.0)
    assert not Rebuilder(doc).rebuild().ok
