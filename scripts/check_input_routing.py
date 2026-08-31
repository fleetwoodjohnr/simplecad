#!/usr/bin/env python
"""Physical stage input must reach floating panels, then the model.

This is the user path that exposed the native-window bug: click Shape in the
rail, click a tile and Create in the card drawn over the 3D stage, then select
the resulting body. Direct calls to ``panel.commit()`` cannot catch it. The
test clicks the real controls and also asserts the X11 topology that makes a
physical click route through Qt rather than directly into the viewport.

Run:  .venv/bin/python scripts/check_input_routing.py
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
    from PySide6.QtWidgets import QApplication, QPushButton

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def build(_app):
        window = MainWindow(Mode.DARK)

        def run() -> None:
            viewport = window.stage.viewport

            # Start where the user starts: the rail is outside the stage.
            QTest.mouseClick(
                window.rail._tiles["shapes"], Qt.LeftButton, Qt.NoModifier
            )
            QApplication.processEvents()
            panel = next(
                (widget for widget, _anchor in window.stage.overlays
                 if type(widget).__name__ == "ShapePanel"),
                None,
            )
            REPORT["shape_panel_opened"] = panel is not None and panel.isVisible()
            if panel is None:
                window.set_hint("Shape panel did not open")
                return

            # Exercise a child hit target inside the composited panel.
            QTest.mouseClick(
                panel._tiles["cylinder"], Qt.LeftButton, Qt.NoModifier
            )
            QApplication.processEvents()
            REPORT["shape_tile_clicked"] = panel._kind == "cylinder"

            create = next(
                (button for button in panel.findChildren(QPushButton)
                 if button.text() == "Create"),
                None,
            )
            REPORT["create_button_found"] = create is not None

            stage_widgets = [window.stage, viewport, panel, window.browser]
            native_handle = int(window.stage.effectiveWinId())
            REPORT["stage_widgets_non_native"] = all(
                not widget.testAttribute(Qt.WA_NativeWindow)
                for widget in stage_widgets
            )
            REPORT["one_effective_window"] = native_handle != 0 and all(
                int(widget.effectiveWinId()) == native_handle
                for widget in stage_widgets
            )
            if create is not None:
                # widgetAt is the target Qt chooses after X11 delivers the
                # click to the shared top-level window.
                target = QApplication.widgetAt(create.mapToGlobal(
                    QPoint(create.width() // 2, create.height() // 2)
                ))
                REPORT["create_is_hit_target"] = (
                    target is create
                    or (target is not None and create.isAncestorOf(target))
                )
                QTest.mouseClick(create, Qt.LeftButton, Qt.NoModifier)

            REPORT["rebuild_finished"] = window.wait_for_rebuild()
            QApplication.processEvents()
            REPORT["body_created"] = len(window.document.bodies) == 1
            REPORT["body_presented"] = len(window._presentations) == 1

            if window.document.bodies:
                name = next(iter(window.document.bodies))
                body = window.document.body(name)
                viewport.fit_all()
                QApplication.processEvents()
                # Select the centre of the cylinder's top face, using the same
                # viewport event path as an ordinary model click.
                x, y = viewport.view.Convert(0.0, 0.0, 30.0)
                ratio = viewport.devicePixelRatioF()
                QTest.mouseClick(
                    viewport, Qt.LeftButton, Qt.NoModifier,
                    QPoint(int(x / ratio), int(y / ratio)),
                )
                QApplication.processEvents()
                window.selection.refresh()
                REPORT["model_selected"] = name in window.selection.bodies
                REPORT["selection_highlighted"] = bool(
                    viewport.context is not None
                    and viewport.context.NbSelected() > 0
                )
                REPORT["context_actions_visible"] = window.context_bar.isVisible()
                REPORT["shape_valid"] = body is not None and body.shape is not None

            window.set_hint("Shape creation and model selection receive input")

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(500, run))
        return window

    run_and_capture(
        build, "/tmp/simplecad-input-routing.png", settle_ms=6500,
        size=(1200, 820),
    )
    print("--- input routing ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    required = (
        "shape_panel_opened", "shape_tile_clicked", "create_button_found",
        "stage_widgets_non_native", "one_effective_window",
        "create_is_hit_target", "rebuild_finished", "body_created",
        "body_presented", "model_selected", "selection_highlighted",
        "context_actions_visible", "shape_valid",
    )
    ok = all(REPORT.get(key) is True for key in required)
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
