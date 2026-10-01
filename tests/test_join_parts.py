"""Join Parts: automatic printable joints between two touching faces."""

from __future__ import annotations

import pytest

from simplecad.core.document import BodyRef, Document
from simplecad.core.errors import CadError
from simplecad.core.geometry_service import build_preview_result
from simplecad.core.history import History
from simplecad.core.naming import fingerprint, make_ref, sub_shapes
from simplecad.core.project import load, save
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.clips import (
    ClipJointFeature, JointSpec, STYLES, mate, plan_clips,
)
from simplecad.kernel.occ import bounding_box, is_valid, volume
from simplecad.kernel.operations import BooleanFeature, MoveFeature, ShellFeature
from simplecad.kernel.primitives import BoxFeature, CylinderFeature


def _face(shape, direction, *, extreme=True):
    """The outermost planar face whose normal points along *direction*."""
    matches = []
    for face in sub_shapes(shape, "face"):
        data = fingerprint(face, "face")
        if data.geometry != "plane" or not data.direction:
            continue
        if sum(a * b for a, b in zip(data.direction, direction)) > 0.99:
            level = sum(a * b for a, b in zip(data.center, direction))
            matches.append((level, face))
    return max(matches, key=lambda item: item[0])[1]


def stacked(width=40.0, depth=30.0, lower=12.0, upper=12.0, hollow=None):
    """Two boxes touching at z = *lower*: Top sits on Bottom."""
    document = Document("Join")
    bottom = document.add_feature(BoxFeature(
        inputs={"width": width, "depth": depth, "height": lower}, outputs=["Bottom"]))
    top = document.add_feature(BoxFeature(
        inputs={"width": width, "depth": depth, "height": upper, "z": lower}, outputs=["Top"]))
    builder = Rebuilder(document)
    assert builder.rebuild().ok
    return document, builder, bottom, top


def joint(document, a="Top", b="Bottom", **inputs):
    """A joint between the lower face of *a* and the upper face of *b*."""
    shape_a = document.bodies[a].shape
    shape_b = document.bodies[b].shape
    producer_a = document.producer_of(a)
    producer_b = document.producer_of(b)
    values = {
        "clip_version": 2,
        "body_a": BodyRef(a), "body_b": BodyRef(b),
        "face_a": make_ref(shape_a, _face(shape_a, (0, 0, -1)), producer_a.id, body=a),
        "face_b": make_ref(shape_b, _face(shape_b, (0, 0, 1)), producer_b.id, body=b),
        "style": "snap_tab", "size": "medium", "material": "petg",
        "retention": "standard", "clearance": 0.15, "placement": "auto",
        "count": 0,
    }
    values.update(inputs)
    names = list(values.get("connector_names", []))
    return ClipJointFeature(inputs=values, outputs=[a, b, *names])


def build(document, builder, feature):
    document.add_feature(feature)
    report = builder.rebuild()
    assert report.ok, report.summary()
    return report


def test_two_cubes_get_mating_tabs_and_pockets_without_moving():
    document, builder, _bottom, _top = stacked()
    before = {n: (volume(b.shape), bounding_box(b.shape)) for n, b in document.bodies.items()}
    feature = joint(document)
    build(document, builder, feature)

    top, bottom = document.bodies["Top"].shape, document.bodies["Bottom"].shape
    assert is_valid(top) and is_valid(bottom)
    # Tabs were added to the top part and pockets cut from the bottom one.
    assert volume(top) > before["Top"][0]
    assert volume(bottom) < before["Bottom"][0]
    # Neither part moved: their outer extents in X/Y are unchanged.
    for name, shape in (("Top", top), ("Bottom", bottom)):
        (low, high) = bounding_box(shape)
        assert low[0] == pytest.approx(before[name][1][0][0], abs=1e-3)
        assert high[1] == pytest.approx(before[name][1][1][1], abs=1e-3)
    clips = feature.annotations["clips"]
    assert len(clips) >= 2, "two joints stop the parts turning about one"


def test_the_tabs_fit_their_pockets_with_clearance():
    document, builder, _b, _t = stacked()
    build(document, builder, joint(document, clearance=0.2))
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from simplecad.kernel.occ import boolean_builder, built_shape

    overlap = built_shape(boolean_builder(
        BRepAlgoAPI_Common, document.bodies["Top"].shape, document.bodies["Bottom"].shape),
        "check")
    # Mated parts never share material: every tab sits in clear space. The
    # barbs are the one deliberate interference -- that is the snap.
    tab_volume = volume(document.bodies["Top"].shape) - 40 * 30 * 12
    assert volume(overlap) < 0.1 * tab_volume


@pytest.mark.parametrize("style", sorted(STYLES))
def test_every_style_builds_and_is_printable(style):
    document, builder, _b, _t = stacked(width=60, depth=40, lower=15, upper=15)
    names = ["Clip", "Clip2", "Clip3", "Clip4"] if style == "connector" else []
    feature = joint(document, style=style, count=2, connector_names=names)
    build(document, builder, feature)
    for body in document.bodies.values():
        assert is_valid(body.shape)
    if style == "connector":
        connectors = [n for n in document.bodies if n.startswith("Clip")]
        assert len(connectors) == 2


def test_a_long_seam_gets_several_joints_and_a_small_face_one():
    document, builder, _b, _t = stacked(width=300, depth=30, lower=15, upper=15)
    long_joint = joint(document)
    build(document, builder, long_joint)
    assert len(long_joint.annotations["clips"]) >= 3

    small, small_builder, _b, _t = stacked(width=10, depth=10, lower=12, upper=12)
    one = joint(small, size="small")
    build(small, small_builder, one)
    assert len(one.annotations["clips"]) == 1


def test_enclosure_rim_places_joints_along_the_wall():
    """Two shelled halves meeting on a rim: joints follow the thin wall."""
    document = Document("Enclosure")
    bottom = document.add_feature(BoxFeature(
        inputs={"width": 120, "depth": 80, "height": 30}, outputs=["Bottom"]))
    top = document.add_feature(BoxFeature(
        inputs={"width": 120, "depth": 80, "height": 20, "z": 30}, outputs=["Top"]))
    builder = Rebuilder(document)
    builder.rebuild()
    for name, producer, direction in (("Bottom", bottom, (0, 0, 1)), ("Top", top, (0, 0, -1))):
        shape = document.bodies[name].shape
        document.add_feature(ShellFeature(inputs={
            "body": BodyRef(name), "thickness": 5,
            "faces": [make_ref(shape, _face(shape, direction), producer.id, body=name)],
        }, outputs=[name]))
    assert builder.rebuild().ok
    feature = joint(document, size="small", edge_offset=0.3, min_wall=0.8)
    build(document, builder, feature)
    clips = feature.annotations["clips"]
    assert len(clips) >= 3
    for clip in clips:
        x, y, _z = clip["at"]
        to_side, to_end = min(x, 120 - x), min(y, 80 - y)
        assert min(to_side, to_end) <= 5.0, f"joint at {x:.1f}, {y:.1f} is off the rim"
        # The joint's long axis runs along the wall it sits in.
        along = clip["x"]
        assert abs(along[1] if to_side < to_end else along[0]) > 0.99


def test_a_face_too_narrow_for_any_joint_says_why():
    document, builder, _b, _t = stacked(width=3, depth=3)
    with pytest.raises(CadError, match="wide"):
        feature = joint(document)
        document.add_feature(feature)
        report = builder.rebuild()
        raise report.failures[feature.id]


def test_parts_that_are_apart_are_reported_not_joined():
    document = Document("Apart")
    document.add_feature(BoxFeature(inputs={"width": 40, "depth": 30, "height": 12},
                                    outputs=["Bottom"]))
    document.add_feature(BoxFeature(inputs={"width": 40, "depth": 30, "height": 12, "z": 20},
                                    outputs=["Top"]))
    builder = Rebuilder(document)
    builder.rebuild()
    feature = joint(document)
    document.add_feature(feature)
    report = builder.rebuild()
    assert not report.ok
    assert "apart" in str(report.failures[feature.id])


def test_joints_keep_clear_of_a_hole_through_the_seam():
    document, builder, bottom, top = stacked(width=40, depth=40, lower=15, upper=15)
    document.add_feature(CylinderFeature(inputs={"radius": 6, "height": 40, "x": 20, "y": 20,
                                                 "z": -5}, outputs=["Drill"]))
    for name in ("Bottom", "Top"):
        document.add_feature(BooleanFeature(inputs={
            "body": BodyRef(name), "tool": BodyRef("Drill"), "operation": "cut",
            "keep_tool": name == "Bottom"}, outputs=[name]))
    assert builder.rebuild().ok
    feature = joint(document, count=4)
    build(document, builder, feature)
    for clip in feature.annotations["clips"]:
        x, y, _z = clip["at"]
        assert ((x - 20) ** 2 + (y - 20) ** 2) ** 0.5 > 6 + 3, "joint crowds the hole"


def test_flipping_the_male_side_swaps_tabs_and_pockets():
    document, builder, _b, _t = stacked()
    feature = joint(document, male="b")
    build(document, builder, feature)
    assert volume(document.bodies["Bottom"].shape) > 40 * 30 * 12
    assert volume(document.bodies["Top"].shape) < 40 * 30 * 12


def test_editing_and_deleting_the_joint_restores_both_parts():
    document, builder, _b, _t = stacked()
    original = {n: volume(b.shape) for n, b in document.bodies.items()}
    history = History(document)
    history.record("Join parts")
    feature = joint(document)
    build(document, builder, feature)
    joined = {n: volume(b.shape) for n, b in document.bodies.items()}

    history.record("Edit joint")
    feature.inputs["size"] = "small"
    builder.rebuild()
    assert volume(document.bodies["Top"].shape) < joined["Top"]

    history.record("Delete joint")
    document.remove_feature(feature.id)
    builder.rebuild()
    for name, value in original.items():
        assert volume(document.bodies[name].shape) == pytest.approx(value, rel=1e-9)

    builder.invalidate(history.undo())   # the delete
    builder.rebuild()
    builder.invalidate(history.undo())   # the edit
    builder.rebuild()
    assert volume(document.bodies["Top"].shape) == pytest.approx(joined["Top"], rel=1e-6)
    builder.invalidate(history.undo())   # the whole joint, in one step
    builder.rebuild()
    assert volume(document.bodies["Top"].shape) == pytest.approx(original["Top"], rel=1e-9)


def test_moving_a_part_after_joining_re_solves_the_joint():
    document, builder, _bottom, top = stacked(width=60, depth=40)
    feature = joint(document)
    build(document, builder, feature)
    # Move both parts together (the joint follows) ...
    document.add_feature(MoveFeature(inputs={"body": BodyRef("Top"), "dx": 10}, outputs=["Top"]))
    document.add_feature(MoveFeature(inputs={"body": BodyRef("Bottom"), "dx": 10}, outputs=["Bottom"]))
    assert builder.rebuild().ok
    # ... then lift the part upstream of the joint: it must say so, not
    # leave pockets and tabs floating in mid-air.
    top.inputs["z"] = 17
    report = builder.rebuild()
    assert not report.ok and "apart" in str(report.failures[feature.id])


def test_joined_parts_survive_save_and_reopen(tmp_path):
    document, builder, _b, _t = stacked()
    feature = joint(document)
    build(document, builder, feature)
    volumes = {n: volume(b.shape) for n, b in document.bodies.items()}
    path = str(tmp_path / "joined.scad3")
    save(document, path)
    reopened, _cached = load(path)
    again = Rebuilder(reopened)
    assert again.rebuild(force=True).ok
    for name, value in volumes.items():
        assert volume(reopened.bodies[name].shape) == pytest.approx(value, rel=1e-6)
    joint_feature = reopened.feature(feature.id)
    joint_feature.inputs["style"] = "tab_slot"
    assert again.rebuild().ok


def test_joined_parts_export_for_printing(tmp_path):
    from simplecad.kernel.io_formats import export_3mf, export_stl

    document, builder, _b, _t = stacked()
    build(document, builder, joint(document))
    for name, body in document.bodies.items():
        export_stl([body.shape], str(tmp_path / f"{name}.stl"))
    export_3mf([(n, [b.shape]) for n, b in document.bodies.items()], str(tmp_path / "parts.3mf"))
    assert (tmp_path / "parts.3mf").stat().st_size > 0


def test_preview_reports_placements_for_the_panel():
    document, builder, _b, _t = stacked()
    result = build_preview_result(document, joint(document).to_dict())
    assert result["error"] is None
    annotations = result["annotations"]
    assert annotations["clips"] and annotations["male"] == "a"
    assert all(clip["ok"] for clip in annotations["clips"])


def test_plan_clips_is_pure_and_honours_count_and_shift():
    document, _builder, _b, _t = stacked(width=200, depth=30)
    top, bottom = document.bodies["Top"].shape, document.bodies["Bottom"].shape
    seam = mate(_face(top, (0, 0, -1)), _face(bottom, (0, 0, 1)))
    from simplecad.kernel.clips import SIZE_PRESETS, ClipSpec
    clip = ClipSpec(**SIZE_PRESETS["medium"], removal_angle=35.0, clearance=0.15)
    spec = JointSpec("snap_tab", clip, None, 0.15, 0.3, 0.4, 0.8, "petg")
    placements, notes = plan_clips(seam, spec, count=4)
    assert len(placements) == 4 and not notes
    shifted, _ = plan_clips(seam, spec, count=4, shift=10.0)
    assert [p.u for p in shifted] != [p.u for p in placements]


def test_joints_keep_clear_of_text_engraved_in_the_seam():
    from simplecad.kernel.text import TextFeature

    document, builder, bottom, _top = stacked(width=80, depth=30, lower=10, upper=10)
    shape = document.bodies["Bottom"].shape
    document.add_feature(TextFeature(inputs={
        "body": BodyRef("Bottom"),
        "face": make_ref(shape, _face(shape, (0, 0, 1)), bottom.id, body="Bottom"),
        "text": "LID", "font_family": "sans-serif", "mode": "engraved",
        "text_height": 12, "depth": 1, "offset_x": 0, "offset_y": 0, "rotation": 0,
    }, outputs=["Bottom"]))
    assert builder.rebuild().ok
    lettering = bounding_box(document.bodies["Bottom"].shape)
    feature = joint(document, count=4)
    build(document, builder, feature)
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from simplecad.kernel.occ import boolean_builder, built_shape
    # Every joint lies clear of the letters: none sits over the text's middle.
    for clip in feature.annotations["clips"]:
        x, y, _z = clip["at"]
        assert not (28 < x < 52 and 9 < y < 21), f"joint at {x:.1f}, {y:.1f} is on the text"
    assert is_valid(document.bodies["Bottom"].shape)


def test_curved_faces_are_explained():
    document = Document("Round")
    document.add_feature(CylinderFeature(inputs={"radius": 10, "height": 20}, outputs=["Rod"]))
    document.add_feature(CylinderFeature(inputs={"radius": 12, "height": 20, "x": 22},
                                         outputs=["Other"]))
    builder = Rebuilder(document)
    builder.rebuild()
    rod, other = document.bodies["Rod"].shape, document.bodies["Other"].shape
    curved = next(f for f in sub_shapes(rod, "face") if fingerprint(f, "face").geometry != "plane")
    flat = _face(other, (0, 0, 1))
    with pytest.raises(CadError, match="flat faces"):
        mate(curved, flat)


def test_bring_together_seats_a_part_without_sliding_it():
    from simplecad.kernel.clips import seat_offsets

    document = Document("Seat")
    document.add_feature(BoxFeature(inputs={"width": 40, "depth": 30, "height": 12},
                                    outputs=["Bottom"]))
    document.add_feature(BoxFeature(inputs={"width": 30, "depth": 20, "height": 12,
                                            "x": 4, "y": 6, "z": 25}, outputs=["Top"]))
    builder = Rebuilder(document)
    builder.rebuild()
    top, bottom = document.bodies["Top"].shape, document.bodies["Bottom"].shape
    sx, sy = seat_offsets(_face(top, (0, 0, -1)), _face(bottom, (0, 0, 1)))
    feature = joint(document, seat=True, seat_x=sx, seat_y=sy, count=1, size="small")
    build(document, builder, feature)
    (low, _high) = bounding_box(document.bodies["Top"].shape)
    assert low[0] == pytest.approx(4.0, abs=1e-3) and low[1] == pytest.approx(6.0, abs=1e-3)
    assert low[2] < 12.0 + 1e-3  # seated (tabs reach below the seam)


def test_the_hidden_seam_is_found_from_any_click_on_each_part():
    from simplecad.kernel.clips import find_seam

    document, _builder, _b, _t = stacked()
    found = find_seam(document.bodies["Top"].shape, document.bodies["Bottom"].shape)
    assert found is not None
    face_a, face_b = found
    assert fingerprint(face_a, "face").center[2] == pytest.approx(12)
    assert fingerprint(face_a, "face").direction[2] == pytest.approx(-1)
    assert fingerprint(face_b, "face").direction[2] == pytest.approx(1)

    apart = Document()
    apart.add_feature(BoxFeature(inputs={"width": 5, "depth": 5, "height": 5}, outputs=["P"]))
    apart.add_feature(BoxFeature(inputs={"width": 5, "depth": 5, "height": 5, "z": 9}, outputs=["Q"]))
    Rebuilder(apart).rebuild()
    assert find_seam(apart.bodies["P"].shape, apart.bodies["Q"].shape) is None
