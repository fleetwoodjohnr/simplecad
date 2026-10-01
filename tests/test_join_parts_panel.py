"""The Join Parts panel: click A, click B, preview, Apply -- and edit later."""

from __future__ import annotations

import pytest

from simplecad.core.naming import fingerprint, sub_shapes
from simplecad.kernel.occ import volume
from simplecad.kernel.primitives import BoxFeature
from tests.test_texture_editing import model  # noqa: F401 - shared Qt fixture


def _face(shape, direction):
    best = None
    for face in sub_shapes(shape, "face"):
        data = fingerprint(face, "face")
        if data.geometry == "plane" and data.direction and \
                sum(a * b for a, b in zip(data.direction, direction)) > 0.99:
            level = sum(a * b for a, b in zip(data.center, direction))
            if best is None or level > best[0]:
                best = (level, face)
    return best[1]


@pytest.fixture
def stacked(model, monkeypatch):
    window = model.window
    window.document.add_feature(BoxFeature(
        inputs={"width": 40, "depth": 30, "height": 12, "y": 60}, outputs=["Lower"]))
    window.document.add_feature(BoxFeature(
        inputs={"width": 40, "depth": 30, "height": 12, "y": 60, "z": 12}, outputs=["Upper"]))
    window.rebuild()
    picks = []

    def entries():
        return [dict(presentation=window._presentations[name], kind="face", shape=face)
                for name, face in picks]

    def pick(*chosen):
        picks[:] = list(chosen)
        window._on_selection()

    monkeypatch.setattr(window.stage.viewport, "selected_entries", entries)
    monkeypatch.setattr(window.stage.viewport, "clear_selection", lambda: pick())
    lower = lambda: window.document.body("Lower").shape  # noqa: E731
    upper = lambda: window.document.body("Upper").shape  # noqa: E731
    return window, pick, lambda: ("Upper", _face(upper(), (0, 0, -1))), \
        lambda: ("Lower", _face(lower(), (0, 0, 1)))


def _panel(window):
    from simplecad.ui.tools.clips import ClipJointPanel

    return next(widget for widget, _ in window.stage.overlays if isinstance(widget, ClipJointPanel))


def _preview(panel):
    controller = panel._preview_controller
    controller.timer.stop()
    assert controller._state is not None
    panel._preview_answer({"token": 1, **controller._in_process()})


def test_click_a_then_b_previews_and_applies_one_undo_step(stacked):
    window, pick, face_a, face_b = stacked
    pick()
    window.activate_tool("clip_joint")
    panel = _panel(window)
    assert panel._role == "a"
    pick(face_a())
    assert panel._role == "b" and panel._bodies["a"] == "Upper"
    pick(face_b())
    assert panel._role is None and panel._bodies["b"] == "Lower"
    assert panel.seat.isHidden()

    _preview(panel)
    assert panel.confirm.isEnabled(), panel.status.text()
    assert panel.marker.footprints, "joint positions are drawn on the model"
    assert panel.marker.arrow is not None

    before = {name: volume(window.document.body(name).shape) for name in ("Upper", "Lower")}
    undo_depth = len(window.history._undo)
    panel.commit()
    assert volume(window.document.body("Upper").shape) > before["Upper"]
    assert volume(window.document.body("Lower").shape) < before["Lower"]
    joint = next(f for f in window.document.features if f.type_name == "clip_joint")
    assert joint.inputs["clip_version"] == 2
    window.history._finish_pending()
    assert len(window.history._undo) == undo_depth + 1

    window.undo()
    assert volume(window.document.body("Upper").shape) == pytest.approx(before["Upper"])


def test_a_joint_reopens_from_history_and_edits_in_place(stacked):
    window, pick, face_a, face_b = stacked
    pick(face_a(), face_b())
    window.activate_tool("clip_joint")
    panel = _panel(window)
    _preview(panel)
    panel.commit()
    joint = next(f for f in window.document.features if f.type_name == "clip_joint")
    joined = volume(window.document.body("Upper").shape)

    pick(face_b())
    assert "edit_joint" in window.context_bar.keys()
    window.run_action("edit_joint")
    panel = _panel(window)
    assert panel._source is joint and panel.title_label.text() == "Edit joint"
    panel.style.setCurrentIndex(panel.style.findData("tab_slot"))
    panel.flip_button.setChecked(True)
    _preview(panel)
    assert panel.confirm.isEnabled(), panel.status.text()
    panel.commit()

    edited = window.document.feature(joint.id)
    assert edited is not joint and edited.inputs["style"] == "tab_slot"
    assert edited.inputs["male"] == "b"
    assert [f.id for f in window.document.features].count(joint.id) == 1
    assert volume(window.document.body("Upper").shape) < joined, "tabs moved to the other part"

    window.undo()
    assert window.document.feature(joint.id).inputs["style"] == "snap_tab"


def test_parts_that_are_apart_offer_to_be_brought_together(stacked):
    window, pick, face_a, face_b = stacked
    upper = next(f for f in window.document.features if f.outputs == ["Upper"])
    upper.inputs["z"] = 20
    window.rebuild()
    pick(face_a(), face_b())
    window.activate_tool("clip_joint")
    panel = _panel(window)
    assert not panel.seat.isHidden() and panel.seat.isChecked()
    assert "8.0 mm apart" in panel.seat.text()
    _preview(panel)
    assert panel.confirm.isEnabled(), panel.status.text()
    panel.commit()
    from simplecad.kernel.occ import bounding_box
    assert bounding_box(window.document.body("Upper").shape)[1][2] == pytest.approx(24.0, abs=1e-3)


def test_clicking_anywhere_on_each_part_finds_the_hidden_seam(stacked, monkeypatch):
    window, _pick, _face_a, _face_b = stacked
    picks = []
    monkeypatch.setattr(window.stage.viewport, "selected_entries", lambda: [
        dict(presentation=window._presentations[name], kind=kind, shape=shape)
        for name, kind, shape in picks])

    def click(name, kind="solid", direction=None):
        shape = window.document.body(name).shape
        if direction is not None:
            shape = _face(shape, direction)
        picks[:] = [(name, kind, shape)]
        window._on_selection()

    click("Upper")
    window.activate_tool("clip_joint")
    panel = _panel(window)
    assert panel._role == "b" and panel._bodies["a"] == "Upper"
    # A visible side face of the lower part, not the hidden top face.
    click("Lower", "face", (1, 0, 0))
    assert panel._role is None
    assert panel.picks["a"].info.normal[2] == pytest.approx(-1)
    assert panel.picks["b"].info.normal[2] == pytest.approx(1)
    _preview(panel)
    assert panel.confirm.isEnabled(), panel.status.text()
