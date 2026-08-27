#!/usr/bin/env python
"""Camera navigation: does the view behave, and behave predictably?

Drives real middle-button orbits and real wheel events over a model and checks
the four things that separate a CAD camera from a free-flying one: it turns
about what you have selected, the horizon never tilts, the view never flips
through a pole however hard you push it at one, and a trackpad's burst of small
scroll events zooms smoothly instead of bolting.
"""

from __future__ import annotations

import math
import os
import sys

os.environ["SIMPLECAD_NO_GRID"] = "1"

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}
FAILURES: list[str] = []


def main() -> int:
    from PySide6.QtCore import QPoint, QPointF, Qt, QTimer
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def build(_app):
        window = MainWindow(Mode.DARK)

        def run() -> None:
            from simplecad.core.document import BodyRef
            from simplecad.kernel.occ import bounding_box
            from simplecad.kernel.operations import MoveFeature
            from simplecad.kernel.primitives import BoxFeature
            from simplecad.ui.viewport.camera import (
                constrained_up, pitch_of, read_state, _dot, _length, _normalise,
                _sub,
            )

            for name, x in (("Near", 0.0), ("Far", 150.0)):
                feature = BoxFeature(
                    inputs={"width": 30, "depth": 30, "height": 30},
                    outputs=[name],
                )
                feature.name = name
                window.add_feature(feature)
                if x:
                    window.document.add_feature(
                        MoveFeature(
                            inputs={"body": BodyRef(name), "dx": x}, outputs=[name]
                        )
                    )
            window.rebuild()
            window.wait_for_rebuild()
            viewport = window.stage.viewport
            viewport.fit_all()
            QApplication.processEvents()

            def orbit(dx, dy, steps=12):
                """A real middle-button drag across the viewport."""
                start = QPoint(viewport.width() // 2, viewport.height() // 2)
                QTest.mouseMove(viewport, start)
                QTest.mousePress(viewport, Qt.MiddleButton, Qt.NoModifier, start)
                for step in range(1, steps + 1):
                    QTest.mouseMove(
                        viewport,
                        QPoint(round(start.x() + dx * step / steps),
                               round(start.y() + dy * step / steps)),
                    )
                end = QPoint(start.x() + dx, start.y() + dy)
                QTest.mouseRelease(viewport, Qt.MiddleButton, Qt.NoModifier, end)

            # -- the pivot is what is selected -----------------------------
            viewport.select_shape(window._presentations["Far"])
            window.selection.refresh()
            far = window.document.bodies["Far"].shape
            low, high = bounding_box(far)
            expected = tuple((low[i] + high[i]) / 2.0 for i in range(3))
            resolved = viewport.camera.resolve_pivot()
            REPORT["expected_pivot"] = tuple(round(v, 2) for v in expected)
            REPORT["resolved_pivot"] = tuple(round(v, 2) for v in resolved)
            if any(abs(a - b) > 1e-6 for a, b in zip(expected, resolved)):
                FAILURES.append("orbit pivot is not the selected body")

            # ...and the selected body stays put on screen while orbiting.
            before = viewport.project(expected)
            orbit(140, 40)
            QApplication.processEvents()
            after = viewport.project(expected)
            drift = math.hypot(after[0] - before[0], after[1] - before[1])
            REPORT["pivot_drift_px"] = round(drift, 2)
            if drift > 6.0:
                FAILURES.append(
                    f"the selection slid {drift:.1f} px across the screen "
                    "during an orbit"
                )

            # -- with nothing selected, the whole scene is the pivot -------
            viewport.clear_selection()
            window.selection.refresh()
            scene = viewport.scene_center()
            REPORT["scene_center"] = tuple(round(v, 2) for v in scene)
            if abs(scene[0] - 90.0) > 1.0:
                FAILURES.append(f"scene centre ignores the model: {scene}")

            # -- the horizon never tilts -----------------------------------
            worst_roll = 0.0
            for dx, dy in ((160, 0), (-90, 70), (200, -120), (-260, 40), (60, 180)):
                orbit(dx, dy)
                QApplication.processEvents()
                state = read_state(viewport.view)
                direction = _normalise(_sub(state[1], state[0]))
                ideal = constrained_up(direction)
                worst_roll = max(
                    worst_roll, max(abs(a - b) for a, b in zip(state[2], ideal))
                )
            REPORT["worst_roll"] = f"{worst_roll:.2e}"
            if worst_roll > 1e-6:
                FAILURES.append(f"the view rolled by {worst_roll:.3e}")

            # -- and never flips through a pole ----------------------------
            flipped = False
            worst_pitch = 0.0
            for _ in range(14):
                orbit(0, 220, steps=8)
                QApplication.processEvents()
                state = read_state(viewport.view)
                worst_pitch = max(
                    worst_pitch, abs(math.degrees(pitch_of(_sub(state[1], state[0]))))
                )
                if state[2][2] < 0:
                    flipped = True
            REPORT["max_pitch_deg"] = round(worst_pitch, 3)
            REPORT["flipped"] = flipped
            if flipped:
                FAILURES.append("the view inverted at a pole")
            if worst_pitch > 89.001:
                FAILURES.append(f"pitch reached {worst_pitch:.2f} deg")

            # -- a trackpad's burst of small events must not bolt ----------
            viewport.fit_all()
            QApplication.processEvents()
            at = QPointF(viewport.width() / 2, viewport.height() / 2)
            scale_before = read_state(viewport.view)[3]
            for _ in range(30):
                QApplication.sendEvent(viewport, QWheelEvent(
                    at, viewport.mapToGlobal(at), QPoint(0, 8), QPoint(0, 0),
                    Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False,
                ))
                QApplication.processEvents()
            # Let the easing settle.
            for _ in range(60):
                QTest.qWait(8)
                QApplication.processEvents()
            trackpad = scale_before / read_state(viewport.view)[3]
            REPORT["trackpad_30_events_zoom"] = round(trackpad, 3)
            if not 1.1 < trackpad < 8.0:
                FAILURES.append(
                    f"30 trackpad events zoomed by {trackpad:.2f}x — "
                    "either nothing happened or it bolted"
                )

            # One wheel notch must be a comfortable, noticeable step.
            viewport.fit_all()
            QApplication.processEvents()
            scale_before = read_state(viewport.view)[3]
            QApplication.sendEvent(viewport, QWheelEvent(
                at, viewport.mapToGlobal(at), QPoint(0, 0), QPoint(0, 120),
                Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False,
            ))
            for _ in range(60):
                QTest.qWait(8)
                QApplication.processEvents()
            notch = scale_before / read_state(viewport.view)[3]
            REPORT["one_wheel_notch_zoom"] = round(notch, 4)
            if not 1.05 < notch < 1.45:
                FAILURES.append(f"one wheel notch zoomed by {notch:.3f}x")

            # -- a standard view re-aims without re-framing ----------------
            from simplecad.ui.viewport.occt_view import StandardView

            before_state = read_state(viewport.view)
            viewport.set_standard_view(StandardView.TOP, animate=False)
            QApplication.processEvents()
            after_state = read_state(viewport.view)
            REPORT["scale_kept"] = (
                abs(after_state[3] - before_state[3]) < 1e-9
            )
            REPORT["centre_kept"] = all(
                abs(a - b) < 1e-9 for a, b in zip(after_state[1], before_state[1])
            )
            direction = _normalise(_sub(after_state[1], after_state[0]))
            REPORT["top_view_direction"] = tuple(round(v, 4) for v in direction)
            if not REPORT["scale_kept"] or not REPORT["centre_kept"]:
                FAILURES.append("a standard view re-framed as well as re-aimed")
            if abs(direction[2] + 1.0) > 1e-6:
                FAILURES.append(f"Top view is not looking down: {direction}")

            viewport.set_standard_view(StandardView.ISO, animate=False)
            viewport.fit_all()
            window.set_hint("Orbited, zoomed and re-aimed without rolling")

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(500, run))
        return window

    run_and_capture(build, "docs/shots/camera.png", settle_ms=9000, size=(1280, 840))
    print("--- camera navigation ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    for failure in FAILURES:
        print(f"  FAIL: {failure}")
    ok = bool(REPORT.get("resolved_pivot")) and not FAILURES
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
