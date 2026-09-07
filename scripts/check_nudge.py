#!/usr/bin/env python
"""Arrow keys nudge a face or a tree-selected body by exact 0.25 mm steps."""

from __future__ import annotations

import math
import os
import sys

os.environ["SIMPLECAD_NO_GRID"] = "1"

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}


def main() -> int:
    from PySide6.QtCore import QPoint, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLineEdit

    from simplecad.core.naming import fingerprint, sub_shapes
    from simplecad.kernel.occ import bounding_box
    from simplecad.kernel.primitives import BoxFeature
    from simplecad.ui.main_window import MainWindow, NUDGE_STEP_MM
    from simplecad.ui.theme import Mode

    def build(_app):
        window = MainWindow(Mode.DARK)

        def key_click(widget, key, modifiers=Qt.NoModifier):
            widget.setFocus()
            QTest.keyClick(window.windowHandle(), key, modifiers)

        def click(widget, position):
            QTest.mouseClick(
                window.windowHandle(), Qt.LeftButton, Qt.NoModifier,
                widget.mapTo(window, position),
            )
            QApplication.processEvents()

        def populate() -> None:
            window.document.add_feature(BoxFeature(
                inputs={"width": 10, "depth": 10, "height": 10},
                outputs=["FacePart"],
            ))
            window.document.add_feature(BoxFeature(
                inputs={"width": 10, "depth": 10, "height": 10, "x": 30},
                outputs=["Mover"],
            ))
            window.rebuild()
            window.wait_for_rebuild()
            QApplication.processEvents()

            viewport = window.stage.viewport
            shape = window.document.body("FacePart").shape
            top = max(
                (
                    face for face in sub_shapes(shape, "face")
                    if (data := fingerprint(face, "face")).direction
                    and data.direction[2] > 0.99
                ),
                key=lambda face: fingerprint(face, "face").center[2],
            )
            viewport.fit_all()
            point = viewport.project(fingerprint(top, "face").center)
            click(viewport, QPoint(round(point[0]), round(point[1])))
            REPORT["face_selected_by_click"] = bool(window.selection.planar_faces())
            QApplication.processEvents()
            key_click(viewport, Qt.Key_Up)
            REPORT["face_preview_step"] = (
                window._nudge_state is not None
                and window._nudge_state["kind"] == "face"
                and abs(window._nudge_state["amount"] - NUDGE_STEP_MM) < 1.0e-9
            )
            QTimer.singleShot(450, after_face)

        def after_face() -> None:
            window.wait_for_rebuild()
            QApplication.processEvents()
            shape = window.document.body("FacePart").shape
            low, high = bounding_box(shape)
            REPORT["face_moved_quarter"] = abs(
                (high[2] - low[2]) - 10.25
            ) < 1.0e-6
            REPORT["face_feature"] = (
                window.document.features[-1].type_name == "push_pull"
                and abs(
                    float(window.document.features[-1].inputs["distance"])
                    - NUDGE_STEP_MM
                ) < 1.0e-9
            )

            # Select through the real browser row, leave focus in the tree, and
            # deliver three physical key events there. This is the path that was
            # previously outside the viewport-scoped shortcuts.
            tree = window.browser.bodies_tree
            matches = tree.findItems("Mover", Qt.MatchExactly | Qt.MatchRecursive)
            REPORT["tree_row_found"] = len(matches) == 1
            if not matches:
                window.set_hint("Mover row was not available for the nudge check")
                return
            click(tree.viewport(), tree.visualItemRect(matches[0]).center())
            QApplication.processEvents()
            window.selection.refresh()
            REPORT["tree_selected_body"] = (
                window.selection.only_bodies
                and window.selection.bodies == ["Mover"]
            )
            for _index in range(3):
                key_click(tree, Qt.Key_Right)
            REPORT["body_preview_steps"] = (
                window._nudge_state is not None
                and window._nudge_state["kind"] == "move"
                and abs(
                    math.dist(window._nudge_state["offset"], (0, 0, 0)) - 0.75
                ) < 1.0e-9
            )
            QTimer.singleShot(450, after_body)

        def after_body() -> None:
            window.wait_for_rebuild()
            QApplication.processEvents()
            feature = window.document.features[-1]
            vector = tuple(float(feature.inputs[key]) for key in ("dx", "dy", "dz"))
            REPORT["body_feature"] = feature.type_name == "move_many"
            REPORT["body_three_quarters"] = abs(
                math.dist(vector, (0, 0, 0)) - 0.75
            ) < 1.0e-9
            REPORT["one_feature_for_burst"] = sum(
                item.type_name == "move_many" for item in window.document.features
            ) == 1

            tree = window.browser.bodies_tree
            tree.setFocus()
            key_click(tree, Qt.Key_Up, Qt.ControlModifier)
            REPORT["depth_preview_step"] = (
                window._nudge_state is not None
                and window._nudge_state["kind"] == "move"
                and abs(
                    math.dist(window._nudge_state["offset"], (0, 0, 0))
                    - NUDGE_STEP_MM
                ) < 1.0e-9
            )
            QTimer.singleShot(450, finish)

        def finish() -> None:
            window.wait_for_rebuild()
            QApplication.processEvents()
            feature = window.document.features[-1]
            vector = tuple(float(feature.inputs[key]) for key in ("dx", "dy", "dz"))
            REPORT["depth_feature"] = (
                feature.type_name == "move_many"
                and abs(math.dist(vector, (0, 0, 0)) - NUDGE_STEP_MM) < 1.0e-9
            )
            REPORT["selection_restored"] = window.selection.bodies == ["Mover"]

            # Escape cancels a pending preview before its quiet timer can add
            # another feature.
            before = len(window.document.features)
            tree = window.browser.bodies_tree
            tree.setFocus()
            key_click(tree, Qt.Key_Right)
            key_click(tree, Qt.Key_Escape)
            QApplication.processEvents()
            REPORT["escape_cancelled"] = (
                window._nudge_state is None
                and not window._nudge_timer.isActive()
                and len(window.document.features) == before
            )
            QTest.keyPress(window.windowHandle(), Qt.Key_Right)
            QTimer.singleShot(400, release_held)

        def release_held() -> None:
            REPORT["held_arrow_not_committed_early"] = (
                window._nudge_state is not None and not window.geometry.busy
            )
            QTest.keyRelease(window.windowHandle(), Qt.Key_Right)
            QTimer.singleShot(450, check_held)

        def check_held() -> None:
            window.wait_for_rebuild()
            QApplication.processEvents()
            feature = window.document.features[-1]
            REPORT["held_arrow_committed_on_release"] = (
                window._nudge_state is None and feature.type_name == "move_many"
                and abs(math.dist(
                    tuple(float(feature.inputs[k]) for k in ("dx", "dy", "dz")),
                    (0, 0, 0),
                ) - NUDGE_STEP_MM) < 1e-9
            )
            editor = QLineEdit("123", window)
            editor.setGeometry(120, 65, 150, 30)
            editor.show()
            click(editor, QPoint(20, 15))
            editor.setCursorPosition(2)
            key_click(editor, Qt.Key_Left)
            REPORT["editor_keeps_arrow_keys"] = (
                editor.cursorPosition() == 1 and window._nudge_state is None
            )
            editor.hide()
            editor.deleteLater()
            window.stage.viewport.fit_all()
            window.set_hint("Arrows: exact 0.25 mm face and object steps")

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(200, populate))
        return window

    run_and_capture(
        build, "/tmp/simplecad-nudge.png", settle_ms=7000, size=(1100, 760),
    )
    print("--- arrow nudging ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    ok = len(REPORT) == 17 and all(value is True for value in REPORT.values())
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
