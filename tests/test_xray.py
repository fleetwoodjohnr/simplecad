"""X-ray is a view baseline layered with temporary tool transparency."""

from simplecad.ui.viewport.occt_view import (
    XRAY_TRANSPARENCY, effective_transparency,
)


def test_xray_wins_over_a_solid_or_lightly_translucent_tool_request():
    assert effective_transparency(0.0, True) == XRAY_TRANSPARENCY
    assert effective_transparency(0.3, True) == XRAY_TRANSPARENCY


def test_stronger_tool_transparency_wins_and_xray_off_restores_tool_value():
    assert effective_transparency(0.82, True) == 0.82
    assert effective_transparency(0.0, False) == 0.0
    assert effective_transparency(0.6, False) == 0.6
