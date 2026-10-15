#!/usr/bin/env python
"""The everyday operations, driven the way a person drives them.

Bodies are picked by clicking their row in the model tree or a face in the
view, actions come from clicking the context bar, and every key goes to
whichever window and widget actually has focus -- not to a widget chosen by the
script. That last part is the point: the other checks hand keys straight to
the viewport, so focus stranded on some unrelated button after a click went
unnoticed while Delete and the arrows quietly stopped reaching the model.

Copy and paste go through an in-memory stand-in: this never touches the
system clipboard.
"""

from __future__ import annotations

import math
import os
import sys

os.environ["SIMPLECAD_NO_GRID"] = "1"

from _harness import run_and_capture  # noqa: E402

RESULTS: list[tuple[str, bool, object]] = []


def main() -> int:
    from PySide6.QtCore import QMimeData, QPoint, Qt, QTimer
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    import simplecad.ui.main_window as main_window
    from check_gizmo import _find_handle
    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    class Clipboard:
        mime = None

        def setMimeData(self, mime):  # noqa: N802
            self.mime = QMimeData()
            for kind in mime.formats():
                self.mime.setData(kind, mime.data(kind))

        def mimeData(self):  # noqa: N802
            return self.mime or QMimeData()

    clipboard = Clipboard()

    class Application:
        def __getattr__(self, name):
            return getattr(QApplication, name)

        @staticmethod
        def clipboard():
            return clipboard

    main_window.QApplication = Application()

    def build(_app):
        window = MainWindow(Mode.DARK)
        view = window.stage.viewport
        tree = window.browser.bodies_tree
        document = lambda: window.document  # noqa: E731

        def settle(ms: int = 0) -> None:
            if ms:
                QTest.qWait(ms)
            window.wait_for_rebuild()
            for _ in range(10):
                QApplication.processEvents()

        def bounds(name):
            from simplecad.kernel.occ import bounding_box
            return bounding_box(document().body(name).shape)

        def click(widget, point) -> None:
            QTest.mouseClick(window.windowHandle(), Qt.LeftButton, Qt.NoModifier,
                             widget.mapTo(window, point))
            settle()

        def click_top_face(name) -> None:
            from simplecad.core.naming import fingerprint, sub_shapes
            top = max((f for f in sub_shapes(document().body(name).shape, "face")
                       if (d := fingerprint(f, "face").direction) and d[2] > 0.99),
                      key=lambda f: fingerprint(f, "face").center[2])
            x, y = view.project(fingerprint(top, "face").center)
            click(view, QPoint(round(x), round(y)))

        def pick_in_tree(name) -> bool:
            # Deselect in the view first, as Escape or an empty click would. The
            # tree must follow, or the row stays lit and cannot be clicked again.
            view.clear_selection()
            settle()
            rows = tree.findItems(name, Qt.MatchExactly | Qt.MatchRecursive)
            if not rows:
                return False
            tree.scrollToItem(rows[0])
            click(tree.viewport(), tree.visualItemRect(rows[0]).center())
            return [str(b) for b in window.selection.bodies] == [name]

        def key(code, modifiers=Qt.NoModifier) -> None:
            target = QGuiApplication.focusWindow()
            if target is not None:
                QTest.keyClick(target, code, modifiers)
            settle()

        def arrows(code, count=4) -> None:
            for _ in range(count):
                key(code)
            settle(600)          # past the nudge's quiet timer, then the rebuild

        def bar(action) -> bool:
            button = next((b for b in window.context_bar._buttons
                           if b.key == action and not b.isHidden()), None)
            if button is None:
                return False
            click(button, button.rect().center())
            return True

        def panel():
            return next((w for w, _a in window.stage.overlays
                         if getattr(w, "is_tool_panel", False) and not w.isHidden()), None)

        def added(before) -> str | None:
            new = sorted(set(document().bodies) - before)
            return new[0] if new else None

        def travelled(name, start) -> float:
            return round(math.dist(bounds(name)[0], start), 3)

        def check(label, ok, detail) -> None:
            RESULTS.append((label, bool(ok), detail))
            print(f"  [{'ok' if ok else 'FAIL'}] {label}: {detail}", flush=True)

        def step(label, body) -> None:
            try:
                body(label)
            except Exception as exc:  # noqa: BLE001 - report and carry on
                import traceback
                traceback.print_exc()
                check(label, False, repr(exc))
            window.cancel_tool()
            settle()

        def run() -> None:
            from simplecad.core.document import BodyRef
            from simplecad.kernel.primitives import BoxFeature, CylinderFeature
            from simplecad.kernel.split import SplitFeature

            window.add_feature(BoxFeature(inputs={"width": 20, "depth": 10, "height": 10},
                                          outputs=["A"]))
            window.add_feature(BoxFeature(inputs={"width": 30, "depth": 30, "height": 6, "x": 50},
                                          outputs=["B"]))
            window.add_feature(CylinderFeature(inputs={"radius": 6, "height": 12, "x": 110},
                                               outputs=["C"]))
            settle()
            window.add_feature(SplitFeature(inputs={
                "body": BodyRef("C"), "normal": [1, 0, 0], "origin": [110, 0, 0],
                "position": 0.0, "names": ["C L", "C R"]}, outputs=["C L", "C R"]))
            settle()
            view.fit_all()
            settle(300)

            def create(label):
                window.activate_tool("shapes")
                shapes = panel()
                shapes.choose("box")
                for field, value in (("width", "8"), ("depth", "8"), ("height", "8")):
                    shapes.fields[field].setText(value)
                    shapes.fields[field]._commit()
                before = set(document().bodies)
                shapes.commit()
                settle()
                new = added(before)
                check(label, new is not None, new)
                view.fit_all()
                settle(300)
            step("Create a box", create)

            def nudge(label):
                picked = pick_in_tree("A")
                start = bounds("A")[0]
                arrows(Qt.Key_Right)
                check(label, picked and travelled("A", start) == 1.0,
                      f"picked={picked} moved {travelled('A', start)} mm")
            step("Arrows move a body picked in the tree", nudge)

            def gizmo(label):
                picked = pick_in_tree("A")
                (x0, y0, z0), high = bounds("A")
                opened = bar("move")
                settle(900)      # a part under the panel is brought into view
                centre = [(a + b) / 2 for a, b in zip((x0, y0, z0), high)]
                axis, mm_per_pixel = view.screen_axis_for(centre, (1.0, 0.0, 0.0))
                handle = _find_handle(view, centre, want_axis=0)
                if handle is None:
                    check(label, False, f"picked={picked} opened={opened}: no X arrow reachable")
                    return
                pixels = 20.0 / mm_per_pixel / view.devicePixelRatioF()
                end = QPoint(round(handle.x() + axis[0] * pixels),
                             round(handle.y() + axis[1] * pixels))
                handle_ = window.windowHandle()
                QTest.mouseMove(handle_, view.mapTo(window, handle))
                QTest.mousePress(handle_, Qt.LeftButton, Qt.NoModifier, view.mapTo(window, handle))
                for index in range(1, 11):
                    QTest.mouseMove(handle_, view.mapTo(window, handle + (end - handle) * index / 10))
                QTest.mouseRelease(handle_, Qt.LeftButton, Qt.NoModifier, view.mapTo(window, end))
                settle()
                (x1, y1, z1), _ = bounds("A")
                check(label, abs(x1 - x0 - 20.0) < 3.0
                      and abs(y1 - y0) < 1e-6 and abs(z1 - z0) < 1e-6,
                      f"moved ({x1 - x0:.2f}, {y1 - y0:.3f}, {z1 - z0:.3f}) mm")
            step("Move: drag the gizmo's X arrow", gizmo)

            def rotate(label):
                pick_in_tree("A")
                bar("rotate")
                field = panel().fields["rz"]
                field.setText("90")
                field._commit()
                panel().commit()
                settle()
                (lx, ly, _), (hx, hy, _) = bounds("A")
                extent = (round(hx - lx, 2), round(hy - ly, 2))
                check(label, extent == (10.0, 20.0), f"X/Y extent {extent}")
            step("Rotate 90° about Z", rotate)

            def duplicate(label):
                pick_in_tree("A")
                before = set(document().bodies)
                key(Qt.Key_D, Qt.ControlModifier)
                copy = added(before)
                if copy is None:
                    check(label, False, "Ctrl+D made nothing")
                    return
                view.fit_all()
                settle(300)
                pick_in_tree(copy)
                start = bounds(copy)[0]
                arrows(Qt.Key_Right)
                moved = travelled(copy, start)
                key(Qt.Key_Delete)              # focus is in the tree
                check(label, moved == 1.0 and copy not in document().bodies
                      and "A" in document().bodies,
                      f"{copy} moved {moved} mm, deleted from the tree: "
                      f"{copy not in document().bodies}")
            step("Ctrl+D, move the copy, Delete it from the tree", duplicate)

            def copy_paste(label):
                pick_in_tree("B")
                key(Qt.Key_C, Qt.ControlModifier)
                before = set(document().bodies)
                key(Qt.Key_V, Qt.ControlModifier)
                pasted = added(before)
                if pasted is None:
                    check(label, False, "Ctrl+V made nothing")
                    return
                view.fit_all()
                settle(300)
                click_top_face(pasted)
                key(Qt.Key_Delete)              # focus is in the view
                check(label, pasted not in document().bodies and "B" in document().bodies,
                      f"{pasted} pasted and deleted from the view")
            step("Ctrl+C / Ctrl+V, Delete the paste", copy_paste)

            def split_half(label):
                pick_in_tree("C R")
                before = set(document().bodies)
                key(Qt.Key_D, Qt.ControlModifier)
                copy = added(before)
                view.fit_all()
                settle(300)
                if copy is not None:
                    pick_in_tree(copy)
                    key(Qt.Key_Delete)
                click_top_face("C L")
                key(Qt.Key_Delete)
                left = sorted(n for n in document().bodies if n.startswith("C"))
                check(label, copy is not None and left == ["C R"], f"copy {copy}, left {left}")
            step("Split half: duplicate, delete the copy and the other half", split_half)

            def stack(label):
                pick_in_tree("A")
                window.activate_tool("align")
                settle()
                click_top_face("B")             # the next click is the target
                align = panel()
                click(align.confirm, align.confirm.rect().center())
                (ax0, ay0, az0), (ax1, ay1, _) = bounds("A")
                (bx0, by0, _), (bx1, by1, bz1) = bounds("B")
                gap = round(az0 - bz1, 3)
                offset = (round((ax0 + ax1 - bx0 - bx1) / 2, 3), round((ay0 + ay1 - by0 - by1) / 2, 3))
                check(label, gap == 0 and offset == (0, 0), f"gap {gap}, centre offset {offset}")
            step("Stack A centred on B's top face", stack)

            def after_stack(label):
                pick_in_tree("A")
                start = bounds("A")[0]
                arrows(Qt.Key_Right)
                moved_a = travelled("A", start)
                pick_in_tree("B")
                start = bounds("B")[0]
                arrows(Qt.Key_Left)
                moved_b = travelled("B", start)
                click_top_face("A")
                key(Qt.Key_Delete)
                check(label, (moved_a, moved_b) == (1.0, 1.0) and "A" not in document().bodies
                      and "B" in document().bodies, f"moved A {moved_a}, B {moved_b}; A deleted")
            step("After stacking: move both parts, delete one", after_stack)

            def bar_then_keys(label):
                pick_in_tree("B")
                before = set(document().bodies)
                bar("duplicate")
                copy = added(before)
                if copy is None:
                    check(label, False, "the bar's Duplicate made nothing")
                    return
                start = bounds(copy)[0]
                arrows(Qt.Key_Right)
                moved = travelled(copy, start)
                key(Qt.Key_Delete)
                check(label, moved == 1.0 and copy not in document().bodies,
                      f"{copy} moved {moved} mm; focus "
                      f"{type(QApplication.focusWidget()).__name__}; deleted "
                      f"{copy not in document().bodies}")
            step("Context-bar Duplicate, then arrows and Delete", bar_then_keys)

            def panel_then_keys(label):
                pick_in_tree("B")
                start = bounds("B")[0]
                bar("move")
                move = panel()
                move.fields["dx"].setText("5")
                move.fields["dx"]._commit()
                click(move.confirm, move.confirm.rect().center())
                moved = travelled("B", start)
                # The closed panel's button had focus: it must go back to the
                # model rather than down the tab chain to some other button.
                focus = type(QApplication.focusWidget()).__name__
                click_top_face("B")
                key(Qt.Key_Delete)
                deleted = "B" not in document().bodies
                key(Qt.Key_Z, Qt.ControlModifier)
                check(label, moved == 5.0 and focus == type(view).__name__ and deleted
                      and "B" in document().bodies,
                      f"moved {moved} mm, focus then on {focus}, Delete {deleted}, "
                      f"Ctrl+Z restored {'B' in document().bodies}")
            step("Move panel's button, then Delete and Ctrl+Z", panel_then_keys)

            def zoom_to_selection(label):
                pick_in_tree("B")
                key(Qt.Key_F, Qt.ControlModifier | Qt.ShiftModifier)
                settle(900)      # the sweep
                low, high = bounds("B")
                x, y = view.project([(a + b) / 2 for a, b in zip(low, high)])
                off = math.dist((x, y), (view.width() / 2, view.height() / 2))
                check(label, off < 0.03 * view.width(), f"B's centre {off:.0f} px from the view's")
            step("Zoom to selection (Ctrl+Shift+F) frames the pick", zoom_to_selection)
            QApplication.instance().quit()

        view.ready.connect(lambda: QTimer.singleShot(500, run))
        return window

    run_and_capture(build, os.path.join(os.getcwd(), "everyday.png"),
                    settle_ms=90_000, size=(1300, 860))
    passed = len(RESULTS) == 12 and all(ok for _label, ok, _detail in RESULTS)
    print("VERDICT:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
