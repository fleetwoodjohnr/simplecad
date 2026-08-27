#!/usr/bin/env python
"""Typing into a field must not trigger single-letter shortcuts.

Qt's shortcut map consumes matching keystrokes before the focused widget sees
them, so `S`, `F` and the digits `1`-`7` can swallow characters mid-expression.
Nothing that sets text with setText() can catch this -- it only shows up when
real key events are delivered, which is what this does.
"""

from __future__ import annotations

import sys

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}


def main() -> int:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtTest import QTest

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def build(_app):
        window = MainWindow(Mode.DARK)

        def run() -> None:
            window.document.parameters.set("wall", "3 mm")
            window.activate_tool("shapes")
            panel = next(
                w for w, _a in window.stage.overlays
                if getattr(w, "is_tool_panel", False)
            )
            field = panel.fields["width"]

            # Typing digits must land in the field, not move the camera.
            before = window.stage.viewport.view.Proj()
            field.setFocus()
            field.clear()
            QTest.keyClicks(field, "50")
            REPORT["digits_typed"] = field.text()
            after = window.stage.viewport.view.Proj()
            REPORT["camera_unmoved"] = tuple(round(v, 6) for v in before) == tuple(
                round(v, 6) for v in after
            )

            # An expression containing 's' must not open command search.
            field.clear()
            QTest.keyClicks(field, "wall*2 + sin(30)")
            REPORT["expression_typed"] = field.text()
            REPORT["search_did_not_open"] = getattr(window, "_search", None) is None

            # And the shortcuts must still work when not typing.
            window.stage.viewport.setFocus()
            QTest.keyClick(window, Qt.Key_S)
            REPORT["search_opens_when_not_typing"] = (
                getattr(window, "_search", None) is not None
            )
            window.close_search()
            window.cancel_tool()

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(400, run))
        return window

    run_and_capture(build, "docs/shots/typing.png", settle_ms=2600, size=(1200, 800))
    print("--- typing vs shortcuts ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value!r}")
    ok = (
        REPORT.get("digits_typed") == "50"
        and REPORT.get("camera_unmoved")
        and REPORT.get("expression_typed") == "wall*2 + sin(30)"
        and REPORT.get("search_did_not_open")
        and REPORT.get("search_opens_when_not_typing")
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
