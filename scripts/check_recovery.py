#!/usr/bin/env python
"""The recovery offer must never lock the window.

This is the one startup path no other check here covers: ``_harness.py`` sets
``SIMPLECAD_NO_RECOVERY=1``, so every screenshot script and every acceptance run
skips it. That blind spot is how the offer stayed an application-modal
``QMessageBox``, raised 400 ms after startup, through a session where the user
reported the whole window as frozen -- correctly, because while that box was up
the window really did ignore every click, and under XWayland it can come up
behind the window it belongs to. Force-quitting then leaves another recovery
file, so the next launch does it again.

What is asserted:

1. The offer appears at all, when a previous session left work behind.
2. It is a stage overlay, not a modal window -- ``QApplication.activeModalWidget``
   is None and the main window still answers input.
3. Recover actually restores the document.
4. Discard actually removes the leftovers, so it cannot repeat forever.

Run:  .venv/bin/python scripts/check_recovery.py
"""

from __future__ import annotations

import os
import sys

from _harness import ROOT  # noqa: F401,E402 - puts the package on sys.path

# *After* _harness, which sets SIMPLECAD_NO_RECOVERY=1 for every scripted run --
# the very thing under test. Clearing it first achieves nothing, which is how
# the first version of this check reported "no offer shown" and looked like a
# failure of the code rather than of itself.
os.environ.pop("SIMPLECAD_NO_RECOVERY", None)

REPORT: dict[str, object] = {}


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication

    from simplecad.core import autosave
    from simplecad.core.document import Document
    from simplecad.kernel.primitives import BoxFeature
    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.panels.recovery_bar import RecoveryBar
    from simplecad.ui.theme import Mode
    from simplecad.ui.viewport.occt_view import default_surface_format

    # A session that did not exit cleanly, with something worth recovering.
    planted = Document()
    planted.add_feature(
        BoxFeature(inputs={"width": 31, "depth": 21, "height": 11}, outputs=["Base"])
    )
    path = autosave.write(planted, "checkrecovery")
    if not path or not os.path.exists(path):
        print("could not plant a recovery file")
        return 1

    QSurfaceFormat.setDefaultFormat(default_surface_format())
    app = QApplication.instance() or QApplication(sys.argv[:1])

    windows: list = []

    def bar_of(window):
        return next(
            (w for w, _a in window.stage.overlays if isinstance(w, RecoveryBar)), None
        )

    def settle(ms: int = 900) -> None:
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.start(ms)
        while deadline.isActive():
            app.processEvents()

    # -- 1 and 2: the offer shows, and nothing is modal -------------------
    window = MainWindow(Mode.LIGHT)
    windows.append(window)
    window.setGeometry(40, 40, 1100, 720)
    window.show()
    settle(1400)                       # past the 400 ms deferral

    bar = bar_of(window)
    REPORT["offer_shown"] = bar is not None and bar.isVisible()
    # The whole point. A modal widget here is the bug.
    REPORT["nothing_modal"] = QApplication.activeModalWidget() is None
    REPORT["window_enabled"] = window.isEnabled()
    REPORT["offer_is_overlay"] = bar is not None and bar.parent() is window.stage

    # -- 3: Recover restores the work ------------------------------------
    if bar is not None:
        bar.recover_requested.emit()
        settle()
    REPORT["recovered_bodies"] = sorted(window.document.bodies)
    REPORT["recovered"] = "Base" in window.document.bodies
    REPORT["offer_dismissed"] = bar_of(window) is None
    window.close()
    settle(300)

    # -- 4: Discard clears the leftovers, so it cannot repeat forever -----
    if not os.path.exists(path):       # Recover must not have deleted it
        REPORT["kept_file_after_recover"] = False
    else:
        REPORT["kept_file_after_recover"] = True
        second = MainWindow(Mode.LIGHT)
        windows.append(second)
        second.setGeometry(40, 40, 1100, 720)
        second.show()
        settle(1400)
        again = bar_of(second)
        REPORT["offered_again"] = again is not None
        if again is not None:
            again.discard_requested.emit()
            settle()
        REPORT["leftovers_gone"] = not os.path.exists(path)
        second.close()
        settle(300)

    for leftover, _when in autosave.pending():
        if "checkrecovery" in leftover:
            os.remove(leftover)

    print("--- recovery offer ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    ok = all(
        REPORT.get(key)
        for key in (
            "offer_shown", "nothing_modal", "window_enabled", "offer_is_overlay",
            "recovered", "offer_dismissed", "kept_file_after_recover",
            "offered_again", "leftovers_gone",
        )
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
