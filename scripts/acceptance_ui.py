#!/usr/bin/env python
"""The acceptance workflow, driven through the real UI.

    Create Box -> Pull Face -> Create Second Part -> Stack Faces ->
    Add Hole -> Create Automatic Thread Pair -> Fillet -> Export

Every step goes through the actual tool panels and the real document, so this
exercises the tools, the feature graph, the rebuild engine, the viewport refresh
and export together.

Picking is the one thing simulated: faces are located geometrically and pushed
into the selection model rather than clicked. Real OCCT picking is covered
separately by scripts/shot_selection.py, which clicks the viewport for real.
"""

from __future__ import annotations

import os
import sys
import tempfile

from _harness import run_and_capture  # noqa: E402

STEPS: list[tuple[str, str]] = []


def record(name: str, detail: str = "ok") -> None:
    STEPS.append((name, detail))
    print(f"  [{len(STEPS)}] {name}: {detail}", flush=True)


def main() -> int:
    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    out = sys.argv[1] if len(sys.argv) > 1 else "docs/shots/acceptance.png"
    failures: list[str] = []

    def build(_app):
        window = MainWindow(Mode.DARK)
        window.stage.viewport.ready.connect(lambda: run(window, failures))
        return window

    result = run_and_capture(build, out, settle_ms=60000, size=(1500, 940))
    print("--- acceptance ---")
    for key, value in result.items():
        print(f"  {key}: {value}")
    if failures:
        print("FAILURES:")
        for failure in failures:
            print("   -", failure)
    print("VERDICT:", "PASS" if not failures else "FAIL")
    return 0 if not failures else 1


def run(window, failures: list[str]) -> None:
    from PySide6.QtWidgets import QApplication

    from simplecad.core.naming import fingerprint, sub_shapes
    from simplecad.kernel.detect import analyse_cylinder, analyse_plane
    from simplecad.kernel.occ import bounding_box, volume
    from simplecad.ui.selection import Picked

    def pick(body_name: str, chooser) -> Picked:
        """Put a specific face/edge of a body into the selection model."""
        body = window.document.body(body_name)
        shape = chooser(body.shape)
        kind = "edge" if shape.ShapeType() == 6 else "face"
        info = None
        if kind == "face":
            info = analyse_plane(shape) or analyse_cylinder(shape)
        return Picked(
            body=body_name, kind=kind, shape=shape,
            presentation=window._presentations.get(body_name), info=info,
        )

    def select(*picks) -> None:
        window.selection.picks = list(picks)

    def top(shape):
        faces = [(f, fingerprint(f, "face")) for f in sub_shapes(shape, "face")]
        up = [(f, p) for f, p in faces
              if p.geometry == "plane" and p.direction and p.direction[2] > 0.99]
        return max(up, key=lambda i: i[1].center[2])[0]

    def bottom(shape):
        faces = [(f, fingerprint(f, "face")) for f in sub_shapes(shape, "face")]
        down = [(f, p) for f, p in faces
                if p.geometry == "plane" and p.direction and p.direction[2] < -0.99]
        return min(down, key=lambda i: i[1].center[2])[0]

    def round_face(shape):
        for face in sub_shapes(shape, "face"):
            if analyse_cylinder(face) is not None:
                return face
        raise AssertionError("no cylindrical face")

    def run_tool(key: str, configure=None) -> None:
        window.activate_tool(key)
        panel = next(
            (w for w, _a in window.stage.overlays if getattr(w, "is_tool_panel", False)),
            None,
        )
        assert panel is not None, f"{key} did not open a panel"
        if configure:
            configure(panel)
        panel.commit()
        QApplication.processEvents()
        assert window.wait_for_rebuild(), "rebuild did not finish in time"

    def check(name: str, condition: bool, detail: str) -> None:
        if condition:
            record(name, detail)
        else:
            failures.append(f"{name}: {detail}")
            print(f"  [FAIL] {name}: {detail}", flush=True)

    try:
        document = window.document
        document.parameters.set("wall", "3 mm")

        # 1. Create Box, through the Shape tool.
        run_tool("shapes", lambda p: (
            p.choose("box"),
            p.fields["width"].setText("60"), p.fields["width"]._commit(),
            p.fields["depth"].setText("40"), p.fields["depth"]._commit(),
            p.fields["height"].setText("12"), p.fields["height"]._commit(),
        ))
        base_name = document.features[0].outputs[0]
        check("Create Box", volume(document.body(base_name).shape) > 0,
              f"{base_name} = {volume(document.body(base_name).shape):.0f} mm3")

        # 2. Pull the top face up by 8 mm.
        select(pick(base_name, top))
        run_tool("pushpull", lambda p: (
            p.fields["distance"].setText("8"), p.fields["distance"]._commit()))
        height = bounding_box(document.body(base_name).shape)[1][2]
        check("Pull face", abs(height - 20.0) < 1e-6, f"top now at z={height:.2f}")

        # 3. Create the second part.
        run_tool("shapes", lambda p: (
            p.choose("box"),
            p.fields["width"].setText("60"), p.fields["width"]._commit(),
            p.fields["depth"].setText("40"), p.fields["depth"]._commit(),
            p.fields["height"].setText("wall"), p.fields["height"]._commit(),
        ))
        lid_name = [n for n in document.bodies if n != base_name][0]
        check("Create second part", lid_name in document.bodies,
              f"{lid_name} using expression 'wall'")

        # 4. Stack it onto the base.
        select(pick(lid_name, bottom), pick(base_name, top))
        run_tool("stack")
        low = bounding_box(document.body(lid_name).shape)[0]
        check("Stack faces", abs(low[2] - 20.0) < 1e-6,
              f"{lid_name} seated at z={low[2]:.2f}, centred at x={low[0] + 30:.1f}")

        # 5. Hole through the lid.
        select(pick(lid_name, top))
        run_tool("hole", lambda p: (
            p.fields["diameter"].setText("12"), p.fields["diameter"]._commit()))
        bores = [f for f in sub_shapes(document.body(lid_name).shape, "face")
                 if (i := analyse_cylinder(f)) and i.internal]
        check("Add hole", len(bores) == 1, f"⌀12 bore through {lid_name}")

        # 6. A post on the base, then the automatic thread pair.
        run_tool("shapes", lambda p: (
            p.choose("cylinder"),
            p.fields["radius"].setText("6"), p.fields["radius"]._commit(),
            p.fields["height"].setText("18"), p.fields["height"]._commit(),
        ))
        post_name = [n for n in document.bodies if n not in (base_name, lid_name)][0]
        select(pick(post_name, round_face), pick(lid_name, round_face))
        run_tool("threaded_connection")
        connection = document.features[-1]
        check("Automatic thread pair",
              connection.inputs.get("designation") == "M12" and not connection.message.startswith("The"),
              f"{connection.message or connection.inputs.get('designation')}")

        # 7. Fillet the base's vertical edges.
        def vertical_edges(shape):
            found = [e for e in sub_shapes(shape, "edge")
                     if (p := fingerprint(e, "edge")).geometry == "line"
                     and p.direction and abs(p.direction[2]) > 0.99]
            return found[0]

        edges = [e for e in sub_shapes(document.body(base_name).shape, "edge")
                 if (p := fingerprint(e, "edge")).geometry == "line"
                 and p.direction and abs(p.direction[2]) > 0.99]
        select(*[
            Picked(body=base_name, kind="edge", shape=e,
                   presentation=window._presentations.get(base_name))
            for e in edges
        ])
        before = volume(document.body(base_name).shape)
        run_tool("fillet", lambda p: (
            p.fields["radius"].setText("4"), p.fields["radius"]._commit()))
        after = volume(document.body(base_name).shape)
        check("Fillet", after < before, f"{len(edges)} edges, {before:.0f} -> {after:.0f} mm3")

        # 8. Export.
        from simplecad.kernel.io_formats import export_shapes

        target = tempfile.mkdtemp()
        shapes = [b.shape for b in document.visible_bodies()]
        sizes = {}
        for extension in (".3mf", ".step", ".stl"):
            path = os.path.join(target, "part" + extension)
            export_shapes(shapes, path)
            sizes[extension] = os.path.getsize(path)
        check("Export", all(v > 500 for v in sizes.values()),
              ", ".join(f"{k} {v // 1024} KB" for k, v in sizes.items()))

        # 9. Save the project, then reopen it and confirm it is still editable.
        from simplecad.core.project import load, save

        project = os.path.join(target, "acceptance.scad3")
        save(document, project, thumbnail=window._thumbnail())
        check("Save project", os.path.getsize(project) > 1000,
              f"{os.path.getsize(project) // 1024} KB")

        reopened, cached = load(project)
        base_volume = volume(document.body(base_name).shape)
        check("Reopen project",
              cached and set(reopened.bodies) == set(document.bodies),
              f"{len(reopened.bodies)} bodies, geometry cached")

        from simplecad.core.rebuild import Rebuilder

        reopened.parameters.set("wall", "6 mm")
        rebuilder = Rebuilder(reopened)
        rebuilder.invalidate()
        report = rebuilder.rebuild()
        lid_height = (lambda b: b[1][2] - b[0][2])(
            bounding_box(reopened.body(lid_name).shape)
        )
        detail = f"wall 3→6 mm rebuilt {lid_name} to {lid_height:.4f} mm thick"
        if not report.ok:
            detail += "  |  " + "; ".join(
                f"{reopened.feature(fid).name}: {err}"
                for fid, err in report.failures.items()
            )
        check("Still parametric after reopening",
              report.ok and abs(lid_height - 6.0) < 0.05, detail)

        window.stage.viewport.fit_all()
        window.set_hint("Box → Pull → Stack → Hole → M12 thread pair → Fillet → Export")
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        failures.append(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    sys.exit(main())
