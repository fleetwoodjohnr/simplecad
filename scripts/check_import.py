#!/usr/bin/env python
"""Import: does a file from another program come in usable?

Writes fixture files with the app's own exporters, brings them back in through
the real UI path, and checks what the user would care about: the bodies turn up,
at the right size, positioned somewhere they can actually be seen, selectable
and editable like anything else -- and that a file SimpleCAD cannot read says so
instead of doing nothing.
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ["SIMPLECAD_NO_GRID"] = "1"

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}
FAILURES: list[str] = []


def _fixtures(directory: str) -> dict[str, str]:
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder

    from simplecad.kernel.io_formats import export_shapes

    box = BRepPrimAPI_MakeBox(30.0, 24.0, 12.0).Shape()
    cylinder = BRepPrimAPI_MakeCylinder(8.0, 20.0).Shape()
    paths = {}
    for extension, shapes in ((".step", [box, cylinder]), (".stl", [box])):
        path = os.path.join(directory, f"fixture{extension}")
        export_shapes(shapes, path)
        paths[extension] = path
    paths[".3mf"] = _production_3mf(directory)
    broken = os.path.join(directory, "broken.step")
    with open(broken, "w") as handle:
        handle.write("ISO-10303-21;\nthis is not a STEP file\n")
    unsupported = os.path.join(directory, "model.xyz")
    with open(unsupported, "w") as handle:
        handle.write("nope")
    paths["broken"] = broken
    paths["unsupported"] = unsupported
    return paths


#: A 30x24x12 block, wound outward, as bare 3MF markup.
_CORNERS = [
    (0, 0, 0), (30, 0, 0), (30, 24, 0), (0, 24, 0),
    (0, 0, 12), (30, 0, 12), (30, 24, 12), (0, 24, 12),
]
_FACETS = [
    (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
    (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
    (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
]


def _production_3mf(directory: str) -> str:
    """A 3MF laid out the way every current slicer writes one.

    The root part holds nothing but a reference; the mesh lives in a part of
    its own, reached through the production extension's ``p:path``. Written by
    hand rather than by this application's own writer, because the point is to
    read a file SimpleCAD did not produce -- the shape that used to import as
    "contains no triangles".
    """
    import zipfile

    core = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    production = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"

    def model(body: str) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<model unit="millimeter" xmlns="{core}" xmlns:p="{production}" '
            f'requiredextensions="p">{body}</model>'
        )

    mesh = (
        "<mesh><vertices>"
        + "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in _CORNERS)
        + "</vertices><triangles>"
        + "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in _FACETS)
        + "</triangles></mesh>"
    )
    path = os.path.join(directory, "slicer.3mf")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns='
            '"http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel-1" Type='
            '"http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
            "</Relationships>",
        )
        archive.writestr(
            "3D/3dmodel.model",
            model(
                '<resources><object id="2" type="model"><components>'
                '<component p:path="/3D/Objects/part.model" objectid="1" '
                'transform="1 0 0 0 1 0 0 0 1 0 0 0"/></components></object>'
                '</resources><build><item objectid="2" '
                'transform="1 0 0 0 1 0 0 0 1 40 0 0"/></build>'
            ),
        )
        archive.writestr(
            "3D/Objects/part.model",
            model(f'<resources><object id="1" type="model">{mesh}</object></resources>'),
        )
    return path


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    directory = tempfile.mkdtemp(prefix="simplecad-import-")

    def build(_app):
        window = MainWindow(Mode.DARK)

        def run() -> None:
            from simplecad.kernel.occ import bounding_box, is_valid, volume
            from simplecad.kernel.primitives import BoxFeature

            paths = _fixtures(directory)
            viewport = window.stage.viewport

            # Something already in the scene, so placement has to avoid it.
            existing = BoxFeature(
                inputs={"width": 50, "depth": 40, "height": 20}, outputs=["Plate"]
            )
            existing.name = "Plate"
            window.add_feature(existing)
            window.wait_for_rebuild()
            plate = bounding_box(window.document.bodies["Plate"].shape)

            # -- a two-body STEP -------------------------------------------
            created = window.import_path(paths[".step"])
            window.wait_for_rebuild()
            REPORT["step_bodies"] = created
            REPORT["step_in_tree"] = [
                n for n in window.document.bodies if n in created
            ]
            if len(created) != 2:
                FAILURES.append(f"STEP: expected 2 bodies, got {created}")

            if created:
                shape = window.document.bodies[created[0]].shape
                low, high = bounding_box(shape)
                size = tuple(round(high[i] - low[i], 3) for i in range(3))
                REPORT["step_size"] = size
                if size != (30.0, 24.0, 12.0):
                    FAILURES.append(f"STEP: wrong size {size}")
                REPORT["placed_clear"] = low[0] >= plate[1][0] - 1e-6
                if not REPORT["placed_clear"]:
                    FAILURES.append("STEP: landed on top of the existing body")

                # visible in the framed view
                window.stage.viewport.fit_all()
                QApplication.processEvents()
                centre = tuple((low[i] + high[i]) / 2.0 for i in range(3))
                screen = viewport.project(centre)
                REPORT["on_screen"] = (
                    screen is not None
                    and 0 <= screen[0] <= viewport.width()
                    and 0 <= screen[1] <= viewport.height()
                )
                if not REPORT["on_screen"]:
                    FAILURES.append("STEP: imported body is off screen")

                # selectable, and the toolbar treats it like any other body
                window.select_item(created[0])
                QApplication.processEvents()
                REPORT["selected"] = window.selection.bodies
                REPORT["toolbar"] = window.context_bar.keys()
                if window.selection.bodies != [created[0]]:
                    FAILURES.append("STEP: imported body could not be selected")
                if "split" not in window.context_bar.keys():
                    FAILURES.append("STEP: imported body is not fully editable")

                # ...and really is editable: split it.
                from simplecad.core.document import BodyRef
                from simplecad.kernel.split import SplitFeature

                before = volume(shape)
                window.add_feature(
                    SplitFeature(
                        inputs={
                            "body": BodyRef(created[0]),
                            "normal": [1.0, 0.0, 0.0],
                            "origin": list(low),
                            "position": 15.0,
                            "names": ["Half A", "Half B"],
                        },
                        outputs=["Half A", "Half B"],
                    )
                )
                window.wait_for_rebuild()
                halves = [n for n in ("Half A", "Half B") if n in window.document.bodies]
                REPORT["split_import"] = halves
                if len(halves) == 2:
                    total = sum(volume(window.document.bodies[h].shape) for h in halves)
                    REPORT["split_volumes_match"] = abs(total - before) < 1.0
                    if not REPORT["split_volumes_match"]:
                        FAILURES.append("STEP: split of an import lost volume")
                else:
                    FAILURES.append("STEP: an imported body could not be split")

            # -- an STL, which must arrive as a solid ----------------------
            stl = window.import_path(paths[".stl"])
            window.wait_for_rebuild()
            REPORT["stl_bodies"] = stl
            if len(stl) != 1:
                FAILURES.append(f"STL: expected 1 body, got {stl}")
            else:
                low, high = bounding_box(window.document.bodies[stl[0]].shape)
                size = tuple(round(high[i] - low[i], 2) for i in range(3))
                REPORT["stl_size"] = size
                if any(abs(a - b) > 0.15 for a, b in zip(size, (30.0, 24.0, 12.0))):
                    FAILURES.append(f"STL: wrong size {size}")

            # -- a slicer 3MF: the reported bug ----------------------------
            three_mf = window.import_path(paths[".3mf"])
            window.wait_for_rebuild()
            REPORT["3mf_bodies"] = three_mf
            if len(three_mf) != 1:
                FAILURES.append(f"3MF: expected 1 body, got {three_mf}")
            else:
                shape = window.document.bodies[three_mf[0]].shape
                low, high = bounding_box(shape)
                size = tuple(round(high[i] - low[i], 2) for i in range(3))
                REPORT["3mf_size"] = size
                REPORT["3mf_solid"] = is_valid(shape)
                REPORT["3mf_volume"] = round(volume(shape), 1)
                if any(abs(a - b) > 0.05 for a, b in zip(size, (30.0, 24.0, 12.0))):
                    FAILURES.append(f"3MF: wrong size {size}")
                if not is_valid(shape):
                    FAILURES.append("3MF: the body did not come back as a solid")
                if abs(volume(shape) - 30 * 24 * 12) > 1.0:
                    FAILURES.append(f"3MF: wrong volume {volume(shape)}")

            # -- files that cannot be read ---------------------------------
            for key, label in (("broken", "corrupt"), ("unsupported", "unknown")):
                count_before = len(window.document.bodies)
                result = window.import_path(paths[key])
                QApplication.processEvents()
                dialogs = [
                    w for w in QApplication.topLevelWidgets()
                    if isinstance(w, QMessageBox) and w.isVisible()
                ]
                REPORT[f"{label}_result"] = result
                REPORT[f"{label}_hint"] = window.stage.hint.full_text()
                REPORT[f"{label}_dialog"] = bool(dialogs)
                REPORT[f"{label}_added_nothing"] = (
                    len(window.document.bodies) == count_before
                )
                if result:
                    FAILURES.append(f"{label}: imported something from a bad file")
                if not dialogs:
                    FAILURES.append(f"{label}: failed silently, with no dialog")
                if not window.stage.hint.full_text():
                    FAILURES.append(f"{label}: no message in the hint line")
                for dialog in dialogs:
                    dialog.close()
                QApplication.processEvents()

            window.stage.viewport.clear_selection()
            window.stage.viewport.fit_all()
            window.set_hint("Imported a STEP assembly and an STL")

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(500, run))
        return window

    run_and_capture(build, "docs/shots/import.png", settle_ms=6500, size=(1320, 850))
    print("--- import ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    for failure in FAILURES:
        print(f"  FAIL: {failure}")
    ok = bool(REPORT.get("step_bodies")) and not FAILURES
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
