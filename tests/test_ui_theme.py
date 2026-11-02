"""The Graphite visual system: tokens, bundled fonts and the icon set."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[1] / "simplecad" / "ui"

#: Callables whose first argument is an icon name, and Command's icon position.
_FIRST_ARG = {"icon", "IconButton", "ToolTile", "ToolSpec", "ToolGroup", "svg"}


def _referenced_icons():
    for path in UI.rglob("*.py"):
        tree = ast.parse(path.read_text(), str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            position = 0 if name in _FIRST_ARG else 2 if name == "Command" else None
            if position is None or len(node.args) <= position:
                continue
            arg = node.args[position]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                yield path.name, node.lineno, arg.value


def test_every_icon_named_in_the_ui_exists():
    """An unknown name silently draws a cross; catch it here instead."""
    from simplecad.ui.icons import PATHS

    missing = [f"{file}:{line} {name!r}" for file, line, name in _referenced_icons()
               if name not in PATHS]
    assert not missing, missing


def test_no_inline_pixel_font_sizes():
    """Type sizes come from the Metrics scale, never a literal per widget."""
    literal = re.compile(r"font-size:\s*[0-9.]+px")
    offenders = [
        f"{path.name}:{number}"
        for path in UI.rglob("*.py") if path.name != "theme.py"
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if literal.search(line)
    ]
    assert not offenders, offenders


def test_palettes_are_complete_and_distinct():
    from simplecad.ui.theme import DARK, LIGHT, stylesheet

    assert DARK.accent != LIGHT.accent or DARK.bg != LIGHT.bg
    for palette in (DARK, LIGHT):
        # The accent must not read as an axis colour or as a warning.
        assert palette.accent not in (palette.warning, palette.danger, palette.success)
        assert palette.hover != palette.selected
        sheet = stylesheet(palette)
        assert palette.accent in sheet and "{" in sheet


def test_bundled_fonts_register(qapp):
    from simplecad.ui.theme import load_fonts

    families = load_fonts()
    assert "Inter" in families
    assert "JetBrains Mono" in families
    assert load_fonts() is families  # idempotent: registered once


@pytest.mark.parametrize("name", ["menu", "more", "lock", "unlock", "new", "mirror", "snap",
                                  "plane", "align_min", "align_center", "align_max",
                                  "distribute"])
def test_new_icons_render(qapp, name):
    from simplecad.ui.icons import icon

    assert not icon(name, "#000000", 18).isNull()
