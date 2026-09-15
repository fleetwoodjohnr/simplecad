"""Recent files and configurable shortcuts."""

from __future__ import annotations

import json
import os

import pytest

from simplecad.core import settings


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Never touch the user's real config."""
    directory = tmp_path / "config" / "simplecad"
    monkeypatch.setattr(settings, "CONFIG_DIR", str(directory))
    monkeypatch.setattr(
        settings, "SETTINGS_PATH", str(directory / "settings.json")
    )
    yield


def a_file(tmp_path, name="project.scad3") -> str:
    path = tmp_path / name
    path.write_text("x")
    return str(path)


# ----------------------------------------------------------------------
def test_no_recent_files_to_start_with():
    assert settings.recent_files() == []


def test_a_remembered_file_comes_back(tmp_path):
    path = a_file(tmp_path)
    settings.remember_file(path)
    assert settings.recent_files() == [os.path.abspath(path)]


def test_the_newest_file_is_first(tmp_path):
    first, second = a_file(tmp_path, "a.scad3"), a_file(tmp_path, "b.scad3")
    settings.remember_file(first)
    settings.remember_file(second)
    assert settings.recent_files()[0] == os.path.abspath(second)


def test_reopening_a_file_moves_it_to_the_top_without_duplicating(tmp_path):
    first, second = a_file(tmp_path, "a.scad3"), a_file(tmp_path, "b.scad3")
    settings.remember_file(first)
    settings.remember_file(second)
    settings.remember_file(first)
    recent = settings.recent_files()
    assert recent[0] == os.path.abspath(first)
    assert len(recent) == 2


def test_files_that_have_gone_are_not_offered(tmp_path):
    path = a_file(tmp_path)
    settings.remember_file(path)
    settings.remember_file("/nowhere/gone.scad3")
    assert settings.recent_files() == [os.path.abspath(path)]


def test_the_list_is_bounded(tmp_path):
    for index in range(settings.MAX_RECENT + 5):
        settings.remember_file(a_file(tmp_path, f"p{index}.scad3"))
    assert len(settings.recent_files()) == settings.MAX_RECENT


def test_recent_files_can_be_cleared(tmp_path):
    settings.remember_file(a_file(tmp_path))
    settings.forget_files()
    assert settings.recent_files() == []


# ----------------------------------------------------------------------
def test_the_shipped_shortcuts_do_not_clash():
    """Two actions on one key means one of them silently never fires."""
    assert settings.conflicts(settings.DEFAULT_SHORTCUTS) == {}


def test_single_key_shortcuts_are_identified():
    assert "search" in settings.SINGLE_KEY
    assert "view_top" in settings.SINGLE_KEY
    assert "save" not in settings.SINGLE_KEY, "Ctrl+S cannot be typed into a field"


def test_return_and_delete_yield_to_typing():
    """Regression: making Return a global shortcut broke dimension entry.

    Return has to commit the field being typed into rather than finish a
    spline, and Delete has to remove a character rather than a body. Neither is
    a single character, so a length check alone misses both.
    """
    assert "confirm" in settings.YIELDS_TO_TYPING
    assert "confirm_alt" in settings.YIELDS_TO_TYPING
    assert "delete" in settings.YIELDS_TO_TYPING


def test_modified_shortcuts_do_not_yield():
    """Ctrl+S cannot be typed, so it should keep working inside a field."""
    for action in ("save", "open", "undo", "export"):
        assert action not in settings.YIELDS_TO_TYPING


def test_every_yielding_action_is_a_real_action():
    assert settings.YIELDS_TO_TYPING <= set(settings.DEFAULT_SHORTCUTS)


def test_return_and_delete_are_scoped_to_the_viewport():
    """Guarding the handler is not enough for these.

    Qt consumes a matching key before the focused widget sees it, so a guard
    that declines to act still swallows the keystroke. Plain characters are
    exempt -- Qt gives those to a focused text field first -- but Return is not,
    which is why a window-wide Return made inline dimension editing impossible.
    """
    assert settings.VIEWPORT_ONLY == {"confirm", "confirm_alt", "delete"}
    assert settings.VIEWPORT_ONLY <= set(settings.DEFAULT_SHORTCUTS)


def test_plain_characters_are_not_viewport_scoped():
    """They do not need to be: Qt already prefers a focused text field."""
    assert not (settings.SINGLE_KEY & settings.VIEWPORT_ONLY)


def test_a_shortcut_can_be_overridden():
    assert settings.set_shortcut("search", "Ctrl+K") is True
    assert settings.shortcuts()["search"] == "Ctrl+K"
    # Everything else keeps its default.
    assert settings.shortcuts()["undo"] == "Ctrl+Z"


def test_overrides_survive_a_reload():
    settings.set_shortcut("dimension", "Ctrl+D")
    with open(settings.SETTINGS_PATH) as handle:
        stored = json.load(handle)
    assert stored["shortcuts"]["dimension"] == "Ctrl+D"


def test_resetting_restores_the_defaults():
    settings.set_shortcut("search", "Ctrl+K")
    settings.reset_shortcuts()
    assert settings.shortcuts()["search"] == settings.DEFAULT_SHORTCUTS["search"]


def test_an_unknown_action_is_refused():
    assert settings.set_shortcut("not_a_command", "Ctrl+Q") is False


def test_a_damaged_settings_file_falls_back_to_defaults():
    """A broken config must never stop the application starting."""
    os.makedirs(settings.CONFIG_DIR, exist_ok=True)
    with open(settings.SETTINGS_PATH, "w") as handle:
        handle.write("{ this is not json")
    assert settings.shortcuts() == settings.DEFAULT_SHORTCUTS
    assert settings.recent_files() == []


def test_a_conflicting_override_is_reported():
    settings.set_shortcut("search", "Ctrl+Z")     # already Undo
    clashes = settings.conflicts()
    assert "Ctrl+Z" in clashes
    assert set(clashes["Ctrl+Z"]) >= {"search", "undo"}


# ----------------------------------------------------------------------
def test_ui_preferences_have_safe_defaults_and_round_trip():
    assert settings.ui_preference("browser_expanded", True) is True
    assert settings.set_ui_preference("browser_expanded", False) is True
    assert settings.ui_preference("browser_expanded", True) is False


def test_a_malformed_ui_preference_uses_the_requested_default():
    os.makedirs(settings.CONFIG_DIR, exist_ok=True)
    with open(settings.SETTINGS_PATH, "w") as handle:
        json.dump({"ui": {"browser_expanded": "sometimes"}}, handle)
    assert settings.ui_preference("browser_expanded", True) is True
