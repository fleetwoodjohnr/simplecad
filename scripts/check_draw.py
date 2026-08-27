#!/usr/bin/env python
"""Drawing a sketch with the mouse.

Clicks land on the sketch plane, geometry follows the cursor, and constraints
are inferred as lines are drawn. This drives real mouse events through the
viewport and checks the sketch that comes out -- the geometry, the inferred
constraints, and that it extrudes.
"""

from __future__ import annotations

import sys

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}


def main() -> int:
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
            REPORT["in_sketch_mode"] = viewport.sketching

            ratio = viewport.devicePixelRatioF()

            def screen(u, v) -> QPoint:
                """Where a sketch coordinate lands on screen."""
                x, y = viewport.view.Convert(*window.canvas.sketch.plane.to_3d(u, v))
                return QPoint(round(x / ratio), round(y / ratio))

            def click(u, v) -> None:
                position = screen(u, v)
                QTest.mouseMove(viewport, position)
                QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, position)

            # Round-trip a coordinate to prove the plane mapping is right.
            # Accuracy is bounded by the pixel: a click cannot resolve finer
            # than one, and with an empty scene the camera is zoomed far out, so
            # a pixel can be worth more than a millimetre. Measure that scale
            # rather than assuming it.
            _axis, mm_per_pixel = viewport.screen_axis_for((0, 0, 0), (1, 0, 0))
            REPORT["mm_per_pixel"] = round(mm_per_pixel, 3)
            probe = viewport.sketch_point(screen(12.0, -7.0))
            REPORT["probe"] = None if probe is None else (
                round(probe[0], 2), round(probe[1], 2)
            )
            allowance = max(2.0 * mm_per_pixel, 0.05)
            REPORT["coordinate_round_trip"] = (
                probe is not None
                and abs(probe[0] - 12.0) <= allowance
                and abs(probe[1] + 7.0) <= allowance
            )

            # Draw a rectangle by dragging out two corners.
            window.canvas.set_tool("rectangle")
            click(0, 0)
            click(40, 25)
            sketch = window.canvas.sketch
            REPORT["rectangle_lines"] = len(sketch.entities)
            REPORT["rectangle_points"] = len(sketch.points)

            # A deliberately not-quite-horizontal line must still be constrained.
            window.canvas.set_tool("line")
            click(0, -20)
            click(40, -20.4)          # 0.6 degrees off horizontal
            inferred = [c.kind for c in sketch.constraints if c.kind == "horizontal"]
            REPORT["horizontal_inferred"] = len(inferred) >= 3

            # A circle, drawn centre-then-radius.
            window.canvas.set_tool("circle")
            click(20, 12)
            click(28, 12)
            circles = [e for e in sketch.entities.values() if e.kind == "circle"]
            REPORT["circle_radius"] = round(circles[0].radius, 2) if circles else None

            from OCP.BRepGProp import BRepGProp
            from OCP.GProp import GProp_GProps
            from simplecad.sketch.to_occ import profile_face, wires

            REPORT["closed_wires"] = len(wires(sketch))
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(profile_face(sketch), props)
            REPORT["profile_area"] = round(props.Mass(), 2)
            REPORT["rect_corners"] = [
                (round(pt.x, 2), round(pt.y, 2)) for pt in list(sketch.points.values())[:4]
            ]
            REPORT["state"] = window.canvas.last_result.state.value
            REPORT["entities"] = len(sketch.entities)

            # Finish, and turn the drawn profile into a solid.
            window.end_sketch(commit=True)
            REPORT["sketch_features"] = [
                f.type_name for f in window.document.features
            ]

            from simplecad.kernel.sketch_features import ExtrudeFeature

            name = window.document.features[-1].name
            window.add_feature(
                ExtrudeFeature(inputs={"sketch": name, "distance": 6},
                               outputs=["Drawn"])
            )
            window.wait_for_rebuild()

            from simplecad.kernel.occ import is_valid, volume

            body = window.document.bodies.get("Drawn")
            REPORT["extruded"] = body is not None and is_valid(body.shape)
            REPORT["volume"] = round(volume(body.shape), 1) if body else None
            from simplecad.ui.viewport.occt_view import StandardView

            window.stage.viewport.set_standard_view(StandardView.ISO)
            window.set_hint("Drawn with the mouse, then extruded")

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(500, run))
        return window

    run_and_capture(build, "docs/shots/draw.png", settle_ms=6000, size=(1400, 900))
    print("--- drawing a sketch ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    # The rectangle profile is 40x25 with a 8mm-radius circle cut from it.
    # The volume must match the profile that was actually drawn. Asserting a
    # nominal 40x25 would be testing the mouse, not the software -- a click can
    # only be as precise as a pixel.
    area = REPORT.get("profile_area", 0)
    expected = area * 6
    ok = (
        REPORT.get("in_sketch_mode")
        and REPORT.get("coordinate_round_trip")
        and REPORT.get("rectangle_lines") == 4
        and REPORT.get("horizontal_inferred")
        and REPORT.get("circle_radius") == 8.0
        and REPORT.get("closed_wires") == 2      # rectangle and circle only
        and REPORT.get("extruded")
        and expected > 0
        and abs(REPORT.get("volume", 0) - expected) / expected < 0.005
    )
    print(f"  expected volume (profile area x 6): {expected:.1f}")
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
