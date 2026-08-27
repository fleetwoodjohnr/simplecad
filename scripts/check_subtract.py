#!/usr/bin/env python
"""Subtraction: cut one body out of another, the way every other CAD does it.

The kernel has had booleans from the start; nothing in the interface could
reach them. This drives the whole route -- select two bodies, take Subtract off
the contextual bar, watch the preview, apply -- and checks the things that make
it subtraction rather than a shape appearing:

* the bar offers it for a two-body selection, and leads with it,
* the panel names which body is being cut, and Swap turns it round,
* the result has the tool's volume missing from it,
* **the tool body is gone**, rather than left sitting inside the part it cut,
* Keep tool bodies leaves it there when that is what you asked for,
* and Join and Intersect do their own thing through the same panel.
"""

from __future__ import annotations

import os
import sys

os.environ["SIMPLECAD_NO_GRID"] = "1"

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}
FAILURES: list[str] = []

#: The block, and the pin that overlaps one corner of it.
BLOCK = {"width": 40.0, "depth": 40.0, "height": 20.0}
PIN_RADIUS, PIN_HEIGHT = 6.0, 40.0


def main() -> int:
    import math

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def two_bodies(window):
        """A block and a pin standing through it, rebuilt and framed."""
        from simplecad.kernel.primitives import BoxFeature, CylinderFeature

        block = BoxFeature(inputs=dict(BLOCK), outputs=["Block"])
        block.name = "Block"
        window.add_feature(block)
        pin = CylinderFeature(
            inputs={"radius": PIN_RADIUS, "height": PIN_HEIGHT, "x": 20, "y": 20,
                    "z": -10},
            outputs=["Pin"],
        )
        pin.name = "Pin"
        window.add_feature(pin)
        window.wait_for_rebuild()
        window.stage.viewport.fit_all()

    def select_both(window):
        viewport = window.stage.viewport
        for name in ("Block", "Pin"):
            viewport.select_shape(window._presentations[name], replace=name == "Block")
        window.selection.refresh()
        window._on_selection()
        QApplication.processEvents()

    def main_run(window) -> None:
        from simplecad.kernel.occ import is_valid, volume

        two_bodies(window)
        select_both(window)

        REPORT["selected"] = window.selection.bodies
        offered = window.context_bar.keys()
        REPORT["toolbar"] = offered
        if "subtract" not in offered:
            FAILURES.append("Subtract is not offered for a two-body selection")
        if offered and offered[0] != "subtract":
            FAILURES.append(f"Subtract does not lead the bar: {offered[:3]}")

        # -- the panel, through the same route the contextual bar uses -------
        window.run_action("subtract")
        QApplication.processEvents()
        panel = next(
            (w for w, _a in window.stage.overlays
             if getattr(w, "is_tool_panel", False)), None
        )
        REPORT["panel_opened"] = panel is not None
        if panel is None:
            FAILURES.append("Subtract did not open a panel")
            return
        REPORT["target"] = panel.target
        REPORT["tools"] = panel.tools
        REPORT["subtitle"] = panel.subtitle.text()
        if "Block" not in panel.subtitle.text():
            FAILURES.append("the panel does not say which body is being cut")

        # Swap has to turn the operation round, or the user cannot correct a
        # selection order they did not choose.
        panel._swap()
        QApplication.processEvents()
        REPORT["target_after_swap"] = panel.target
        if panel.target == REPORT["target"]:
            FAILURES.append("Swap did not change the target")
        panel._swap()
        QApplication.processEvents()
        if panel.target != REPORT["target"]:
            FAILURES.append("Swap did not come back to where it started")

        # -- the preview is the real result, before anything is committed ----
        panel._refresh_preview()
        QApplication.processEvents()
        REPORT["preview_shown"] = getattr(window.stage.viewport, "_ghost", None) is not None
        REPORT["bodies_before_apply"] = sorted(window.document.bodies)
        if sorted(window.document.bodies) != ["Block", "Pin"]:
            FAILURES.append("the preview changed the document")

        before = volume(window.document.bodies["Block"].shape)
        panel.commit()
        window.wait_for_rebuild()
        QApplication.processEvents()

        REPORT["bodies_after"] = sorted(window.document.bodies)
        if "Pin" in window.document.bodies:
            FAILURES.append("the tool body is still there after a subtract")
        if "Block" not in window.document.bodies:
            FAILURES.append("the target body did not survive the subtract")
            return

        after = window.document.bodies["Block"].shape
        REPORT["valid"] = is_valid(after)
        REPORT["volume"] = f"{before:.1f} -> {volume(after):.1f}"
        # The pin runs right through the block, so exactly its cross-section
        # times the block's height comes out.
        expected = before - math.pi * PIN_RADIUS ** 2 * BLOCK["height"]
        REPORT["removed"] = round(before - volume(after), 1)
        REPORT["expected_removed"] = round(before - expected, 1)
        if abs(volume(after) - expected) > 1.0:
            FAILURES.append(
                f"cut removed {before - volume(after):.1f} mm3, expected "
                f"{before - expected:.1f}"
            )
        if not is_valid(after):
            FAILURES.append("the cut produced an invalid solid")

        REPORT["ghost_cleared"] = getattr(window.stage.viewport, "_ghost", None) is None
        REPORT["displayed"] = len(window._presentations)
        if "Pin" in window._presentations:
            FAILURES.append("the consumed tool is still on screen")

        # -- and it undoes ---------------------------------------------------
        window.undo()
        window.wait_for_rebuild()
        QApplication.processEvents()
        REPORT["bodies_after_undo"] = sorted(window.document.bodies)
        if sorted(window.document.bodies) != ["Block", "Pin"]:
            FAILURES.append("undo did not bring the tool body back")
        window.redo()
        window.wait_for_rebuild()
        QApplication.processEvents()

        # -- keep tool bodies ------------------------------------------------
        window.undo()
        window.wait_for_rebuild()
        select_both(window)
        window.run_action("subtract")
        QApplication.processEvents()
        panel = next(
            (w for w, _a in window.stage.overlays
             if getattr(w, "is_tool_panel", False)), None
        )
        panel.keep.setChecked(True)
        panel.commit()
        window.wait_for_rebuild()
        QApplication.processEvents()
        REPORT["kept"] = sorted(window.document.bodies)
        if "Pin" not in window.document.bodies:
            FAILURES.append("Keep tool bodies did not keep the tool")
        window.undo()
        window.wait_for_rebuild()

        # -- Join and Intersect come out of the same panel --------------------
        for key, expectation in (("join", "more"), ("intersect", "less")):
            select_both(window)
            window.run_action(key)
            QApplication.processEvents()
            panel = next(
                (w for w, _a in window.stage.overlays
                 if getattr(w, "is_tool_panel", False)), None
            )
            if panel is None or panel.operation != {"join": "join",
                                                    "intersect": "intersect"}[key]:
                FAILURES.append(f"{key} did not open on its own operation")
                continue
            panel.commit()
            window.wait_for_rebuild()
            QApplication.processEvents()
            result = volume(window.document.bodies["Block"].shape)
            REPORT[f"{key}_volume"] = round(result, 1)
            if expectation == "more" and result <= before:
                FAILURES.append(f"Join did not add material ({result:.1f})")
            if expectation == "less" and result >= before:
                FAILURES.append(f"Intersect did not reduce the body ({result:.1f})")
            window.undo()
            window.wait_for_rebuild()

        # Leave the shot showing the subtraction itself.
        select_both(window)
        window.run_action("subtract")
        QApplication.processEvents()
        panel = next(
            (w for w, _a in window.stage.overlays
             if getattr(w, "is_tool_panel", False)), None
        )
        panel.commit()
        window.wait_for_rebuild()
        window.stage.viewport.clear_selection()
        window.stage.viewport.fit_all()
        window.set_hint("Subtracted Pin from Block — the cutter is consumed")

    def build(_app):
        window = MainWindow(Mode.DARK)
        window.stage.viewport.ready.connect(
            lambda: QTimer.singleShot(500, lambda: main_run(window))
        )
        return window

    run_and_capture(build, "docs/shots/subtract.png", settle_ms=6000,
                    size=(1280, 840))
    print("--- subtract ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    for failure in FAILURES:
        print(f"  FAIL: {failure}")
    ok = bool(REPORT.get("panel_opened")) and not FAILURES
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
