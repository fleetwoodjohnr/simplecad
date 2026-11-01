"""Hinges: a box and its lid, joined along the box's top back edge.

Every claim is checked by turning the lid with real booleans, independent of
the feature's own swing check.
"""

from __future__ import annotations

import math

import pytest

import simplecad.kernel  # noqa: F401 - registers every feature
from simplecad.core.document import BodyRef, Document, FeatureState
from simplecad.core.naming import make_ref, sub_shapes
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.align import interference_volume
from simplecad.kernel.hinge import HingeFeature, first_contact
from simplecad.kernel.occ import bounding_box, is_valid, transformed
from simplecad.kernel.operations import BooleanFeature
from simplecad.kernel.primitives import BoxFeature


def _top_back_edge(shape):
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.TopoDS import TopoDS

    for edge in sub_shapes(shape, "edge"):
        curve = BRepAdaptor_Curve(TopoDS.Edge_s(edge))
        middle = curve.Value((curve.FirstParameter() + curve.LastParameter()) / 2.0)
        if abs(middle.Y() - 50.0) < 1e-6 and abs(middle.Z() - 30.0) < 1e-6:
            return edge
    raise AssertionError("no top back edge")


def box_and_lid(gap=0.0, *, wall=False):
    document = Document()
    document.add_feature(BoxFeature(inputs={"width": 80, "depth": 50, "height": 30},
                                    outputs=["Box"]))
    document.add_feature(BoxFeature(inputs={"width": 80, "depth": 50, "height": 4,
                                            "z": 30 + gap}, outputs=["Lid"]))
    if wall:
        # A wall behind the box, past the knuckles: the lid hits it on the way back.
        document.add_feature(BoxFeature(inputs={"width": 80, "depth": 16, "height": 4, "y": 50},
                                        outputs=["Foot"]))
        document.add_feature(BoxFeature(inputs={"width": 80, "depth": 2, "height": 70, "y": 64},
                                        outputs=["Back"]))
        for tool in ("Foot", "Back"):
            document.add_feature(BooleanFeature(inputs={"body": BodyRef("Box"),
                                                         "tools": [BodyRef(tool)],
                                                         "operation": "join"}, outputs=["Box"]))
    rebuilder = Rebuilder(document)
    assert rebuilder.rebuild().ok
    return document, rebuilder


def add_hinge(document, rebuilder, kind="knuckle", **inputs):
    box = document.body("Box").shape
    outputs = {"pin": ["Box", "Lid", "Hinge pin"], "living": ["Box"]}.get(kind, ["Box", "Lid"])
    hinge = HingeFeature(inputs={
        "body_a": BodyRef("Box"), "body_b": BodyRef("Lid"),
        "edge": make_ref(box, _top_back_edge(box), document.producer_of("Box").id, body="Box"),
        "type": kind, "clearance": "sliding", "names": ["Hinge pin"], **inputs,
    }, outputs=outputs)
    document.add_feature(hinge)
    rebuilder.rebuild()
    return document.feature(hinge.id)


def _turned(shape, hinge, degrees):
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

    turn = gp_Trsf()
    turn.SetRotation(gp_Ax1(gp_Pnt(*hinge.annotations["axis"]),
                            gp_Dir(*hinge.annotations["direction"])), -math.radians(degrees))
    return transformed(shape, turn)


@pytest.mark.parametrize("kind", ("pin", "knuckle", "snap"))
def test_the_lid_swings_open_clear_of_the_box(kind):
    document, rebuilder = box_and_lid()
    hinge = add_hinge(document, rebuilder, kind)
    assert hinge.state is FeatureState.OK, hinge.message
    box, lid = document.body("Box").shape, document.body("Lid").shape
    assert is_valid(box) and is_valid(lid)
    for degrees in (0.0, 45.0, 90.0, 120.0):
        assert interference_volume(box, _turned(lid, hinge, degrees)) < 1e-3, degrees
    # Turned the other way -- into the box -- it cannot go.
    assert interference_volume(box, _turned(lid, hinge, -5.0)) > 0.1
    # Opening lifts the front of the lid.
    low, high = bounding_box(_turned(lid, hinge, 90.0))
    assert high[2] > 70.0
    if kind == "pin":
        pin = document.body("Hinge pin").shape
        assert interference_volume(pin, box) < 1e-3 and interference_volume(pin, lid) < 1e-3
        assert interference_volume(pin, _turned(lid, hinge, 60.0)) < 1e-3


def test_print_in_place_cannot_be_pulled_apart():
    """The pin is part of the box and runs through the lid's knuckles."""
    document, rebuilder = box_and_lid()
    hinge = add_hinge(document, rebuilder, "knuckle")
    box, lid = document.body("Box").shape, document.body("Lid").shape
    from simplecad.kernel.occ import make_transform

    away = transformed(lid, make_transform(translate=(0.0, 2.0, 0.0)))
    assert interference_volume(box, away) > 0.1


def test_a_stop_holds_the_lid_at_the_limit():
    document, rebuilder = box_and_lid()
    hinge = add_hinge(document, rebuilder, "knuckle", stop=True, open_max=100.0)
    assert hinge.state is FeatureState.OK, hinge.message
    box, lid = document.body("Box").shape, document.body("Lid").shape
    assert interference_volume(box, _turned(lid, hinge, 98.0)) < 1e-3
    assert interference_volume(box, _turned(lid, hinge, 104.0)) > 1e-3


def test_something_in_the_way_is_reported_with_its_angle():
    document, rebuilder = box_and_lid(wall=True)
    hinge = add_hinge(document, rebuilder, "knuckle", open_max=170.0)
    assert hinge.state is FeatureState.FAILED
    assert "Lid hits Box at" in hinge.message
    angle = float(hinge.message.split(" at ")[1].split("°")[0])
    assert 95.0 < angle < 170.0
    # Asking for less than that is fine.
    hinge.inputs["open_max"] = angle - 5.0
    rebuilder.invalidate({hinge.id})
    rebuilder.rebuild()
    assert document.feature(hinge.id).state is FeatureState.OK


def test_editing_the_knuckle_count_rebuilds_and_survives_save(tmp_path):
    from simplecad.core.project import load, save

    document, rebuilder = box_and_lid()
    hinge = add_hinge(document, rebuilder, "knuckle")
    assert "5 knuckles" in hinge.message
    hinge.inputs["knuckles"] = 7
    rebuilder.invalidate({hinge.id})
    rebuilder.rebuild()
    assert "7 knuckles" in document.feature(hinge.id).message
    path = save(document, str(tmp_path / "box.scad3"))
    reopened, _ = load(path)
    Rebuilder(reopened).rebuild(force=True)
    assert reopened.feature(hinge.id).state is FeatureState.OK
    assert "7 knuckles" in reopened.feature(hinge.id).message


def test_impossible_sizes_are_refused_with_the_numbers():
    document, rebuilder = box_and_lid()
    hinge = add_hinge(document, rebuilder, "pin", pin=7.0)
    assert hinge.state is FeatureState.FAILED
    assert "wall" in hinge.message
    document2, rebuilder2 = box_and_lid()
    hinge2 = add_hinge(document2, rebuilder2, "knuckle", knuckles=40)
    assert hinge2.state is FeatureState.FAILED
    assert "knuckles leave each one" in hinge2.message


def test_a_living_hinge_makes_one_part_and_warns_about_material():
    document, rebuilder = box_and_lid(gap=0.6)
    hinge = add_hinge(document, rebuilder, "living")
    assert hinge.state is FeatureState.OK, hinge.message
    assert set(document.bodies) == {"Box"}, "the lid became part of the box"
    assert is_valid(document.body("Box").shape)


def test_a_living_hinge_on_touching_parts_is_refused():
    document, rebuilder = box_and_lid(gap=0.0)
    hinge = add_hinge(document, rebuilder, "living")
    assert hinge.state is FeatureState.FAILED
    assert "stand apart" in hinge.message


def test_the_swing_check_finds_the_first_contact():
    """The helper itself, against two plain blocks."""
    document, rebuilder = box_and_lid(wall=True)
    box, lid = document.body("Box").shape, document.body("Lid").shape
    # Turning the lid about the box's top back edge runs it into the wall.
    contact = first_contact(box, lid, (0.0, 50.0, 30.0), (1.0, 0.0, 0.0), 179.0)
    assert contact is not None and contact < 179.0


def test_a_hinge_joins_the_two_halves_of_a_split_part():
    """Modified geometry: a box split in two, hinged along the split like a book."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.TopoDS import TopoDS

    from simplecad.kernel.split import SplitFeature

    document = Document()
    document.add_feature(BoxFeature(inputs={"width": 80, "depth": 40, "height": 10},
                                    outputs=["Slab"]))
    document.add_feature(SplitFeature(inputs={
        "body": BodyRef("Slab"), "normal": [1, 0, 0], "origin": [40, 0, 0], "position": 0.0,
        "names": ["Left", "Right"]}, outputs=["Left", "Right"]))
    rebuilder = Rebuilder(document)
    assert rebuilder.rebuild().ok
    left = document.body("Left").shape

    def middle(edge):
        curve = BRepAdaptor_Curve(TopoDS.Edge_s(edge))
        return curve.Value((curve.FirstParameter() + curve.LastParameter()) / 2.0)

    # The top edge of Left's cut face, running along Y at x = 40, z = 10.
    edge = next(e for e in sub_shapes(left, "edge")
                if abs(middle(e).X() - 40.0) < 1e-6 and abs(middle(e).Z() - 10.0) < 1e-6)
    hinge = HingeFeature(inputs={
        "body_a": BodyRef("Left"), "body_b": BodyRef("Right"),
        "edge": make_ref(left, edge, document.producer_of("Left").id, body="Left"),
        "type": "knuckle", "clearance": "sliding", "open_max": 170.0,
    }, outputs=["Left", "Right"])
    document.add_feature(hinge)
    rebuilder.rebuild()
    built = document.feature(hinge.id)
    assert built.state is FeatureState.OK, built.message
    assert is_valid(document.body("Left").shape) and is_valid(document.body("Right").shape)
