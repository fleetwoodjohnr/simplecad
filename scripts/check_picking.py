#!/usr/bin/env python
"""Face, edge and vertex picking must each win where they should.

Activating a smaller-dimension selection mode raises its pick priority in OCCT,
so adding vertices can quietly make faces unselectable. This clicks three known
points on a cube -- a face centre, an edge midpoint, a corner -- and checks each
returns what a person would expect.

Run:  .venv/bin/python scripts/check_picking.py
"""

from __future__ import annotations

import sys

from _harness import run_and_capture  # noqa: E402

RESULT: dict[str, list[str]] = {}


def main() -> int:
    from PySide6.QtCore import QPoint, Qt, QTimer
    from PySide6.QtTest import QTest

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def build(_app):
        window = MainWindow(Mode.DARK)

        def populate() -> None:
            from simplecad.kernel.primitives import BoxFeature

            window.add_feature(
                BoxFeature(inputs={"width": 50, "depth": 50, "height": 50},
                           outputs=["Cube"])
            )
            window.wait_for_rebuild()
            window.stage.viewport.fit_all()
            QTimer.singleShot(600, probe)

        def probe() -> None:
            viewport = window.stage.viewport
            view = viewport.view
            ratio = viewport.devicePixelRatioF()

            def to_screen(point):
                x, y = view.Convert(point[0], point[1], point[2])
                return QPoint(int(x / ratio), int(y / ratio))

            for label, point in (
                ("face", (25.0, 25.0, 50.0)),
                ("edge", (25.0, 0.0, 50.0)),
                ("vertex", (0.0, 0.0, 50.0)),
            ):
                position = to_screen(point)
                QTest.mouseMove(viewport, position)
                QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, position)
                window.selection.refresh()
                RESULT[label] = [p.kind for p in window.selection.picks]

        window.stage.viewport.ready.connect(populate)
        return window

    run_and_capture(build, "docs/shots/picking.png", settle_ms=3400, size=(1100, 760))
    print("--- picking ---")
    ok = True
    for expected, got in RESULT.items():
        good = got == [expected]
        ok = ok and good
        print(f"  click on a {expected:7s} -> {got} {'' if good else '  <-- wrong'}")
    if not RESULT:
        ok = False
        print("  no clicks registered")
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
