#!/usr/bin/env python
"""Point-to-point measuring must not stall the window.

The tool worked on a box and froze the application on real models, which is the
worst shape a bug can have: correct in every test and unusable in practice. So
this is a *timing* check rather than a correctness one -- correctness is
check_measure_points.py's job.

It puts up a scene of the kind that caused it: a modelled thread, a mesh body of
the sort an import produces, and a grouped assembly. Then it moves the cursor
across the model the way a hand does and reports what each snap cost. The
numbers that matter are p95 -- the ordinary experience -- and the worst single
reading, because one 400 ms snap in a stream of them is a visible lurch.

The viewport turns its own effort down on a body that cannot afford full
snapping (see occt_view.SNAP_BUDGET), so a heavy model is expected to get
*cheaper* as this runs, not more expensive. That is the behaviour under test.
"""

from __future__ import annotations

import os
import statistics
import sys
import time

os.environ["SIMPLECAD_NO_GRID"] = "1"

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}

#: Ordinary snaps must stay under this, in milliseconds. A mouse delivers an
#: event every few milliseconds; anything slower than a frame and the queue
#: stops draining, which is what the freeze was.
P95_LIMIT_MS = 25.0
#: And no single snap may lurch past this.
MAX_LIMIT_MS = 100.0
#: How many cursor positions to sample.
SAMPLES = 200


def main() -> int:
    from PySide6.QtCore import QPoint, Qt, QTimer
    from PySide6.QtTest import QTest

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def build(_app):
        window = MainWindow(Mode.DARK)

        def populate() -> None:
            from simplecad.core.document import Body, BodyRef
            from simplecad.kernel.io_formats import _shell_from_triangles
            from simplecad.kernel.operations import MoveFeature, ThreadFeature
            from simplecad.kernel.primitives import BoxFeature, CylinderFeature
            from simplecad.kernel.tessellate import triangulate

            # A threaded post: hundreds of faces and a helical surface.
            window.document.add_feature(
                CylinderFeature(inputs={"radius": 6.0, "height": 30.0},
                                outputs=["Post"])
            )
            window.rebuild()
            window.wait_for_rebuild()
            from simplecad.core.naming import make_ref
            from simplecad.kernel.detect import cylindrical_faces

            shape = window.document.bodies["Post"].shape
            face = cylindrical_faces(shape)[0][0]
            window.document.add_feature(
                ThreadFeature(
                    inputs={
                        "body": BodyRef("Post"),
                        "face": make_ref(
                            shape, face,
                            window.document.features[0].id, body="Post",
                        ),
                        "designation": "P12",
                    },
                    outputs=["Post"],
                )
            )

            # Two boxes, grouped -- the assembly case.
            for index, name in enumerate(("Left", "Right")):
                window.document.add_feature(
                    BoxFeature(
                        inputs={"width": 18, "depth": 18, "height": 18},
                        outputs=[name],
                    )
                )
                window.document.add_feature(
                    MoveFeature(
                        inputs={"body": BodyRef(name), "dx": 30 + index * 26,
                                "dy": 30},
                        outputs=[name],
                    )
                )
            window.rebuild()
            window.wait_for_rebuild()
            window.document.add_group(["Left", "Right"], "Assembly")

            # And a mesh body, of the kind an STL import produces.
            from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere
            from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

            sphere = BRepPrimAPI_MakeSphere(
                gp_Ax2(gp_Pnt(-40.0, 0.0, 15.0), gp_Dir(0, 0, 1)), 15.0
            ).Shape()
            mesh = triangulate(sphere, 0.06)
            shell, _closed = _shell_from_triangles(mesh.vertices, mesh.triangles)
            window.document.bodies["Imported"] = Body(name="Imported", shape=shell)
            REPORT["mesh_triangles"] = len(mesh.triangles)

            window.refresh_view()
            window.stage.viewport.fit_all()
            QTimer.singleShot(900, run)

        def run() -> None:
            viewport = window.stage.viewport
            window.activate_tool("measure_points")
            panel = next(
                (w for w, _a in window.stage.overlays
                 if type(w).__name__ == "PointMeasurePanel"), None
            )
            REPORT["opens_in_point_mode"] = (
                panel is not None and viewport.picking_points
            )
            if panel is None:
                return

            width, height = viewport.width(), viewport.height()
            timings = []
            for index in range(SAMPLES):
                # A lawnmower sweep across the whole viewport, so the cursor
                # crosses every body and the empty space between them.
                across = index / (SAMPLES - 1)
                x = int(width * (0.12 + 0.76 * abs(2 * (across % 0.25) * 2 - 1)))
                y = int(height * (0.20 + 0.60 * across))
                started = time.perf_counter()
                viewport.snap_at(QPoint(x, y))
                timings.append((time.perf_counter() - started) * 1000.0)

            timings.sort()
            REPORT["p50_ms"] = round(statistics.median(timings), 2)
            REPORT["p95_ms"] = round(timings[int(len(timings) * 0.95)], 2)
            REPORT["max_ms"] = round(timings[-1], 2)
            REPORT["effort_downgrades"] = len(viewport._snap_effort)

            # And the real event path, which is where the freeze was felt: if
            # the queue drains, these all get delivered and the window repaints.
            started = time.perf_counter()
            for index in range(40):
                QTest.mouseMove(
                    viewport,
                    QPoint(int(width * (0.2 + 0.6 * index / 39)), int(height * 0.5)),
                )
            REPORT["40_moves_ms"] = round(
                (time.perf_counter() - started) * 1000.0, 1
            )
            REPORT["still_in_point_mode"] = viewport.picking_points

            # The safety net itself, exercised directly. A model heavy enough to
            # stall has to make the tool get cheaper rather than make the window
            # stop -- and since a model that slow is exactly what cannot be
            # relied on to turn up in a check, the mechanism is triggered by
            # hand instead of hoped for.
            notices = []
            viewport.notice.connect(notices.append)
            shape = window.document.bodies["Imported"].shape
            viewport._note_snap_cost(shape, 0.040)
            REPORT["downgraded_to"] = viewport._snap_effort.get(
                viewport._snap_key(shape)
            )
            viewport._note_snap_cost(shape, 0.400)
            REPORT["downgraded_again_to"] = viewport._snap_effort.get(
                viewport._snap_key(shape)
            )
            viewport._note_snap_cost(shape, 0.040)     # must not go back up
            REPORT["stays_downgraded"] = viewport._snap_effort.get(
                viewport._snap_key(shape)
            )
            REPORT["said_so_once"] = len(notices)
            window.set_hint("Swept the cursor across a heavy model")

        window.stage.viewport.ready.connect(populate)
        return window

    run_and_capture(build, "docs/shots/measure_perf.png",
                    settle_ms=6000, size=(1200, 820))
    print("--- point-to-point snapping cost ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    ok = (
        REPORT.get("opens_in_point_mode") is True
        and REPORT.get("still_in_point_mode") is True
        and REPORT.get("p95_ms", 1e9) < P95_LIMIT_MS
        and REPORT.get("max_ms", 1e9) < MAX_LIMIT_MS
        and REPORT.get("downgraded_to") == "ray"
        and REPORT.get("downgraded_again_to") == "none"
        and REPORT.get("stays_downgraded") == "none"
        and REPORT.get("said_so_once") == 1
    )
    print(f"  limits: p95 < {P95_LIMIT_MS} ms, max < {MAX_LIMIT_MS} ms")
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
