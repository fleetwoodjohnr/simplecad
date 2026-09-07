#!/usr/bin/env python
"""Open Clip Joint through the UI and create a separate connector body."""

from __future__ import annotations

import os
import sys

os.environ["SIMPLECAD_NO_GRID"] = "1"

from _harness import run_and_capture  # noqa: E402

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

    def horizontal_face(shape, upward: bool):
        candidates = []
        for face in sub_shapes(shape, "face"):
            data = fingerprint(face, "face")
            if (
                data.geometry == "plane" and data.direction
                and data.direction[2] * (1 if upward else -1) > 0.99
            ):
                candidates.append((data.center[2], face))
        return (max if upward else min)(candidates, key=lambda item: item[0])[1]

    def build(_app):
        window = MainWindow(Mode.DARK)

        def populate() -> None:
            window.document.add_feature(BoxFeature(
                inputs={"width": 30, "depth": 20, "height": 12, "x": 50},
                outputs=["Moving"],
            ))
            window.document.add_feature(BoxFeature(
                inputs={"width": 30, "depth": 20, "height": 12},
                outputs=["Target"],
            ))
            window.rebuild()
            window.wait_for_rebuild()
            QApplication.processEvents()

            viewport = window.stage.viewport
            moving = window.document.body("Moving").shape
            target = window.document.body("Target").shape
            viewport.select_subshape(
                window._presentations["Moving"], horizontal_face(moving, False)
            )
            viewport.select_subshape(
                window._presentations["Target"], horizontal_face(target, True),
                replace=False,
            )
            QApplication.processEvents()
            window.run_action("clip_joint")
            QApplication.processEvents()

            panels = [
                widget for widget, _anchor in window.stage.overlays
                if type(widget).__name__ == "ClipJointPanel"
            ]
            REPORT["panel_opened"] = len(panels) == 1 and panels[0].isVisible()
            if not panels:
                finish(None)
                return
            panel = panels[0]
            REPORT["qt_size_method_intact"] = callable(panel.size)

            # Use the same physical viewport click a person uses to add a clip.
            viewport.fit_all()
            QApplication.processEvents()
            x, y = viewport.view.Convert(*panel.frame.center)
            ratio = viewport.devicePixelRatioF()
            QTest.mouseClick(
                viewport, Qt.LeftButton, Qt.NoModifier,
                QPoint(round(x / ratio), round(y / ratio)),
            )
            QApplication.processEvents()
            REPORT["anchor_picked"] = len(panel.anchors) == 1
            QTimer.singleShot(1500, lambda: preview_ready(panel))

        def preview_ready(panel) -> None:
            QApplication.processEvents()
            REPORT["preview_ready"] = (
                panel._preview_ok and panel.confirm.isEnabled()
            )
            if REPORT["preview_ready"]:
                QTest.mouseClick(panel.confirm, Qt.LeftButton)
            finish(panel)

        def finish(_panel) -> None:
            window.wait_for_rebuild()
            QApplication.processEvents()
            REPORT["three_bodies"] = set(window.document.bodies) == {
                "Moving", "Target", "Clip",
            }
            joints = [
                feature for feature in window.document.features
                if feature.type_name == "clip_joint"
            ]
            REPORT["one_joint_feature"] = (
                len(joints) == 1
                and joints[0].outputs == ["Moving", "Target", "Clip"]
            )
            REPORT["separate_connector"] = (
                "Clip" not in window.document.groups
                and window.document.body("Clip") is not None
                and is_valid(window.document.body("Clip").shape)
            )
            REPORT["paired_sockets"] = (
                window.document.body("Moving") is not None
                and window.document.body("Target") is not None
                and volume(window.document.body("Moving").shape) < 30 * 20 * 12
                and volume(window.document.body("Target").shape) < 30 * 20 * 12
            )
            window.stage.viewport.fit_all()
            window.set_hint("Clip Joint: two socketed parts and one removable clip")

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(200, populate))
        return window

    run_and_capture(
        build, "/tmp/simplecad-clips.png", settle_ms=8000, size=(1200, 820),
    )
    print("--- clip joint ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    ok = len(REPORT) == 8 and all(value is True for value in REPORT.values())
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
