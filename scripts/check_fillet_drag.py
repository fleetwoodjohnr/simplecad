#!/usr/bin/env python
"""Interactive fillet: is the radius something you can pull out with the mouse?

Selects an edge, opens Fillet, finds the handle the tool put on the model, and
drags it -- then checks the four things that make this a direct-manipulation
tool rather than a form with a preview button: the handle exists, the number in
the panel tracks the drag, a live preview of the real filleted body is on
screen, and Apply writes the dragged value as a parametric feature.

Also checks the two-way binding the other way round: typing a value must move
the handle, or the panel and the model are telling the user different things.
"""

from __future__ import annotations

import os
import sys

os.environ["SIMPLECAD_NO_GRID"] = "1"

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}


def wait_for(condition, timeout_ms: int = 4000) -> float:
    """Pump the event loop until *condition* holds. Returns the wait, in ms."""
    import time

    from PySide6.QtCore import QEventLoop
    from PySide6.QtWidgets import QApplication

    started = time.perf_counter()
    while (time.perf_counter() - started) * 1000.0 < timeout_ms:
        if condition():
            break
        QApplication.processEvents(QEventLoop.AllEvents, 10)
    return round((time.perf_counter() - started) * 1000.0, 1)


def main() -> int:
    from PySide6.QtCore import QPoint, Qt, QTimer
    from PySide6.QtTest import QTest

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    WIDTH, DEPTH, HEIGHT = 40.0, 40.0, 30.0
    TARGET_MM = 6.0

    def build(_app):
        window = MainWindow(Mode.DARK)

        def run() -> None:
            from OCP.TopAbs import TopAbs_EDGE
            from OCP.TopExp import TopExp_Explorer
            from OCP.TopoDS import TopoDS

            from simplecad.kernel.occ import volume
            from simplecad.kernel.primitives import BoxFeature
            from simplecad.ui.selection import Picked

            window.add_feature(
                BoxFeature(
                    inputs={"width": WIDTH, "depth": DEPTH, "height": HEIGHT},
                    outputs=["Block"],
                )
            )
            window.wait_for_rebuild()
            viewport = window.stage.viewport
            viewport.fit_all()

            shape = window.document.bodies["Block"].shape
            REPORT["volume_before"] = round(volume(shape), 1)

            # A vertical edge, which in an isometric view is unambiguous on
            # screen and has both its faces visible.
            chosen = None
            explorer = TopExp_Explorer(shape, TopAbs_EDGE)
            while explorer.More():
                edge = TopoDS.Edge_s(explorer.Current())
                from simplecad.kernel.edge_frame import edge_midpoint

                mid = edge_midpoint(edge)
                if abs(mid[0] - WIDTH) < 1e-6 and abs(mid[1]) < 1e-6:
                    chosen = edge
                    break
                explorer.Next()
            REPORT["edge_found"] = chosen is not None
            if chosen is None:
                return

            window.selection.picks = [
                Picked(body="Block", kind="edge", shape=chosen,
                       presentation=window._presentations.get("Block"))
            ]
            window.activate_tool("fillet")
            panel = next(
                (w for w, _a in window.stage.overlays
                 if type(w).__name__ == "FilletPanel"), None
            )
            REPORT["panel_opened"] = panel is not None
            if panel is None:
                return

            handle = panel._handle
            REPORT["handle_created"] = handle is not None
            REPORT["handles_on_screen"] = len(viewport.handles)
            if handle is None:
                return

            # Typing must move the handle: check before touching the mouse.
            was = handle.anchor
            panel.fields["radius"].set_value(5.0)
            panel.preview()
            REPORT["handle_follows_typing"] = handle.anchor != was
            panel.fields["radius"].set_value(2.0)
            panel.preview()

            start = viewport.project(handle.anchor)
            REPORT["handle_on_screen"] = (
                start is not None
                and 0 <= start[0] <= viewport.width()
                and 0 <= start[1] <= viewport.height()
            )
            if start is None:
                return

            axis, mm_per_pixel = viewport.screen_axis_for(
                handle.anchor, handle.direction
            )
            ratio = viewport.devicePixelRatioF()
            begin = QPoint(round(start[0]), round(start[1]))
            pixels = (TARGET_MM - 2.0) / mm_per_pixel / ratio
            end = QPoint(round(begin.x() + axis[0] * pixels),
                         round(begin.y() + axis[1] * pixels))

            QTest.mouseMove(viewport, begin)
            QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, begin)
            REPORT["drag_started"] = viewport._nav.name == "DRAG_HANDLE"
            QTest.mouseMove(viewport, end)
            REPORT["radius_during_drag"] = round(panel.value("radius"), 2)
            # The preview is built in the geometry process now -- OCCT's fillet
            # solver segfaulted the window twice when it ran here -- so the
            # ghost arrives on a reply rather than before this line returns.
            REPORT["preview_ms"] = wait_for(
                lambda: getattr(viewport, "_ghost", None) is not None
            )
            REPORT["preview_shown"] = getattr(viewport, "_ghost", None) is not None
            REPORT["readout_visible"] = window.stage.drag_readout.isVisible()
            # The readout must show where the hand is, not what the preview
            # that just came back was asked about.
            REPORT["readout_text"] = window.stage.drag_readout.size_label.text()
            REPORT["readout_tracks_the_drag"] = (
                f"{REPORT['radius_during_drag']:.2f}"
                in window.stage.drag_readout.size_label.text()
            )
            REPORT["body_dimmed"] = panel._dimmed == "Block"
            REPORT["radius_during_release"] = round(panel.value("radius"), 2)
            REPORT["radius_after_drag"] = round(panel.value("radius"), 2)
            # Letting go is what commits, exactly as it does for a dragged
            # face. Nothing is clicked here on purpose: a drag that leaves the
            # model unchanged until you find a button is the bug this checks.
            QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, end)
            window.wait_for_rebuild()
            REPORT["features"] = [f.type_name for f in window.document.features]
            REPORT["committed_on_release"] = (
                REPORT["features"].count("fillet") == 1
            )
            fillet = next(
                (f for f in window.document.features if f.type_name == "fillet"), None
            )
            # Stored as an expression ("6 mm"), because every dimension in
            # SimpleCAD is one -- so read it the way the rebuild does.
            from simplecad.core.units import Dimension

            REPORT["committed_radius"] = (
                round(
                    window.document.parameters.evaluate(
                        str(fillet.inputs["radius"]), Dimension.LENGTH
                    ),
                    2,
                )
                if fillet else None
            )
            after = window.document.bodies["Block"].shape
            REPORT["volume_after"] = round(volume(after), 1)
            REPORT["ghost_cleared"] = getattr(viewport, "_ghost", None) is None
            REPORT["handles_cleared"] = len(viewport.handles) == 0
            REPORT["transparency_restored"] = panel._dimmed is None
            viewport.fit_all()
            window.set_hint(
                f"Dragged a {REPORT['committed_radius']} mm fillet onto an edge"
            )

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(500, run))
        return window

    run_and_capture(build, "docs/shots/fillet_drag.png", settle_ms=5200,
                    size=(1280, 840))
    print("--- interactive fillet ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")

    dragged = REPORT.get("radius_during_release") or 0
    committed = REPORT.get("committed_radius") or 0
    ok = (
        REPORT.get("edge_found")
        and REPORT.get("panel_opened")
        and REPORT.get("handle_created")
        and REPORT.get("handle_on_screen")
        and REPORT.get("handle_follows_typing")
        and REPORT.get("drag_started")
        and abs(dragged - TARGET_MM) < 1.0          # drag steered the value
        and REPORT.get("preview_shown")
        and REPORT.get("readout_visible")
        and REPORT.get("readout_tracks_the_drag")
        and REPORT.get("body_dimmed")
        and abs(committed - dragged) < 1e-6         # committed what was dragged
        and REPORT.get("committed_on_release")
        and REPORT.get("volume_after", 0) < REPORT.get("volume_before", 0)
        and REPORT.get("ghost_cleared")
        and REPORT.get("handles_cleared")
        and REPORT.get("transparency_restored")
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
