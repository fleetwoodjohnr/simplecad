#!/usr/bin/env python
"""How long one gizmo drag step takes, with other bodies around to snap to.

A real window under Xvfb; 40 boxes; Move on one of them; the X arrow grabbed
and dragged through 120 steps. Reports the median and 95th-percentile time of
``gizmo.drag`` (solve + snap filter + preview transform; the repaint itself is
scheduled, not timed) and of the press that starts the drag.

    xvfb-run -a .venv/bin/python scripts/benchmark_gizmo.py
"""

from __future__ import annotations

import statistics
import sys
import time

from _harness import run_and_capture  # noqa: E402

RESULT: dict[str, object] = {}


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def build(_app):
        window = MainWindow(Mode.DARK)

        def run() -> None:
            try:
                scenario(window)
            finally:
                QApplication.quit()

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(500, run))
        return window

    def scenario(window) -> None:
        from OCP.AIS import AIS_MM_Translation

        from simplecad.kernel.primitives import BoxFeature
        from simplecad.ui.selection import Picked

        for index in range(40):
            window.add_feature(BoxFeature(inputs={
                "width": 10, "depth": 10, "height": 10,
                "x": (index % 8) * 16.0, "y": (index // 8) * 16.0,
            }, outputs=[f"B{index}"]))
        window.wait_for_rebuild()
        viewport = window.stage.viewport
        viewport.fit_all()
        QApplication.processEvents()
        shape = window.document.body("B0").shape
        window.selection.picks = [Picked("B0", "body", shape, window._presentations["B0"])]
        window.activate_tool("move")
        QApplication.processEvents()
        gizmo = viewport.gizmo
        centre = viewport.project(gizmo._pivot)
        screen, _ = viewport.screen_axis_for(gizmo._pivot, (1.0, 0.0, 0.0))
        manipulator = gizmo._manipulator
        handle = None
        for reach in range(24, 170, 2):
            x = round((centre[0] + screen[0] * reach) * viewport.devicePixelRatioF())
            y = round((centre[1] + screen[1] * reach) * viewport.devicePixelRatioF())
            viewport.context.MoveTo(x, y, viewport.view, False)
            if (viewport.context.HasDetected() and viewport.context.DetectedInteractive()
                    is manipulator and int(manipulator.ActiveMode()) == int(AIS_MM_Translation)
                    and int(manipulator.ActiveAxisIndex()) == 0):
                handle = (x, y)
                handle_reach = reach
        if handle is None:
            RESULT["error"] = "X arrow not found"
            return
        presses, drags = [], []
        for _round in range(3):
            start = time.perf_counter()
            assert gizmo.press(*handle)
            presses.append((time.perf_counter() - start) * 1000.0)
            for step in range(1, 41):
                start = time.perf_counter()
                gizmo.drag(round(handle[0] + screen[0] * step * 2),
                           round(handle[1] + screen[1] * step * 2))
                drags.append((time.perf_counter() - start) * 1000.0)
            gizmo.release()
        # Letting go commits a Move; the gizmo detaches and comes back once
        # the rebuild lands. How long is the user without a handle to grab?
        gaps = []
        for _round in range(5):
            assert gizmo.press(*handle)
            gizmo.drag(round(handle[0] + screen[0] * 30), round(handle[1] + screen[1] * 30))
            components = gizmo.release()
            start = time.perf_counter()
            gizmo.committed.emit(*components)
            while time.perf_counter() - start < 5.0:
                QApplication.processEvents()
                current = viewport.gizmo
                if current is not None and current is not gizmo and current.active:
                    break
            gaps.append((time.perf_counter() - start) * 1000.0)
            gizmo = viewport.gizmo
            centre = viewport.project(gizmo._pivot)
            handle = (round((centre[0] + screen[0] * (handle_reach)) * viewport.devicePixelRatioF()),
                      round((centre[1] + screen[1] * (handle_reach)) * viewport.devicePixelRatioF()))
        RESULT["reattach_median_ms"] = round(statistics.median(gaps), 1)
        RESULT["reattach_max_ms"] = round(max(gaps), 1)
        drags.sort()
        RESULT["press_ms"] = round(statistics.median(presses), 2)
        RESULT["drag_median_ms"] = round(statistics.median(drags), 3)
        RESULT["drag_p95_ms"] = round(drags[int(len(drags) * 0.95)], 3)
        RESULT["drags"] = len(drags)

    run_and_capture(build, "/tmp/simplecad-benchmark-gizmo.png", settle_ms=600_000)
    print("--- gizmo drag cost ---")
    for key, value in RESULT.items():
        print(f"  {key}: {value}")
    return 0 if "error" not in RESULT else 1


if __name__ == "__main__":
    sys.exit(main())
