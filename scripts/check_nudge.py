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

            # Activate Pull through the visible contextual action and send real
            # keys to its live panel. This covers the tool-state route as well
            # as the direct selected-face nudge route below.
            pull_index = next(
                (index for index, action in enumerate(window.context_bar._actions)
                 if action[0] == "pushpull"),
                -1,
            )
            if pull_index >= 0:
                QTest.mouseClick(
                    window.context_bar._buttons[pull_index], Qt.LeftButton,
                    Qt.NoModifier,
                    window.context_bar._buttons[pull_index].rect().center(),
                )
                QApplication.processEvents()
            panels = [
                widget for widget, _anchor in window.stage.overlays
                if getattr(widget, "is_tool_panel", False)
            ]
            panel = panels[0] if panels else None
            start = panel.value("distance") if panel is not None else 0.0
            if panel is not None:
                for _index in range(4):
                    QTest.keyClick(panel, Qt.Key_Up)
                QTest.keyClick(panel, Qt.Key_Down)
                QTest.keyClick(panel, Qt.Key_Up)
            REPORT["active_pull_four_steps_equal_one_mm"] = (
                panel is not None
                and abs(panel.value("distance") - start - 1.0) < 1.0e-9
                and "+0.25 mm" in window.stage.hint._full
            )
            if panel is not None:
                QTest.keyClick(panel, Qt.Key_Escape)
                QApplication.processEvents()
            REPORT["active_pull_cancelled"] = (
                not any(getattr(widget, "is_tool_panel", False)
                        for widget, _anchor in window.stage.overlays)
                and len(window.document.features) == 2
            )
            viewport.clear_selection()
            click(viewport, QPoint(round(point[0]), round(point[1])))
            for _index in range(4):
                key_click(viewport, Qt.Key_Up)
            four_up = (
                window._nudge_state is not None
                and abs(window._nudge_state["amount"] - 4 * NUDGE_STEP_MM) < 1.0e-9
            )
            key_click(viewport, Qt.Key_Down)
            reversed_once = abs(
                window._nudge_state["amount"] - 3 * NUDGE_STEP_MM
            ) < 1.0e-9
            key_click(viewport, Qt.Key_Up)
            REPORT["face_preview_step"] = (
                four_up and reversed_once and window._nudge_state is not None
                and window._nudge_state["kind"] == "face"
                and abs(window._nudge_state["amount"] - 4 * NUDGE_STEP_MM) < 1.0e-9
            )
            QTimer.singleShot(450, after_face)

        def after_face() -> None:
            window.wait_for_rebuild()
            QApplication.processEvents()
            shape = window.document.body("FacePart").shape
            low, high = bounding_box(shape)
            REPORT["face_four_steps_equal_one_mm"] = abs(
                (high[2] - low[2]) - 11.0
            ) < 1.0e-6
            REPORT["face_feature"] = (
                window.document.features[-1].type_name == "push_pull"
                and abs(
                    float(window.document.features[-1].inputs["distance"])
                    - 4 * NUDGE_STEP_MM
                ) < 1.0e-9
            )

            # Select through the real browser row, leave focus in the tree, and
            # deliver four physical key events there. This is the path that was
            # previously outside the viewport-scoped shortcuts.
            # The production UI starts compact at this check's 1100 px width;
            # explicitly expand the inspector because the point of this step is
            # to exercise a real browser row, not its responsive shell.
            window.browser.set_collapsed(False, emit=False)
            window.stage._layout_overlays()
            QApplication.processEvents()
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
            for _index in range(4):
                key_click(tree, Qt.Key_Right)
            four_right = (
                window._nudge_state is not None
                and abs(math.dist(window._nudge_state["offset"], (0, 0, 0)) - 1.0) < 1e-9
            )
            key_click(tree, Qt.Key_Left)
            reversed_once = (
                abs(math.dist(window._nudge_state["offset"], (0, 0, 0)) - .75) < 1e-9
            )
            key_click(tree, Qt.Key_Right)
            REPORT["body_preview_steps"] = (
                four_right and reversed_once and window._nudge_state is not None
                and window._nudge_state["kind"] == "move"
                and abs(
                    math.dist(window._nudge_state["offset"], (0, 0, 0)) - 1.0
                ) < 1.0e-9
            )
            QTimer.singleShot(450, after_body)

        def after_body() -> None:
            window.wait_for_rebuild()
            QApplication.processEvents()
            feature = window.document.features[-1]
            vector = tuple(float(feature.inputs[key]) for key in ("dx", "dy", "dz"))
            REPORT["body_feature"] = feature.type_name == "move_many"
            REPORT["body_four_steps_equal_one_mm"] = abs(
                math.dist(vector, (0, 0, 0)) - 1.0
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
            held_vector = tuple(
                float(feature.inputs[key]) for key in ("dx", "dy", "dz")
            )
            REPORT["held_arrow_committed_on_release"] = (
                window._nudge_state is None and feature.type_name == "move_many"
                and abs(math.dist(
                    held_vector, (0, 0, 0),
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
            final_low = bounding_box(window.document.body("Mover").shape)[0]
            QTest.mouseClick(
                window.top_bar.buttons["undo"], Qt.LeftButton, Qt.NoModifier,
                window.top_bar.buttons["undo"].rect().center(),
            )
            QApplication.processEvents()
            window.wait_for_rebuild()
            QApplication.processEvents()
            undo_low = bounding_box(window.document.body("Mover").shape)[0]
            REPORT["arrow_move_undo"] = math.dist(
                undo_low,
                tuple(final_low[i] - held_vector[i] for i in range(3)),
            ) < 1.0e-6
            QTest.mouseClick(
                window.top_bar.buttons["redo"], Qt.LeftButton, Qt.NoModifier,
                window.top_bar.buttons["redo"].rect().center(),
            )
            QApplication.processEvents()
            window.wait_for_rebuild()
            QApplication.processEvents()
            REPORT["arrow_move_redo"] = math.dist(
                bounding_box(window.document.body("Mover").shape)[0], final_low,
            ) < 1.0e-6
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
    ok = len(REPORT) == 21 and all(value is True for value in REPORT.values())
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
