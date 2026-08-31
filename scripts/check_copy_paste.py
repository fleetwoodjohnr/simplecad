#!/usr/bin/env python
"""Exercise Ctrl+C/Ctrl+V through the real window and viewport shortcuts."""

from __future__ import annotations

import sys

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}


def main() -> int:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from simplecad.ui.main_window import MODEL_CLIPBOARD_MIME, MainWindow
    from simplecad.ui.theme import Mode

    def build(_app):
        window = MainWindow(Mode.DARK)

        def populate() -> None:
            from simplecad.kernel.primitives import BoxFeature

            window.add_feature(BoxFeature(
                inputs={"width": 20, "depth": 15, "height": 10},
                outputs=["Box"],
            ))
            window.wait_for_rebuild()
            presentation = window._presentations["Box"]
            viewport = window.stage.viewport
            viewport.select_shape(presentation)
            window.selection.refresh()
            viewport.setFocus()

            QTest.keyClick(viewport, Qt.Key_C, Qt.ControlModifier)
            QApplication.processEvents()
            REPORT["custom_clipboard"] = QApplication.clipboard().mimeData().hasFormat(
                MODEL_CLIPBOARD_MIME
            )

            QTest.keyClick(viewport, Qt.Key_V, Qt.ControlModifier)
            window.wait_for_rebuild()
            QApplication.processEvents()
            REPORT["first_paste"] = set(window.document.bodies) == {"Box", "Box2"}

            QTest.keyClick(viewport, Qt.Key_V, Qt.ControlModifier)
            window.wait_for_rebuild()
            QApplication.processEvents()
            REPORT["second_paste"] = set(window.document.bodies) == {
                "Box", "Box2", "Box3",
            }

            from simplecad.kernel.occ import bounding_box

            left_edges = [
                bounding_box(window.document.body(name).shape)[0][0]
                for name in ("Box", "Box2", "Box3")
            ]
            REPORT["cascades_right"] = left_edges[0] < left_edges[1] < left_edges[2]
            REPORT["pasted_selected"] = set(window.selection.bodies) == {"Box3"}
            REPORT["new_feature_ids"] = len({f.id for f in window.document.features}) == len(
                window.document.features
            )
            window.stage.viewport.fit_all()
            window.set_hint("Ctrl+C / Ctrl+V: independent copies placed beside the model")

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(200, populate))
        return window

    run_and_capture(
        build, "/tmp/simplecad-copy-paste.png", settle_ms=8000, size=(1100, 760),
    )
    print("--- copy/paste ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    ok = all(value is True for value in REPORT.values()) and len(REPORT) == 6
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
