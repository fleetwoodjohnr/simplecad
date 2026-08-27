#!/usr/bin/env python
"""The contextual toolbar: does it load completely, every time?

The regression guard for the bug this bar had -- appearing with only part of
its tools, or with the previous selection's tools, so that the user had to
deselect and reselect to get what they wanted.

Cycles through every kind of selection and after each one checks four things:
the bar is up, every action that applies is present (shown or one click away in
the overflow, but never silently dropped), every button drew its icon, and the
bar's geometry is already final -- measured twice, with the event loop turned in
between, because the original bug was precisely that the first measurement was
taken against the *previous* selection's layout and the bar jumped a frame later.
"""

from __future__ import annotations

import os
import sys

os.environ["SIMPLECAD_NO_GRID"] = "1"

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}
FAILURES: list[str] = []


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def build(_app):
        window = MainWindow(Mode.DARK)

        def run() -> None:
            from OCP.TopAbs import (
                TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID, TopAbs_VERTEX,
            )
            from OCP.TopExp import TopExp_Explorer
            from OCP.TopoDS import TopoDS

            KINDS = {TopAbs_SOLID: "solid", TopAbs_FACE: "face"}

            from simplecad.kernel.detect import analyse_plane
            from simplecad.kernel.primitives import BoxFeature
            from simplecad.ui.selection import Picked

            for name, x in (("Base", 0.0), ("Lid", 60.0)):
                feature = BoxFeature(
                    inputs={"width": 40, "depth": 30, "height": 20},
                    outputs=[name],
                )
                feature.name = name
                window.add_feature(feature)
                if x:
                    from simplecad.core.document import BodyRef
                    from simplecad.kernel.operations import MoveFeature

                    window.document.add_feature(
                        MoveFeature(
                            inputs={"body": BodyRef(name), "dx": x}, outputs=[name]
                        )
                    )
            window.rebuild()
            window.wait_for_rebuild()
            window.stage.viewport.fit_all()

            base = window.document.bodies["Base"].shape
            lid = window.document.bodies["Lid"].shape
            prs = window._presentations

            def sub(shape, kind):
                enum = {"face": TopAbs_FACE, "edge": TopAbs_EDGE,
                        "vertex": TopAbs_VERTEX}[kind]
                explorer = TopExp_Explorer(shape, enum)
                while explorer.More():
                    found = explorer.Current()
                    if kind != "face" or analyse_plane(TopoDS.Face_s(found)):
                        return found
                    explorer.Next()
                return None

            def pick(body, kind, shape):
                """A Picked exactly as SelectionModel.refresh would build it.

                Two details matter and both have bitten. A face needs its
                PlaneInfo, or it is not a *flat* face as far as the action rules
                are concerned. And a whole body is reported by its OCCT shape
                type, which for a primitive is "solid" and not "body" -- so
                asking for "body" here would test a selection the viewport never
                actually produces.
                """
                info = analyse_plane(shape) if kind == "face" else None
                if kind == "body":
                    kind = KINDS.get(shape.ShapeType(), "body")
                return Picked(body=body, kind=kind, shape=shape,
                              presentation=prs[body], info=info)

            cases = [
                ("planar face", [pick("Base", "face", sub(base, "face"))],
                 {"pushpull", "hole", "sketch", "shell", "move", "measure"}),
                ("edge", [pick("Base", "edge", sub(base, "edge"))],
                 {"fillet", "chamfer", "move", "measure"}),
                ("corner", [pick("Base", "vertex", sub(base, "vertex"))],
                 {"fillet", "chamfer", "measure"}),
                ("one body", [pick("Base", "body", base)],
                 {"move", "rotate", "scale", "split", "duplicate", "measure",
                  "hide", "delete"}),
                ("two bodies", [pick("Base", "body", base),
                                pick("Lid", "body", lid)],
                 {"group", "move", "rotate", "scale", "duplicate", "measure",
                  "hide", "delete"}),
            ]

            bar = window.context_bar
            observed = {}
            for label, picks, expected in cases:
                window.selection.picks = picks
                window.refresh_context_bar()
                QApplication.processEvents()

                offered = set(bar.keys())
                geometry_first = bar.geometry()
                QApplication.processEvents()
                QApplication.processEvents()
                geometry_second = bar.geometry()

                icons_ok = all(
                    not b.icon().isNull() for b in bar._buttons
                )
                # Every button the user can see must be at its natural width.
                # A bar sized from a stale layout squeezes them all down to
                # slivers with the labels and icons clipped away -- which looks
                # exactly like a toolbar that failed to finish loading.
                shown_buttons = [b for b in bar._buttons if not b.isHidden()]
                sized = all(
                    b.width() >= b.sizeHint().width() for b in shown_buttons
                )
                observed[label] = {
                    "visible": bar.isVisible(),
                    "shown": bar.visible_keys(),
                    "overflow": bar.overflow_keys(),
                    "missing": sorted(expected - offered),
                    "icons_ok": icons_ok,
                    "not_squeezed": sized,
                    "bar_at_natural_width": bar._fits(),
                    "stable": geometry_first == geometry_second,
                }
                if not bar.isVisible():
                    FAILURES.append(f"{label}: bar not visible")
                if expected - offered:
                    FAILURES.append(f"{label}: missing {sorted(expected - offered)}")
                if not icons_ok:
                    FAILURES.append(f"{label}: a button has no icon")
                if not sized:
                    FAILURES.append(
                        f"{label}: buttons are squeezed below their labels — "
                        f"{[(b.text(), b.width(), b.sizeHint().width()) for b in shown_buttons]}"
                    )
                if not bar._fits():
                    FAILURES.append(
                        f"{label}: the bar is {bar.width()} px but needs "
                        f"{bar.sizeHint().width()}"
                    )
                if geometry_first != geometry_second:
                    FAILURES.append(
                        f"{label}: geometry shifted after layout "
                        f"({geometry_first} -> {geometry_second})"
                    )

            REPORT["cases"] = observed

            # A group reads as one object and offers Ungroup.
            window.group_names(["Base", "Lid"])
            window.selection.picks = [pick("Base", "body", base),
                                      pick("Lid", "body", lid)]
            window.refresh_context_bar()
            QApplication.processEvents()
            keys = set(bar.keys())
            REPORT["group_actions"] = sorted(keys)
            if "ungroup" not in keys:
                FAILURES.append("group: no Ungroup offered")
            if "group" in keys:
                FAILURES.append("group: offered to group a group with itself")
            if "split" in keys:
                FAILURES.append("group: offered to split a group")

            # Nothing selected: the bar goes away entirely.
            window.selection.picks = []
            window.refresh_context_bar()
            QApplication.processEvents()
            REPORT["hidden_when_empty"] = not bar.isVisible()
            if bar.isVisible():
                FAILURES.append("empty selection: bar still visible")

            # A narrow window must overflow rather than overlap its neighbours.
            window.selection.picks = [pick("Base", "body", base)]
            window.resize(760, 600)
            QApplication.processEvents()
            window.refresh_context_bar()
            QApplication.processEvents()
            REPORT["narrow_shown"] = len(bar.visible_keys())
            REPORT["narrow_overflow"] = len(bar.overflow_keys())
            REPORT["narrow_total"] = len(bar.keys())
            REPORT["narrow_fits"] = bar.width() <= window.stage.width()
            REPORT["narrow_not_squeezed"] = all(
                b.width() >= b.sizeHint().width()
                for b in bar._buttons if not b.isHidden()
            )
            if not REPORT["narrow_not_squeezed"]:
                FAILURES.append("narrow: the remaining buttons are squeezed")
            # Base is inside the group now, so a click on it selects the pair.
            expected_narrow = len(bar.visible_keys()) + len(bar.overflow_keys())
            if len(bar.keys()) != expected_narrow:
                FAILURES.append("narrow: actions were lost, not overflowed")
            if not bar.overflow_keys():
                FAILURES.append("narrow: nothing overflowed in a narrow window")
            if bar.width() > window.stage.width():
                FAILURES.append("narrow: the bar is wider than the window")
            window.resize(1360, 860)
            QApplication.processEvents()
            window.refresh_context_bar()
            QApplication.processEvents()
            REPORT["wide_again_shown"] = len(bar.visible_keys())
            REPORT["wide_again_not_squeezed"] = all(
                b.width() >= b.sizeHint().width()
                for b in bar._buttons if not b.isHidden()
            )
            if not REPORT["wide_again_not_squeezed"]:
                FAILURES.append(
                    "widening the window again left the bar squeezed"
                )
            if len(bar.visible_keys()) <= REPORT["narrow_shown"]:
                FAILURES.append("widening the window did not bring buttons back")
            window.set_hint("Contextual toolbar checked across five selections")

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(500, run))
        return window

    run_and_capture(build, "docs/shots/context_bar.png", settle_ms=4500,
                    size=(1360, 860))
    print("--- contextual toolbar ---")
    for label, data in (REPORT.get("cases") or {}).items():
        print(f"  {label}:")
        for key, value in data.items():
            print(f"      {key}: {value}")
    for key in ("group_actions", "hidden_when_empty", "narrow_shown",
                "narrow_overflow", "narrow_total", "narrow_fits"):
        if key in REPORT:
            print(f"  {key}: {REPORT[key]}")
    for failure in FAILURES:
        print(f"  FAIL: {failure}")
    ok = bool(REPORT.get("cases")) and not FAILURES
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
