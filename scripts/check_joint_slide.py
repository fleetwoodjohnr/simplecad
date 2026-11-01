#!/usr/bin/env python
"""A holder that drops into a wall plate on T-rails, made through the real UI.

The reference case for sliding joints: an open-top holder and a thin plate
that would be screwed to a wall. Click the holder's back, click the plate's
front, choose T-rail, watch the preview slide apart and back, Apply. Then the
built parts are checked with real booleans: assembled they do not overlap,
lifted straight up they come free, pushed down they hit the stop, and pulled
off the wall the channel holds them.

    xvfb-run -a .venv/bin/python scripts/check_joint_slide.py
"""

from __future__ import annotations

import sys
import time

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def build(_app):
        window = MainWindow(Mode.LIGHT)

        def run() -> None:
            try:
                scenario(window)
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                import traceback

                traceback.print_exc()
                REPORT["error"] = f"{type(exc).__name__}: {exc}"

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(500, run))
        return window

    def wait(condition, seconds=60.0):
        end = time.monotonic() + seconds
        while not condition() and time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(0.02)
        return condition()

    def scenario(window) -> None:
        from simplecad.core.naming import sub_shapes
        from simplecad.kernel.align import interference_volume
        from simplecad.kernel.detect import analyse_plane
        from simplecad.kernel.occ import area, bounding_box, make_transform, transformed
        from simplecad.kernel.primitives import BoxFeature
        from simplecad.ui.selection import Picked

        document = window.document
        window.add_feature(BoxFeature(inputs={"width": 60, "depth": 40, "height": 90},
                                      outputs=["Holder"]))
        window.add_feature(BoxFeature(inputs={"width": 50, "depth": 2, "height": 80,
                                              "y": 60, "z": 5}, outputs=["Plate"]))
        window.wait_for_rebuild()
        window.stage.viewport.fit_all()

        def face(name, normal):
            shape = document.body(name).shape
            found = max((f for f in sub_shapes(shape, "face") if analyse_plane(f) is not None
                         and sum(a * b for a, b in zip(analyse_plane(f).normal, normal)) > 0.99),
                        key=area)
            return Picked(name, "face", found, window._presentations.get(name),
                          analyse_plane(found))

        window.selection.picks = [face("Holder", (0, 1, 0)), face("Plate", (0, -1, 0))]
        window.activate_tool("clip_joint")
        panel = next(w for w, _a in window.stage.overlays if getattr(w, "title", "") == "Joint")
        REPORT["panel_open"] = True
        panel.style.setCurrentIndex(panel.style.findData("t_rail"))
        REPORT["seat_offered"] = panel.seat.isVisible() and panel.seat.isChecked()
        REPORT["fit"] = panel.fit.currentData()
        REPORT["directions"] = [panel.insertion.itemText(i) for i in range(panel.insertion.count())]
        down = next((i for i in range(panel.insertion.count())
                     if "down" in panel.insertion.itemText(i)), None)
        if down is not None:
            panel.insertion.setCurrentIndex(down)
        panel.preview()
        REPORT["preview_ready"] = wait(lambda: panel._preview_ok or bool(panel.status.text()
                                                                       and not panel._preview_ok
                                                                       and "Working" not in panel.status.text()))
        REPORT["preview_ok"] = panel._preview_ok
        REPORT["preview_message"] = panel.status.text()
        REPORT["moving_part_shown"] = panel._moving is not None
        if panel._moving is not None:
            panel.apart.setValue(100)
            QApplication.processEvents()
            moved = panel._moving.LocalTransformation().TranslationPart()
            REPORT["apart_moves_up"] = round(moved.Z(), 1)
            panel.apart.setValue(0)
            panel._play()
            seen = []
            panel.apart.valueChanged.connect(seen.append)
            wait(lambda: not panel._play_timer.isActive(), 20.0)
            REPORT["play_went_out"] = max(seen or [0])
            REPORT["play_returns_home"] = panel.apart.value() == 0 and not panel._play_timer.isActive()
            window.set_hint("T-rail joint: the holder slides down into the plate")
            QApplication.processEvents()
        if not panel._preview_ok:
            return
        panel.commit()
        window.wait_for_rebuild()
        joint = document.features[-1]
        REPORT["feature"] = (joint.type_name, joint.inputs.get("style"), str(joint.state.value))
        holder, plate = document.body("Holder").shape, document.body("Plate").shape

        def moved(shape, vector):
            return transformed(shape, make_transform(translate=vector))

        travel = joint.annotations["travel"] if getattr(joint, "annotations", None) else 80.0
        REPORT["assembled_overlap"] = round(interference_volume(holder, plate), 4)
        REPORT["lifted_out_overlap"] = round(interference_volume(moved(holder, (0, 0, travel)), plate), 4)
        REPORT["stop_catches"] = interference_volume(moved(holder, (0, 0, -1.0)), plate) > 0.1
        REPORT["holds_on_wall"] = interference_volume(moved(holder, (0, -1.0, 0)), plate) > 0.1
        REPORT["holder_box"] = [round(v, 2) for pair in bounding_box(holder) for v in pair]
        REPORT["undo_label"] = window.history.undo_label
        # The picture: lifted half way out, seen from behind the plate's edge,
        # so the rails on the holder and the lips on the plate both show. Only
        # the presentation moves; the model is untouched.
        from OCP.TopLoc import TopLoc_Location

        from simplecad.ui.viewport.camera import read_state, state_for_direction, write_state

        viewport = window.stage.viewport
        window.selection.picks = []
        window.cancel_tool()
        viewport.place(window._presentations["Holder"],
                       TopLoc_Location(make_transform(translate=(0, 0, travel * 0.55))))
        write_state(viewport.view, state_for_direction(read_state(viewport.view), (-0.8, -0.9, -0.45)))
        viewport.fit_all()
        window.set_hint("T-rails on the holder, a raised channel with a stop on the wall plate")

    run_and_capture(build, "docs/shots/joint_slide.png", settle_ms=60_000, size=(1300, 860))
    print("--- sliding joint ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    ok = (
        "error" not in REPORT
        and REPORT.get("seat_offered")
        and REPORT.get("fit") == "sliding"
        and REPORT.get("preview_ok")
        and REPORT.get("moving_part_shown")
        and REPORT.get("apart_moves_up", 0) > 50
        and REPORT.get("play_returns_home")
        and REPORT.get("feature", ("", "", ""))[2] == "ok"
        and REPORT.get("assembled_overlap", 1) < 1e-3
        # The detent is the only thing it brushes on the way out, at the stop end.
        and REPORT.get("lifted_out_overlap", 1) < 1e-3
        and REPORT.get("stop_catches")
        and REPORT.get("holds_on_wall")
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
