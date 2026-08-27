#!/usr/bin/env python
"""The features finished last, driven through the window.

Measure, sketching on a face, ellipse and spline, the model browser actions,
Hole "to object", and the combined Align & Thread command.
"""

from __future__ import annotations

import sys

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}


def main() -> int:
    import math

    from PySide6.QtCore import QTimer

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def build(_app):
        window = MainWindow(Mode.DARK)

        def run() -> None:
            from simplecad.core.naming import fingerprint, sub_shapes
            from simplecad.kernel.detect import analyse_plane
            from simplecad.kernel.occ import bounding_box, volume
            from simplecad.kernel.primitives import BoxFeature
            from simplecad.ui.selection import Picked

            # --- every rail tile resolves to a tool ---
            from simplecad.ui.main_window import TOOL_RAIL
            from simplecad.ui.tools.registry import TOOLS
            from simplecad.ui.tools import (  # noqa: F401
                matching, measuring, modeling, shapes, sketching,
            )
            from simplecad.ui.printws import fit_panel, panel  # noqa: F401

            REPORT["dead_rail_tiles"] = [
                key for _icon, _label, key in TOOL_RAIL if key not in TOOLS
            ]

            window.add_feature(
                BoxFeature(inputs={"width": 40, "depth": 30, "height": 20},
                           outputs=["Plate"])
            )
            window.wait_for_rebuild()
            shape = window.document.bodies["Plate"].shape
            top = max(
                sub_shapes(shape, "face"),
                key=lambda f: fingerprint(f, "face").center[2],
            )

            # --- Measure ---
            window.selection.picks = [
                Picked(body="Plate", kind="body", shape=shape,
                       presentation=window._presentations.get("Plate"))
            ]
            window.activate_tool("measure")
            measure_panel = next(
                w for w, _a in window.stage.overlays
                if getattr(w, "is_tool_panel", False)
            )
            REPORT["measured"] = measure_panel.measurement.headline.text
            window.cancel_tool()

            # --- Sketch on a face, drawing an ellipse ---
            window.selection.picks = [
                Picked(body="Plate", kind="face", shape=top,
                       presentation=window._presentations.get("Plate"),
                       info=analyse_plane(top))
            ]
            REPORT["sketch_on_face"] = window.sketch_on_selection()
            canvas = window.canvas
            canvas.grid = 0
            canvas.set_tool("ellipse")
            for point in ((0, 0), (12, 0), (0, 6)):
                canvas.click(*point)
            REPORT["ellipse_drawn"] = any(
                e.kind == "ellipse" for e in canvas.sketch.entities.values()
            )
            canvas.set_tool("spline")
            for point in ((-15, -10), (-5, -14), (5, -8), (15, -12)):
                canvas.click(*point)
            REPORT["spline_finished"] = canvas.finish_open_shape()
            window.end_sketch(commit=True)
            window.wait_for_rebuild()
            REPORT["sketch_feature"] = any(
                f.type_name == "sketch" for f in window.document.features
            )

            # --- Model browser: rename, duplicate, isolate ---
            window.rename_body("Plate", "Base")
            REPORT["renamed"] = "Base" in window.document.bodies
            REPORT["refs_follow_rename"] = all(
                "Plate" not in f.outputs for f in window.document.features
            )

            window.duplicate_body("Base")
            window.wait_for_rebuild()
            REPORT["bodies_after_duplicate"] = sorted(window.document.bodies)

            window.isolate_body("Base")
            REPORT["isolated"] = [
                b.name for b in window.document.bodies.values() if b.visible
            ]
            window.show_all_bodies()

            # --- Hole to object ---
            from simplecad.core.document import BodyRef
            from simplecad.core.naming import make_ref
            from simplecad.kernel.operations import HoleFeature

            base = window.document.bodies["Base"].shape
            base_top = max(
                sub_shapes(base, "face"),
                key=lambda f: fingerprint(f, "face").center[2],
            )
            producer = window.document.producer_of("Base")
            target = [n for n in window.document.bodies if n != "Base"][0]
            before = volume(base)
            window.document.add_feature(
                HoleFeature(
                    inputs={
                        "body": BodyRef("Base"),
                        "face": make_ref(base, base_top, producer.id, body="Base"),
                        "diameter": 8,
                        "depth_mode": "to_object",
                        "until": BodyRef(target),
                        "position": tuple(analyse_plane(base_top).center),
                    },
                    outputs=["Base"],
                )
            )
            window.rebuild()
            window.wait_for_rebuild()
            after = volume(window.document.bodies["Base"].shape)
            REPORT["hole_to_object_removed"] = round(before - after, 1)

            window.stage.viewport.fit_all()
            window.set_hint("Measure, face sketching, browser actions, hole to object")

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(400, run))
        return window

    run_and_capture(build, "docs/shots/complete.png", settle_ms=9000, size=(1400, 900))
    print("--- finishing the plan ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    ok = (
        REPORT.get("dead_rail_tiles") == []
        and REPORT.get("measured")
        and REPORT.get("sketch_on_face")
        and REPORT.get("ellipse_drawn")
        and REPORT.get("spline_finished")
        and REPORT.get("sketch_feature")
        and REPORT.get("renamed")
        and REPORT.get("refs_follow_rename")
        and len(REPORT.get("bodies_after_duplicate") or []) == 2
        and REPORT.get("isolated") == ["Base"]
        and REPORT.get("hole_to_object_removed", 0) > 0
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
