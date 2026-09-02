#!/usr/bin/env python
"""Box selection: drag a rectangle, get everything under it.

Multi-select by Shift-clicking each part in turn already worked. What was
missing was the gesture everything else in the world uses -- drag a box over
several things and they are all picked. This drives a real press-move-release
over two bodies and checks three things: that both are selected, that they came
back as *bodies* rather than a spray of faces (which is what decides whether the
contextual bar offers Group, Move and Subtract at all), and that an ordinary
click still selects exactly one thing.
"""

from __future__ import annotations

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

    def build(_app):
        window = MainWindow(Mode.DARK)

        def populate() -> None:
            from simplecad.core.document import BodyRef
            from simplecad.kernel.operations import MoveFeature
            from simplecad.kernel.primitives import BoxFeature

            for name in ("Left", "Right"):
                window.add_feature(
                    BoxFeature(
                        inputs={"width": 20, "depth": 20, "height": 20},
                        outputs=[name],
                    )
                )
            window.document.add_feature(
                MoveFeature(
                    inputs={"body": BodyRef("Right"), "dx": 45},
                    outputs=["Right"],
                )
            )
            window.rebuild()
            window.wait_for_rebuild()
            window.stage.viewport.fit_all()
            QTimer.singleShot(600, run)

        def drag(start: QPoint, end: QPoint, modifier=Qt.NoModifier) -> None:
            """A real press, several moves, and a release."""
            viewport = window.stage.viewport
            QTest.mousePress(viewport, Qt.LeftButton, modifier, start)
            steps = 6
            for step in range(1, steps + 1):
                QTest.mouseMove(
                    viewport,
                    QPoint(
                        start.x() + (end.x() - start.x()) * step // steps,
                        start.y() + (end.y() - start.y()) * step // steps,
                    ),
                )
            REPORT.setdefault("band_drawn", window.stage.selection_band.isVisible())
            QTest.mouseRelease(viewport, Qt.LeftButton, modifier, end)

        def run() -> None:
            from simplecad.ui.selection import available_actions

            from simplecad.kernel.occ import bounding_box

            viewport = window.stage.viewport
            # All eight corners, not just the bounding box's two: the screen
            # extent of a rotated box is set by corners neither of those is, and
            # a rectangle drawn from two of them does not enclose the part.
            corners = []
            body_corners = {}
            for name in ("Left", "Right"):
                projected = []
                low, high = bounding_box(window.document.bodies[name].shape)
                for x in (low[0], high[0]):
                    for y in (low[1], high[1]):
                        for z in (low[2], high[2]):
                            at = viewport.project((x, y, z))
                            if at is not None:
                                corners.append(at)
                                projected.append(at)
                body_corners[name] = projected
            xs = [c[0] for c in corners]
            ys = [c[1] for c in corners]
            margin = 30
            start = QPoint(int(min(xs)) - margin, int(min(ys)) - margin)
            end = QPoint(int(max(xs)) + margin, int(max(ys)) + margin)

            drag(start, end)
            REPORT["after_box"] = sorted(window.selection.bodies)
            REPORT["kinds"] = sorted({p.kind for p in window.selection.picks})
            REPORT["only_bodies"] = window.selection.only_bodies
            REPORT["actions"] = [key for key, _l, _i in available_actions(window.selection)]
            REPORT["band_cleared"] = not window.stage.selection_band.isVisible()

            # The original duplicate-owner bug was easiest to see with just
            # one enclosed body: BODY and SOLID each found it, then the second
            # additive toggle removed what the first had selected.
            viewport.clear_selection()
            left = body_corners["Left"]
            left_x = [point[0] for point in left]
            left_y = [point[1] for point in left]
            drag(
                QPoint(int(min(left_x)) - margin, int(min(left_y)) - margin),
                QPoint(int(max(left_x)) + margin, int(max(left_y)) + margin),
            )
            REPORT["after_single_box"] = sorted(window.selection.bodies)

            # A crossing drag -- right to left -- catches what it merely
            # touches. A thin band straight across both parts at mid height
            # encloses neither of them, so only a crossing box can find them.
            window.stage.viewport.clear_selection()
            middle = (start.y() + end.y()) // 2
            drag(QPoint(end.x(), middle), QPoint(start.x(), middle + 24))
            REPORT["after_crossing"] = sorted(window.selection.bodies)

            # And the same band dragged the other way encloses nothing, so it
            # finds nothing -- which is what makes the two directions mean
            # different things rather than being the same gesture twice.
            window.stage.viewport.clear_selection()
            drag(QPoint(start.x(), middle), QPoint(end.x(), middle + 24))
            REPORT["after_enclosing_band"] = sorted(window.selection.bodies)

            # A plain click still means one thing, not "everything I dragged past".
            low, high = bounding_box(window.document.bodies["Left"].shape)
            centre = tuple((low[i] + high[i]) / 2.0 for i in range(3))
            at = viewport.project((centre[0], centre[1], high[2] - 0.01))
            QTest.mouseClick(
                viewport, Qt.LeftButton, Qt.NoModifier,
                QPoint(int(at[0]), int(at[1])), 60,
            )
            REPORT["after_click"] = sorted(window.selection.bodies)

            # Shift-dragging a box adds to what is already selected.
            drag(start, end, Qt.ShiftModifier)
            REPORT["after_shift_box"] = sorted(window.selection.bodies)
            window.set_hint("Dragged a box over both parts")

        window.stage.viewport.ready.connect(populate)
        return window

    run_and_capture(build, "docs/shots/box_select.png",
                    settle_ms=3600, size=(1200, 820))
    print("--- box selection ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    ok = (
        REPORT.get("after_box") == ["Left", "Right"]
        and REPORT.get("after_single_box") == ["Left"]
        and REPORT.get("only_bodies") is True
        and REPORT.get("kinds") in (["solid"], ["body"], ["compound"])
        and "group" in (REPORT.get("actions") or [])
        and REPORT.get("band_drawn") is True
        and REPORT.get("band_cleared") is True
        and REPORT.get("after_crossing") == ["Left", "Right"]
        and REPORT.get("after_enclosing_band") == []
        and REPORT.get("after_click") == ["Left"]
        and REPORT.get("after_shift_box") == ["Left", "Right"]
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
