#!/usr/bin/env python
"""Hovering a shape must not cost more than the frame it is drawn in.

The report was "when I added a shape I could not do anything with it, the
application would freeze". It did: with a body under the cursor and the Shape
panel still open -- which is where Create leaves you -- every mouse move ran
``set_hint`` -> ``_layout_overlays`` -> ``raise_()`` on every floating panel,
re-compositing the GL viewport and re-blurring a 36 px drop shadow per panel.
Measured at 69.7 ms a move against a 17 ms frame, and worse the more panels
were open, so the loop never drained and clicks queued behind the pointer.

This puts the numbers back under a frame and, just as important, checks that
the cheaper path did not quietly break the stacking those raises existed for.

Run:  .venv/bin/python scripts/check_hover_perf.py
"""

from __future__ import annotations

import sys
import time

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}

#: A mouse move may cost a repaint plus a little. Measured against the machine's
#: own repaint floor rather than a fixed number of milliseconds: that floor is
#: one display frame and it moves -- 17 ms at 60 Hz, 27 ms when the compositor
#: is throttling -- so a hard budget silently becomes a test of the refresh
#: rate. The bug this guards against was hovering at four times the floor.
FLOOR_MULTIPLE = 1.6
#: Slack for the measurement itself, on top of the multiple.
FLOOR_SLACK_MS = 4.0
#: And an absolute backstop, so "the floor was also terrible" cannot pass.
CEILING_MS = 60.0
#: A layout pass is moves and text metrics. It has no business being visible,
#: whatever the display is doing.
LAYOUT_BUDGET_MS = 5.0
#: The very first move over a freshly displayed body is dearer, and always was:
#: OCCT builds that object's selection structures on the first ``MoveTo`` and
#: they are cached from then on. Measured at ~96 ms here. Budgeted separately
#: rather than folded into the worst case, because it is paid once per body and
#: not per motion event -- the thing that made the window unusable was the
#: steady state, not this.
FIRST_HOVER_BUDGET_MS = 200.0


def main() -> int:
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def hover(widget, position: QPoint) -> None:
        """Move the cursor over *widget*.

        Not ``QTest.mouseMove``: that warps the real pointer, which XWayland
        declines to do, so the widget never sees the move at all.
        """
        QApplication.sendEvent(widget, QMouseEvent(
            QEvent.MouseMove, QPointF(position),
            widget.mapToGlobal(QPointF(position)),
            Qt.NoButton, Qt.NoButton, Qt.NoModifier,
        ))

    def build(_app):
        window = MainWindow(Mode.DARK)
        failures: list[str] = []

        def populate() -> None:
            # Through the real tool, because the state that matters is the one
            # Create leaves behind: the Shape panel is still open.
            window.activate_tool("shape_box")
            panel = next(
                (w for w, _a in window.stage.overlays
                 if type(w).__name__ == "ShapePanel"), None
            )
            if panel is None:
                failures.append("the Shape panel did not open")
                finish()
                return
            panel.commit()
            QTimer.singleShot(3000, probe)

        def probe() -> None:
            stage = window.stage
            viewport = stage.viewport
            viewport.fit_all()
            QApplication.processEvents()

            open_panels = [type(w).__name__ for w, _a in stage.overlays]
            REPORT["overlays"] = open_panels
            if "ShapePanel" not in open_panels:
                failures.append(
                    "the Shape panel closed itself; this measures the wrong state"
                )

            ratio = viewport.devicePixelRatioF()

            def to_screen(point):
                x, y = viewport.view.Convert(point[0], point[1], point[2])
                return QPoint(int(x / ratio), int(y / ratio))

            # The floor: what a repaint costs with no mouse involved at all.
            # Everything below is judged against this.
            floor_times = []
            for _ in range(12):
                started = time.perf_counter()
                viewport.update()
                QApplication.processEvents()
                floor_times.append((time.perf_counter() - started) * 1000.0)
            floor = sorted(floor_times)[len(floor_times) // 2]
            REPORT["repaint_floor_ms"] = round(floor, 1)
            budget = floor * FLOOR_MULTIPLE + FLOOR_SLACK_MS
            REPORT["hover_budget_ms"] = round(budget, 1)

            # The first touch of a new body, on its own: OCCT's one-off.
            started = time.perf_counter()
            hover(viewport, to_screen((20.0, 15.0, 20.0)))
            QApplication.processEvents()
            first = (time.perf_counter() - started) * 1000.0
            REPORT["first_hover_ms"] = round(first, 1)
            if first > FIRST_HOVER_BUDGET_MS:
                failures.append(
                    f"the first move over a new body costs {first:.1f} ms, over "
                    f"the {FIRST_HOVER_BUDGET_MS:.0f} ms budget"
                )

            # Then the steady state, which is what a moving cursor actually pays.
            timings: list[float] = []
            for index in range(16):
                spot = to_screen((20.0 + (index % 7) - 3, 15.0, 20.0))
                started = time.perf_counter()
                hover(viewport, spot)
                QApplication.processEvents()
                timings.append((time.perf_counter() - started) * 1000.0)
            median = sorted(timings)[len(timings) // 2]
            worst = max(timings)
            REPORT["every_move_ms"] = [round(v, 1) for v in timings]
            REPORT["hover_median_ms"] = round(median, 1)
            REPORT["hover_worst_ms"] = round(worst, 1)
            if median > budget:
                failures.append(
                    f"a mouse move costs {median:.1f} ms against a "
                    f"{floor:.1f} ms repaint floor, over the "
                    f"{budget:.1f} ms budget"
                )
            if median > CEILING_MS:
                failures.append(
                    f"a mouse move costs {median:.1f} ms, over the "
                    f"{CEILING_MS:.0f} ms backstop whatever the floor is"
                )
            if worst > budget * 2:
                failures.append(
                    f"the worst mouse move costs {worst:.1f} ms, more than "
                    f"twice the {budget:.1f} ms budget"
                )

            # The layout pass on its own, with the panel open.
            passes: list[float] = []
            for _ in range(8):
                started = time.perf_counter()
                stage._layout_overlays()
                QApplication.processEvents()
                passes.append((time.perf_counter() - started) * 1000.0)
            layout = sorted(passes)[len(passes) // 2]
            REPORT["layout_ms"] = round(layout, 1)
            if layout > LAYOUT_BUDGET_MS:
                failures.append(
                    f"a layout pass costs {layout:.1f} ms with "
                    f"{len(stage.overlays)} panels open"
                )

            # The repeat guard: saying the same thing twice must not re-lay out.
            counted = {"n": 0}
            real_layout = stage._layout_overlays

            def counting() -> None:
                counted["n"] += 1
                real_layout()

            stage._layout_overlays = counting
            window.set_hint("A hint that is definitely new.")
            window.set_hint("A hint that is definitely new.")
            window.set_hint("A different one.")
            stage._layout_overlays = real_layout
            REPORT["layouts_for_3_hints_2_distinct"] = counted["n"]
            if counted["n"] != 2:
                failures.append(
                    f"three hints with two distinct strings ran {counted['n']} "
                    "layout passes, expected 2"
                )

            # And the stacking the raises existed for is still right: the
            # marquee remains above the panels. Geometry highlighting is now
            # native OCCT shading and is deliberately not a Qt overlay.
            band = stage.selection_band
            band.show_rect(viewport.rect().adjusted(20, 20, -20, -20))
            stage._layout_overlays()
            QApplication.processEvents()
            children = stage.children()
            order = {id(child): index for index, child in enumerate(children)}
            panels = [order.get(id(w), -1) for w, _a in stage.overlays]
            REPORT["stacking_ok"] = True
            for name, widget in (("marquee", stage.selection_band),):
                # ``isVisible`` includes every ancestor; the harness may begin
                # closing the top-level window during this final probe. What
                # matters here is that the overlay itself was shown and placed.
                if widget.isHidden():
                    failures.append(f"the {name} was not visible to check stacking")
                    continue
                if panels and order.get(id(widget), -1) < max(panels):
                    REPORT["stacking_ok"] = False
                    failures.append(f"the {name} sits below a floating panel")
            band.show_rect(None)
            finish()

        def finish() -> None:
            REPORT["failures"] = failures
            window.set_hint(
                "Hovering costs "
                f"{REPORT.get('hover_median_ms', '?')} ms a move"
            )

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(600, populate))
        return window

    result = run_and_capture(
        build, "docs/shots/hover_perf.png", settle_ms=9000, size=(1280, 820)
    )
    REPORT["shot"] = result

    for key, value in REPORT.items():
        print(f"{key}: {value}")
    failures = REPORT.get("failures")
    ok = failures == [] and "error" not in result
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
