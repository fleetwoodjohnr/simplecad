#!/usr/bin/env python
"""Vent: a hex grille, both ways round, and timed.

The plate case makes a fan grille as its own part. The cut case perforates a
wall on a body that already exists. Both are timed, because the failure mode
that matters here is not a wrong answer -- it is cutting a hundred holes one at
a time and taking minutes over it.
"""

from __future__ import annotations

import os
import sys
import time

os.environ["SIMPLECAD_NO_GRID"] = "1"

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}
#: A single boolean over a compound is seconds. One cut per hole is minutes.
BUDGET_S = 20.0


def main() -> int:
    from PySide6.QtCore import QTimer

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def build(_app):
        window = MainWindow(Mode.DARK)

        def populate() -> None:
            from simplecad.kernel.primitives import BoxFeature

            # An enclosure wall to perforate.
            window.add_feature(
                BoxFeature(inputs={"width": 80, "depth": 80, "height": 4},
                           outputs=["Panel"])
            )
            window.wait_for_rebuild()
            window.stage.viewport.fit_all()
            QTimer.singleShot(400, run)

        def run() -> None:
            from simplecad.core.naming import fingerprint, sub_shapes
            from simplecad.kernel.detect import analyse_plane
            from simplecad.kernel.occ import is_valid, volume
            from simplecad.kernel.vent import hex_positions
            from simplecad.ui.selection import Picked

            # --- 1. the cutter: vent an existing wall -------------------
            shape = window.document.bodies["Panel"].shape
            before = volume(shape)
            top = max(
                (f for f in sub_shapes(shape, "face")
                 if (p := fingerprint(f, "face")).direction and p.direction[2] > 0.9),
                key=lambda f: fingerprint(f, "face").center[2],
            )
            window.selection.picks = [
                Picked(body="Panel", kind="face", shape=top,
                       presentation=window._presentations.get("Panel"),
                       info=analyse_plane(top))
            ]
            window.activate_tool("vent")
            panel = next(
                (w for w, _a in window.stage.overlays
                 if type(w).__name__ == "VentPanel"), None
            )
            REPORT["cutter_chosen_from_selection"] = panel is not None
            if panel is None:
                return
            start = time.perf_counter()
            panel.commit()
            window.wait_for_rebuild()
            REPORT["cut_seconds"] = round(time.perf_counter() - start, 2)

            cut = window.document.bodies["Panel"].shape
            REPORT["cut_valid"] = is_valid(cut)
            REPORT["cut_removed_mm3"] = round(before - volume(cut), 1)
            REPORT["cut_feature"] = window.document.features[-1].type_name

            # --- 2. the plate: a grille as its own part -----------------
            window.cancel_tool()
            window.stage.viewport.clear_selection()
            window.selection.picks = []
            window.activate_tool("vent")
            shape_panel = next(
                (w for w, _a in window.stage.overlays
                 if type(w).__name__ == "ShapePanel"), None
            )
            REPORT["plate_chosen_without_selection"] = shape_panel is not None
            if shape_panel is None:
                return
            REPORT["plate_tile"] = shape_panel._kind
            start = time.perf_counter()
            shape_panel.commit()
            window.wait_for_rebuild()
            REPORT["plate_seconds"] = round(time.perf_counter() - start, 2)

            names = [n for n in window.document.bodies if n != "Panel"]
            REPORT["plate_body"] = names[0] if names else None
            if names:
                plate = window.document.bodies[names[0]].shape
                REPORT["plate_valid"] = is_valid(plate)
                REPORT["plate_holes"] = len(hex_positions(60, 60, 5, 1.2, 2))
                REPORT["plate_volume"] = round(volume(plate), 1)
            window.stage.viewport.fit_all()
            window.set_hint("Vented a panel, and made a grille of its own")

        window.stage.viewport.ready.connect(populate)
        return window

    run_and_capture(build, "docs/shots/vent.png", settle_ms=30000, size=(1200, 820))
    print("--- vent: cutter and plate ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    ok = (
        REPORT.get("cutter_chosen_from_selection")
        and REPORT.get("cut_valid")
        and REPORT.get("cut_feature") == "vent_cut"
        and REPORT.get("cut_removed_mm3", 0) > 1000
        and REPORT.get("cut_seconds", 999) < BUDGET_S
        and REPORT.get("plate_chosen_without_selection")
        and REPORT.get("plate_tile") == "vent_plate"
        and REPORT.get("plate_valid")
        and REPORT.get("plate_seconds", 999) < BUDGET_S
    )
    print(f"  budget: {BUDGET_S:.0f}s each")
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
