#!/usr/bin/env python
"""Point-to-point measuring: does the cursor snap where it should, and is the
distance right?

Drives real mouse moves and clicks over a box, checking three things: that the
cursor snaps to a corner rather than to wherever the pointer happened to land,
that a second click completes the measurement, and that the number matches the
geometry to the micron.
"""

from __future__ import annotations

import math
import os
import sys

os.environ["SIMPLECAD_NO_GRID"] = "1"

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}


def main() -> int:
    from PySide6.QtCore import QPoint, Qt, QTimer
    from PySide6.QtTest import QTest

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    WIDTH, DEPTH, HEIGHT = 50.0, 40.0, 20.0

    def build(_app):
        window = MainWindow(Mode.DARK)

        def populate() -> None:
            from simplecad.kernel.primitives import BoxFeature

            window.add_feature(
                BoxFeature(inputs={"width": WIDTH, "depth": DEPTH, "height": HEIGHT},
                           outputs=["Block"])
            )
            window.wait_for_rebuild()
            window.stage.viewport.fit_all()
            QTimer.singleShot(500, run)

        def run() -> None:
            from simplecad.kernel.snapping import snap_points

            viewport = window.stage.viewport
            window.activate_tool("measure")
            panel = next(
                (w for w, _a in window.stage.overlays
                 if type(w).__name__ == "MeasurePanel"), None
            )
            REPORT["panel_opened"] = panel is not None
            if panel is None:
                return
            panel.set_mode("points")
            REPORT["point_mode"] = viewport.picking_points

            shape = window.document.bodies["Block"].shape
            corners = [s for s in snap_points(shape) if s.kind == "vertex"]
            REPORT["vertex_snaps"] = len(corners)

            # Two opposite corners of the *top* face. The body diagonal would be
            # the obvious test, but its far corner is behind the box and OCCT
            # only detects what is actually visible -- as it should.
            top = [s for s in corners if abs(s.position[2] - HEIGHT) < 1e-6]
            low = min(top, key=lambda s: (s.position[0], s.position[1]))
            high = max(top, key=lambda s: (s.position[0], s.position[1]))
            REPORT["from"] = tuple(round(v, 2) for v in low.position)
            REPORT["to"] = tuple(round(v, 2) for v in high.position)

            # The centre of the body on screen. Offsets aim toward it, so a
            # deliberately-inaccurate click still lands on the solid rather than
            # off its silhouette into empty space -- which is what a real user
            # missing a corner does too.
            from simplecad.kernel.occ import bounding_box

            low_c, high_c = bounding_box(shape)
            middle = viewport.project(
                tuple((low_c[i] + high_c[i]) / 2.0 for i in range(3))
            )

            def click_near(snap, gap=6.0):
                """Aim *gap* pixels off the corner -- snapping must close it."""
                screen = viewport.project(snap.position)
                dx, dy = middle[0] - screen[0], middle[1] - screen[1]
                length = math.hypot(dx, dy) or 1.0
                at = QPoint(
                    int(screen[0] + dx / length * gap),
                    int(screen[1] + dy / length * gap),
                )
                QTest.mouseMove(viewport, at)
                # A delay, or Qt turns two quick clicks into a double-click.
                QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, at, 60)
                return at

            click_near(low)
            REPORT["after_first"] = len(panel.points)
            REPORT["first_snapped_to"] = panel.points[0].kind if panel.points else None
            click_near(high)
            REPORT["after_second"] = len(panel.points)

            expected = math.sqrt(WIDTH**2 + DEPTH**2)
            REPORT["expected_mm"] = round(expected, 4)
            headline = panel.measurement.headline
            REPORT["measured_mm"] = round(headline.value, 4) if headline else None
            REPORT["overlay_reading"] = window.stage.measure_overlay.reading
            REPORT["overlay_visible"] = window.stage.measure_overlay.isVisible()

            # Switching back must put entity mode -- and picking -- back too.
            panel.set_mode("entities")
            REPORT["left_point_mode"] = not viewport.picking_points
            REPORT["overlay_hidden"] = not window.stage.measure_overlay.isVisible()
            panel.set_mode("points")
            click_near(low)
            click_near(high)
            window.set_hint("Measured the top face diagonal, corner to corner")

        window.stage.viewport.ready.connect(populate)
        return window

    run_and_capture(build, "docs/shots/measure_points.png",
                    settle_ms=3600, size=(1200, 820))
    print("--- point to point measure ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    measured = REPORT.get("measured_mm")
    ok = (
        REPORT.get("panel_opened")
        and REPORT.get("point_mode")
        and REPORT.get("vertex_snaps") == 8          # a box has eight corners
        and REPORT.get("after_first") == 1
        and REPORT.get("first_snapped_to") == "vertex"   # snapped, not landed
        and REPORT.get("after_second") == 2
        and measured is not None
        and abs(measured - REPORT["expected_mm"]) < 1e-3
        and REPORT.get("overlay_visible")
        and REPORT.get("overlay_reading")
        and REPORT.get("left_point_mode")
        and REPORT.get("overlay_hidden")
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
