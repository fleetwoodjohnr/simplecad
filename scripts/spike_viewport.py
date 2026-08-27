#!/usr/bin/env python
"""M1 gate: OCCT must render into a QOpenGLWidget's framebuffer.

Exercises the real OcctViewport rather than a throwaway copy, so it doubles as a
regression check on the viewport wiring. See docs/architecture.md.

Run:  .venv/bin/python scripts/spike_viewport.py
"""

from __future__ import annotations

import sys

from _harness import run_and_capture  # noqa: E402


def main() -> int:
    from simplecad.ui.theme import DARK
    from simplecad.ui.viewport.occt_view import OcctViewport, SelectionMode

    holder: dict[str, object] = {}

    def build(_app):
        viewport = OcctViewport(DARK)
        holder["viewport"] = viewport

        def on_ready() -> None:
            from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
            from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

            viewport.set_selection_modes(
                [SelectionMode.BODY, SelectionMode.FACE, SelectionMode.EDGE]
            )
            viewport.display(BRepPrimAPI_MakeBox(60.0, 40.0, 20.0).Shape())
            axis = gp_Ax2(gp_Pnt(30.0, 20.0, 20.0), gp_Dir(0.0, 0.0, 1.0))
            viewport.display(
                BRepPrimAPI_MakeCylinder(axis, 12.0, 30.0).Shape(),
                color="#7FA8D8",
            )
            viewport.fit_all()

        viewport.ready.connect(on_ready)
        return viewport

    result = run_and_capture(build, "docs/spike_viewport.png")
    viewport = holder.get("viewport")
    failure = getattr(viewport, "failure", None)

    print("--- viewport spike ---")
    for key, value in result.items():
        print(f"  {key}: {value}")
    print(f"  viewport_failure: {failure}")
    ok = failure is None and int(result.get("distinct_colors", 0)) > 12
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
