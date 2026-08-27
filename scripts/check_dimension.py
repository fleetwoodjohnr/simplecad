#!/usr/bin/env python
"""On-canvas dimensions: D -> click -> type -> Enter.

Draws a rectangle, dimensions a side by clicking it, types a new value, and
checks the geometry actually resizes and the sketch reports as more constrained
than before.
"""

from __future__ import annotations

import sys

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}


def main() -> int:
    import math

    from PySide6.QtCore import QPoint, Qt, QTimer
    from PySide6.QtTest import QTest

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def build(_app):
        window = MainWindow(Mode.DARK)

        def run() -> None:
            from simplecad.sketch.sketch import SketchPlane

            viewport = window.stage.viewport
            window.begin_sketch(SketchPlane.named("XY"))
            canvas = window.canvas
            ratio = viewport.devicePixelRatioF()

            def screen(u, v) -> QPoint:
                x, y = viewport.view.Convert(*canvas.sketch.plane.to_3d(u, v))
                return QPoint(round(x / ratio), round(y / ratio))

            def click(u, v) -> None:
                position = screen(u, v)
                QTest.mouseMove(viewport, position)
                QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, position)

            canvas.set_tool("rectangle")
            click(0, 0)
            click(40, 25)

            def width_of() -> float:
                bottom = next(
                    e for e in canvas.sketch.entities.values()
                    if e.kind == "line"
                    and abs(canvas.sketch.points[e.start].y) < 1e-6
                    and abs(canvas.sketch.points[e.end].y) < 1e-6
                )
                a = canvas.sketch.points[bottom.start]
                b = canvas.sketch.points[bottom.end]
                return math.dist((a.x, a.y), (b.x, b.y)), bottom

            drawn_width, bottom = width_of()
            REPORT["drawn_width"] = round(drawn_width, 2)
            REPORT["dof_before"] = canvas.last_result.dof

            # D, then click the bottom edge: a dimension appears and opens for
            # editing.
            window._dimension_shortcut()
            REPORT["tool_is_dimension"] = canvas.state.tool == "dimension"
            midpoint = (
                (canvas.sketch.points[bottom.start].x
                 + canvas.sketch.points[bottom.end].x) / 2.0,
                0.0,
            )
            click(*midpoint)
            REPORT["labels"] = len(window.dimensions._labels)
            REPORT["editor_open"] = window.dimensions.editing

            # Type a new value and press Enter.
            editor = window.dimensions._editor
            editor.clear()
            QTest.keyClicks(editor, "62")
            QTest.keyClick(editor, Qt.Key_Return)

            new_width, _ = width_of()
            REPORT["width_after"] = round(new_width, 3)
            REPORT["dof_after"] = canvas.last_result.dof
            REPORT["state_after"] = canvas.last_result.state.value
            REPORT["editor_closed"] = not window.dimensions.editing
            REPORT["label_text"] = (
                window.dimensions._labels[0].text()
                if window.dimensions._labels else None
            )

            # An expression works too.
            window.set_hint("Dimensioned by clicking and typing")

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(500, run))
        return window

    run_and_capture(build, "docs/shots/dimension.png", settle_ms=5000, size=(1300, 860))
    print("--- on-canvas dimensions ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    ok = (
        REPORT.get("tool_is_dimension")
        and REPORT.get("labels", 0) >= 1
        and REPORT.get("editor_open")
        and REPORT.get("editor_closed")
        and abs(REPORT.get("width_after", 0) - 62.0) < 1e-3
        and REPORT.get("dof_after", 99) < REPORT.get("dof_before", 0)
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
