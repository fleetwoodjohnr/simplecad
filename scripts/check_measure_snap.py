#!/usr/bin/env python
"""Point-to-point measuring: the snap indicator, and whether it tells the truth.

``check_measure_points.py`` covers corner-to-corner. This covers the two things
that were actually broken.

**The indicator used to disappear.** Away from a corner, a midpoint or a centre
there was no snap at all, so the cursor showed nothing and clicking did nothing
-- which reads as a dead tool rather than as "no snap here". There must now
always be a point under the cursor while it is over the model.

**The pick used to disagree with the indicator.** Hover and click computed the
snap independently, from different cursor positions and separate detection
passes, so the point you got was not always the point you were shown. The
clicked point must be exactly the hovered one.
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
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    WIDTH, DEPTH, HEIGHT = 60.0, 50.0, 20.0

    def build(_app):
        window = MainWindow(Mode.DARK)

        def run() -> None:
            from simplecad.kernel.primitives import BoxFeature
            from simplecad.kernel.snapping import snap_points

            window.add_feature(
                BoxFeature(
                    inputs={"width": WIDTH, "depth": DEPTH, "height": HEIGHT},
                    outputs=["Block"],
                )
            )
            window.wait_for_rebuild()
            viewport = window.stage.viewport
            viewport.set_standard_view(
                __import__(
                    "simplecad.ui.viewport.occt_view", fromlist=["StandardView"]
                ).StandardView.TOP,
                animate=False,
            )
            viewport.fit_all()
            QApplication.processEvents()

            window.activate_tool("measure")
            panel = next(
                (w for w, _a in window.stage.overlays
                 if type(w).__name__ == "MeasurePanel"), None
            )
            if panel is None:
                FAILURES.append("the measure panel did not open")
                return
            panel.set_mode("points")
            overlay = window.stage.measure_overlay
            QApplication.processEvents()

            model_centre = (WIDTH / 2, DEPTH / 2, HEIGHT)

            def at(point):
                screen = viewport.project(point)
                return QPoint(round(screen[0]), round(screen[1]))

            def hover(point):
                """Move the cursor over *point* and report what it snapped to.

                Sent straight to the widget rather than through
                ``QTest.mouseMove``, which warps the real pointer and leaves it
                to the X server to deliver a motion event -- which it does not
                do reliably for a window that is not under the physical cursor.
                A real user's mouse move arrives here identically.
                """
                where = at(point)
                local = QPointF(where)
                QApplication.sendEvent(viewport, QMouseEvent(
                    QEvent.MouseMove, local,
                    QPointF(viewport.mapToGlobal(where)),
                    Qt.NoButton, Qt.NoButton, Qt.NoModifier,
                ))
                QApplication.processEvents()
                # Snapping is paced so it can never monopolise the event loop,
                # so the answer lands a fraction of a frame after the move. A
                # hand never notices; a script that looks immediately would read
                # the previous position's answer, so ask for it now.
                viewport.flush_snap()
                return overlay.hover

            def hover_near(point, gap):
                """Hover *gap* **pixels** short of *point*, from inside the body.

                Pixels, not millimetres: the snap radius is a screen distance by
                design, so a snap does not get harder to hit as you zoom out, and
                a test that missed by a fixed number of millimetres would be
                testing the zoom level instead.

                Aimed inward, toward the middle of the part, because that is
                where a real near-miss lands. Missing outward goes off the
                silhouette into empty space, where there is correctly nothing to
                snap to at all.
                """
                where = at(point)
                inward = at(model_centre)
                dx, dy = inward.x() - where.x(), inward.y() - where.y()
                length = math.hypot(dx, dy) or 1.0
                target = QPoint(
                    round(where.x() + dx / length * gap),
                    round(where.y() + dy / length * gap),
                )
                QApplication.sendEvent(viewport, QMouseEvent(
                    QEvent.MouseMove, QPointF(target),
                    QPointF(viewport.mapToGlobal(target)),
                    Qt.NoButton, Qt.NoButton, Qt.NoModifier,
                ))
                QApplication.processEvents()
                viewport.flush_snap()
                return overlay.hover

            # -- the middle of a face, far from any named snap --------------
            middle = (WIDTH / 2, DEPTH / 2, HEIGHT)
            # deliberately off-centre, so it is not the face centre either
            off_centre = (WIDTH * 0.72, DEPTH * 0.31, HEIGHT)
            snap = hover(off_centre)
            REPORT["mid_face_snap"] = snap.kind if snap else None
            REPORT["mid_face_label"] = snap.label if snap else None
            if snap is None:
                FAILURES.append(
                    "no indicator in the middle of a face — the cursor shows "
                    "nothing and a click does nothing"
                )
            else:
                REPORT["mid_face_position"] = tuple(round(v, 2) for v in snap.position)
                if abs(snap.position[2] - HEIGHT) > 1e-3:
                    FAILURES.append("the mid-face snap is not on the face")

            # -- and a click there actually places a point ------------------
            QTest.mouseClick(
                viewport, Qt.LeftButton, Qt.NoModifier, at(off_centre), 60
            )
            QApplication.processEvents()
            REPORT["points_after_mid_face_click"] = len(panel.points)
            if len(panel.points) != 1:
                FAILURES.append("clicking in the middle of a face did nothing")

            # -- what was picked is exactly what was shown ------------------
            if panel.points:
                shown = REPORT.get("mid_face_position")
                picked = tuple(round(v, 2) for v in panel.points[0].position)
                REPORT["picked_position"] = picked
                REPORT["indicator_matches_pick"] = picked == shown
                if picked != shown:
                    FAILURES.append(
                        f"picked {picked} but the indicator was at {shown}"
                    )

            panel.clear_points()
            QApplication.processEvents()

            # -- named snaps still win where they should --------------------
            corner = (0.0, 0.0, HEIGHT)
            snap = hover_near(corner, 8)
            REPORT["near_corner_snap"] = snap.kind if snap else None
            if snap is None or snap.kind != "vertex":
                FAILURES.append("a corner no longer wins near a corner")
            elif any(abs(a - b) > 1e-6 for a, b in zip(snap.position, corner)):
                FAILURES.append("snapped to the wrong corner")

            # Far enough from the corner that the fallback should take over.
            snap = hover_near(corner, 85)
            REPORT["well_off_corner_snap"] = snap.kind if snap else None
            if snap is None:
                FAILURES.append("no indicator away from a named snap")
            elif snap.kind == "vertex":
                FAILURES.append("a corner won from 85 px away — the snap is greedy")

            edge_middle = (WIDTH / 2, 0.0, HEIGHT)
            snap = hover_near(edge_middle, 6)
            REPORT["edge_midpoint_snap"] = snap.kind if snap else None
            if snap is None or snap.kind != "midpoint":
                FAILURES.append("an edge midpoint no longer wins at a midpoint")

            along_edge = (WIDTH * 0.22, 0.0, HEIGHT)
            snap = hover(along_edge)
            REPORT["along_edge_snap"] = snap.kind if snap else None
            REPORT["along_edge_on_the_edge"] = (
                snap is not None and abs(snap.position[1]) < 0.5
            )
            if snap is None:
                FAILURES.append("no indicator part-way along an edge")
            elif not REPORT["along_edge_on_the_edge"]:
                FAILURES.append(
                    f"the part-way-along-an-edge snap left the edge: "
                    f"{tuple(round(v, 2) for v in snap.position)}"
                )

            snap = hover(middle)
            REPORT["face_centre_snap"] = snap.kind if snap else None
            if snap is None or snap.kind != "face":
                FAILURES.append("the face centre no longer wins at the centre")

            # -- a full mid-face to mid-face measurement --------------------
            panel.clear_points()
            first = (WIDTH * 0.2, DEPTH * 0.5, HEIGHT)
            second = (WIDTH * 0.8, DEPTH * 0.5, HEIGHT)
            for point in (first, second):
                hover(point)
                QTest.mouseClick(
                    viewport, Qt.LeftButton, Qt.NoModifier, at(point), 60
                )
                QApplication.processEvents()
            REPORT["points"] = len(panel.points)
            headline = panel.measurement.headline
            REPORT["measured"] = round(headline.value, 3) if headline else None
            expected = math.dist(
                panel.points[0].position, panel.points[1].position
            ) if len(panel.points) == 2 else None
            REPORT["expected"] = round(expected, 3) if expected else None
            REPORT["overlay_reading"] = overlay.reading
            if len(panel.points) != 2:
                FAILURES.append("two mid-face clicks did not make a measurement")
            elif abs(headline.value - expected) > 1e-6:
                FAILURES.append("the reported distance is not between the points")
            if not overlay.reading:
                FAILURES.append("the distance is not drawn on the model")

            # -- the first point is drawn differently from the second -------
            REPORT["first_point_kind"] = panel.points[0].kind if panel.points else None

            window.set_hint("Snapped and measured across a face")

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(700, run))
        return window

    run_and_capture(build, "docs/shots/measure_snap.png", settle_ms=5200,
                    size=(1240, 820))
    print("--- measure snapping ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    for failure in FAILURES:
        print(f"  FAIL: {failure}")
    ok = bool(REPORT.get("mid_face_snap")) and not FAILURES
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
