#!/usr/bin/env python
"""A fillet that kills OCCT must not kill the window.

Two SIGSEGV core dumps on this machine share one stack: ``ChFi3d_Builder``
walking off a null curve adaptor inside ``BRepFilletAPI_MakeFillet::Build``,
called from a Qt signal handler on the GUI thread. That is the fillet *preview*,
which used to run in the parent process. ``except BaseException`` cannot catch a
segfault, so the window died mid-drag and took the unsaved model with it.

The preview now runs in the geometry process, which is what
``docs/architecture.md`` already promised: "one bad fillet radius never empties
the viewport or crashes the app."

The exact geometry that crashed OCCT is not known -- the cores predate any
faulthandler and the machine has no python debuginfo -- so this does not hunt
for a magic radius. It asserts the property that makes the radius irrelevant:
kill the geometry process outright, mid-session, and the window survives, brings
it back, and goes on previewing.

Run:  .venv/bin/python scripts/check_fillet_survives.py
"""

from __future__ import annotations

import os
import signal
import sys
import time

os.environ["SIMPLECAD_NO_GRID"] = "1"

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}

#: Radii to sweep, including several no 40x30x20 edge can carry. None of them
#: may end the process, and the unbuildable ones must say so.
RADII = (2.0, 6.0, 9.0, 40.0, 250.0, 1000.0)


def wait_for(condition, timeout_ms: int = 8000) -> bool:
    from PySide6.QtCore import QEventLoop
    from PySide6.QtWidgets import QApplication

    started = time.perf_counter()
    while (time.perf_counter() - started) * 1000.0 < timeout_ms:
        if condition():
            return True
        QApplication.processEvents(QEventLoop.AllEvents, 10)
    return bool(condition())


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def build(_app):
        window = MainWindow(Mode.DARK)
        failures: list[str] = []

        def populate() -> None:
            from simplecad.kernel.primitives import BoxFeature

            window.add_feature(
                BoxFeature(inputs={"width": 40, "depth": 30, "height": 20},
                           outputs=["Block"])
            )
            window.wait_for_rebuild()
            window.stage.viewport.fit_all()
            QTimer.singleShot(600, probe)

        def open_fillet() -> object:
            """Select an edge and open the Fillet tool on it.

            The selection is set directly rather than clicked, the way
            ``check_fillet_drag`` does: this check is about surviving a crash,
            not about picking.
            """
            from OCP.TopAbs import TopAbs_EDGE
            from OCP.TopExp import TopExp_Explorer
            from OCP.TopoDS import TopoDS

            from simplecad.ui.selection import Picked

            shape = window.document.bodies["Block"].shape
            explorer = TopExp_Explorer(shape, TopAbs_EDGE)
            edge = TopoDS.Edge_s(explorer.Current())
            window.selection.picks = [
                Picked(body="Block", kind="edge", shape=edge,
                       presentation=window._presentations.get("Block"))
            ]
            window.activate_tool("fillet")
            QApplication.processEvents()
            return next(
                (w for w, _a in window.stage.overlays
                 if type(w).__name__ == "FilletPanel"), None
            )

        def probe() -> None:
            viewport = window.stage.viewport
            panel = open_fillet()
            REPORT["panel_opened"] = panel is not None
            if panel is None:
                failures.append("the Fillet panel did not open on the edge")
                finish()
                return

            # -- 1. every radius answers, none of them ends the process ----
            answers = {}
            for radius in RADII:
                # Cleared first, or the ghost left by the previous radius makes
                # every answer look like a success.
                viewport.clear_ghost()
                panel._preview_failed = False
                panel.fields["radius"].set_value(radius)
                panel.preview()
                # The answer is in when a ghost appears for this value, or the
                # panel says it could not build one.
                wait_for(lambda: panel._preview_failed
                         or getattr(viewport, "_ghost", None) is not None)
                answers[radius] = "no" if panel._preview_failed else "yes"
            REPORT["radii_answered"] = answers
            REPORT["alive_after_sweep"] = QApplication.instance() is not None
            REPORT["big_radius_refused"] = answers.get(1000.0) == "no"
            REPORT["small_radius_built"] = answers.get(2.0) == "yes"
            if not REPORT["big_radius_refused"]:
                failures.append("a 1000 mm fillet on a 20 mm box was not refused")
            if not REPORT["small_radius_built"]:
                failures.append("an ordinary 2 mm fillet did not preview")

            # -- 2. kill the geometry process outright ---------------------
            # Stands in for OCCT segfaulting inside it, which is what the core
            # dumps show and what no `except` in Python can intercept.
            child = window.geometry._process
            REPORT["child_pid"] = None if child is None else child.pid
            if child is None:
                failures.append("no geometry process to kill; nothing isolated")
                finish()
                return
            os.kill(child.pid, signal.SIGSEGV)

            recovered = wait_for(lambda: window.geometry.available
                                 and window.geometry._process is not None
                                 and window.geometry._process.pid != child.pid,
                                 timeout_ms=45000)
            REPORT["window_alive_after_kill"] = window.isVisible()
            REPORT["child_restarted"] = recovered
            REPORT["new_child_pid"] = (
                None if window.geometry._process is None
                else window.geometry._process.pid
            )
            if not REPORT["window_alive_after_kill"]:
                failures.append("the window did not survive the geometry crash")
            if not recovered:
                failures.append("the geometry process was not brought back")

            # -- 3. and it still works afterwards --------------------------
            window.wait_for_rebuild()
            panel2 = open_fillet() if panel is None else panel
            viewport.clear_ghost()
            panel2.fields["radius"].set_value(3.0)
            panel2.preview()
            works = wait_for(
                lambda: getattr(viewport, "_ghost", None) is not None,
                timeout_ms=15000,
            )
            REPORT["previews_again_after_restart"] = works
            if not works:
                failures.append("previews did not resume after the restart")
            finish()

        def finish() -> None:
            REPORT["failures"] = failures
            window.set_hint(
                "Survived a geometry-process crash"
                if not failures else "; ".join(failures)
            )

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(600, populate))
        return window

    result = run_and_capture(
        build, "docs/shots/fillet_survives.png", settle_ms=90000, size=(1280, 820)
    )
    REPORT["shot"] = result

    for key, value in REPORT.items():
        print(f"{key}: {value}")
    ok = REPORT.get("failures") == [] and "error" not in result
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
