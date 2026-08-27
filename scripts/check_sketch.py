#!/usr/bin/env python
"""The Sketch tool, driven through the window.

Each profile must solve, extrude, and leave a valid solid of the right size.
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
            from simplecad.kernel.occ import bounding_box, is_valid, volume

            expectations = {
                "rectangle": ("Rectangle 40x25 x10", 40 * 25 * 10),
                "circle": ("Circle d30 x10", math.pi * 15 ** 2 * 10),
                "polygon": ("Hexagon af30 x10",
                            6 * (30 / (2 * math.cos(math.pi / 6))) ** 2
                            * math.sin(math.pi / 3) / 2 * 10),
                "slot": ("Slot 40x12 x10",
                         ((40 - 12) * 12 + math.pi * 36) * 10),
            }
            outcomes = {}
            for profile, (label, expected) in expectations.items():
                window.activate_tool("sketch")
                panel = next(
                    w for w, _a in window.stage.overlays
                    if getattr(w, "is_tool_panel", False)
                )
                panel.choose(profile)
                panel.commit()
                window.wait_for_rebuild()

                body = list(window.document.bodies.values())[-1]
                got = volume(body.shape)
                outcomes[profile] = {
                    "label": label,
                    "volume": round(got, 1),
                    "expected": round(expected, 1),
                    "valid": is_valid(body.shape),
                    "ok": abs(got - expected) / expected < 0.02 and is_valid(body.shape),
                }
                # Space them out so the screenshot shows all four.
                from simplecad.core.document import BodyRef
                from simplecad.kernel.operations import MoveFeature

                window.document.add_feature(
                    MoveFeature(
                        inputs={"body": BodyRef(body.name),
                                "dx": 60 * len(outcomes), "dy": 0, "dz": 0},
                        outputs=[body.name],
                    )
                )
                window.rebuild()

            REPORT.update(outcomes)
            window.stage.viewport.fit_all()
            window.set_hint("Rectangle, circle, hexagon and slot — sketched and extruded")

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(300, run))
        return window

    run_and_capture(build, "docs/shots/sketch.png", settle_ms=6000, size=(1400, 900))
    print("--- sketch profiles ---")
    ok = True
    for profile, data in REPORT.items():
        ok = ok and data["ok"]
        mark = "" if data["ok"] else "   <-- wrong"
        print(f"  {data['label']:22s} volume {data['volume']:10.1f} "
              f"(expected {data['expected']:10.1f}) valid={data['valid']}{mark}")
    if not REPORT:
        ok = False
        print("  nothing built")
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
