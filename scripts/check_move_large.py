#!/usr/bin/env python
"""Move a large body in a real window: fast, one feature, picks still true.

Without an argument a dense body is generated (a finely tessellated sphere
rebuilt as a solid, like an imported STL). With a path, a project is opened
and its largest body is moved instead:

    .venv/bin/python scripts/check_move_large.py "~/Desktop/Atlas/UPS Atlas.scad3"
"""

from __future__ import annotations

import os
import sys
import time

os.environ["SIMPLECAD_NO_GRID"] = "1"

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}
TIMES: dict[str, float] = {}


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from simplecad.core.naming import sub_shapes
    from simplecad.kernel.detect import analyse_plane
    from simplecad.kernel.occ import bounding_box
    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    project = os.path.expanduser(sys.argv[1]) if len(sys.argv) > 1 else None

    def build(_app):
        window = MainWindow(Mode.DARK)
        state = {}

        def timed_refresh():
            original = window.refresh_view

            def wrapper():
                start = time.perf_counter()
                original()
                TIMES.setdefault("refresh_ms", []).append((time.perf_counter() - start) * 1000)
            window.refresh_view = wrapper

        def populate() -> None:
            if project:
                window._open_path(project)
            else:
                from simplecad.core.geometry_service import serialise_shape
                from simplecad.kernel.importing import EmbeddedImportFeature
                from simplecad.kernel.io_formats import export_stl, import_bodies
                from simplecad.kernel.primitives import SphereFeature
                from simplecad.core.document import Document
                from simplecad.core.rebuild import Rebuilder
                import tempfile
                seed = Document()
                seed.add_feature(SphereFeature(inputs={"radius": 40}, outputs=["Ball"]))
                Rebuilder(seed).rebuild()
                path = os.path.join(tempfile.mkdtemp(), "ball.stl")
                export_stl([seed.body("Ball").shape], path, deflection=0.02)
                mesh = import_bodies(path)[0].shape
                asset = window.document.add_asset(serialise_shape(mesh))
                window.document.add_feature(EmbeddedImportFeature(
                    inputs={"asset": asset}, outputs=["Mesh"]))
                window.rebuild()
            window.wait_for_rebuild()
            QApplication.processEvents()
            body = max(window.document.bodies.values(),
                       key=lambda b: len(sub_shapes(b.shape, "face")) if b.shape is not None else 0)
            state["name"] = body.name
            REPORT_faces = len(sub_shapes(body.shape, "face"))
            print(f"moving {body.name}: {REPORT_faces} faces")
            state["low"] = bounding_box(body.shape)[0]
            state["features"] = len(window.document.features)
            timed_refresh()
            QTimer.singleShot(300, move)

        def move() -> None:
            from simplecad.core.document import BodyRef
            from simplecad.kernel.operations import MoveFeature

            name = state["name"]
            for step in range(3):
                start = time.perf_counter()
                window.history.record("Move")
                if not window.document.fold_translation([name], (5.0, 0.0, 0.0)):
                    window.document.add_feature(MoveFeature(
                        inputs={"body": BodyRef(name), "dx": 5.0}, outputs=[name]))
                window.mark_dirty()
                window.rebuild()
                window.wait_for_rebuild()
                QApplication.processEvents()
                TIMES.setdefault("move_ms", []).append((time.perf_counter() - start) * 1000)
            body = window.document.body(name)
            low = bounding_box(body.shape)[0]
            REPORT["moved_15_mm"] = abs(low[0] - state["low"][0] - 15.0) < 1e-6
            REPORT["one_move_feature"] = len(window.document.features) <= state["features"] + 1

            # Pick the body's flat face farthest in +X: it must report where it
            # is now, not where the presentation was first built.
            faces = [(analyse_plane(f), f) for f in sub_shapes(body.shape, "face")]
            faces = [(info, f) for info, f in faces if info is not None]
            if faces:
                info, face = max(faces, key=lambda item: item[0].center[0])
                viewport = window.stage.viewport
                viewport.select_subshape(window._presentations[name], face)
                QApplication.processEvents()
                picks = window.selection.picks
                REPORT["pick_reports_moved_face"] = bool(picks) and picks[0].info is not None and \
                    abs(picks[0].info.center[0] - info.center[0]) < 1e-6
            else:
                REPORT["pick_reports_moved_face"] = True

            start = time.perf_counter()
            window.undo()
            window.wait_for_rebuild()
            QApplication.processEvents()
            TIMES["undo_ms"] = [(time.perf_counter() - start) * 1000]
            low = bounding_box(window.document.body(name).shape)[0]
            REPORT["undo_moves_back_5_mm"] = abs(low[0] - state["low"][0] - 10.0) < 1e-6
            window.stage.viewport.fit_all()

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(200, populate))
        return window

    run_and_capture(build, "/tmp/simplecad-move-large.png", settle_ms=60000 if project else 45000,
                    size=(1200, 820))
    print("--- move large body ---")
    for key, value in TIMES.items():
        print(f"  {key}: " + ", ".join(f"{v:.0f}" for v in value))
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    ok = len(REPORT) == 4 and all(value is True for value in REPORT.values())
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
