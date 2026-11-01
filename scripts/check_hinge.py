#!/usr/bin/env python
"""A box and its lid get a print-in-place hinge through the real UI.

Click the box's top back edge, then the lid; Hinge; watch the lid swing on the
slider and Play; Apply. The built parts are then checked by turning the lid
with real booleans: clear of the box from 0 to the limit, and held on its pin.

    xvfb-run -a .venv/bin/python scripts/check_hinge.py
"""

from __future__ import annotations

import math
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
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf
        from OCP.TopLoc import TopLoc_Location
        from OCP.TopoDS import TopoDS

        from simplecad.core.naming import sub_shapes
        from simplecad.kernel.align import interference_volume
        from simplecad.kernel.occ import make_transform, transformed
        from simplecad.kernel.primitives import BoxFeature
        from simplecad.ui.selection import Picked
        from simplecad.ui.viewport.camera import read_state, state_for_direction, write_state

        document = window.document
        window.add_feature(BoxFeature(inputs={"width": 80, "depth": 50, "height": 30},
                                      outputs=["Box"]))
        window.add_feature(BoxFeature(inputs={"width": 80, "depth": 50, "height": 4, "z": 30},
                                      outputs=["Lid"]))
        window.wait_for_rebuild()
        box = document.body("Box").shape
        edge = next(e for e in sub_shapes(box, "edge") if (lambda c: abs(
            c.Value((c.FirstParameter() + c.LastParameter()) / 2).Y() - 50) < 1e-6 and abs(
            c.Value((c.FirstParameter() + c.LastParameter()) / 2).Z() - 30) < 1e-6)(
            BRepAdaptor_Curve(TopoDS.Edge_s(e))))
        lid = document.body("Lid").shape
        window.selection.picks = [
            Picked("Box", "edge", edge, window._presentations.get("Box")),
            Picked("Lid", "body", lid, window._presentations.get("Lid")),
        ]
        from simplecad.ui.selection import available_actions

        REPORT["offered_in_context_bar"] = "hinge" in [k for k, _l, _i in
                                                       available_actions(window.selection)]
        window.activate_tool("hinge")
        panel = next(w for w, _a in window.stage.overlays if getattr(w, "title", "") == "Hinge")
        REPORT["roles"] = panel.roles.text()
        REPORT["preview_ok"] = wait(lambda: panel._preview_ok)
        REPORT["preview_message"] = panel.status.text()
        REPORT["lid_shown_separately"] = panel._moving is not None
        if panel._moving is not None:
            panel.angle.setValue(90)
            QApplication.processEvents()
            turned = panel._moving.LocalTransformation()
            REPORT["slider_turns_lid"] = abs(turned.GetRotation().W()) < 0.99
            panel._play()
            REPORT["play_returns_closed"] = wait(
                lambda: not panel._timer.isActive(), 30.0) and panel.angle.value() == 0
        if not panel._preview_ok:
            return
        notes = dict(panel._annotations)
        panel.commit()
        window.wait_for_rebuild()
        hinge = document.features[-1]
        REPORT["feature"] = (hinge.type_name, hinge.inputs.get("type"), hinge.state.value)
        box, lid = document.body("Box").shape, document.body("Lid").shape
        # The rebuild ran in the geometry process; the axis comes from the preview.
        axis, direction = notes["axis"], notes["direction"]

        def turned(degrees):
            turn = gp_Trsf()
            turn.SetRotation(gp_Ax1(gp_Pnt(*axis), gp_Dir(*direction)), -math.radians(degrees))
            return transformed(lid, turn)

        REPORT["clear_at"] = {d: round(interference_volume(box, turned(d)), 4)
                              for d in (0, 45, 90, 120)}
        REPORT["held_on_pin"] = interference_volume(
            box, transformed(lid, make_transform(translate=(0, 2, 0)))) > 0.1
        REPORT["undo_label"] = window.history.undo_label
        # The picture: the lid shown open, seen from behind and above.
        window.selection.picks = []
        turn = gp_Trsf()
        turn.SetRotation(gp_Ax1(gp_Pnt(*axis), gp_Dir(*direction)), -math.radians(100))
        viewport = window.stage.viewport
        viewport.place(window._presentations["Lid"], TopLoc_Location(turn))
        write_state(viewport.view, state_for_direction(read_state(viewport.view), (-0.5, -1.0, -0.6)))
        viewport.fit_all()
        window.set_hint("Print-in-place hinge: five knuckles, the lid shown open at 100°")

    run_and_capture(build, "docs/shots/hinge.png", settle_ms=60_000, size=(1300, 860))
    print("--- hinge ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    ok = (
        "error" not in REPORT
        and REPORT.get("offered_in_context_bar")
        and REPORT.get("preview_ok")
        and REPORT.get("lid_shown_separately")
        and REPORT.get("slider_turns_lid")
        and REPORT.get("play_returns_closed")
        and REPORT.get("feature", ("", "", ""))[2] == "ok"
        and all(v < 1e-3 for v in REPORT.get("clear_at", {0: 1}).values())
        and REPORT.get("held_on_pin")
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
