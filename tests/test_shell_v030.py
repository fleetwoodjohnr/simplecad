"""The v0.3.0 shell: rail, app menu, Properties card, lock and Select all."""

from __future__ import annotations

import math

import pytest

from simplecad.kernel.occ import bounding_box
from tests.test_texture_editing import model  # noqa: F401 - the shared window fixture


def test_about_reads_name_and_version():
    from simplecad import __version__
    from simplecad.ui.main_window import about_heading

    assert about_heading() == f"SimpleCAD — Version {__version__}"


def test_rail_pins_the_everyday_transforms():
    from simplecad.ui.main_window import PINNED_TOOLS, TOOL_GROUPS

    pinned = [tool.key for tool in PINNED_TOOLS]
    for key in ("move", "rotate", "scale", "align_arrange"):
        assert key in pinned
    flyout = {tool.key for group in TOOL_GROUPS for tool in group.tools}
    assert not flyout & set(pinned), "a tool lives in one place, not two"


def test_new_shortcuts_do_not_clash():
    from simplecad.core import settings

    assert settings.DEFAULT_SHORTCUTS["new"] == "Ctrl+N"
    assert settings.DEFAULT_SHORTCUTS["select_all"] == "Ctrl+A"
    assert settings.conflicts(settings.DEFAULT_SHORTCUTS) == {}


def test_reference_points_and_euler_angles():
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

    from simplecad.ui.panels.properties_card import euler_xyz, reference_point

    low, high = (0.0, 0.0, 2.0), (10.0, 20.0, 6.0)
    assert reference_point(low, high, "bottom") == (5.0, 10.0, 2.0)
    assert reference_point(low, high, "centre") == (5.0, 10.0, 4.0)
    assert reference_point(low, high, "min") == low
    turn = gp_Trsf()
    turn.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), math.radians(45))
    assert euler_xyz(turn) == pytest.approx((0.0, 0.0, 45.0))
    assert euler_xyz(gp_Trsf()) == (0.0, 0.0, 0.0)


def test_move_rotate_scale_are_offered_for_a_picked_face(model):  # noqa: F811
    from simplecad.ui.selection import available_actions

    model.pick("A", kind="face")
    keys = [key for key, _label, _icon in available_actions(model.window.selection)]
    assert {"move", "rotate", "scale"} <= set(keys)


def test_a_locked_body_offers_nothing_that_changes_it(model):  # noqa: F811
    from simplecad.ui.selection import SAFE_WHEN_LOCKED, available_actions

    window = model.window
    window.set_locked(["A"], True)
    assert window.is_locked("A")
    model.pick("A")
    keys = {key for key, _label, _icon in available_actions(window.selection)}
    assert "unlock" in keys
    assert keys <= SAFE_WHEN_LOCKED
    count = len(window.document.features)
    window.delete_selection()
    window.quarter_turn("rz")
    window.drop_to_plate()
    window.activate_tool("move")
    assert len(window.document.features) == count
    assert window.document.body("A") is not None
    window.set_locked(["A"], False)
    assert "locked" not in window.document.body("A").metadata


def test_lock_survives_save_and_reopen(model, tmp_path):  # noqa: F811
    from simplecad.core.project import load, save

    window = model.window
    window.set_locked(["B"], True)
    path = str(tmp_path / "locked.scad3")
    save(window.document, path)
    document, _cached = load(path)
    assert document.body("B").metadata.get("locked") is True
    assert not document.body("A").metadata.get("locked")


def test_select_all_skips_locked_and_hidden(model, monkeypatch):  # noqa: F811
    window = model.window
    chosen = []
    monkeypatch.setattr(window, "select_items", lambda items: chosen.extend(items))
    window.set_locked(["B"], True)
    window.select_all()
    assert chosen == ["A"]


def test_properties_show_position_and_size(model):  # noqa: F811
    window = model.window
    window.browser.set_collapsed(False, emit=False)
    model.pick("B")
    card = window.browser.properties
    assert not card.isHidden()
    assert card.title.text() == "B"
    assert [field.value() for field in card.size] == pytest.approx([15.0, 15.0, 3.0])
    # Bottom centre: B starts at x = 30.
    assert [field.value() for field in card.position] == pytest.approx([37.5, 7.5, 0.0])


def test_properties_position_is_one_move(model):  # noqa: F811
    window = model.window
    window.browser.set_collapsed(False, emit=False)
    model.pick("B")
    window.move_selection_to(2, 12.0)
    low, _high = bounding_box(window.document.body("B").shape)
    assert low[2] == pytest.approx(12.0, abs=1e-6)
    assert window.history.undo_label == "Move Z"
    window.undo()
    assert bounding_box(window.document.body("B").shape)[0][2] == pytest.approx(0.0, abs=1e-6)


def test_properties_size_keeps_the_reference_point(model):  # noqa: F811
    window = model.window
    window.browser.set_collapsed(False, emit=False)
    model.pick("A")
    window.resize_selection(2, 9.0, False)  # height 3 -> 9, bottom stays on z = 0
    low, high = bounding_box(window.document.body("A").shape)
    assert high[2] - low[2] == pytest.approx(9.0)
    assert low[2] == pytest.approx(0.0, abs=1e-6)
    assert high[0] - low[0] == pytest.approx(15.0), "only the edited axis changes"
    window.resize_selection(0, 30.0, True)  # width doubles, so does everything
    low, high = bounding_box(window.document.body("A").shape)
    assert [high[i] - low[i] for i in range(3)] == pytest.approx([30.0, 30.0, 18.0])


def test_new_project_empties_the_window(model):  # noqa: F811
    window = model.window
    window._dirty = False
    window.new_project()
    assert not window.document.features
    assert not window.document.bodies


def test_app_menu_holds_files_settings_and_help(model):  # noqa: F811
    menu = model.window.build_app_menu()
    seen = [action.text() for action in menu.actions() if action.text()]
    menu.deleteLater()
    for label in ("New project", "Open…", "Save", "Save as…", "Import…", "Export…",
                  "Settings…", "Keyboard shortcuts…", "Check for updates…", "About SimpleCAD"):
        assert label in seen


def test_search_offers_the_new_shell_commands():
    from simplecad.ui.panels.command_search import catalogue

    keys = {command.key for command in catalogue()}
    assert {"new", "select_all", "lock", "unlock", "align_arrange", "shortcuts"} <= keys
    assert not {"sweep", "loft"} & keys, "commands with no tool are not offered"
