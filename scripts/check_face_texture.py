#!/usr/bin/env python
"""Real-window texture picking, editing, removal, and undo/redo check."""

from __future__ import annotations

import os
import sys

os.environ["SIMPLECAD_NO_GRID"] = "1"

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, bool] = {}


def main() -> int:
    from PySide6.QtCore import QPoint, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from simplecad.core.naming import fingerprint, sub_shapes
    from simplecad.kernel.occ import is_valid, volume
    from simplecad.kernel.primitives import BoxFeature
    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode
    from simplecad.ui.viewport.occt_view import StandardView

    def build(_app):
        window = MainWindow(Mode.DARK)

        def select_texture():
            viewport = window.stage.viewport
            viewport.set_standard_view(StandardView.TOP, animate=False)
            viewport.fit_all()
            shape = window.document.body("Plate").shape
            relief = max(
                (face for face in sub_shapes(shape, "face")
                 if (mark := fingerprint(face, "face")).direction
                 and mark.direction[2] > 0.99),
                key=lambda face: fingerprint(face, "face").center[2],
            )
            point = viewport.project(fingerprint(relief, "face").center)
            QTest.mouseClick(window.windowHandle(), Qt.LeftButton, Qt.NoModifier,
                             viewport.mapTo(window, QPoint(round(point[0]), round(point[1]))))
            QApplication.processEvents()

        def click_action(key):
            button = next(button for button in window.context_bar._buttons if button.key == key)
            assert not button.isHidden(), f"{key} should be directly visible"
            QTest.mouseClick(button, Qt.LeftButton)

        def panel():
            return next(
                widget for widget, _anchor in window.stage.overlays
                if getattr(widget, "is_tool_panel", False)
            )

        def when_ready(callback, attempts=70):
            QApplication.processEvents()
            current = panel()
            if current.confirm.isEnabled() and current._preview_valid:
                callback(current)
            elif attempts:
                QTimer.singleShot(100, lambda: when_ready(callback, attempts - 1))
            else:
                REPORT["exact_preview"] = False

        def populate():
            window.document.add_feature(BoxFeature(
                inputs={"width": 22, "depth": 22, "height": 3}, outputs=["Plate"]
            ))
            window.rebuild()
            window.wait_for_rebuild()
            from tempfile import TemporaryDirectory
            from simplecad.core.project import save

            with TemporaryDirectory() as folder:
                window._open_path(save(window.document, f"{folder}/cached.scad3"))
            REPORT["cached_reopen"] = window.document.body("Plate").shape is not None
            viewport = window.stage.viewport
            viewport.set_standard_view(StandardView.TOP, animate=False)
            viewport.fit_all()
            shape = window.document.body("Plate").shape
            top = max(
                (face for face in sub_shapes(shape, "face")
                 if (mark := fingerprint(face, "face")).direction
                 and mark.direction[2] > 0.99),
                key=lambda face: fingerprint(face, "face").center[2],
            )
            point = viewport.project(fingerprint(top, "face").center)
            QTest.mouseClick(window.windowHandle(), Qt.LeftButton, Qt.NoModifier,
                             viewport.mapTo(window, QPoint(round(point[0]), round(point[1]))))
            QApplication.processEvents()
            REPORT["face_selected"] = len(window.selection.planar_faces()) == 1
            window.run_action("face_texture")
            current = panel()
            REPORT["library_and_guides"] = (
                current.pattern.count() == 16 and current.overlay.layout is not None
            )
            initial_pattern = current.pattern.currentIndex()
            current.pattern.setCurrentIndex(next(
                index for index in range(current.pattern.count())
                if current.pattern.itemData(index)[1] == "dots"
            ))
            current.fields["size"].set_value(1.0)
            current.fields["spacing"].set_value(0.5)
            current.preview()
            REPORT["dense_guide_ready"] = (
                current.overlay.layout is not None
                and len(current.overlay.layout.motifs) > 24
                and not current.confirm.isEnabled()
                and current._preview._state is not None
            )
            current.pattern.setCurrentIndex(initial_pattern)
            current.fields["size"].set_value(3.0)
            current.fields["spacing"].set_value(2.0)
            current.preview()
            when_ready(apply_texture)

        def apply_texture(current):
            REPORT["exact_preview"] = current._preview_valid
            before = volume(window.document.body("Plate").shape)
            QTest.mouseClick(current.confirm, Qt.LeftButton)
            window.wait_for_rebuild()
            QApplication.processEvents()
            shape = window.document.body("Plate").shape
            REPORT["applied_solid"] = (
                window.document.features[-1].type_name == "face_texture"
                and is_valid(shape) and volume(shape) > before
            )
            if not REPORT["applied_solid"]:
                return
            feature_id = window.document.features[-1].id
            select_texture()
            REPORT["texture_click_actions"] = window.context_bar.keys()[:2] == [
                "edit_face_texture", "remove_face_texture",
            ]
            click_action("edit_face_texture")
            edit = panel()
            REPORT["editor_loaded"] = (
                edit._editing.id == feature_id
                and edit.title_label.text() == "Edit texture"
                and edit.confirm.text() == "Save changes"
            )
            edit.fields["depth"].set_value(1.0)
            edit.preview()
            when_ready(lambda ready: apply_edit(ready, feature_id, volume(shape)))

        def apply_edit(current, feature_id, old_volume):
            from _harness import grab_composited

            grab_composited(window).save("/tmp/simplecad-texture-edit.png")
            REPORT["edit_preview"] = (
                (current._preview_valid or current._guide_valid)
                and current.overlay.layout is not None
            )
            QTest.mouseClick(current.confirm, Qt.LeftButton)
            window.wait_for_rebuild()
            QApplication.processEvents()
            edited = volume(window.document.body("Plate").shape)
            REPORT["history_edit"] = (
                window.document.features[-1].id == feature_id
                and edited > old_volume and is_valid(window.document.body("Plate").shape)
            )
            window.undo()
            window.wait_for_rebuild()
            REPORT["undo_edit"] = abs(volume(window.document.body("Plate").shape) - old_volume) < 1e-5
            window.redo()
            window.wait_for_rebuild()
            REPORT["redo_edit"] = abs(volume(window.document.body("Plate").shape) - edited) < 1e-5

            from simplecad.core.document import BodyRef
            from simplecad.kernel.operations import MoveFeature
            from simplecad.core.project import save
            from tempfile import TemporaryDirectory

            window.add_feature(MoveFeature(inputs={"body": BodyRef("Plate"), "dx": 5}, outputs=["Plate"]))
            window.wait_for_rebuild()
            with TemporaryDirectory() as folder:
                window._open_path(save(window.document, f"{folder}/textured.scad3"))
            select_texture()
            REPORT["reopened_texture_actions"] = "remove_face_texture" in window.context_bar.keys()
            click_action("remove_face_texture")
            window.wait_for_rebuild()
            REPORT["removed_after_move"] = (
                window.document.feature(feature_id) is None
                and abs(volume(window.document.body("Plate").shape) - 22 * 22 * 3) < 1e-5
            )
            REPORT["removed_actions_cleared"] = "remove_face_texture" not in window.context_bar.keys()
            window.undo()
            window.wait_for_rebuild()
            REPORT["undo_removal"] = abs(volume(window.document.body("Plate").shape) - edited) < 1e-5
            window.redo()
            window.wait_for_rebuild()
            REPORT["redo_removal"] = abs(volume(window.document.body("Plate").shape) - 22 * 22 * 3) < 1e-5
            REPORT["redo_removed_actions_cleared"] = "remove_face_texture" not in window.context_bar.keys()

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(300, populate))
        return window

    run_and_capture(build, "/tmp/simplecad-face-texture.png", settle_ms=20_000)
    for key, result in REPORT.items():
        print(f"{key}: {result}")
    passed = len(REPORT) == 18 and all(REPORT.values())
    print("VERDICT:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
