#!/usr/bin/env python
"""The window must stay live while the kernel works.

Modelling a thread takes seconds. With rebuilds in the geometry process the UI
thread should be free the whole time -- repainting, orbiting, responding. This
measures that directly: how many times the event loop comes back round while a
threaded hole is being built, and the longest single stall.

For contrast it then runs the same work in-process, which is what the app falls
back to if the geometry process cannot start.
"""

from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
os.environ.setdefault("SIMPLECAD_NO_RECOVERY", "1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

REPORT: dict[str, object] = {}


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.qt_runtime import configure_qt_application
    from simplecad.ui.theme import Mode
    from simplecad.ui.viewport.occt_view import default_surface_format

    configure_qt_application()
    QSurfaceFormat.setDefaultFormat(default_surface_format())
    app = QApplication(sys.argv[:1])
    window = MainWindow(Mode.DARK)
    window.setGeometry(40, 40, 1100, 760)
    window.show()

    def threaded_hole(position):
        from simplecad.core.document import BodyRef
        from simplecad.core.naming import fingerprint, make_ref, sub_shapes
        from simplecad.kernel.operations import HoleFeature

        shape = window.document.bodies["Plate"].shape
        top = max(
            (f for f in sub_shapes(shape, "face")
             if (p := fingerprint(f, "face")).direction and p.direction[2] > 0.9),
            key=lambda f: fingerprint(f, "face").center[2],
        )
        return HoleFeature(
            inputs={
                "body": BodyRef("Plate"),
                "face": make_ref(shape, top, window.document.features[0].id,
                                 body="Plate"),
                "diameter": 6, "style": "threaded",
                "depth_mode": "through", "position": position,
            },
            outputs=["Plate"],
        )

    def measure(label: str, use_process: bool) -> None:
        viewport = window.stage.viewport
        window.geometry.available = use_process and window.geometry._pipe is not None

        started = time.time()
        window.document.add_feature(threaded_hole((20.0, 25.0, 8.0)))
        window.mark_dirty()
        window.rebuild()

        turns = 0
        worst = 0.0
        while window.is_rebuilding:
            tick = time.time()
            viewport.repaint()
            app.processEvents()
            worst = max(worst, time.time() - tick)
            turns += 1
            time.sleep(0.004)
        window.wait_for_rebuild()
        app.processEvents()
        elapsed = time.time() - started
        if turns == 0:
            # The loop never ran because rebuild() blocked start to finish, so
            # the whole operation was one uninterrupted stall.
            worst = elapsed
        REPORT[label] = {
            "seconds": round(elapsed, 2),
            "ui_turns": turns,
            "worst_stall_ms": round(worst * 1000),
        }
        print(f"  {label:22s} {elapsed:5.2f}s  {turns:5d} UI turns  "
              f"worst stall {worst * 1000:6.0f} ms", flush=True)

    def run():
        from simplecad.kernel.occ import is_valid, volume
        from simplecad.kernel.primitives import BoxFeature

        window.add_feature(
            BoxFeature(inputs={"width": 50, "depth": 50, "height": 8},
                       outputs=["Plate"])
        )
        window.wait_for_rebuild()
        REPORT["geometry_process"] = window.geometry.available

        measure("out of process", True)
        REPORT["valid_after_process"] = is_valid(
            window.document.bodies["Plate"].shape
        )

        # Reset, then do the same work on the UI thread for comparison.
        window.document.features = window.document.features[:1]
        window._stale = {f.id for f in window.document.features}
        window.rebuild()
        window.wait_for_rebuild()
        measure("in process (fallback)", False)

        window.geometry.available = window.geometry._pipe is not None
        app.quit()

    QTimer.singleShot(2400, run)
    app.exec()
    window.geometry.stop()

    print("--- responsiveness ---")
    print(f"  geometry process available: {REPORT.get('geometry_process')}")
    out = REPORT.get("out of process", {})
    inp = REPORT.get("in process (fallback)", {})
    ok = (
        REPORT.get("geometry_process")
        and REPORT.get("valid_after_process")
        and out.get("ui_turns", 0) >= 50
        and out.get("worst_stall_ms", 9999) <= 250
        and out.get("worst_stall_ms", 0) < inp.get("worst_stall_ms", 0)
    )
    print(f"  out-of-process stall {out.get('worst_stall_ms')} ms vs "
          f"in-process {inp.get('worst_stall_ms')} ms")
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
