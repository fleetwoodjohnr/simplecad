#!/usr/bin/env python
"""Capture light mode with a live selection, after a runtime theme toggle.

Checks the case that is easy to get wrong: switching theme while a selection is
active, so the contextual bar and any open tool panel have to re-theme too --
they are children of the stage, not of the docked chrome.

The 3D background is checked here too, and that is not a formality: this script
used to assert only that the *chrome* re-themed, which is how "the side bar goes
dark but the screen with the shape stays white" reached a user. The brightness
is read from Qt's composed top-level image, not OCCT's off-screen ``ToPixMap``;
an off-screen render can be correct while the framebuffer visible underneath a
native overlay is stale.
"""

from __future__ import annotations

import sys

from _harness import grab_composited, run_and_capture  # noqa: E402

#: How far the mean background brightness has to move for the switch to count.
#: The two gradients are far apart, so
#: this only asks that something happened, it does not measure a colour.
MIN_SHIFT = 40.0


def background_level(window, viewport):
    """Mean brightness of clear viewport patches in the composed window.

    Samples sit across the upper middle: away from the model, the shape panel,
    the browser and the ViewCube. Small patches make crossing grid lines noise
    rather than the answer. Coordinates are mapped from Qt's logical pixels to
    the captured image's device pixels, so this exercises the reported 2x-DPI
    path as well as a 1x display.
    """
    from PySide6.QtCore import QPoint

    image = grab_composited(window)
    if image.isNull() or not window.width() or not window.height():
        return None
    origin = viewport.mapTo(window, QPoint(0, 0))
    scale_x = image.width() / window.width()
    scale_y = image.height() / window.height()
    samples = (
        (0.36, 0.06), (0.50, 0.06), (0.64, 0.06),
        (0.38, 0.15), (0.52, 0.15), (0.66, 0.15),
    )
    total = 0.0
    count = 0
    radius = 3
    for relative_x, relative_y in samples:
        centre_x = int(round(
            (origin.x() + viewport.width() * relative_x) * scale_x
        ))
        centre_y = int(round(
            (origin.y() + viewport.height() * relative_y) * scale_y
        ))
        for y in range(
            max(0, centre_y - radius), min(image.height(), centre_y + radius + 1)
        ):
            for x in range(
                max(0, centre_x - radius), min(image.width(), centre_x + radius + 1)
            ):
                color = image.pixelColor(x, y)
                total += (color.red() + color.green() + color.blue()) / 3.0
                count += 1
    return total / count if count else None

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
            before = background_level(window, viewport)
            window.toggle_theme()                  # dark -> light
            after = background_level(window, viewport)
            REPORT["dark_background"] = None if before is None else round(before, 1)
            REPORT["light_background"] = None if after is None else round(after, 1)
            REPORT["viewport_themed"] = (
                before is not None and after is not None
                and after - before >= MIN_SHIFT
            )
            REPORT["palette"] = "light" if window.mode is Mode.LIGHT else "dark"
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
        and REPORT.get("viewport_themed")
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
