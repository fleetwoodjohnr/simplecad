#!/usr/bin/env python
"""Split: drag a plane through a part and get two parts.

Checks the whole interaction, not just the boolean: that a visible plane and its
handles appear, that dragging a handle moves the cut, that the resulting size of
*both* halves is reported live while it moves, that an exact position can be
typed instead, and -- the point of the whole feature -- that what comes out is
two independently selectable bodies rather than one body in two pieces.
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

    WIDTH, DEPTH, HEIGHT = 60.0, 30.0, 25.0
    TYPED = 22.0

    def build(_app):
        window = MainWindow(Mode.DARK)

        def run() -> None:
            from simplecad.kernel.occ import bounding_box, volume
            from simplecad.kernel.primitives import BoxFeature
            from simplecad.ui.selection import Picked

            window.add_feature(
                BoxFeature(
                    inputs={"width": WIDTH, "depth": DEPTH, "height": HEIGHT},
                    outputs=["Bracket"],
                )
            )
            window.wait_for_rebuild()
            viewport = window.stage.viewport
            viewport.fit_all()

            shape = window.document.bodies["Bracket"].shape
            REPORT["volume_before"] = round(volume(shape), 1)

            window.selection.picks = [
                Picked(body="Bracket", kind="body", shape=shape,
                       presentation=window._presentations.get("Bracket"))
            ]
            window.activate_tool("split")
            panel = next(
                (w for w, _a in window.stage.overlays
                 if type(w).__name__ == "SplitPanel"), None
            )
            REPORT["panel_opened"] = panel is not None
            if panel is None:
                return

            # X is the longest dimension, so that is what it should offer.
            REPORT["default_axis"] = panel.source
            REPORT["plane_shown"] = panel._plane is not None
            REPORT["handle_count"] = len(panel._handles)
            REPORT["span"] = round(panel.span, 2)
            REPORT["default_position"] = round(panel.value("position"), 2)
            REPORT["sizes_at_open"] = panel.sizes.text()

            # Nothing is highlighted while the plane is on screen. The subject
            # of this tool is the plane, and a selection glowing through a
            # translucent body competes with the one thing there is to look at.
            REPORT["nothing_selected"] = viewport.context.NbSelected() == 0
            REPORT["picking_off"] = not viewport.picking_enabled
            # And a click on the body cannot bring the highlight back.
            from simplecad.kernel.occ import bounding_box as _bounds

            low, high = _bounds(shape)
            centre = viewport.project(
                tuple((low[i] + high[i]) / 2.0 for i in range(3))
            )
            if centre is not None:
                QTest.mouseClick(
                    viewport, Qt.LeftButton, Qt.NoModifier,
                    QPoint(int(centre[0]), int(centre[1])), 60,
                )
            REPORT["stays_unselected"] = viewport.context.NbSelected() == 0

            # At least one handle has to be reachable from this camera angle.
            on_screen = [
                h for h in panel._handles
                if (p := viewport.project(h.anchor)) is not None
                and 0 <= p[0] <= viewport.width() and 0 <= p[1] <= viewport.height()
            ]
            REPORT["handles_on_screen"] = len(on_screen)
            if not on_screen:
                return
            handle = on_screen[0]

            start = viewport.project(handle.anchor)
            axis, mm_per_pixel = viewport.screen_axis_for(
                handle.anchor, handle.direction
            )
            ratio = viewport.devicePixelRatioF()
            begin = QPoint(round(start[0]), round(start[1]))
            move_mm = -10.0
            pixels = move_mm / mm_per_pixel / ratio
            end = QPoint(round(begin.x() + axis[0] * pixels),
                         round(begin.y() + axis[1] * pixels))

            QTest.mouseMove(viewport, begin)
            QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, begin)
            REPORT["drag_started"] = viewport._nav.name == "DRAG_HANDLE"
            QTest.mouseMove(viewport, end)
            REPORT["position_during_drag"] = round(panel.value("position"), 2)
            REPORT["sizes_during_drag"] = panel.sizes.text()
            REPORT["readout_visible"] = window.stage.drag_readout.isVisible()
            REPORT["readout_text"] = window.stage.drag_readout.size_label.text()
            QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, end)
            dragged = panel.value("position")
            REPORT["position_after_drag"] = round(dragged, 2)
            near, far = panel._sides(dragged)
            REPORT["sides_sum_to_span"] = abs((near + far) - panel.span) < 1e-6

            # ...and an exact number instead.
            panel.fields["position"].set_value(TYPED)
            panel.preview()
            REPORT["sizes_after_typing"] = panel.sizes.text()

            panel.commit()
            window.wait_for_rebuild()
            bodies = list(window.document.bodies)
            REPORT["bodies"] = bodies
            REPORT["features"] = [f.type_name for f in window.document.features]
            halves = [b for b in bodies if b.startswith("Bracket ")]
            REPORT["halves"] = halves
            if len(halves) == 2:
                volumes = [
                    round(volume(window.document.bodies[h].shape), 1) for h in halves
                ]
                REPORT["half_volumes"] = volumes
                REPORT["volumes_add_up"] = (
                    abs(sum(volumes) - REPORT["volume_before"]) < 1.0
                )
                widths = [
                    round(bounding_box(window.document.bodies[h].shape)[1][0]
                          - bounding_box(window.document.bodies[h].shape)[0][0], 2)
                    for h in halves
                ]
                REPORT["half_widths"] = widths
                REPORT["cut_where_asked"] = abs(min(widths) - TYPED) < 0.01

                # Each half must be its own body: independently selectable.
                first = window._presentations.get(halves[0])
                viewport.select_shape(first)
                window.selection.refresh()
                REPORT["selected_one_half"] = window.selection.bodies == [halves[0]]
                REPORT["picking_restored"] = viewport.picking_enabled

                # ...and deleting one must not take the other with it.
                window.delete_body(halves[1])
                window.wait_for_rebuild()
                REPORT["after_delete"] = [
                    b for b in window.document.bodies if b.startswith("Bracket ")
                ]
                window.undo()
                window.wait_for_rebuild()
                REPORT["after_undo"] = sorted(
                    b for b in window.document.bodies if b.startswith("Bracket ")
                )

            viewport.clear_selection()
            viewport.fit_all()
            window.set_hint(f"Split into {', '.join(halves)}")

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(500, run))
        return window

    run_and_capture(build, "docs/shots/split.png", settle_ms=6000, size=(1300, 850))
    print("--- split ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")

    ok = (
        REPORT.get("panel_opened")
        and REPORT.get("default_axis") == "x"            # the longest dimension
        and REPORT.get("plane_shown")
        and REPORT.get("handle_count") == 5
        and REPORT.get("handles_on_screen", 0) >= 1
        and abs(REPORT.get("span", 0) - 60.0) < 1e-6
        and REPORT.get("nothing_selected")
        and REPORT.get("picking_off")
        and REPORT.get("stays_unselected")
        and REPORT.get("picking_restored")
        and REPORT.get("drag_started")
        and REPORT.get("position_during_drag") != REPORT.get("default_position")
        and REPORT.get("readout_visible")
        and "│" in (REPORT.get("sizes_during_drag") or "")
        and REPORT.get("sides_sum_to_span")
        and REPORT.get("cut_where_asked")
        and REPORT.get("volumes_add_up")
        and len(REPORT.get("halves") or []) == 2
        and REPORT.get("selected_one_half")
        and len(REPORT.get("after_delete") or []) == 1   # the other survived
        and len(REPORT.get("after_undo") or []) == 2     # and undo brings it back
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
