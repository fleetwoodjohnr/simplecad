#!/usr/bin/env python
"""The transform gizmo.

Attaches to a selected body, drags an axis handle, and checks the body actually
moved by the dragged amount -- and that the move landed in the feature tree as a
parametric Move, not a one-off nudge.
"""

from __future__ import annotations

import sys

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}


def _find_handle(viewport, centre, ratio, want_axis: int):
    """Locate a translation arrow on screen by asking OCCT what is under it."""
    from PySide6.QtCore import QPoint
    from OCP.AIS import AIS_MM_Translation

    context = viewport.context
    manipulator = viewport.gizmo._manipulator
    cx, cy = viewport.view.Convert(*centre)
    best = None
    for dx in range(-220, 221, 6):
        for dy in range(-220, 221, 6):
            context.MoveTo(int(cx + dx), int(cy + dy), viewport.view, False)
            if (
                manipulator.HasActiveMode()
                and manipulator.ActiveMode() == AIS_MM_Translation
                and manipulator.ActiveAxisIndex() == want_axis
            ):
                distance = dx * dx + dy * dy
                if best is None or distance > best[0]:
                    best = (distance, dx, dy)   # the far end of the arrow
    if best is None:
        return None
    _d, dx, dy = best
    return QPoint(round((cx + dx) / ratio), round((cy + dy) / ratio))


def main() -> int:
    from PySide6.QtCore import QPoint, Qt, QTimer
    from PySide6.QtTest import QTest

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def build(_app):
        window = MainWindow(Mode.DARK)

        def run() -> None:
            from simplecad.kernel.occ import bounding_box
            from simplecad.kernel.primitives import BoxFeature
            from simplecad.ui.selection import Picked

            window.add_feature(
                BoxFeature(inputs={"width": 30, "depth": 30, "height": 30},
                           outputs=["Block"])
            )
            window.wait_for_rebuild()
            viewport = window.stage.viewport
            viewport.fit_all()

            # Select the body, then open Move -- which attaches the gizmo.
            shape = window.document.bodies["Block"].shape
            window.selection.picks = [
                Picked(body="Block", kind="body", shape=shape,
                       presentation=window._presentations.get("Block"))
            ]
            window.activate_tool("move")
            gizmo = viewport.gizmo
            REPORT["gizmo_attached"] = gizmo is not None and gizmo.active

            before = bounding_box(shape)[0]

            # Find the +X arrow tip on screen and drag it.
            ratio = viewport.devicePixelRatioF()
            centre = [(a + b) / 2 for a, b in zip(*bounding_box(shape))]
            axis, mm_per_pixel = viewport.screen_axis_for(centre, (1.0, 0.0, 0.0))
            REPORT["mm_per_pixel"] = round(mm_per_pixel, 4)

            # Find the X translation arrow rather than guessing where it is:
            # the manipulator sizes and orients itself, and in an isometric view
            # +X on screen is diagonal.
            handle = _find_handle(viewport, centre, ratio, want_axis=0)
            REPORT["handle_found"] = handle is not None
            if handle is None:
                return
            QTest.mouseMove(viewport, handle)
            QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, handle)
            REPORT["drag_started"] = viewport._nav.name == "GIZMO"

            target_mm = 25.0
            pixels = target_mm / mm_per_pixel / ratio
            end = QPoint(round(handle.x() + axis[0] * pixels),
                         round(handle.y() + axis[1] * pixels))
            QTest.mouseMove(viewport, end)
            QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, end)
            window.wait_for_rebuild()

            after = bounding_box(window.document.bodies["Block"].shape)[0]
            REPORT["moved_x"] = round(after[0] - before[0], 2)
            REPORT["moved_yz"] = (round(after[1] - before[1], 2),
                                  round(after[2] - before[2], 2))
            REPORT["features"] = [f.type_name for f in window.document.features]
            REPORT["move_inputs"] = {
                key: window.document.features[-1].inputs.get(key)
                for key in ("dx", "dy", "dz")
            }
            REPORT["undoable"] = window.history.can_undo
            REPORT["gizmo_released"] = (
                viewport.gizmo is None or not viewport.gizmo.dragging
            )
            viewport.fit_all()
            window.set_hint("Dragged the gizmo 25 mm along X")

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(500, run))
        return window

    run_and_capture(build, "docs/shots/gizmo.png", settle_ms=5000, size=(1300, 860))
    print("--- transform gizmo ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    moved = REPORT.get("moved_x", 0)
    ok = (
        REPORT.get("gizmo_attached")
        and REPORT.get("drag_started")
        and abs(moved - 25.0) < 2.0
        and REPORT.get("moved_yz") == (0.0, 0.0)
        and "move" in (REPORT.get("features") or [])
        and REPORT.get("undoable")
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
