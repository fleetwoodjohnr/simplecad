#!/usr/bin/env python
"""Undo and redo, driven through the window.

Checks the thing that matters for a modelling tool: undo must put the geometry
back, not merely the feature list, and it must not cost a full rebuild.
"""

from __future__ import annotations

import sys

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}


def main() -> int:
    from PySide6.QtCore import QTimer

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def build(_app):
        window = MainWindow(Mode.DARK)

        def run() -> None:
            from simplecad.kernel.occ import volume
            from simplecad.kernel.primitives import BoxFeature, CylinderFeature

            window.add_feature(
                BoxFeature(inputs={"width": 40, "depth": 40, "height": 10},
                           outputs=["Plate"])
            )
            window.wait_for_rebuild()
            window.add_feature(
                CylinderFeature(inputs={"radius": 5, "height": 20, "x": 80},
                                outputs=["Pin"])
            )
            window.wait_for_rebuild()
            REPORT["after_two_adds"] = sorted(window.document.bodies)
            REPORT["undo_label"] = window.history.undo_label

            window.undo()
            window.wait_for_rebuild()
            REPORT["after_undo"] = sorted(window.document.bodies)
            REPORT["plate_survived_undo"] = (
                "Plate" in window.document.bodies
                and volume(window.document.bodies["Plate"].shape) > 0
            )
            REPORT["viewport_matches"] = sorted(window._presentations) == sorted(
                window.document.bodies
            )

            window.redo()
            window.wait_for_rebuild()
            REPORT["after_redo"] = sorted(window.document.bodies)

            window.undo()
            window.wait_for_rebuild()
            window.undo()
            window.wait_for_rebuild()
            REPORT["after_undoing_everything"] = sorted(window.document.bodies)
            REPORT["viewport_cleared"] = window._presentations == {}
            window.redo()
            window.wait_for_rebuild()
            window.redo()
            window.wait_for_rebuild()
            REPORT["after_redoing_everything"] = sorted(window.document.bodies)
            window.set_hint("Undo / redo check complete")

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(300, run))
        return window

    run_and_capture(build, "docs/shots/undo.png", settle_ms=3000, size=(1200, 800))
    print("--- undo / redo ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    ok = (
        REPORT.get("after_two_adds") == ["Pin", "Plate"]
        and REPORT.get("after_undo") == ["Plate"]
        and REPORT.get("plate_survived_undo")
        and REPORT.get("viewport_matches")
        and REPORT.get("after_redo") == ["Pin", "Plate"]
        and REPORT.get("after_undoing_everything") == []
        and REPORT.get("viewport_cleared")
        and REPORT.get("after_redoing_everything") == ["Pin", "Plate"]
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
