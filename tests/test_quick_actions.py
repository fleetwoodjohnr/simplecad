"""Colour, quarter turns, drop to plate and Backspace, through the real window."""

from __future__ import annotations

import pytest

from simplecad.kernel.occ import bounding_box
from tests.test_texture_editing import model  # noqa: F401 - the shared window fixture


def test_colour_is_one_undo_step_and_rebuilds_nothing(model):  # noqa: F811
    window = model.window
    model.pick("A")
    features = len(window.document.features)
    reports = len(model.reports)
    window.set_body_colour(["A"], "#3366cc")
    assert window.document.body("A").color == "#3366cc"
    assert len(window.document.features) == features
    assert len(model.reports) == reports, "a colour change must not rebuild geometry"
    assert window.history.undo_label == "Colour"
    window.undo()
    assert window.document.body("A").color is None
    window.redo()
    assert window.document.body("A").color == "#3366cc"
    assert window.document.export_colours() == {"A": "#3366cc"}


@pytest.mark.parametrize("key,swapped", [("turn_x", (1, 2)), ("turn_y", (0, 2)), ("turn_z", (0, 1))])
def test_quarter_turn_swaps_the_turned_extents(model, key, swapped):  # noqa: F811
    window = model.window
    model.pick("A")
    low, high = bounding_box(window.document.body("A").shape)
    before = [high[i] - low[i] for i in range(3)]
    window.run_action(key)
    low, high = bounding_box(window.document.body("A").shape)
    after = [high[i] - low[i] for i in range(3)]
    a, b = swapped
    assert after[a] == pytest.approx(before[b], abs=1e-6)
    assert after[b] == pytest.approx(before[a], abs=1e-6)
    assert window.document.features[-1].inputs["order"] == "xyz"
    assert bounding_box(window.document.body("B").shape)[0][0] == pytest.approx(30.0)


def test_drop_to_plate_sits_the_selection_on_zero(model):  # noqa: F811
    window = model.window
    model.pick("A")
    window.run_action("turn_x")
    window.run_action("drop_to_plate")
    assert bounding_box(window.document.body("A").shape)[0][2] == pytest.approx(0.0, abs=1e-6)
    count = len(window.document.features)
    window.run_action("drop_to_plate")
    assert len(window.document.features) == count, "already on the plate: nothing to add"


def test_backspace_is_bound_to_delete_and_yields_to_typing():
    from simplecad.core import settings

    assert settings.DEFAULT_SHORTCUTS["delete_alt"] == "Backspace"
    assert "delete_alt" in settings.YIELDS_TO_TYPING
    assert "delete_alt" in settings.VIEWPORT_ONLY


def test_new_actions_are_offered_for_bodies(model):  # noqa: F811
    from simplecad.ui.selection import available_actions

    model.pick("A")
    keys = [key for key, _label, _icon in available_actions(model.window.selection)]
    for key in ("colour", "turn_x", "turn_y", "turn_z", "drop_to_plate"):
        assert key in keys


def test_preferences_turn_on_smaller_project_files(model, tmp_path, monkeypatch):  # noqa: F811
    from simplecad.core import settings

    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    window = model.window
    window.activate_tool("preferences")
    panel = next(w for w, _a in window.stage.overlays if getattr(w, "title", "") == "Preferences")
    assert not panel.compact.isChecked()
    panel.compact.setChecked(True)
    panel.commit()
    assert settings.ui_preference("compact_saves", False) is True

    sent = {}
    monkeypatch.setattr(window.geometry, "save", lambda *a, **k: sent.update(k) or True)
    window._project_path = str(tmp_path / "part.scad3")
    window.save_document()
    assert sent["compact"] is True
