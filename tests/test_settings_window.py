"""The Settings window: theme, display unit, modelling numbers, shortcuts."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QKeySequence

from tests.test_texture_editing import model  # noqa: F401 - the shared window fixture


@pytest.fixture
def profile(monkeypatch, tmp_path):
    from simplecad.core import settings

    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    yield settings
    from simplecad.core.units import set_display_unit

    set_display_unit("mm")


def _dialog(window):
    window.open_settings()
    return window._settings_dialog


def _bound(window, binding):
    return [s for s in window._shortcuts if s.key().toString() == binding and s.isEnabled()]


def test_rebinding_moves_the_action_to_the_new_key(model, profile):  # noqa: F811
    window = model.window
    dialog = _dialog(window)
    assert _bound(window, "M")
    assert dialog.set_shortcut("move", "Ctrl+Alt+M")
    assert profile.shortcuts()["move"] == "Ctrl+Alt+M"
    assert _bound(window, "Ctrl+Alt+M")
    assert not _bound(window, "M"), "the old key must stop moving"
    triggered = []
    window.activate_tool = lambda key: triggered.append(key)
    window.reload_shortcuts()
    _bound(window, "Ctrl+Alt+M")[0].activated.emit()
    assert triggered == ["move"]
    dialog.close()


def test_a_key_another_action_has_is_refused(model, profile):  # noqa: F811
    dialog = _dialog(model.window)
    assert not dialog.set_shortcut("move", "Ctrl+S")
    assert profile.shortcuts()["move"] == "M"
    assert "Save" in dialog.shortcut_status.text()
    dialog.close()


def test_restoring_defaults(model, profile):  # noqa: F811
    dialog = _dialog(model.window)
    dialog.set_shortcut("hole", "Ctrl+Alt+H")
    dialog.reset_shortcut("hole")
    assert profile.shortcuts()["hole"] == "H"
    dialog.set_shortcut("hole", "Ctrl+Alt+H")
    dialog.reset_all_shortcuts()
    assert profile.shortcuts() == profile.DEFAULT_SHORTCUTS
    assert dialog.editors["hole"].keySequence() == QKeySequence("H")
    dialog.close()


def test_group_and_ungroup_have_shortcuts(model, profile):  # noqa: F811
    assert profile.shortcuts()["group"] == "Ctrl+G"
    assert profile.shortcuts()["ungroup"] == "Ctrl+Shift+G"
    assert _bound(model.window, "Ctrl+G") and _bound(model.window, "Ctrl+Shift+G")
    assert not profile.conflicts()


def test_follow_the_desktop_theme_is_a_choice_that_persists(model, profile):  # noqa: F811
    from simplecad.ui.theme import Mode

    window = model.window
    dialog = _dialog(window)
    dialog.theme.setCurrentIndex(dialog.theme.findData("light"))
    assert profile.theme_choice() == "light" and window.mode is Mode.LIGHT
    dialog.theme.setCurrentIndex(dialog.theme.findData("system"))
    assert profile.theme_choice() == "system" and window.mode is Mode.SYSTEM
    dialog.close()


def test_display_unit_shows_lengths_in_inches_and_reads_back(model, profile):  # noqa: F811
    from simplecad.core.params import evaluate
    from simplecad.core.units import format_quantity

    dialog = _dialog(model.window)
    dialog.unit.setCurrentIndex(dialog.unit.findData("in"))
    assert profile.display_unit() == "in"
    shown = format_quantity(50.8)
    assert shown == "2 in"
    for value in (12.0, 3.175, 0.4, 250.0):
        assert evaluate(format_quantity(value)) == pytest.approx(value, abs=0.003)
    dialog.unit.setCurrentIndex(dialog.unit.findData("mm"))
    assert format_quantity(12.5) == "12.5 mm"
    dialog.close()


def test_modelling_numbers_are_saved_and_clamped(model, profile):  # noqa: F811
    dialog = _dialog(model.window)
    dialog._number_changed("move_step", 0.5)
    assert profile.number_preference("move_step") == 0.5
    dialog._number_changed("history_depth", 5)          # below the minimum of 10
    assert profile.number_preference("history_depth") == 10
    assert model.window.history.limit == 10
    dialog.close()


def test_a_file_named_from_outside_is_opened_or_imported(model, tmp_path, monkeypatch):  # noqa: F811
    window = model.window
    opened, imported = [], []
    monkeypatch.setattr(window, "_open_path", lambda p: opened.append(p))
    monkeypatch.setattr(window, "import_path", lambda p: imported.append(p))
    project = tmp_path / "bracket.scad3"
    project.write_text("x")
    step = tmp_path / "bracket.step"
    step.write_text("x")
    window._dirty = False
    window.open_file(str(project))
    window.open_file(str(step))
    window.open_file(str(tmp_path / "missing.scad3"))
    assert opened == [str(project)]
    assert imported == [str(step)]
