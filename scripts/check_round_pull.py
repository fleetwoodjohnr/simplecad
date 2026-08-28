#!/usr/bin/env python
"""Pulling a round face: making a cylinder or a tube thinner by dragging it.

Push/Pull only ever worked on flat faces, so the only way to change the size of
a shaft was to delete it and model it again. This drives the real gesture --
press on the side of a cylinder and drag toward its axis -- and checks that the
part actually gets thinner, that the readout talks about the diameter rather
than about how far the mouse travelled, and, on a tube, that the bore survives.

The bore is the interesting half. A naive implementation turns a shaft down with
a solid cylinder, which fills the hole on the way past.
"""

from __future__ import annotations

import os
import sys

os.environ["SIMPLECAD_NO_GRID"] = "1"

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}


def main() -> int:
    from PySide6.QtCore import QPoint, Qt, QTimer
    from PySide6.QtTest import QTest

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    OUTER, BORE, HEIGHT, TAKE_OFF = 10.0, 6.0, 30.0, 2.0

    def build(_app):
        window = MainWindow(Mode.DARK)

        def populate() -> None:
            from simplecad.core.document import BodyRef
            from simplecad.kernel.operations import BooleanFeature
            from simplecad.kernel.primitives import CylinderFeature

            window.document.add_feature(
                CylinderFeature(
                    inputs={"radius": OUTER, "height": HEIGHT}, outputs=["Tube"]
                )
            )
            window.document.add_feature(
                CylinderFeature(
                    inputs={"radius": BORE, "height": HEIGHT}, outputs=["Bore"]
                )
            )
            window.document.add_feature(
                BooleanFeature(
                    inputs={
                        "body": BodyRef("Tube"), "tools": [BodyRef("Bore")],
                        "operation": "cut",
                    },
                    outputs=["Tube"],
                )
            )
            window.rebuild()
            window.wait_for_rebuild()
            window.stage.viewport.fit_all()
            QTimer.singleShot(600, run)

        def outer_face(shape):
            from simplecad.kernel.detect import analyse_cylinder, cylindrical_faces

            return max(
                (f for f, _i in cylindrical_faces(shape)
                 if not analyse_cylinder(f).internal),
                key=lambda f: analyse_cylinder(f).radius,
            )

        def radii(shape):
            from simplecad.kernel.detect import analyse_cylinder, cylindrical_faces

            return sorted(
                round(info.radius, 3) for _f, info in cylindrical_faces(shape)
            )

        def run() -> None:
            from simplecad.kernel.detect import analyse_cylinder
            from simplecad.kernel.occ import bounding_box, is_valid
            from simplecad.ui.selection import Picked, available_actions

            viewport = window.stage.viewport
            readout = window.stage.drag_readout
            shape = window.document.bodies["Tube"].shape
            REPORT["radii_before"] = radii(shape)
            low, high = bounding_box(shape)
            REPORT["height_before"] = round(high[2] - low[2], 3)

            face = outer_face(shape)
            info = analyse_cylinder(face)
            window.selection.picks = [
                Picked(body="Tube", kind="face", shape=face,
                       presentation=window._presentations.get("Tube"), info=info)
            ]
            REPORT["reads_as_shaft"] = not info.internal

            # The point on the shaft nearest the camera: that is the one a user
            # can actually see and press on, and its radial direction is the
            # one a mouse can push along.
            REPORT["offers_pull"] = "pushpull" in [
                key for key, _l, _i in available_actions(window.selection)
            ]

            eye = viewport.camera_state()[0]
            towards = (eye[0], eye[1], HEIGHT / 2.0)
            length = max(1e-9, (towards[0] ** 2 + towards[1] ** 2) ** 0.5)
            surface = (
                towards[0] / length * info.radius,
                towards[1] / length * info.radius,
                HEIGHT / 2.0,
            )
            radial = (towards[0] / length, towards[1] / length, 0.0)

            ratio = viewport.devicePixelRatioF()
            x, y = viewport.view.Convert(*surface)
            start = QPoint(int(x / ratio), int(y / ratio))
            axis, scale = viewport.screen_axis_for(surface, radial)
            pixels = -TAKE_OFF / scale / ratio          # inward: thinner
            end = QPoint(int(start.x() + axis[0] * pixels),
                         int(start.y() + axis[1] * pixels))

            QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, start)
            window._maybe_start_face_drag()
            QTest.mouseMove(viewport, end)

            REPORT["ghost_shown"] = getattr(viewport, "_ghost", None) is not None
            REPORT["readout_visible"] = readout.isVisible()
            REPORT["readout_size"] = readout.size_label.text()

            QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, end)
            window.wait_for_rebuild()

            after = window.document.bodies["Tube"].shape
            REPORT["radii_after"] = radii(after)
            REPORT["valid"] = is_valid(after)
            REPORT["feature_added"] = window.document.features[-1].type_name
            low, high = bounding_box(after)
            REPORT["height_after"] = round(high[2] - low[2], 3)
            REPORT["ghost_cleared"] = getattr(viewport, "_ghost", None) is None
            viewport.fit_all()
            window.set_hint(f"Turned the tube down by {TAKE_OFF:.0f} mm")

        window.stage.viewport.ready.connect(populate)
        return window

    run_and_capture(build, "docs/shots/round_pull.png",
                    settle_ms=3600, size=(1200, 820))
    print("--- pull a round face ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    before = REPORT.get("radii_before") or []
    after = REPORT.get("radii_after") or []
    ok = (
        REPORT.get("reads_as_shaft")
        and REPORT.get("offers_pull")
        and REPORT.get("ghost_shown")
        and REPORT.get("readout_visible")
        and "Diameter" in str(REPORT.get("readout_size"))
        and REPORT.get("feature_added") == "resize_round"
        and before == [BORE, OUTER]
        # Thinner outside, and the bore exactly where it was.
        and after and abs(after[-1] - (OUTER - TAKE_OFF)) < 0.4
        and abs(after[0] - BORE) < 1e-3
        # Only the radius moved: the part is as tall as it was. Compared
        # loosely on purpose -- an optimal bounding box is computed from the
        # tessellation and shifts by a few hundredths when the geometry does,
        # which says nothing about whether the part changed height.
        and abs(
            REPORT.get("height_after", 0) - REPORT.get("height_before", -1)
        ) < 0.05
        and REPORT.get("valid")
        and REPORT.get("ghost_cleared")
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
