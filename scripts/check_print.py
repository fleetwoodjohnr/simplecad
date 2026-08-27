#!/usr/bin/env python
"""The 3D Print workspace, driven through the window.

Places a model badly on purpose, then checks the panel notices, that Place on
Plate and Center fix it, and that export still works with warnings outstanding --
warnings inform, they never block.
"""

from __future__ import annotations

import os
import sys
import tempfile

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}


def main() -> int:
    from PySide6.QtCore import QTimer

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def build(_app):
        window = MainWindow(Mode.DARK)

        def run() -> None:
            from simplecad.kernel.occ import bounding_box
            from simplecad.kernel.primitives import BoxFeature, ConeFeature

            # A cone point-down, floating well above the plate.
            window.add_feature(
                ConeFeature(
                    inputs={"bottom_radius": 0.5, "top_radius": 25,
                            "height": 12, "z": 40},
                    outputs=["Funnel"],
                )
            )
            window.wait_for_rebuild()

            window.activate_tool("print")
            panel = next(
                w for w, _a in window.stage.overlays
                if getattr(w, "is_tool_panel", False)
            )
            REPORT["initial"] = [
                f"{f.check}:{f.severity.value}" for f in panel.analysis.warnings()
            ]
            REPORT["notices_floating"] = any(
                f.check == "on_plate" for f in panel.analysis.warnings()
            )
            REPORT["notices_overhang"] = any(
                f.check == "overhangs" for f in panel.analysis.warnings()
            )

            panel.place_on_plate()
            low = bounding_box(window.document.bodies["Funnel"].shape)[0]
            REPORT["on_plate_after_place"] = abs(low[2]) < 1e-6

            panel.center_on_plate()
            shape = window.document.bodies["Funnel"].shape
            low, high = bounding_box(shape)
            REPORT["centred"] = (
                abs((low[0] + high[0]) / 2 - 128.0) < 1e-3
                and abs((low[1] + high[1]) / 2 - 128.0) < 1e-3
            )

            panel.orient_for_printing()
            REPORT["after_orient"] = [
                f"{f.check}:{f.severity.value}" for f in panel.analysis.warnings()
            ]

            # Export must work even with warnings outstanding.
            from simplecad.kernel.io_formats import export_shapes

            target = os.path.join(tempfile.mkdtemp(), "part.3mf")
            export_shapes(
                [b.shape for b in window.document.visible_bodies()], target
            )
            REPORT["exported_bytes"] = os.path.getsize(target)
            REPORT["slicer"] = (
                __import__("simplecad.kernel.printing", fromlist=["find_slicer"])
                .find_slicer()
            )
            REPORT["finding_rows"] = panel.findings_layout.count()
            REPORT["host_height"] = panel.findings_host.height()
            window.stage.viewport.fit_all()
            window.set_hint("3D Print workspace — placed, oriented, exported")

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(400, run))
        return window

    run_and_capture(build, "docs/shots/print.png", settle_ms=9000, size=(1400, 900))
    print("--- 3D print workspace ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    ok = (
        REPORT.get("notices_floating")
        and REPORT.get("notices_overhang")
        and REPORT.get("on_plate_after_place")
        and REPORT.get("centred")
        and REPORT.get("exported_bytes", 0) > 500
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
