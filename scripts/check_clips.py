#!/usr/bin/env python
"""Join two stacked parts through the real UI: click A, click B, preview, Apply.

The clicks land on *visible* side faces, as a person's would -- the faces
where the parts meet are hidden inside the assembly, and the tool finds them.
Then a real Ctrl+Z / Ctrl+Shift+Z checks the join is one history step.
"""

from __future__ import annotations

import os
import sys

os.environ["SIMPLECAD_NO_GRID"] = "1"

from _harness import grab_composited, run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}


def main() -> int:
    from PySide6.QtCore import QPoint, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from simplecad.core.naming import fingerprint, sub_shapes
    from simplecad.kernel.occ import is_valid, volume
    from simplecad.kernel.primitives import BoxFeature
    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def face_towards(shape, direction):
        best = None
        for face in sub_shapes(shape, "face"):
            data = fingerprint(face, "face")
            if data.geometry == "plane" and data.direction and \
                    sum(a * b for a, b in zip(data.direction, direction)) > 0.99:
                if best is None or data.measure > best[0]:
                    best = (data.measure, data.center)
        return best[1]

    def build(_app):
        window = MainWindow(Mode.DARK)
        state = {}

        def click_at(point) -> None:
            viewport = window.stage.viewport
            x, y = viewport.view.Convert(*point)
            ratio = viewport.devicePixelRatioF()
            position = QPoint(round(x / ratio), round(y / ratio))
            QTest.mouseMove(viewport, position)
            QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, position)
            QApplication.processEvents()

        def populate() -> None:
            window.document.add_feature(BoxFeature(
                inputs={"width": 60, "depth": 40, "height": 15}, outputs=["Lower"]))
            window.document.add_feature(BoxFeature(
                inputs={"width": 60, "depth": 40, "height": 15, "z": 15}, outputs=["Upper"]))
            window.rebuild()
            window.wait_for_rebuild()
            window.stage.viewport.fit_all()
            QApplication.processEvents()
            state["volumes"] = {n: volume(window.document.body(n).shape) for n in ("Lower", "Upper")}
            window.run_action("clip_joint")
            QApplication.processEvents()
            panels = [w for w, _ in window.stage.overlays if type(w).__name__ == "ClipJointPanel"]
            REPORT["panel_opened"] = len(panels) == 1
            if not panels:
                finish()
                return
            panel = state["panel"] = panels[0]
            REPORT["asks_for_first_face"] = panel._role == "a"
            # Two visible faces: the front of the upper part, then the front of
            # the lower one -- as a person would click them.
            click_at(face_towards(window.document.body("Upper").shape, (0, -1, 0)))
            REPORT["first_part_taken"] = panel._bodies["a"] == "Upper"
            click_at(face_towards(window.document.body("Lower").shape, (0, -1, 0)))
            REPORT["second_part_taken"] = panel._bodies["b"] == "Lower"
            REPORT["seam_found"] = (panel.picks["a"] is not None and panel.picks["b"] is not None
                                    and abs(panel.picks["a"].info.normal[2] + 1) < 1e-6)
            QTimer.singleShot(3000, preview_ready)

        def preview_ready() -> None:
            panel = state["panel"]
            QApplication.processEvents()
            REPORT["preview_ready"] = panel._preview_ok and panel.confirm.isEnabled()
            REPORT["joints_drawn"] = len(panel.marker.footprints) >= 2
            state["undo_depth"] = len(window.history._undo)
            grab_composited(window).save("/tmp/simplecad-clips-preview.png")
            if REPORT["preview_ready"]:
                QTest.mouseClick(panel.confirm, Qt.LeftButton)
            window.wait_for_rebuild()
            QApplication.processEvents()
            finish()

        def finish() -> None:
            volumes = state.get("volumes", {})
            upper, lower = window.document.body("Upper"), window.document.body("Lower")
            REPORT["tabs_and_pockets"] = (
                upper is not None and lower is not None
                and is_valid(upper.shape) and is_valid(lower.shape)
                and volume(upper.shape) > volumes.get("Upper", 0)
                and volume(lower.shape) < volumes.get("Lower", 0)
            )
            joints = [f for f in window.document.features if f.type_name == "clip_joint"]
            REPORT["one_joint_feature"] = len(joints) == 1
            window.history._finish_pending()
            REPORT["one_undo_step"] = len(window.history._undo) == state.get("undo_depth", -9) + 1
            # Real key presses, focus on the window, exactly as a person types.
            window.activateWindow()
            window.stage.viewport.setFocus()
            QTest.keyClick(window.stage.viewport, Qt.Key_Z, Qt.ControlModifier)
            window.wait_for_rebuild()
            QApplication.processEvents()
            REPORT["ctrl_z_undoes_join"] = (
                not any(f.type_name == "clip_joint" for f in window.document.features)
                and abs(volume(window.document.body("Upper").shape) - volumes.get("Upper", 0)) < 1e-6
            )
            QTest.keyClick(window.stage.viewport, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
            window.wait_for_rebuild()
            QApplication.processEvents()
            REPORT["ctrl_shift_z_redoes_join"] = any(
                f.type_name == "clip_joint" for f in window.document.features)
            window.stage.viewport.fit_all()
            window.set_hint("Join Parts: tabs on the upper part, pockets in the lower")

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(200, populate))
        return window

    run_and_capture(build, "/tmp/simplecad-clips.png", settle_ms=12000, size=(1200, 820))
    print("--- join parts ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    ok = len(REPORT) == 12 and all(value is True for value in REPORT.values())
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
