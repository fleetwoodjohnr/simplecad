#!/usr/bin/env python
"""Pushing a face in: the preview has to be visible and the number has to be live.

Dragging a face *outward* was already covered by check_drag.py. Inward was not,
and it is the direction where everything is harder: the slab being removed sits
inside the solid, so the preview is hidden unless the body gets out of its own
way, and the useful number is the size the part ends up rather than how far the
mouse has travelled.
"""

from __future__ import annotations

import os
import sys

# This check counts objects in the AIS context, so the scene must hold only the
# model, the ViewCube and the preview under test.
os.environ["SIMPLECAD_NO_GRID"] = "1"

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
            from simplecad.kernel.primitives import BoxFeature

            window.add_feature(
                BoxFeature(inputs={"width": 50, "depth": 50, "height": 20},
                           outputs=["Plate"])
            )
            window.wait_for_rebuild()
            window.stage.viewport.fit_all()
            QTimer.singleShot(500, push)

        def push() -> None:
            from simplecad.core.naming import fingerprint, sub_shapes
            from simplecad.kernel.detect import analyse_plane
            from simplecad.kernel.occ import bounding_box, is_valid
            from simplecad.ui.selection import Picked

            viewport = window.stage.viewport
            readout = window.stage.drag_readout
            shape = window.document.bodies["Plate"].shape
            top = max(
                (f for f in sub_shapes(shape, "face")
                 if (p := fingerprint(f, "face")).direction and p.direction[2] > 0.9),
                key=lambda f: fingerprint(f, "face").center[2],
            )
            window.selection.picks = [
                Picked(body="Plate", kind="face", shape=top,
                       presentation=window._presentations.get("Plate"),
                       info=analyse_plane(top))
            ]
            REPORT["height_before"] = round(
                bounding_box(shape)[1][2] - bounding_box(shape)[0][2], 3
            )

            centre = analyse_plane(top).center
            ratio = viewport.devicePixelRatioF()
            x, y = viewport.view.Convert(*centre)
            start = QPoint(int(x / ratio), int(y / ratio))
            axis, scale = viewport.screen_axis_for(centre, (0.0, 0.0, 1.0))

            # Negative: push the top face down 6 mm into the solid.
            target_mm = -6.0
            pixels = target_mm / scale / ratio
            end = QPoint(int(start.x() + axis[0] * pixels),
                         int(start.y() + axis[1] * pixels))

            QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, start)
            window._maybe_start_face_drag()
            QTest.mouseMove(viewport, end)

            # Mid-drag: preview visible, body see-through, number on screen.
            REPORT["ghost_shown"] = getattr(viewport, "_ghost", None) is not None
            REPORT["body_transparent"] = window._drag_transparent == "Plate"
            REPORT["readout_visible"] = readout.isVisible()
            REPORT["readout_size"] = readout.size_label.text()
            REPORT["readout_delta"] = readout.delta_label.text()

            QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, end)
            window.wait_for_rebuild()

            after = window.document.bodies["Plate"].shape
            REPORT["height_after"] = round(
                bounding_box(after)[1][2] - bounding_box(after)[0][2], 3
            )
            REPORT["valid"] = is_valid(after)
            REPORT["feature_added"] = window.document.features[-1].type_name
            # Everything temporary must be put back.
            REPORT["ghost_cleared"] = getattr(viewport, "_ghost", None) is None
            REPORT["readout_hidden"] = not readout.isVisible()
            REPORT["transparency_restored"] = window._drag_transparent is None

            from OCP.AIS import AIS_ListOfInteractive

            displayed = AIS_ListOfInteractive()
            viewport.context.DisplayedObjects(displayed)
            REPORT["objects_displayed"] = sum(1 for _ in displayed)
            viewport.fit_all()
            window.set_hint("Pushed the top face down by 6 mm")

        window.stage.viewport.ready.connect(populate)
        return window

    run_and_capture(build, "docs/shots/push.png", settle_ms=3600, size=(1200, 820))
    print("--- push a face inward ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    shrank = REPORT.get("height_before", 0) - REPORT.get("height_after", 0)
    ok = (
        REPORT.get("ghost_shown")
        and REPORT.get("body_transparent")          # preview must not be hidden
        and REPORT.get("readout_visible")
        and "Height" in str(REPORT.get("readout_size"))
        and "−" in str(REPORT.get("readout_delta"))  # a cut, shown as a cut
        and REPORT.get("feature_added") == "push_pull"
        and abs(shrank - 6.0) < 0.6
        and REPORT.get("valid")
        and REPORT.get("ghost_cleared")
        and REPORT.get("readout_hidden")
        and REPORT.get("transparency_restored")
        and REPORT.get("objects_displayed") == 2
    )
    print(f"  shrank_by: {shrank:.2f} mm (asked for 6.00)")
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
