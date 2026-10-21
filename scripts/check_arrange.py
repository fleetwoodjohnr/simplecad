#!/usr/bin/env python
"""Auto-Arrange through the real window.

Box-selects six objects, opens Arrange from the contextual bar, clicks the face
they go on, chooses Packed, waits for the live preview and applies it. Then
undo and redo, reopens the arrangement with Edit arrangement, switches it to a
grid and saves -- one more undo must bring the packed layout back, not the
original scatter. Last, asks for a layout that cannot fit and checks Apply is
refused and a suggestion repairs it.
"""

from __future__ import annotations

import os
import sys

os.environ["SIMPLECAD_NO_GRID"] = "1"

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}
CHECKS = 17


def main() -> int:
    from PySide6.QtCore import QPoint, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from simplecad.core.naming import fingerprint, sub_shapes
    from simplecad.kernel.align import interference_volume
    from simplecad.kernel.measure import minimum_distance
    from simplecad.kernel.occ import bounding_box
    from simplecad.kernel.primitives import BoxFeature, CylinderFeature
    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode
    from simplecad.ui.viewport.occt_view import StandardView

    names = [f"Part{i}" for i in range(6)]

    def build(_app):
        window = MainWindow(Mode.DARK)
        viewport = window.stage.viewport

        def wait(predicate, timeout_ms=20_000) -> bool:
            for _ in range(timeout_ms // 50):
                QApplication.processEvents()
                if predicate():
                    return True
                QTest.qWait(50)
            return False

        def press(button):
            QTest.mouseClick(button, Qt.LeftButton, Qt.NoModifier, button.rect().center())
            QApplication.processEvents()

        def panel():
            return next((w for w, _a in window.stage.overlays
                         if getattr(w, "is_tool_panel", False)), None)

        def positions():
            return {n: tuple(round(v, 4) for v in bounding_box(window.document.body(n).shape)[0])
                    for n in names}

        def arranged_on_tray():
            tray = window.document.body("Tray").shape
            shapes = [window.document.body(n).shape for n in names]
            # Touching, not just near: a box read off a meshed cylinder is
            # padded by the mesh's deflection, so measure contact directly.
            seated = all(minimum_distance(tray, s) < 1e-4 for s in shapes)
            clear = all(interference_volume(tray, s) < 1e-4 for s in shapes) and all(
                interference_volume(a, b) < 1e-4
                for i, a in enumerate(shapes) for b in shapes[i + 1:])
            return seated and clear

        def populate():
            window.document.add_feature(BoxFeature(
                inputs={"width": 120, "depth": 80, "height": 5}, outputs=["Tray"]))
            sizes = [(18, 12, 10), (10, 10, 14), (24, 8, 6), (14, 14, 8), (9, 20, 7)]
            for index, (w, d, h) in enumerate(sizes):
                window.document.add_feature(BoxFeature(
                    inputs={"width": w, "depth": d, "height": h, "x": 160 + (index % 3) * 32,
                            "y": (index // 3) * 30}, outputs=[names[index]]))
            window.document.add_feature(CylinderFeature(
                inputs={"radius": 7, "height": 9, "x": 232, "y": 42}, outputs=[names[5]]))
            window.rebuild()
            window.wait_for_rebuild()
            viewport.set_standard_view(StandardView.TOP, animate=False)
            viewport.fit_all()
            QApplication.processEvents()
            QTimer.singleShot(200, scenario)

        def box_select():
            corners = []
            for name in names:
                low, high = bounding_box(window.document.body(name).shape)
                for x in (low[0], high[0]):
                    for y in (low[1], high[1]):
                        for z in (low[2], high[2]):
                            at = viewport.project((x, y, z))
                            if at is not None:
                                corners.append(at)
            start = QPoint(int(min(c[0] for c in corners)) - 12, int(min(c[1] for c in corners)) - 12)
            end = QPoint(int(max(c[0] for c in corners)) + 12, int(max(c[1] for c in corners)) + 12)
            QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, start)
            for step in range(1, 7):
                QTest.mouseMove(viewport, QPoint(start.x() + (end.x() - start.x()) * step // 6,
                                                 start.y() + (end.y() - start.y()) * step // 6))
            QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, end)
            QApplication.processEvents()

        def scenario():
            original = positions()
            box_select()
            REPORT["box_selected_six"] = sorted(window.selection.bodies) == names
            REPORT["arrange_offered"] = "arrange" in window.context_bar.keys()
            window.run_action("arrange")
            tool = panel()
            REPORT["panel_waits_for_the_face"] = (
                tool is not None and tool._role == "target" and sorted(tool._names) == names)

            top = max((f for f in sub_shapes(window.document.body("Tray").shape, "face")
                       if (data := fingerprint(f, "face")).direction and data.direction[2] > 0.99),
                      key=lambda f: fingerprint(f, "face").center[2])
            # Off-centre, so the click lands on the tray rather than between parts.
            at = viewport.project(tuple(c + d for c, d in zip(fingerprint(top, "face").center,
                                                              (-40.0, -25.0, 0.0))))
            QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, QPoint(round(at[0]), round(at[1])))
            QApplication.processEvents()
            REPORT["face_clicked_is_target"] = (
                tool._target is not None and str(tool._target.body) == "Tray"
                and sorted(tool._names) == names and tool._role is None)

            press(tool.pattern_buttons["packed"])
            REPORT["packed_preview_fits"] = wait(lambda: tool.confirm.isEnabled() and tool._fits)
            REPORT["preview_drawn"] = len(tool._overlays) >= 2      # placed parts + boundary
            press(tool.confirm)
            window.wait_for_rebuild()
            QApplication.processEvents()
            arranges = [f for f in window.document.features if f.type_name == "arrange"]
            REPORT["applied_as_one_feature"] = (
                len(arranges) == 1 and arranges[0].inputs.get("pattern") == "packed")
            REPORT["packed_on_the_tray"] = arranged_on_tray()
            packed = positions()

            press(window.top_bar.buttons["undo"])
            window.wait_for_rebuild()
            REPORT["undo_restores_scatter"] = positions() == original
            press(window.top_bar.buttons["redo"])
            window.wait_for_rebuild()
            REPORT["redo_rearranges"] = positions() == packed

            viewport.select_shape(window._presentations[names[2]])
            window.selection.refresh()
            window._on_selection()
            REPORT["edit_offered"] = "edit_arrange" in window.context_bar.keys()
            window.run_action("edit_arrange")
            tool = panel()
            arranges = [f for f in window.document.features if f.type_name == "arrange"]
            REPORT["editor_loads_packed"] = (
                tool is not None and tool._source is arranges[0] and tool.pattern == "packed"
                and tool.confirm.text() == "Save changes")
            press(tool.pattern_buttons["grid"])
            ready = wait(lambda: tool.confirm.isEnabled() and tool._fits)
            press(tool.confirm)
            window.wait_for_rebuild()
            QApplication.processEvents()
            arranges = [f for f in window.document.features if f.type_name == "arrange"]
            REPORT["edit_saved_in_place"] = (
                ready and len(arranges) == 1 and arranges[0].inputs.get("pattern") == "grid"
                and positions() != packed and arranged_on_tray())
            press(window.top_bar.buttons["undo"])
            window.wait_for_rebuild()
            REPORT["one_undo_returns_to_packed"] = positions() == packed

            # Ask for more than the tray can hold: Apply refuses, a suggestion fixes it.
            viewport.select_shape(window._presentations[names[0]])
            window.selection.refresh()
            window._on_selection()
            window.run_action("edit_arrange")
            tool = panel()
            press(tool.pattern_buttons["linear"])
            press(tool.spacing_buttons["fixed"])
            field = tool.fields["gap"]
            field.setText("40")
            field._commit()
            REPORT["impossible_refused"] = wait(
                lambda: bool(tool._suggestion_buttons) and not tool.confirm.isEnabled())
            if tool._suggestion_buttons:
                press(tool._suggestion_buttons[0])
            REPORT["suggestion_fits"] = wait(lambda: tool.confirm.isEnabled() and tool._fits)
            window.cancel_tool()
            QApplication.processEvents()
            REPORT["cancel_changes_nothing"] = positions() == packed and panel() is None
            viewport.fit_all()
            window.set_hint("Auto-Arrange verified")

        viewport.ready.connect(lambda: QTimer.singleShot(300, populate))
        return window

    run_and_capture(build, "/tmp/simplecad-arrange.png", settle_ms=45_000, size=(1300, 860))
    print("--- auto-arrange ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    ok = len(REPORT) == CHECKS and all(value is True for value in REPORT.values())
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
