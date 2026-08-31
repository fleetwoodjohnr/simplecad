#!/usr/bin/env python
"""Verify Fusion-style native face hover and selection highlighting.

Run:  .venv/bin/python scripts/check_highlight_box.py
"""

from __future__ import annotations

import sys

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}


def main() -> int:
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def hover(widget, position: QPoint) -> None:
        QApplication.sendEvent(widget, QMouseEvent(
            QEvent.MouseMove, QPointF(position),
            widget.mapToGlobal(QPointF(position)),
            Qt.NoButton, Qt.NoButton, Qt.NoModifier,
        ))

    def build(_app):
        window = MainWindow(Mode.DARK)

        def populate() -> None:
            from simplecad.kernel.primitives import BoxFeature

            window.add_feature(BoxFeature(
                inputs={"width": 50, "depth": 40, "height": 30},
                outputs=["Block"],
            ))
            window.wait_for_rebuild()
            window.stage.viewport.fit_all()
            QTimer.singleShot(700, probe)

        def probe() -> None:
            viewport = window.stage.viewport
            ratio = viewport.devicePixelRatioF()
            x, y = viewport.view.Convert(25.0, 20.0, 30.0)
            top = QPoint(int(x / ratio), int(y / ratio))

            hover(viewport, top)
            QApplication.processEvents()
            REPORT["native_hover_detected"] = viewport.context.HasDetected()
            REPORT["no_rectangle_overlay"] = not hasattr(
                window.stage, "highlight_box"
            )

            QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, top)
            QApplication.processEvents()
            entries = viewport.selected_entries()
            REPORT["one_native_selection"] = viewport.context.NbSelected() == 1
            REPORT["face_selected"] = bool(entries and entries[0]["kind"] == "face")

            siblings = (
                window.stage, viewport, window.stage.hint,
                window.stage.measure_overlay, window.stage.selection_band,
            )
            REPORT["widgets_are_not_native"] = all(
                not widget.testAttribute(Qt.WA_NativeWindow) for widget in siblings
            )
            window.set_hint("Native face shading: hover and selected face")

        window.stage.viewport.ready.connect(populate)
        return window

    run_and_capture(
        build, "docs/shots/native_highlight.png",
        settle_ms=3600, size=(1100, 760),
    )
    print("--- native highlight ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    ok = all(REPORT.get(key) is True for key in (
        "native_hover_detected", "no_rectangle_overlay", "one_native_selection",
        "face_selected", "widgets_are_not_native",
    ))
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
