#!/usr/bin/env python
"""Direct manipulation: select a face, drag it, watch the model change.

The spec's core interaction is Select -> Drag -> Snap -> Type -> Done. This
drives a real mouse press-move-release over a selected face and checks the
geometry actually changed by the dragged amount.
"""

from __future__ import annotations

import os
import sys

# This check counts the objects in the AIS context, so the scene has to hold
# nothing but the model, the ViewCube and any preview under test.
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
                BoxFeature(inputs={"width": 50, "depth": 50, "height": 10},
                           outputs=["Plate"])
            )
            window.wait_for_rebuild()
            window.stage.viewport.fit_all()
            QTimer.singleShot(500, drag)

        def drag() -> None:
            from simplecad.core.naming import fingerprint, sub_shapes
            from simplecad.kernel.detect import analyse_plane
            from simplecad.kernel.occ import bounding_box, volume
            from simplecad.ui.selection import Picked

            viewport = window.stage.viewport
            shape = window.document.bodies["Plate"].shape
            top = max(
                (f for f in sub_shapes(shape, "face")
                 if (p := fingerprint(f, "face")).direction and p.direction[2] > 0.9),
                key=lambda f: fingerprint(f, "face").center[2],
            )
            # Select the top face the way a click would.
            window.selection.picks = [
                Picked(body="Plate", kind="face", shape=top,
                       presentation=window._presentations.get("Plate"),
                       info=analyse_plane(top))
            ]
            REPORT["height_before"] = round(
                bounding_box(shape)[1][2] - bounding_box(shape)[0][2], 3
            )

            # Where is that face on screen, and which way does +Z drag?
            centre = analyse_plane(top).center
            ratio = viewport.devicePixelRatioF()
            x, y = viewport.view.Convert(*centre)
            start = QPoint(int(x / ratio), int(y / ratio))
            axis, scale = viewport.screen_axis_for(centre, (0.0, 0.0, 1.0))
            REPORT["mm_per_pixel"] = round(scale, 4)

            # Drag 8 mm worth of pixels along the face normal.
            target_mm = 8.0
            pixels = target_mm / scale / ratio
            end = QPoint(int(start.x() + axis[0] * pixels),
                         int(start.y() + axis[1] * pixels))

            QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, start)
            window._maybe_start_face_drag()
            REPORT["drag_started"] = viewport._drag is not None
            QTest.mouseMove(viewport, end)
            REPORT["ghost_shown"] = getattr(viewport, "_ghost", None) is not None
            QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, end)
            window.wait_for_rebuild()

            after = window.document.bodies["Plate"].shape
            REPORT["height_after"] = round(
                bounding_box(after)[1][2] - bounding_box(after)[0][2], 3
            )
            REPORT["volume_after"] = round(volume(after), 1)
            REPORT["feature_added"] = window.document.features[-1].type_name
            REPORT["undo_available"] = window.history.can_undo
            REPORT["ghost_cleared"] = getattr(viewport, "_ghost", None) is None

            from OCP.AIS import AIS_ListOfInteractive

            displayed = AIS_ListOfInteractive()
            viewport.context.DisplayedObjects(displayed)
            # One body + the ViewCube. Anything more means a leftover preview.
            REPORT["objects_displayed"] = sum(1 for _ in displayed)
            viewport.fit_all()
            window.set_hint("Dragged the top face up by 8 mm")

        window.stage.viewport.ready.connect(populate)
        return window

    run_and_capture(build, "docs/shots/drag.png", settle_ms=3600, size=(1200, 820))
    print("--- drag to pull ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    grew = REPORT.get("height_after", 0) - REPORT.get("height_before", 0)
    ok = (
        REPORT.get("drag_started")
        and REPORT.get("ghost_shown")
        and REPORT.get("feature_added") == "push_pull"
        and abs(grew - 8.0) < 0.6            # a pixel of rounding is expected
        and REPORT.get("ghost_cleared")
        and REPORT.get("objects_displayed") == 2   # the body and the ViewCube
    )
    print(f"  grew_by: {grew:.2f} mm (asked for 8.00)")
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
