"""Align & Arrange (v0.3.0): line objects up, Stack & Place opens on what was
clicked, Arrange on the build plate, Mirror, and a quieter context bar."""

from __future__ import annotations

import pytest

from simplecad.core.document import BodyRef, Document
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.align import align_offsets, distribute_offsets
from simplecad.kernel.occ import bounding_box, volume
from simplecad.kernel.primitives import BoxFeature
from tests.test_texture_editing import model  # noqa: F401 - the shared window fixture

BOXES = [((0.0, 0.0, 0.0), (10.0, 10.0, 10.0)),
         ((20.0, 5.0, 0.0), (25.0, 15.0, 4.0)),
         ((50.0, 0.0, 0.0), (60.0, 2.0, 2.0))]


# -- the arithmetic -------------------------------------------------------------
@pytest.mark.parametrize("side,expected", [
    ("min", [0.0, -20.0, -50.0]),
    ("centre", [25.0, 7.5, -25.0]),
    ("max", [50.0, 35.0, 0.0]),
])
def test_align_to_the_selection_lines_up_that_side(side, expected):
    assert align_offsets(BOXES, 0, side) == pytest.approx(expected)


def test_align_to_the_first_or_last_leaves_that_one_alone():
    first = align_offsets(BOXES, 2, "max", "first")
    last = align_offsets(BOXES, 2, "max", "last")
    assert first[0] == 0.0 and first == pytest.approx([0.0, 6.0, 8.0])
    assert last[-1] == 0.0 and last == pytest.approx([-8.0, -2.0, 0.0])


def test_equal_gaps_keep_the_outermost_two():
    offsets = distribute_offsets(BOXES, 0)
    assert offsets[0] == 0.0 and offsets[2] == 0.0
    assert offsets[1] == pytest.approx(7.5)        # 35 mm free, two gaps of 17.5


def test_a_fixed_gap_works_from_the_first_and_needs_only_two():
    assert distribute_offsets(BOXES, 0, 5.0) == pytest.approx([0.0, -5.0, -25.0])
    assert distribute_offsets(BOXES[:2], 0, 3.0) == pytest.approx([0.0, -7.0])
    assert distribute_offsets(BOXES[:2], 0) == [0.0, 0.0], "equal gaps need three"


def test_distribute_orders_by_position_not_selection():
    shuffled = [BOXES[2], BOXES[0], BOXES[1]]
    offsets = distribute_offsets(shuffled, 0, 5.0)
    assert offsets == pytest.approx([-25.0, 0.0, -5.0])


def test_unknown_side_is_refused():
    with pytest.raises(ValueError):
        align_offsets(BOXES, 0, "left")


# -- the Align tab, through the window ------------------------------------------
def _panel(window, title):
    return next(w for w, _a in window.stage.overlays if getattr(w, "title", "") == title)


def test_align_tab_lines_objects_up_in_one_step(model):  # noqa: F811
    window = model.window
    model.pick("A", "B")
    window.activate_tool("align_arrange")
    panel = _panel(window, "Align")
    assert not panel.align(1, "max"), "already lined up in Y: nothing to do"
    assert panel.align(0, "max")
    highs = [bounding_box(window.document.body(n).shape)[1][0] for n in ("A", "B")]
    assert highs[0] == pytest.approx(highs[1])
    assert window.history.undo_label.startswith("Align")
    window.undo()
    assert bounding_box(window.document.body("A").shape)[0][0] == pytest.approx(0.0, abs=1e-6)


def test_align_tab_spreads_three_objects_evenly(model):  # noqa: F811
    window = model.window
    window.document.add_feature(BoxFeature(
        inputs={"width": 5, "depth": 5, "height": 3, "x": 100}, outputs=["C"]))
    window.rebuild()
    model.pick("A", "B", "C")
    window.activate_tool("align_bodies")
    panel = _panel(window, "Align")
    assert panel.distribute(0)
    boxes = sorted((bounding_box(window.document.body(n).shape) for n in "ABC"),
                   key=lambda b: b[0][0])
    gaps = [boxes[i + 1][0][0] - boxes[i][1][0] for i in range(2)]
    assert gaps[0] == pytest.approx(gaps[1], abs=1e-6)
    panel.fixed_gap.setChecked(True)
    panel.gap.set_value(2.0)
    assert panel.distribute(0)
    boxes = sorted((bounding_box(window.document.body(n).shape) for n in "ABC"),
                   key=lambda b: b[0][0])
    assert [boxes[i + 1][0][0] - boxes[i][1][0] for i in range(2)] == pytest.approx([2.0, 2.0])


def test_align_needs_two_objects(model):  # noqa: F811
    window = model.window
    model.pick("A")
    window.activate_tool("align_bodies")
    count = len(window.document.features)
    assert not _panel(window, "Align").align(0, "min")
    assert len(window.document.features) == count


@pytest.mark.parametrize("key,operation", [("center", "center"), ("stack", "stack"),
                                           ("align_stack", "stack")])
def test_stack_and_place_opens_on_the_operation_that_was_clicked(model, key, operation):  # noqa: F811
    window = model.window
    model.pick("A", "B", kind="face")
    window.activate_tool(key)
    assert _panel(window, "Stack & Place").operation == operation


def test_the_three_tabs_switch_between_each_other(model):  # noqa: F811
    window = model.window
    model.pick("A", "B")
    window.activate_tool("align_bodies")
    from simplecad.ui.tools.base import ToolTabs

    (strip,) = _panel(window, "Align").findChildren(ToolTabs)
    strip.buttons["arrange"].click()
    assert _panel(window, "Arrange") is not None


# -- the context bar -------------------------------------------------------------
def _keys(window):
    from simplecad.ui.selection import available_actions

    return [key for key, _label, _icon in available_actions(window.selection)]


def test_two_flat_faces_lead_with_stack_and_offer_one_way_to_the_rest(model):  # noqa: F811
    model.pick("A", "B", kind="face")
    keys = _keys(model.window)
    assert keys[0] == "stack"
    assert "align_arrange" in keys and "clip_joint" in keys
    for gone in ("center", "align_stack", "section_replace", "hole", "shell"):
        assert gone not in keys


def test_objects_alone_offer_align_and_arrange_not_arrange(model):  # noqa: F811
    model.pick("A", "B")
    keys = _keys(model.window)
    assert "align_arrange" in keys and "mirror" in keys
    assert "arrange" not in keys


def test_the_router_picks_the_tab_for_the_selection(model):  # noqa: F811
    window = model.window
    model.pick("A", "B")
    assert window._align_arrange_tool() == "align_bodies"
    model.pick("A", "B", kind="face")
    assert window._align_arrange_tool() == "align"


# -- Arrange on the build plate ---------------------------------------------------
def test_arrange_on_the_plate_needs_no_face():
    from simplecad.kernel.arrange import ArrangeFeature
    from simplecad.kernel.thread_specs import printer_profile

    document = Document("Plate")
    for index, x in enumerate((0, 400, -300)):
        document.add_feature(BoxFeature(
            inputs={"width": 20, "depth": 20, "height": 10, "x": x, "z": 15},
            outputs=[chr(ord("A") + index)]))
    builder = Rebuilder(document)
    assert builder.rebuild().ok
    feature = document.add_feature(ArrangeFeature(
        inputs={"bodies": [BodyRef(n) for n in "ABC"], "pattern": "grid", "target": "plate",
                "spacing": "fixed", "gap": "5", "boundary": "auto", "margin": "2",
                "move_x": 0.0, "move_y": 0.0, "move_normal": 0.0, "frame_mode": "reference"},
        outputs=["A", "B", "C"]))
    report = builder.rebuild()
    assert report.ok, report.failures
    plate = printer_profile()["build_volume"]
    for name in "ABC":
        low, high = bounding_box(document.body(name).shape)
        assert low[2] == pytest.approx(0.0, abs=1e-6), "standing on the plate"
        assert 0.0 <= low[0] and high[0] <= plate["x"] + 1e-6
        assert 0.0 <= low[1] and high[1] <= plate["y"] + 1e-6
    assert feature.to_dict()["inputs"]["target"] == "plate"


# -- Mirror ---------------------------------------------------------------------
def test_mirror_in_place_replaces_and_old_files_still_fuse():
    from simplecad.kernel.advanced import MirrorFeature

    document = Document("Mirror")
    document.add_feature(BoxFeature(inputs={"width": 10, "depth": 10, "height": 10, "x": 5},
                                    outputs=["A"]))
    builder = Rebuilder(document)
    assert builder.rebuild().ok
    document.add_feature(MirrorFeature(
        inputs={"body": BodyRef("A"), "normal": [1, 0, 0], "origin": [0, 0, 0], "keep": False},
        outputs=["A"]))
    assert builder.rebuild().ok
    low, high = bounding_box(document.body("A").shape)
    assert (low[0], high[0]) == pytest.approx((-15.0, -5.0), abs=1e-6)
    assert volume(document.body("A").shape) == pytest.approx(1000.0, rel=1e-6)

    legacy = Document("Legacy")
    legacy.add_feature(BoxFeature(inputs={"width": 10, "depth": 10, "height": 10, "x": 5},
                                  outputs=["A"]))
    legacy.add_feature(MirrorFeature(
        inputs={"body": BodyRef("A"), "normal": [1, 0, 0], "origin": [0, 0, 0]}, outputs=["A"]))
    assert Rebuilder(legacy).rebuild().ok
    assert volume(legacy.body("A").shape) == pytest.approx(2000.0, rel=1e-6), "both halves"


def test_mirror_copy_sits_beside_the_original_and_is_its_own_object(model, tmp_path):  # noqa: F811
    from simplecad.core.project import load, save

    window = model.window
    model.pick("B")
    window.activate_tool("mirror")
    panel = _panel(window, "Mirror")
    panel._choose("0")
    panel.copy.setChecked(True)
    before = set(window.document.bodies)
    panel.commit()
    added = set(window.document.bodies) - before
    assert len(added) == 1
    copy = added.pop()
    low, high = bounding_box(window.document.body(copy).shape)
    original = bounding_box(window.document.body("B").shape)
    assert low[0] == pytest.approx(original[1][0], abs=1e-6), "mirrored across B's right side"
    assert high[0] - low[0] == pytest.approx(original[1][0] - original[0][0])
    assert window.history.undo_label == "Mirror copy"
    path = str(tmp_path / "mirror.scad3")
    save(window.document, path)
    reopened, _cached = load(path)
    assert copy in reopened.bodies
    window.undo()
    assert copy not in window.document.bodies
    window.redo()
    assert copy in window.document.bodies


def test_a_mirrored_face_still_reports_an_outward_normal():
    """Regression: a mirror makes left-handed planes, and analyse_plane read
    their axis as the normal -- every face of a mirrored part pointed inward,
    and Arrange stood a mirrored copy upside down beneath the plate."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf

    from simplecad.core.naming import sub_shapes
    from simplecad.kernel.detect import analyse_plane
    from simplecad.kernel.occ import transformed

    mirror = gp_Trsf()
    mirror.SetMirror(gp_Ax2(gp_Pnt(20, 5, 2.5), gp_Dir(1, 0, 0)))
    shape = transformed(BRepPrimAPI_MakeBox(20, 10, 5).Shape(), mirror)
    low, high = bounding_box(shape)
    centre = [(a + b) / 2 for a, b in zip(low, high)]
    for face in sub_shapes(shape, "face"):
        info = analyse_plane(face)
        outward = [c - m for c, m in zip(info.center, centre)]
        assert sum(n * o for n, o in zip(info.normal, outward)) > 0, info
