"""Sliding joints: dovetail, T-rail and tongue-and-groove rails.

The reference case is an open-top holder whose back carries two rails that drop
into channels on a thin, screw-on wall plate and stop at the bottom. Every
check here is geometric: the parts are slid apart and together with real
booleans, not by trusting the feature's own report.
"""

from __future__ import annotations

import pytest

import simplecad.kernel  # noqa: F401 - registers every feature
from simplecad.core.document import BodyRef, Document, FeatureState
from simplecad.core.naming import make_ref, sub_shapes
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.align import interference_volume
from simplecad.kernel.clips import ClipJointFeature
from simplecad.kernel.detect import analyse_plane
from simplecad.kernel.occ import bounding_box, make_transform, transformed
from simplecad.kernel.operations import BooleanFeature
from simplecad.kernel.primitives import BoxFeature
from simplecad.kernel.slides import RailSpec, channel_profile, rail_profile

STYLES = ("dovetail", "t_rail", "tongue_groove")


def _face(shape, normal):
    """The largest flat face pointing along *normal*."""
    from simplecad.kernel.occ import area

    return max((f for f in sub_shapes(shape, "face") if analyse_plane(f) is not None
                and sum(a * b for a, b in zip(analyse_plane(f).normal, normal)) > 0.99),
               key=area)


def holder_and_plate(plate_thickness=2.0, *, ledge=False):
    """A 60 x 40 x 90 holder and a 50 x 80 plate facing its back, 20 mm away."""
    document = Document()
    document.add_feature(BoxFeature(inputs={"width": 60, "depth": 40, "height": 90},
                                    outputs=["Holder"]))
    document.add_feature(BoxFeature(inputs={"width": 50, "depth": plate_thickness, "height": 80,
                                            "y": 60, "z": 5}, outputs=["Plate"]))
    if ledge:
        # A shelf on the plate right over the channels' open (top) end.
        document.add_feature(BoxFeature(inputs={"width": 50, "depth": 12, "height": 3,
                                                "y": 50, "z": 84}, outputs=["Ledge"]))
        document.add_feature(BooleanFeature(inputs={"body": BodyRef("Plate"),
                                                     "tools": [BodyRef("Ledge")],
                                                     "operation": "join"}, outputs=["Plate"]))
    rebuilder = Rebuilder(document)
    assert rebuilder.rebuild().ok
    return document, rebuilder


def add_joint(document, rebuilder, **inputs):
    holder, plate = document.body("Holder").shape, document.body("Plate").shape
    joint = ClipJointFeature(inputs={
        "clip_version": 2, "body_a": BodyRef("Holder"), "body_b": BodyRef("Plate"),
        "face_a": make_ref(holder, _face(holder, (0, 1, 0)), document.producer_of("Holder").id,
                           body="Holder"),
        "face_b": make_ref(plate, _face(plate, (0, -1, 0)), document.producer_of("Plate").id,
                           body="Plate"),
        "style": "t_rail", "clearance": "sliding", "seat": True, "seat_x": 0, "seat_y": 0,
        "rails": 2, "stop": True, **inputs,
    }, outputs=["Holder", "Plate"])
    document.add_feature(joint)
    rebuilder.rebuild()
    return document.feature(joint.id)


def _moved(shape, vector):
    return transformed(shape, make_transform(translate=vector))


@pytest.mark.parametrize("style", STYLES)
def test_the_holder_drops_into_the_plate_and_lifts_out(style):
    document, rebuilder = holder_and_plate()
    joint = add_joint(document, rebuilder, style=style, detent=0)
    assert joint.state is FeatureState.OK, joint.message
    notes = joint.annotations
    assert notes["raised"], "a 2 mm plate cannot take a cut channel"
    assert notes["exit_a"] == pytest.approx([0.0, 0.0, 1.0]), "on a wall it slides in from above"
    holder, plate = document.body("Holder").shape, document.body("Plate").shape
    assert interference_volume(holder, plate) < 1e-3, "assembled, nothing overlaps"
    for lift in (5.0, 30.0, notes["travel"]):
        assert interference_volume(_moved(holder, (0, 0, lift)), plate) < 1e-3
    # Pushed further down, the stop catches the rails.
    assert interference_volume(_moved(holder, (0, 0, -1.0)), plate) > 0.1
    # And it cannot be pulled straight off the wall: the channel holds it.
    if style != "tongue_groove":
        assert interference_volume(_moved(holder, (0, -1.0, 0)), plate) > 0.1


@pytest.mark.parametrize("style", STYLES)
def test_a_thick_part_gets_a_cut_channel_with_the_faces_touching(style):
    document, rebuilder = holder_and_plate(plate_thickness=10.0)
    joint = add_joint(document, rebuilder, style=style, detent=0)
    assert joint.state is FeatureState.OK, joint.message
    assert joint.annotations["raised"] is False
    holder, plate = document.body("Holder").shape, document.body("Plate").shape
    assert bounding_box(plate)[0][1] == pytest.approx(60.0, abs=1e-4), "nothing added to the plate"
    assert bounding_box(holder)[1][1] > 60.0, "the rails reach into the plate"
    assert interference_volume(holder, plate) < 1e-3
    assert interference_volume(_moved(holder, (0, 0, joint.annotations["travel"])), plate) < 1e-3


def test_a_cut_channel_in_a_thin_plate_is_refused_with_the_reason():
    document, rebuilder = holder_and_plate(plate_thickness=2.0)
    joint = add_joint(document, rebuilder, female_mode="cut")
    assert joint.state is FeatureState.FAILED
    assert "too thin" in joint.message


def test_something_blocking_the_open_end_is_found_before_it_is_built():
    document, rebuilder = holder_and_plate(ledge=True)
    joint = add_joint(document, rebuilder)
    assert joint.state is FeatureState.FAILED
    assert "cannot be slid apart" in joint.message or "overlap" in joint.message


def test_the_slide_direction_can_be_reversed():
    document, rebuilder = holder_and_plate()
    joint = add_joint(document, rebuilder, insertion="+u")
    assert joint.state is FeatureState.OK, joint.message
    assert joint.annotations["exit_a"] == pytest.approx([0.0, 0.0, -1.0])


def test_clearance_sets_the_channel_exactly():
    def spec(clearance, style="dovetail"):
        return RailSpec(style=style, width=8.0, height=3.0, angle=15.0, clearance=clearance,
                        depth_clearance=0.3, lead_in=0.4, min_wall=0.8, lip=2.0, stop=True,
                        detent=0.0)

    from shapely.geometry import Polygon

    for style in STYLES:
        male = Polygon(rail_profile(spec(0.3, style)))
        channel = channel_profile(spec(0.3, style))
        # Half the clearance between every face of the rail and its channel:
        # no less anywhere, and no more where they bear on each other.
        assert channel.buffer(-0.1499, join_style=2).covers(male)
        assert not channel.buffer(-0.1501, join_style=2).covers(male)
    for style in ("t_rail", "tongue_groove"):
        tight = channel_profile(spec(0.0, style)).bounds
        loose = channel_profile(spec(0.6, style)).bounds
        assert (loose[2] - loose[0]) - (tight[2] - tight[0]) == pytest.approx(0.6)


def test_no_clearance_on_a_sliding_joint_is_refused():
    document, rebuilder = holder_and_plate()
    joint = add_joint(document, rebuilder, clearance=0.0)
    assert joint.state is FeatureState.FAILED
    assert "bind" in joint.message


def test_a_steep_dovetail_that_leaves_no_neck_is_refused():
    document, rebuilder = holder_and_plate()
    joint = add_joint(document, rebuilder, style="dovetail", rail_width=4, rail_height=6,
                      rail_angle=30)
    assert joint.state is FeatureState.FAILED
    assert "neck" in joint.message


def test_editing_the_clearance_rebuilds_and_survives_save(tmp_path):
    from simplecad.core.project import load, save

    document, rebuilder = holder_and_plate()
    joint = add_joint(document, rebuilder)
    from simplecad.kernel.occ import volume

    narrow = volume(document.body("Plate").shape)
    joint.inputs["clearance"] = 0.8
    rebuilder.invalidate({joint.id})
    rebuilder.rebuild()
    assert document.feature(joint.id).state is FeatureState.OK
    assert "0.80 mm clearance" in document.feature(joint.id).message
    assert volume(document.body("Plate").shape) != pytest.approx(narrow)
    path = save(document, str(tmp_path / "bracket.scad3"))
    reopened, _ = load(path)
    again = Rebuilder(reopened)
    again.rebuild(force=True)
    assert reopened.feature(joint.id).state is FeatureState.OK
    assert reopened.feature(joint.id).inputs["style"] == "t_rail"


def test_the_detent_clicks_home_and_drags_on_the_way_out():
    """Home, the bump sits in its dimple; a few mm out it presses the plate --
    that pressure is what holds the holder in place."""
    document, rebuilder = holder_and_plate()
    joint = add_joint(document, rebuilder)
    assert joint.state is FeatureState.OK, joint.message
    holder, plate = document.body("Holder").shape, document.body("Plate").shape
    assert interference_volume(holder, plate) < 1e-3
    scraped = interference_volume(_moved(holder, (0, 0, 5.0)), plate)
    assert 0.0 < scraped < 10.0
    assert interference_volume(_moved(holder, (0, 0, joint.annotations["travel"])), plate) < 1e-3


def test_rails_join_the_two_halves_of_a_split_block():
    """Modified geometry: a block split in two gets a cut dovetail across the split."""
    from simplecad.kernel.split import SplitFeature

    document = Document()
    document.add_feature(BoxFeature(inputs={"width": 60, "depth": 40, "height": 30},
                                    outputs=["Block"]))
    document.add_feature(SplitFeature(inputs={
        "body": BodyRef("Block"), "normal": [0, 0, 1], "origin": [0, 0, 15], "position": 0.0,
        "names": ["Base", "Top"]}, outputs=["Base", "Top"]))
    rebuilder = Rebuilder(document)
    assert rebuilder.rebuild().ok
    base, top = document.body("Base").shape, document.body("Top").shape
    joint = ClipJointFeature(inputs={
        "clip_version": 2, "body_a": BodyRef("Top"), "body_b": BodyRef("Base"),
        "face_a": make_ref(top, _face(top, (0, 0, -1)), document.producer_of("Top").id, body="Top"),
        "face_b": make_ref(base, _face(base, (0, 0, 1)), document.producer_of("Base").id,
                           body="Base"),
        "style": "dovetail", "clearance": "sliding", "rails": 1, "detent": 0,
    }, outputs=["Top", "Base"])
    document.add_feature(joint)
    rebuilder.rebuild()
    built = document.feature(joint.id)
    assert built.state is FeatureState.OK, built.message
    assert built.annotations["raised"] is False, "15 mm of base takes a cut channel"
    top, base = document.body("Top").shape, document.body("Base").shape
    assert interference_volume(top, base) < 1e-3
    out = [c * built.annotations["travel"] for c in built.annotations["exit_a"]]
    assert interference_volume(_moved(top, tuple(out)), base) < 1e-3
    # Lifting the top straight off is what the dovetail stops.
    assert interference_volume(_moved(top, (0, 0, 1.0)), base) > 0.1
