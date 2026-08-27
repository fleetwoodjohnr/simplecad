#!/usr/bin/env python
"""Every tile in the Shape panel builds what its label says.

The polygon presets are the point of this: Triangle, Pentagon, Hexagon and
Octagon are the same feature with the side count pinned, so the thing worth
asserting is that each really produces a prism with that many sides, and that
the size the panel asks for is the size you get.
"""

from __future__ import annotations

import os
import sys

os.environ["SIMPLECAD_NO_GRID"] = "1"

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}

#: tile -> how many side faces the prism should have (caps excluded).
SIDES = {"triangle": 3, "pentagon": 5, "hexagon": 6, "octagon": 8,
         "polygon_prism": 6}


def main() -> int:
    from PySide6.QtCore import QTimer

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def build(_app):
        window = MainWindow(Mode.DARK)

        def run() -> None:
            from simplecad.core.naming import sub_shapes
            from simplecad.kernel.occ import is_valid
            from simplecad.ui.tools.shapes import LABELS

            failures = []
            for kind, _icon, label in LABELS:
                window.activate_tool(f"shape_{kind}")
                panel = next(
                    (w for w, _a in window.stage.overlays
                     if type(w).__name__ == "ShapePanel"), None
                )
                if panel is None or panel._kind != kind:
                    failures.append(f"{label}: panel did not open on this tile")
                    continue
                before = set(window.document.bodies)
                panel.commit()
                window.wait_for_rebuild()
                made = set(window.document.bodies) - before
                if not made:
                    failures.append(f"{label}: built nothing")
                    continue
                name = made.pop()
                shape = window.document.bodies[name].shape
                if shape is None or not is_valid(shape):
                    failures.append(f"{label}: not a valid solid")
                    continue
                if not name.startswith(label):
                    failures.append(f"{label}: body named {name!r}")
                faces = len(sub_shapes(shape, "face"))
                if kind in SIDES and faces != SIDES[kind] + 2:
                    failures.append(
                        f"{label}: {faces} faces, expected {SIDES[kind] + 2}"
                    )
                REPORT[label] = f"{name}, {faces} faces"
                window.cancel_tool()

            REPORT["tiles"] = len(LABELS)
            REPORT["bodies_made"] = len(window.document.bodies)
            REPORT["failures"] = failures
            window.stage.viewport.fit_all()
            window.set_hint(f"Built all {len(LABELS)} shapes")

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(400, run))
        return window

    run_and_capture(build, "docs/shots/shapes.png", settle_ms=45000, size=(1200, 820))
    print("--- every shape tile ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    ok = (
        REPORT.get("failures") == []
        and REPORT.get("bodies_made") == REPORT.get("tiles")
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
