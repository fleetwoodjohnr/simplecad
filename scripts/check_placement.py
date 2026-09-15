#!/usr/bin/env python
"""Real-window placement, free drag, Section Replace, and undo/redo check."""

from __future__ import annotations

import os
import sys

os.environ["SIMPLECAD_NO_GRID"] = "1"

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}


def main() -> int:
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_OUT
    from OCP.gp import gp_Pnt
    from PySide6.QtCore import QPoint, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from simplecad.core.naming import fingerprint, sub_shapes
    from simplecad.kernel.align import reference_frame
    from simplecad.kernel.occ import bounding_box, is_valid, transformed
    from simplecad.kernel.primitives import BoxFeature
    from simplecad.kernel.vent import VentPlateFeature
    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode
    from simplecad.ui.viewport.occt_view import StandardView

    def build(_app):
        window = MainWindow(Mode.DARK)

        def click(widget, position, modifiers=Qt.NoModifier):
            QTest.mouseClick(
                window.windowHandle(), Qt.LeftButton, modifiers,
                widget.mapTo(window, position),
            )
            QApplication.processEvents()

        def top_face(shape):
            return max(
                (face for face in sub_shapes(shape, "face")
                 if (data := fingerprint(face, "face")).direction
                 and data.direction[2] > .99),
                key=lambda face: fingerprint(face, "face").center[2],
            )

        def populate():
            window.document.add_feature(BoxFeature(
                # Rotate the receiving body in its own plane so the live UI
                # check exercises face-local directions, not world X/Y.
                inputs={
                    "width": 80, "depth": 80, "height": 4, "rz": 27,
                },
                outputs=["Wall"],
            ))
            window.document.add_feature(VentPlateFeature(inputs={
                "width": 40, "depth": 40, "thickness": 3,
                "across_flats": 5, "wall": 1.2, "margin": 2,
                "x": 110, "y": 20,
            }, outputs=["Vent"]))
            window.rebuild()
            window.wait_for_rebuild()
            QApplication.processEvents()
            viewport = window.stage.viewport
            viewport.set_standard_view(StandardView.TOP, animate=False)
            viewport.fit_all()

            # Actual browser-row selection, followed by an actual Ctrl-click on
            # the target face in the GL viewport.
            tree = window.browser.bodies_tree
            item = tree.findItems("Vent", Qt.MatchExactly | Qt.MatchRecursive)[0]
            click(tree.viewport(), tree.visualItemRect(item).center())
            wall_face = top_face(window.document.body("Wall").shape)
            screen = viewport.project(fingerprint(wall_face, "face").center)
            click(viewport, QPoint(round(screen[0]), round(screen[1])), Qt.ControlModifier)
            REPORT["selected_body_and_face"] = (
                set(window.selection.bodies) == {"Wall", "Vent"}
                and len(window.selection.planar_faces()) == 1
            )

            # Activate through the visible command-search control and keyboard,
            # rather than constructing a panel directly.
            click(window.top_bar.buttons["search"], window.top_bar.buttons["search"].rect().center())
            QTest.keyClicks(window._search.query, "section replace")
            QTest.keyClick(window._search.query, Qt.Key_Return)
            QApplication.processEvents()
            panel = next(
                w for w, _a in window.stage.overlays
                if getattr(w, "is_tool_panel", False)
            )
            REPORT["tool_activated"] = panel.__class__.__name__ == "SectionReplacePanel"
            REPORT["anchor_presets"] = (
                len(panel.moving_anchor.buttons) == 9
                and len(panel.target_anchor.buttons) == 9
            )

            # Numerical left really is negative U, before and after a physical
            # camera orbit. The camera must not mutate the placement values.
            field = panel.fields["u"]
            click(field, field.rect().center())
            QTest.keyClick(field, Qt.Key_A, Qt.ControlModifier)
            QTest.keyClicks(field, "-21")
            QApplication.processEvents()
            moving, target = panel._pair()
            result = panel._solve(moving, target)
            placed = transformed(window.document.body(moving.body).shape, result.transform)
            frame = reference_frame(target.shape)
            placed_center = tuple(sum(pair) / 2 for pair in zip(*bounding_box(placed)))
            relative = tuple(placed_center[i] - frame.center[i] for i in range(3))
            signed_u = sum(relative[i] * frame.x_axis[i] for i in range(3))
            REPORT["left_21_is_negative_u"] = abs(signed_u + 21) < 1e-5
            before_orbit = panel.value("u")
            centre = viewport.rect().center()
            QTest.mousePress(viewport, Qt.RightButton, Qt.NoModifier, centre)
            QTest.mouseMove(viewport, centre + QPoint(70, 35), 30)
            QTest.mouseRelease(viewport, Qt.RightButton, Qt.NoModifier, centre + QPoint(70, 35))
            QApplication.processEvents()
            REPORT["orbit_keeps_coordinates"] = panel.value("u") == before_orbit

            # Return to centre and drag directly on the target face. This is the
            # same mouse path used by a person, including viewport hit testing.
            field.set_value(0)
            panel.preview()
            start = viewport.project(fingerprint(target.shape, "face").center)
            start = QPoint(round(start[0]), round(start[1]))
            QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, start)
            QTest.mouseMove(viewport, start + QPoint(35, -18), 40)
            QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, start + QPoint(35, -18))
            QApplication.processEvents()
            REPORT["freeform_drag"] = (
                abs(panel.value("u")) > .01 or abs(panel.value("v")) > .01
            )
            REPORT["live_offsets_visible"] = panel._frame_overlay.isVisible()

            boundary = panel.fields["boundary"]
            click(boundary, boundary.rect().center())
            QTest.keyClick(boundary, Qt.Key_A, Qt.ControlModifier)
            QTest.keyClicks(boundary, "0.75")
            QTimer.singleShot(1500, lambda: finish_replace(panel, frame))

        def finish_replace(panel, frame):
            QApplication.processEvents()
            REPORT["section_preview"] = (
                panel.confirm.isEnabled()
                and len(panel._section_overlays) >= 2
                and getattr(window.stage.viewport, "_ghost", None) is not None
            )
            u, v = panel.value("u"), panel.value("v")
            # Deliver the same real button press a user generates. Sending the
            # coordinate through the native top-level window is useful for GL
            # hit-testing above, but is unreliable for a button nested inside
            # the panel's scroll-aware layout on high-DPI displays.
            QTest.mouseClick(
                panel.confirm, Qt.LeftButton, Qt.NoModifier,
                panel.confirm.rect().center(),
            )
            QApplication.processEvents()
            window.wait_for_rebuild()
            QApplication.processEvents()
            REPORT["replacement_committed"] = (
                list(window.document.bodies) == ["Wall"]
                and window.document.features[-1].type_name == "section_replace"
                and is_valid(window.document.body("Wall").shape)
            )
            point = frame.point(u, v)
            shape = window.document.body("Wall").shape
            REPORT["vent_opening_clear"] = all(
                BRepClass3d_SolidClassifier(
                    shape, gp_Pnt(point[0], point[1], z), 1e-6
                ).State() == TopAbs_OUT
                for z in (.25, 1.0, 2.0, 3.9)
            )
            click(window.top_bar.buttons["undo"], window.top_bar.buttons["undo"].rect().center())
            window.wait_for_rebuild()
            QApplication.processEvents()
            REPORT["undo_restores_vent"] = set(window.document.bodies) == {"Wall", "Vent"}
            click(window.top_bar.buttons["redo"], window.top_bar.buttons["redo"].rect().center())
            window.wait_for_rebuild()
            QApplication.processEvents()
            REPORT["redo_replaces_again"] = list(window.document.bodies) == ["Wall"]
            window.stage.viewport.fit_all()
            window.set_hint("Placement and Section Replace verified")

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(300, populate))
        return window

    run_and_capture(
        build, "/tmp/simplecad-placement.png", settle_ms=11_000, size=(1300, 860)
    )
    print("--- placement and section replace ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    ok = len(REPORT) == 12 and all(value is True for value in REPORT.values())
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
