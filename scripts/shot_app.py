#!/usr/bin/env python
"""Screenshot the whole application window. Used to check the UI by eye."""

from __future__ import annotations

import sys

from _harness import run_and_capture  # noqa: E402


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "docs/shots/app.png"
    seed = "--empty" not in sys.argv

    def build(_app):
        from simplecad.ui.main_window import MainWindow
        from simplecad.ui.theme import Mode

        mode = Mode.LIGHT if "--light" in sys.argv else Mode.DARK
        window = MainWindow(mode)

        if seed:
            def populate() -> None:
                from simplecad.kernel.primitives import BoxFeature, CylinderFeature
                window.document.parameters.set("wall", "2 mm")
                window.add_feature(
                    BoxFeature(inputs={"width": 60, "depth": 40, "height": 18})
                )
                window.add_feature(
                    CylinderFeature(
                        inputs={"radius": 9, "height": 26, "x": 30, "y": 20, "z": 18}
                    )
                )
                window.wait_for_rebuild()
                window.stage.viewport.fit_all()
                window.set_hint("Select a face to pull, or pick a tool on the left.")

            window.stage.viewport.ready.connect(populate)
        if "--tool" in sys.argv:
            window.stage.viewport.ready.connect(lambda: window.activate_tool("shapes"))
        return window

    result = run_and_capture(build, out, settle_ms=2200, size=(1500, 940))
    print("--- app shot ---")
    for key, value in result.items():
        print(f"  {key}: {value}")
    return 0 if result.get("distinct_colors", 0) > 20 else 1


if __name__ == "__main__":
    sys.exit(main())
