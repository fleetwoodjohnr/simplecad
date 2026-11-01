#!/usr/bin/env python
"""Dragging one arrow or one ring changes that axis only, from every camera angle.

Drives the real window: a box, Move, then for 26 camera directions (cube faces,
edges and corners) and both projections, grabs each translation arrow found on
screen and drags it both along and across its screen direction. Every committed
Move must leave the other two coordinates exactly unchanged. Then Rotate: each
ring is swept 40 degrees and must turn about its own axis only.

    xvfb-run -a .venv/bin/python scripts/check_axis_lock.py
"""

from __future__ import annotations

import itertools
import math
import os
import sys
import tempfile

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {"failures": [], "drags": 0, "skipped": 0, "turns": 0}


def _directions():
    return [tuple(float(x) for x in v) for v in itertools.product((-1, 0, 1), repeat=3)
            if v != (0, 0, 0)]


def _probe(viewport, points, want_mode, want_axis, outermost=False):
    """A widget point on the wanted handle, asking OCCT's detection.

    Arrows are taken at their outermost hit: near the hub two arrows can both
    be within the pick tolerance, and which one OCCT reports there is its call.
    """
    from PySide6.QtCore import QPoint

    context = viewport.context
    manipulator = viewport.gizmo._manipulator
    hits = []
    for x, y in points:
        point = QPoint(round(x), round(y))
        if not viewport.rect().contains(point):
            continue
        context.MoveTo(*viewport._device_pos(point), viewport.view, False)
        if (context.HasDetected() and context.DetectedInteractive() is manipulator
                and manipulator.HasActiveMode()
                and int(manipulator.ActiveMode()) == int(want_mode)
                and int(manipulator.ActiveAxisIndex()) == want_axis):
            hits.append(point)
    if not hits:
        return None
    # Well out along an arrow, but not its last pixel: the tip is a few pixels
    # wide, and a press on its very edge can fall to the part behind it.
    return hits[int(len(hits) * 0.7)] if outermost else hits[len(hits) // 2]


GRABBED: list = []


def _record_presses() -> None:
    """Note the constraint of every press that takes, on whichever gizmo.

    The panel re-attaches a new gizmo after each rebuild, so hooking the one
    on screen before a drag could miss the press that actually happened.
    """
    from simplecad.ui.viewport.gizmo import TransformGizmo

    if getattr(TransformGizmo.press, "_recording", False):
        return
    original = TransformGizmo.press

    def press(self, x, y):
        taken = original(self, x, y)
        if taken:
            GRABBED.append(self.constraint)
        return taken

    press._recording = True
    TransformGizmo.press = press


def _drag(viewport, start, end, steps=8):
    """Drag from *start* to *end*; returns the constraint the press took."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    GRABBED.clear()
    _record_presses()
    QTest.mouseMove(viewport, start)
    QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, start)
    for step in range(1, steps + 1):
        QTest.mouseMove(viewport, QPoint(
            round(start.x() + (end.x() - start.x()) * step / steps),
            round(start.y() + (end.y() - start.y()) * step / steps),
        ))
    QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, end)
    return GRABBED[0] if GRABBED else None


def main() -> int:
    from PySide6.QtCore import QPoint, QTimer
    from PySide6.QtWidgets import QApplication

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def build(_app):
        window = MainWindow(Mode.DARK)

        def run() -> None:
            try:
                scenario(window)
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                import traceback

                traceback.print_exc()
                REPORT["error"] = f"{type(exc).__name__}: {exc}"
            QApplication.quit()

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(500, run))
        return window

    def scenario(window) -> None:
        from OCP.AIS import AIS_MM_Rotation, AIS_MM_Translation

        from simplecad.core.units import Dimension
        from simplecad.kernel.occ import bounding_box
        from simplecad.kernel.primitives import BoxFeature
        from simplecad.ui.selection import Picked
        from simplecad.ui.viewport.camera import read_state, state_for_direction, write_state

        window.add_feature(BoxFeature(inputs={"width": 30, "depth": 20, "height": 12},
                                      outputs=["Block"]))
        window.wait_for_rebuild()
        viewport = window.stage.viewport

        def select_and_open(tool):
            window.cancel_tool()
            shape = window.document.bodies["Block"].shape
            window.selection.picks = [Picked(body="Block", kind="body", shape=shape,
                                             presentation=window._presentations.get("Block"))]
            window.activate_tool(tool)
            QApplication.processEvents()

        def aim(direction):
            state = read_state(viewport.view)
            write_state(viewport.view, state_for_direction(state, direction))
            viewport.fit_all(animate=False)
            viewport.camera_moved()
            QApplication.processEvents()

        def body_box():
            return bounding_box(window.document.bodies["Block"].shape)

        def settle():
            """Let the last change land and the panel put its gizmo on it.

            The panel re-attaches a fresh gizmo at the moved position a moment
            after the rebuild; probing the old one aimed presses at where the
            arrow used to be, and they landed on the part instead.
            """
            import time

            window.wait_for_rebuild()
            end = time.monotonic() + 0.4
            # ...and if the part went under the panel, the camera eases round
            # to show it; aiming at the arrow mid-sweep misses it.
            while time.monotonic() < end or viewport.animator.running:
                QApplication.processEvents()
                time.sleep(0.01)
                if time.monotonic() > end + 5.0:
                    break

        # The reported "diagonal" move: a click on the part with a little
        # tremor, near another body, then a drag on the Z arrow. The click left
        # magnet locks on X and Y that the drag then applied.
        window.add_feature(BoxFeature(inputs={"width": 10, "depth": 10, "height": 10},
                                      outputs=["Other"]))
        window.wait_for_rebuild()
        from simplecad.core.document import BodyRef
        from simplecad.kernel.operations import MoveFeature

        window.add_feature(MoveFeature(inputs={"body": BodyRef("Other"), "dx": 20.6,
                                               "dy": 15.3, "dz": 0}, outputs=["Other"]))
        window.wait_for_rebuild()
        aim((-1.0, -1.0, -1.0))
        select_and_open("move")
        gizmo = viewport.gizmo
        low, high = body_box()
        grab = viewport.project((high[0] - 2.0, high[1] - 2.0, high[2]))
        grab = QPoint(round(grab[0]), round(grab[1]))
        _drag(viewport, grab, QPoint(grab.x() + 3, grab.y() + 1), steps=3)
        window.wait_for_rebuild()
        REPORT["click_committed"] = len([f for f in window.document.features
                                         if f.type_name == "move"]) > 1
        gizmo = viewport.gizmo
        screen, _mm = viewport.screen_axis_for(gizmo._pivot, (0.0, 0.0, 1.0))
        centre = viewport.project(gizmo._pivot)
        handle = _probe(viewport, [(centre[0] + screen[0] * r, centre[1] + screen[1] * r)
                                   for r in range(24, 170, 3)], AIS_MM_Translation, 2)
        if handle is None:
            REPORT["failures"].append("click-then-drag: Z arrow not found")
        else:
            before = body_box()
            _drag(viewport, handle, QPoint(round(handle.x() + screen[0] * 40),
                                           round(handle.y() + screen[1] * 40)))
            window.wait_for_rebuild()
            after = body_box()
            shift = tuple(round(after[0][i] - before[0][i], 6) for i in range(3))
            REPORT["click_then_z_drag"] = shift
            if shift[0] != 0.0 or shift[1] != 0.0:
                REPORT["failures"].append(f"click-then-drag moved X/Y: {shift}")
        # Pressing Z while dragging the part locks the drag to Z: the part was
        # sliding over the ground, and from then on it only goes up.
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        select_and_open("move")
        low, high = body_box()
        grab = viewport.project((high[0] - 2.0, high[1] - 2.0, high[2]))
        grab = QPoint(round(grab[0]), round(grab[1]))
        before = body_box()
        QTest.mouseMove(viewport, grab)
        QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, grab)
        took = viewport.gizmo is not None and viewport.gizmo.dragging
        for step in range(1, 5):
            QTest.mouseMove(viewport, QPoint(grab.x() + 6 * step, grab.y() + 3 * step))
        QTest.keyPress(viewport, Qt.Key_Z)
        QTest.keyRelease(viewport, Qt.Key_Z)
        locked = viewport.gizmo.constraint if viewport.gizmo is not None else None
        end = QPoint(grab.x() + 24, grab.y() - 60)
        for step in range(1, 9):
            QTest.mouseMove(viewport, QPoint(grab.x() + 24, grab.y() + 12 - 9 * step))
        QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, end)
        window.wait_for_rebuild()
        after = body_box()
        shift = tuple(round(after[0][i] - before[0][i], 6) for i in range(3))
        REPORT["z_key_lock"] = (took, locked, shift)
        if not took or locked != ("axis", 2) or shift[0] != 0.0 or shift[1] != 0.0 or shift[2] == 0.0:
            REPORT["failures"].append(f"Z key during a part drag: took={took} "
                                      f"constraint={locked} moved={shift}")

        for perspective in (False, True):
            viewport.set_perspective(perspective)
            select_and_open("move")
            for direction in _directions():
                aim(direction)
                for axis in range(3):
                    settle()
                    gizmo = viewport.gizmo
                    if gizmo is None:
                        select_and_open("move")
                        gizmo = viewport.gizmo
                    pivot = gizmo._pivot
                    unit = [0.0, 0.0, 0.0]
                    unit[axis] = 1.0
                    screen, _mm = viewport.screen_axis_for(pivot, unit)
                    if screen == (0.0, 0.0) or math.hypot(*screen) < 0.5:
                        REPORT["skipped"] += 1
                        continue
                    centre = viewport.project(pivot)
                    line = [(centre[0] + screen[0] * r, centre[1] + screen[1] * r)
                            for r in range(24, 170, 3)]
                    handle = _probe(viewport, line, AIS_MM_Translation, axis, outermost=True)
                    if handle is None:
                        REPORT["skipped"] += 1   # arrow foreshortened to nothing
                        continue
                    for along, across in ((50, 0), (0, 50), (-35, 35)):
                        end = QPoint(round(handle.x() + screen[0] * along - screen[1] * across),
                                     round(handle.y() + screen[1] * along + screen[0] * across))
                        before = body_box()
                        grabbed = _drag(viewport, handle, end)
                        window.wait_for_rebuild()
                        QApplication.processEvents()
                        after = body_box()
                        REPORT["drags"] += 1
                        if grabbed is None:
                            REPORT["failures"].append(
                                f"{direction} axis {axis}: the press took nothing")
                            continue
                        if grabbed[0] == "plane":
                            # The press fell on the part: it slides in a plane,
                            # and must not leave it.
                            REPORT["missed_handle"] = REPORT.get("missed_handle", 0) + 1
                            shift = after[0][grabbed[1]] - before[0][grabbed[1]]
                            if abs(shift) > 1e-9:
                                REPORT["failures"].append(
                                    f"{direction} part drag left its plane by {shift:+.6f}")
                            continue
                        if grabbed[1] != axis:
                            REPORT["other_arrow"] = REPORT.get("other_arrow", 0) + 1
                        for other in range(3):
                            if other == grabbed[1]:
                                continue
                            shift = after[0][other] - before[0][other]
                            if abs(shift) > 1e-9:
                                REPORT["failures"].append(
                                    f"{'persp' if perspective else 'ortho'} {direction} "
                                    f"axis {axis} drag {along},{across}: axis {other} "
                                    f"moved {shift:+.6f}"
                                )
                        if viewport.gizmo is None:
                            select_and_open("move")
                        # Re-find the arrow next time: the part has moved.
                        break

            select_and_open("rotate")
            for direction in _directions()[::3]:
                aim(direction)
                for axis in range(3):
                    settle()
                    gizmo = viewport.gizmo
                    if gizmo is None:
                        select_and_open("rotate")
                        gizmo = viewport.gizmo
                    centre = viewport.project(gizmo._pivot)
                    ring = None
                    for radius in range(40, 140, 6):
                        ring = _probe(viewport, [
                            (centre[0] + radius * math.cos(a), centre[1] + radius * math.sin(a))
                            for a in (k * math.pi / 12 for k in range(24))
                        ], AIS_MM_Rotation, axis)
                        if ring is not None:
                            break
                    if ring is None:
                        REPORT["skipped"] += 1
                        continue
                    count = len(window.document.features)
                    vx, vy = ring.x() - centre[0], ring.y() - centre[1]
                    start_angle = math.atan2(vy, vx)
                    radius = math.hypot(vx, vy)
                    end_angle = start_angle + math.radians(40)
                    end = QPoint(round(centre[0] + radius * math.cos(end_angle)),
                                 round(centre[1] + radius * math.sin(end_angle)))
                    grabbed = _drag(viewport, ring, end, steps=12)
                    if grabbed is not None and grabbed != ("ring", axis):
                        REPORT["other_ring"] = REPORT.get("other_ring", 0) + 1
                        axis = grabbed[1]
                    window.wait_for_rebuild()
                    QApplication.processEvents()
                    REPORT["turns"] += 1
                    added = window.document.features[count:]
                    if not added:
                        continue
                    feature = added[-1]
                    keys = ("rx", "ry", "rz")
                    for other, key in enumerate(keys):
                        if other == axis:
                            continue
                        value = window.document.parameters.evaluate(
                            str(feature.inputs.get(key, "0")), Dimension.ANGLE)
                        if abs(value) > 1e-9:
                            REPORT["failures"].append(
                                f"{'persp' if perspective else 'ortho'} {direction} ring "
                                f"{axis}: also turned {key} = {feature.inputs.get(key)}"
                            )
                    if viewport.gizmo is None:
                        select_and_open("rotate")

    out = os.path.join(tempfile.gettempdir(), "simplecad-axis-lock.png")
    run_and_capture(build, out, settle_ms=600_000, size=(1300, 860))
    print("--- axis lock ---")
    for key, value in REPORT.items():
        if key == "failures":
            print(f"  failures: {len(value)}")
            for line in value[:30]:
                print(f"    {line}")
        else:
            print(f"  {key}: {value}")
    ok = (not REPORT["failures"] and "error" not in REPORT
          and REPORT["drags"] >= 60 and REPORT["turns"] >= 12)
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
