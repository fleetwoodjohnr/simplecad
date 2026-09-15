"""The compact shell must never advertise a dead or generic-looking action."""

from simplecad.ui.icons import available
from simplecad.ui.main_window import PINNED_TOOLS, TOOL_GROUPS
from simplecad.ui.panels.command_search import catalogue
from simplecad.ui.tools.registry import load


def test_every_rail_entry_resolves_to_a_registered_tool():
    entries = list(PINNED_TOOLS) + [
        tool for group in TOOL_GROUPS for tool in group.tools
    ]
    assert entries
    assert {entry.key for entry in entries} <= set(load())


def test_pinned_tools_are_not_duplicated_in_flyouts():
    pinned = {entry.key for entry in PINNED_TOOLS}
    grouped = {tool.key for group in TOOL_GROUPS for tool in group.tools}
    assert pinned.isdisjoint(grouped)
    assert {group.key for group in TOOL_GROUPS} == {"create", "modify", "inspect"}


def test_every_command_has_an_intentional_icon():
    assert {command.icon for command in catalogue()} <= set(available())
