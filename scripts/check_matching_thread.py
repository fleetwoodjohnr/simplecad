#!/usr/bin/env python
"""Build a physical source thread, preview its mate, then create it."""

from __future__ import annotations

import sys

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}


def main() -> int:
    from PySide6.QtCore import QTimer

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def panel_named(window, name):
        return next(
            (widget for widget, _anchor in window.stage.overlays
             if type(widget).__name__ == name),
            None,
        )

    def build(_app):
        window = MainWindow(Mode.DARK)

        def populate() -> None:
            from simplecad.kernel.fasteners import MatchingNutFeature

            source = MatchingNutFeature(
                inputs={
                    "designation": "P8", "clearance": "loose",
                    "form": "printed", "left_hand": True,
                },
                outputs=["Nut"],
            )
            window.add_feature(source)
            window.wait_for_rebuild()
            REPORT["source_modelled"] = bool(
                source.inputs.get("thread_modelled")
                and window.document.threads_on("Nut")
            )
            window.stage.viewport.select_shape(window._presentations["Nut"])
            window.selection.refresh()
            window.activate_tool("matching_part")
            QTimer.singleShot(250, await_preview)

        attempts = {"n": 0}

        def await_preview() -> None:
            attempts["n"] += 1
            panel = panel_named(window, "MatchingPartPanel")
            if panel is None:
                REPORT["panel_open"] = False
                return
            REPORT["panel_open"] = True
            if not panel._preview_valid and attempts["n"] < 100:
                QTimer.singleShot(250, await_preview)
                return
            REPORT["preview_valid"] = panel._preview_valid
            REPORT["preview_visible"] = window.stage.viewport._ghost is not None
            REPORT["locked_size"] = (
                panel.sizes.currentData() == "P8" and not panel.sizes.isEnabled()
            )
            REPORT["compatible_kind"] = (
                panel.kind == "bolt"
                and panel._buttons["bolt"].isEnabled()
                and not panel._buttons["nut"].isEnabled()
                and not panel._buttons["hole"].isEnabled()
            )
            REPORT["preview_error"] = panel.status.text()
            if not panel._preview_valid:
                return

            panel.commit()
            window.wait_for_rebuild()
            bolt = window.document.body("Bolt")
            REPORT["mate_created"] = bolt is not None and bolt.shape is not None
            thread = window.document.threads_on("Bolt")
            REPORT["mate_metadata"] = bool(
                thread
                and thread[0]["designation"] == "P8"
                and thread[0]["internal"] is False
                and thread[0]["left_hand"] is True
                and thread[0]["form"] == "printed"
            )
            from simplecad.kernel.occ import bounding_box

            nut_high = bounding_box(window.document.body("Nut").shape)[1][0]
            bolt_low = bounding_box(bolt.shape)[0][0]
            REPORT["mate_beside_source"] = bolt_low > nut_high
            window.stage.viewport.fit_all()
            window.set_hint("Physical P8 left-hand matching bolt created beside its nut")

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(200, populate))
        return window

    run_and_capture(
        build, "/tmp/simplecad-matching-thread.png",
        settle_ms=45000, size=(1200, 820),
    )
    print("--- matching thread ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    boolean_keys = (
        "source_modelled", "panel_open", "preview_valid", "preview_visible",
        "locked_size", "compatible_kind", "mate_created", "mate_metadata",
        "mate_beside_source",
    )
    ok = all(REPORT.get(key) is True for key in boolean_keys)
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
