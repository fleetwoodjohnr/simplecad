#!/usr/bin/env python
"""Capture light mode with a live selection, after a runtime theme toggle.

Checks the case that is easy to get wrong: switching theme while a selection is
active, so the contextual bar and any open tool panel have to re-theme too --
they are children of the stage, not of the docked chrome.
"""

from __future__ import annotations

import sys

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}


def main() -> int:
    from PySide6.QtCore import QPoint, Qt, QTimer
    from PySide6.QtTest import QTest

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def build(_app):
        window = MainWindow(Mode.DARK)   # start dark, toggle to light below

        def populate() -> None:
            from simplecad.kernel.primitives import BoxFeature, CylinderFeature

            window.add_feature(
                BoxFeature(inputs={"width": 70, "depth": 45, "height": 16},
                           outputs=["Base"])
            )
            window.add_feature(
                CylinderFeature(
                    inputs={"radius": 7, "height": 26, "x": 35, "y": 22, "z": 16},
                    outputs=["Post"],
                )
            )
            window.wait_for_rebuild()
            window.stage.viewport.fit_all()
            QTimer.singleShot(400, select_then_toggle)

        def select_then_toggle() -> None:
            viewport = window.stage.viewport
            centre = viewport.rect().center() + QPoint(-60, 70)
            QTest.mouseMove(viewport, centre)
            QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, centre)
            REPORT["selected_before_toggle"] = window.selection.count
            window.activate_tool("hole")          # a tool panel open across the switch
            window.toggle_theme()                  # dark -> light
            REPORT["palette"] = "light" if window.palette_.bg == "#EDEFF3" else "dark"
            bar = window.context_bar
            REPORT["bar_themed"] = window.palette_.surface_raised in bar.styleSheet()
            panels = [w for w, _a in window.stage.overlays
                      if getattr(w, "is_tool_panel", False)]
            REPORT["tool_panel_themed"] = bool(panels) and all(
                window.palette_.surface_raised in p.styleSheet() for p in panels
            )

        window.stage.viewport.ready.connect(populate)
        return window

    out = sys.argv[1] if len(sys.argv) > 1 else "docs/shots/app_light.png"
    result = run_and_capture(build, out, settle_ms=3200, size=(1500, 940))
    print("--- theme shot ---")
    for key, value in {**REPORT, **result}.items():
        print(f"  {key}: {value}")
    ok = (
        REPORT.get("palette") == "light"
        and REPORT.get("bar_themed")
        and REPORT.get("tool_panel_themed")
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
