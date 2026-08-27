#!/usr/bin/env python
"""Drive a real click in the viewport and capture the contextual bar.

Exercises the actual selection path -- OCCT picking, the selection model, the
reference builder and the contextual actions -- rather than faking a selection.
"""

from __future__ import annotations

import sys

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}


def main() -> int:
    from PySide6.QtCore import QPoint, Qt, QTimer
    from PySide6.QtTest import QTest

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def build(_app):
        window = MainWindow(Mode.DARK)

        def populate() -> None:
            from simplecad.kernel.primitives import BoxFeature, CylinderFeature

            window.add_feature(
                BoxFeature(inputs={"width": 60, "depth": 40, "height": 18},
                           outputs=["Base"])
            )
            window.add_feature(
                CylinderFeature(
                    inputs={"radius": 6, "height": 24, "x": 30, "y": 20, "z": 18},
                    outputs=["Post"],
                )
            )
            window.wait_for_rebuild()
            window.stage.viewport.fit_all()
            QTimer.singleShot(500, click_model)

        def click_model() -> None:
            viewport = window.stage.viewport
            centre = viewport.rect().center() + QPoint(-40, 60)
            QTest.mouseMove(viewport, centre)
            QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, centre)
            REPORT["selected"] = window.selection.count
            REPORT["summary"] = window.selection.summary()
            REPORT["bodies"] = window.selection.bodies
            from simplecad.ui.selection import available_actions
            REPORT["actions"] = [a[1] for a in available_actions(window.selection)]
            REPORT["bar_visible"] = window.context_bar.isVisible()

        window.stage.viewport.ready.connect(populate)
        return window

    out = sys.argv[1] if len(sys.argv) > 1 else "docs/shots/app_selection.png"
    result = run_and_capture(build, out, settle_ms=2800, size=(1500, 940))
    print("--- selection shot ---")
    for key, value in {**REPORT, **result}.items():
        print(f"  {key}: {value}")
    return 0 if REPORT.get("selected") else 1


if __name__ == "__main__":
    sys.exit(main())
