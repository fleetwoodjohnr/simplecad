#!/usr/bin/env python
"""Hollow: turn a box into a bucket, through the UI.

Select the top face, set a wall thickness, apply -- and get a container with
walls of exactly that thickness and its outside dimensions untouched.
"""

from __future__ import annotations

import os
import sys

os.environ["SIMPLECAD_NO_GRID"] = "1"

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}


def main() -> int:
    from PySide6.QtCore import QTimer

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    WIDTH, DEPTH, HEIGHT, WALL = 60.0, 45.0, 40.0, 5.0

    def build(_app):
        window = MainWindow(Mode.DARK)

        def populate() -> None:
            from simplecad.kernel.primitives import BoxFeature

            window.add_feature(
                BoxFeature(inputs={"width": WIDTH, "depth": DEPTH, "height": HEIGHT},
                           outputs=["Bucket"])
            )
            window.wait_for_rebuild()
            window.stage.viewport.fit_all()
            QTimer.singleShot(400, hollow)

        def hollow() -> None:
            from simplecad.core.naming import fingerprint, sub_shapes
            from simplecad.kernel.detect import analyse_plane
            from simplecad.kernel.occ import bounding_box, is_valid, volume
            from simplecad.ui.selection import Picked

            shape = window.document.bodies["Bucket"].shape
            REPORT["volume_before"] = round(volume(shape), 1)

            top = max(
                (f for f in sub_shapes(shape, "face")
                 if (p := fingerprint(f, "face")).direction and p.direction[2] > 0.9),
                key=lambda f: fingerprint(f, "face").center[2],
            )
            window.selection.picks = [
                Picked(body="Bucket", kind="face", shape=top,
                       presentation=window._presentations.get("Bucket"),
                       info=analyse_plane(top))
            ]

            # Drive the real panel: this is the path a user takes.
            window.activate_tool("shell")
            panel = next(
                (w for w, _a in window.stage.overlays
                 if type(w).__name__ == "ShellPanel"), None
            )
            REPORT["panel_title"] = panel.title if panel else None
            REPORT["field_label"] = (
                "thickness" in panel.fields if panel else None
            )
            panel.fields["thickness"].setText(str(WALL))
            panel.commit()
            window.wait_for_rebuild()

            after = window.document.bodies["Bucket"].shape
            REPORT["valid"] = is_valid(after)
            REPORT["volume_after"] = round(volume(after), 1)
            low, high = bounding_box(after)
            REPORT["outside"] = tuple(
                round(high[i] - low[i], 3) for i in range(3)
            )
            # A box hollowed from the top: outer solid minus the cavity.
            expected = (
                WIDTH * DEPTH * HEIGHT
                - (WIDTH - 2 * WALL) * (DEPTH - 2 * WALL) * (HEIGHT - WALL)
            )
            REPORT["expected_volume"] = round(expected, 1)
            REPORT["feature"] = window.document.features[-1].type_name
            window.stage.viewport.fit_all()
            window.set_hint(f"Hollowed to a {WALL:.0f} mm wall, open at the top")

        window.stage.viewport.ready.connect(populate)
        return window

    run_and_capture(build, "docs/shots/hollow.png", settle_ms=3000, size=(1200, 820))
    print("--- hollow a box into a bucket ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    ok = (
        REPORT.get("panel_title") == "Hollow"
        and REPORT.get("field_label")
        and REPORT.get("valid")
        and REPORT.get("feature") == "shell"
        and REPORT.get("outside") == (WIDTH, DEPTH, HEIGHT)
        and abs(REPORT.get("volume_after", 0) - REPORT["expected_volume"]) < 1.0
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
