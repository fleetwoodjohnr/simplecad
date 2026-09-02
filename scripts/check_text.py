#!/usr/bin/env python
"""Raised and engraved text through the real selection-driven UI."""

from __future__ import annotations

import os
import sys

os.environ["SIMPLECAD_NO_GRID"] = "1"

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}


def panel(window):
    return next(
        (widget for widget, _anchor in window.stage.overlays
         if type(widget).__name__ == "TextPanel"),
        None,
    )


def main() -> int:
    from PySide6.QtCore import QTimer

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def build(_app):
        window = MainWindow(Mode.DARK)

        def populate() -> None:
            from simplecad.kernel.primitives import BoxFeature

            window.add_feature(BoxFeature(
                inputs={"width": 70, "depth": 32, "height": 8},
                outputs=["Raised plate"],
            ))
            window.add_feature(BoxFeature(
                inputs={"width": 70, "depth": 32, "height": 8, "y": 48},
                outputs=["Engraved plate"],
            ))
            window.wait_for_rebuild()
            choose("Raised plate", "RAISED", "raised", raised_ready)

        def choose(body_name, content, mode, callback) -> None:
            from simplecad.core.naming import fingerprint, sub_shapes
            from simplecad.kernel.detect import analyse_plane
            from simplecad.ui.selection import Picked

            shape = window.document.bodies[body_name].shape
            top = max(
                (face for face in sub_shapes(shape, "face")
                 if (mark := fingerprint(face, "face")).direction
                 and mark.direction[2] > 0.9),
                key=lambda face: fingerprint(face, "face").center[2],
            )
            window.selection.picks = [Picked(
                body=body_name, kind="face", shape=top,
                presentation=window._presentations.get(body_name),
                info=analyse_plane(top),
            )]
            window.activate_tool("text")
            tool = panel(window)
            if tool is None:
                REPORT[f"{mode}_panel"] = False
                return
            REPORT[f"{mode}_panel"] = True
            tool.content.setText(content)
            tool._choose_mode(mode)
            tool.preview()
            QTimer.singleShot(4500, lambda: callback(tool))

        def raised_ready(tool) -> None:
            REPORT["raised_preview"] = tool._preview_valid
            if tool._preview_valid:
                tool.commit()
                window.wait_for_rebuild()
            choose("Engraved plate", "ENGRAVED", "engraved", engraved_ready)

        def engraved_ready(tool) -> None:
            from simplecad.kernel.occ import bounding_box, is_valid, volume

            REPORT["engraved_preview"] = tool._preview_valid
            if tool._preview_valid:
                tool.commit()
                window.wait_for_rebuild()
            raised = window.document.bodies["Raised plate"].shape
            engraved = window.document.bodies["Engraved plate"].shape
            REPORT["raised_valid"] = is_valid(raised)
            REPORT["engraved_valid"] = is_valid(engraved)
            REPORT["raised_top"] = round(bounding_box(raised)[1][2], 2)
            REPORT["raised_added"] = round(volume(raised) - 70 * 32 * 8, 2)
            REPORT["engraved_removed"] = round(70 * 32 * 8 - volume(engraved), 2)
            window.stage.viewport.fit_all()
            window.set_hint("Raised and engraved text are both parametric features")

        window.stage.viewport.ready.connect(populate)
        return window

    run_and_capture(build, "docs/shots/text.png", settle_ms=14000, size=(1200, 820))
    print("--- face-attached text ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    ok = (
        REPORT.get("raised_panel") is True
        and REPORT.get("engraved_panel") is True
        and REPORT.get("raised_preview") is True
        and REPORT.get("engraved_preview") is True
        and REPORT.get("raised_valid") is True
        and REPORT.get("engraved_valid") is True
        and REPORT.get("raised_top") == 9.0
        and REPORT.get("raised_added", 0) > 0
        and REPORT.get("engraved_removed", 0) > 0
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
