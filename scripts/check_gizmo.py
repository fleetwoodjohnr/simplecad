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


def _find_handle(viewport, centre, want_axis: int):
    """Locate a translation arrow on screen by asking OCCT what is under it.

    Probed in widget pixels through the viewport's own conversion, exactly as a
    click is, and only where the viewport is not covered by a panel. The middle
    of the arrow, not its tip: at a fractional scale the outermost pixel rounds
    onto the neighbouring plane handle, and that drag is not axis-locked.
    """
    from PySide6.QtCore import QPoint
    from OCP.AIS import AIS_Manipulator, AIS_MM_Translation

    context = viewport.context
    manipulator = viewport.gizmo._manipulator
    cx, cy = viewport.project(centre)
    hits = []
    for dx in range(-160, 161, 3):
        for dy in range(-160, 161, 3):
            point = QPoint(round(cx + dx), round(cy + dy))
            # Covered by a card? Asked of the stage in its own coordinates:
            # global positions are unreliable without a window manager.
            stage = viewport.parentWidget()
            if stage is not None and stage.childAt(viewport.mapTo(stage, point)) is not viewport:
                continue
            context.MoveTo(*viewport._device_pos(point), viewport.view, False)
            # The manipulator keeps its last mode after the cursor leaves it.
            if (
                context.HasDetected()
                and isinstance(context.DetectedInteractive(), AIS_Manipulator)
                and manipulator.HasActiveMode()
                and manipulator.ActiveMode() == AIS_MM_Translation
                and manipulator.ActiveAxisIndex() == want_axis
            ):
                hits.append(point)
    if not hits:
        return None
    middle_x = sorted(p.x() for p in hits)[len(hits) // 2]
    middle_y = sorted(p.y() for p in hits)[len(hits) // 2]
    return min(hits, key=lambda p: (p.x() - middle_x) ** 2 + (p.y() - middle_y) ** 2)


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

            # Find the +X arrow on screen and drag it.
            ratio = viewport.devicePixelRatioF()
            centre = [(a + b) / 2 for a, b in zip(*bounding_box(shape))]
            axis, mm_per_pixel = viewport.screen_axis_for(centre, (1.0, 0.0, 0.0))
            REPORT["mm_per_pixel"] = round(mm_per_pixel, 4)

            # Find the X translation arrow rather than guessing where it is:
            # the manipulator sizes and orients itself, and in an isometric view
            # +X on screen is diagonal.
            handle = _find_handle(viewport, centre, want_axis=0)
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

            # The Y arrow used to be all but buried under OCCT's invisible
            # plane handles.
            low, high = bounding_box(window.document.bodies["Block"].shape)
            centre = [(a + b) / 2 for a, b in zip(low, high)]
            REPORT["y_arrow_found"] = _find_handle(viewport, centre, want_axis=1) is not None

            # Grabbing the part away from the arrows slides it over the ground.
            # A top corner: well clear of the arrows, which sit at the centre.
            corner = viewport.project((low[0] + 3, low[1] + 3, high[2]))
            grab = QPoint(round(corner[0]), round(corner[1]))
            axis, mm_per_pixel = viewport.screen_axis_for(centre, (0.0, 1.0, 0.0))
            pixels = 20.0 / mm_per_pixel / ratio
            QTest.mouseMove(viewport, grab)
            QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, grab)
            REPORT["part_drag_started"] = viewport._nav.name == "GIZMO"
            for step in range(1, 11):
                QTest.mouseMove(viewport, QPoint(round(grab.x() + axis[0] * pixels * step / 10),
                                                 round(grab.y() + axis[1] * pixels * step / 10)))
            end = QPoint(round(grab.x() + axis[0] * pixels), round(grab.y() + axis[1] * pixels))
            QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, end)
            window.wait_for_rebuild()
            moved = bounding_box(window.document.bodies["Block"].shape)[0]
            REPORT["part_drag_moved"] = tuple(round(moved[i] - low[i], 2) for i in range(3))
            # Where the cursor travelled over the ground plane through the
            # pivot -- what the part should have followed, whatever the camera.
            here, there = (viewport._ray_plane(viewport.cursor_ray(point), centre, (0, 0, 1))
                           for point in (grab, end))
            REPORT["part_drag_cursor"] = tuple(round(there[i] - here[i], 2) for i in range(3))
            REPORT["perspective"] = viewport.is_perspective()

            # v0.3: a plane square moves along its two axes only. Move stays
            # open and re-attaches once the last drag's rebuild has landed.
            def reattached():
                for _ in range(40):
                    if viewport.gizmo is not None and viewport.gizmo.active:
                        return True
                    QTest.qWait(100)
                return False

            reattached()
            gizmo = viewport.gizmo
            squares = dict(gizmo.plane_handles()) if gizmo is not None else {}
            REPORT["plane_squares"] = sorted(squares)
            normal = 1 if 1 in squares else (sorted(squares) or [None])[0]
            if normal is not None:
                corners = squares[normal]
                start = QPoint(round(sum(x for x, _ in corners) / 4 / ratio),
                               round(sum(y for _, y in corners) / 4 / ratio))
                before_plane = bounding_box(window.document.bodies["Block"].shape)[0]
                QTest.mouseMove(viewport, start)
                QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, start)
                REPORT["plane_constraint"] = viewport.gizmo.constraint if viewport.gizmo else None
                for step in range(1, 9):
                    QTest.mouseMove(viewport, QPoint(start.x() + 5 * step, start.y() + 3 * step))
                QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier,
                                   QPoint(start.x() + 40, start.y() + 24))
                window.wait_for_rebuild()
                after_plane = bounding_box(window.document.bodies["Block"].shape)[0]
                REPORT["plane_moved"] = tuple(round(after_plane[i] - before_plane[i], 3)
                                              for i in range(3))
                REPORT["plane_normal"] = normal

            # Esc mid-drag puts the part back and keeps the tool.
            reattached()
            low, high = bounding_box(window.document.bodies["Block"].shape)
            centre = [(a + b) / 2 for a, b in zip(low, high)]
            handle = _find_handle(viewport, centre, want_axis=0)
            features = len(window.document.features)
            if handle is not None:
                QTest.mouseMove(viewport, handle)
                QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, handle)
                for step in range(1, 7):
                    QTest.mouseMove(viewport, QPoint(handle.x() + 8 * step, handle.y()))
                REPORT["esc_dragging_before"] = bool(viewport.gizmo and viewport.gizmo.dragging)
                QTest.keyClick(viewport, Qt.Key_Escape)
                REPORT["esc_nav_after"] = viewport._nav.name
                QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier,
                                   QPoint(handle.x() + 48, handle.y()))
                window.wait_for_rebuild()
                REPORT["esc_detail"] = (len(window.document.features) - features, tuple(
                    round(a - b, 3) for a, b in zip(
                        bounding_box(window.document.bodies["Block"].shape)[0], low)))
                REPORT["esc_restored"] = (
                    bounding_box(window.document.bodies["Block"].shape)[0] == low
                    and len(window.document.features) == features
                )
                REPORT["esc_kept_tool"] = viewport.gizmo is not None and viewport.gizmo.active

            # Two bodies drag together, as one step.
            window.add_feature(BoxFeature(inputs={"width": 20, "depth": 20, "height": 20,
                                                  "x": 60}, outputs=["Second"]))
            window.wait_for_rebuild()
            window.cancel_tool()
            window.select_items(["Block", "Second"])
            viewport.fit_all()
            QTest.qWait(1200)               # the camera sweeps; let it land
            window.activate_tool("move")
            REPORT["two_bodies_gizmo"] = reattached()
            QTest.qWait(300)
            starts = {n: bounding_box(window.document.bodies[n].shape)[0]
                      for n in ("Block", "Second")}
            shapes = [window.document.bodies[n].shape for n in ("Block", "Second")]
            lows = [bounding_box(s)[0] for s in shapes]
            highs = [bounding_box(s)[1] for s in shapes]
            centre = [(min(lo[i] for lo in lows) + max(hi[i] for hi in highs)) / 2 for i in range(3)]
            handle = _find_handle(viewport, centre, want_axis=0)
            REPORT["two_bodies_handle"] = handle is not None
            if handle is not None:
                axis, mm_per_pixel = viewport.screen_axis_for(centre, (1.0, 0.0, 0.0))
                pixels = 15.0 / mm_per_pixel / ratio
                end = QPoint(round(handle.x() + axis[0] * pixels), round(handle.y() + axis[1] * pixels))
                QTest.mouseMove(viewport, handle)
                QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, handle)
                QTest.mouseMove(viewport, end)
                QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, end)
                window.wait_for_rebuild()
                REPORT["two_bodies_moved"] = tuple(
                    round(bounding_box(window.document.bodies[n].shape)[0][0] - starts[n][0], 2)
                    for n in ("Block", "Second"))
                REPORT["two_bodies_label"] = window.history.undo_label
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
        and REPORT.get("y_arrow_found")
        and REPORT.get("part_drag_started")
        and REPORT.get("part_drag_moved", (0, 0, 0))[1] > 10
        and REPORT.get("part_drag_moved", (0, 0, 1))[2] == 0.0
        # Within the 1 mm step snapping of where the cursor went.
        and all(abs(a - b) <= 0.5 + 1e-6 for a, b in zip(
            REPORT.get("part_drag_moved", (9, 9, 9)), REPORT.get("part_drag_cursor", (0, 0, 0))))
        # v0.3: plane squares, Esc, and two bodies together.
        and REPORT.get("plane_constraint") == ("plane", REPORT.get("plane_normal"))
        and REPORT.get("plane_moved", (1, 1, 1))[REPORT.get("plane_normal", 0)] == 0.0
        and any(abs(v) > 1.0 for v in REPORT.get("plane_moved", (0, 0, 0)))
        and REPORT.get("esc_restored") and REPORT.get("esc_kept_tool")
        and len(set(REPORT.get("two_bodies_moved", (0, 1)))) == 1
        and abs(REPORT.get("two_bodies_moved", (0, 0))[0] - 15.0) < 2.0
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
