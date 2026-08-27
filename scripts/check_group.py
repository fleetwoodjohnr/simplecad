#!/usr/bin/env python
"""Grouping: several objects handled as one, without being joined into one.

Multi-selects two bodies, groups them, and checks the whole point of the
feature: the tree shows one group with its members underneath, clicking either
member selects the pair, moving the group moves both, and -- the line that must
never be crossed -- the two bodies are still two bodies afterwards, with their
own geometry and their own history. Then reaches inside for a single member, and
ungroups.
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

    def main_run(window) -> None:
        from simplecad.core.document import BodyRef
        from simplecad.kernel.occ import bounding_box, volume
        from simplecad.kernel.operations import MoveFeature
        from simplecad.kernel.primitives import BoxFeature

        for name, x in (("Base", 0.0), ("Lid", 60.0), ("Pin", 120.0)):
            feature = BoxFeature(
                inputs={"width": 40, "depth": 30, "height": 20}, outputs=[name]
            )
            feature.name = name
            window.add_feature(feature)
            if x:
                window.document.add_feature(
                    MoveFeature(inputs={"body": BodyRef(name), "dx": x},
                                outputs=[name])
                )
        window.rebuild()
        window.wait_for_rebuild()
        viewport = window.stage.viewport
        viewport.fit_all()

        volumes_before = {
            n: round(volume(window.document.bodies[n].shape), 1)
            for n in ("Base", "Lid", "Pin")
        }
        REPORT["volumes_before"] = volumes_before

        # -- multi-select two bodies through the real selection path --------
        for name in ("Base", "Lid"):
            viewport.select_shape(window._presentations[name], replace=name == "Base")
        window.selection.refresh()
        window._on_selection()
        QApplication.processEvents()
        REPORT["selected_before_group"] = window.selection.bodies
        REPORT["group_offered"] = "group" in window.context_bar.keys()
        if "group" not in window.context_bar.keys():
            FAILURES.append("Group is not offered for a two-body selection")

        # -- group ----------------------------------------------------------
        window.group_selection()
        QApplication.processEvents()
        REPORT["groups"] = {
            name: list(g.members) for name, g in window.document.groups.items()
        }
        if len(window.document.groups) != 1:
            FAILURES.append(f"expected one group, got {REPORT['groups']}")

        group_name = next(iter(window.document.groups), None)
        REPORT["bodies_still_separate"] = sorted(window.document.bodies)
        if sorted(window.document.bodies) != ["Base", "Lid", "Pin"]:
            FAILURES.append("grouping changed the bodies")
        after = {
            n: round(volume(window.document.bodies[n].shape), 1)
            for n in ("Base", "Lid", "Pin")
        }
        REPORT["volumes_after_group"] = after
        if after != volumes_before:
            FAILURES.append("grouping altered geometry — it must never do that")

        # -- the tree shows the membership ----------------------------------
        window.browser.refresh()
        tree = window.browser.bodies_tree
        rows = []
        for index in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(index)
            rows.append((
                item.data(0, 257),                       # KIND_ROLE
                item.data(0, 256),                       # NAME_ROLE
                [item.child(c).data(0, 256) for c in range(item.childCount())],
            ))
        REPORT["tree"] = rows
        group_rows = [r for r in rows if r[0] == "group"]
        if len(group_rows) != 1 or sorted(group_rows[0][2]) != ["Base", "Lid"]:
            FAILURES.append(f"the tree does not show the group's members: {rows}")
        if not any(r[0] == "body" and r[1] == "Pin" for r in rows):
            FAILURES.append("the ungrouped body left the top level of the tree")

        # -- clicking one member selects the group --------------------------
        viewport.select_shape(window._presentations["Base"])
        window._on_selection()
        QApplication.processEvents()
        REPORT["click_selects_group"] = sorted(window.selection.bodies)
        if sorted(window.selection.bodies) != ["Base", "Lid"]:
            FAILURES.append("clicking a member did not select the whole group")
        REPORT["group_toolbar"] = window.context_bar.keys()
        if "ungroup" not in window.context_bar.keys():
            FAILURES.append("Ungroup is not offered for a selected group")

        # -- moving the group moves all of it, together ---------------------
        low_before = {
            n: bounding_box(window.document.bodies[n].shape)[0]
            for n in ("Base", "Lid", "Pin")
        }
        window.activate_tool("move")
        panel = next(
            (w for w, _a in window.stage.overlays
             if type(w).__name__ == "MovePanel"), None
        )
        if panel is None:
            FAILURES.append("Move did not open for a group")
        else:
            panel.fields["dz"].set_value(25.0)
            panel.commit()
            window.wait_for_rebuild()
            moved = {
                n: bounding_box(window.document.bodies[n].shape)[0]
                for n in ("Base", "Lid", "Pin")
            }
            REPORT["moved_dz"] = {
                n: round(moved[n][2] - low_before[n][2], 2)
                for n in ("Base", "Lid", "Pin")
            }
            if REPORT["moved_dz"] != {"Base": 25.0, "Lid": 25.0, "Pin": 0.0}:
                FAILURES.append(f"the group did not move as one: {REPORT['moved_dz']}")
            still = {
                n: round(volume(window.document.bodies[n].shape), 1)
                for n in ("Base", "Lid", "Pin")
            }
            if still != volumes_before:
                FAILURES.append("moving the group changed its geometry")

        # -- hiding a group hides all of it ---------------------------------
        window.set_group_visible(group_name, False)
        QApplication.processEvents()
        REPORT["hidden"] = [
            n for n in ("Base", "Lid", "Pin")
            if not window.document.bodies[n].visible
        ]
        if sorted(REPORT["hidden"]) != ["Base", "Lid"]:
            FAILURES.append("hiding the group did not hide exactly its members")
        window.set_group_visible(group_name, True)
        QApplication.processEvents()

        # -- reaching inside for one member ---------------------------------
        window.stage.viewport._pick_inside_group = True
        try:
            viewport.select_shape(window._presentations["Lid"])
            window.selection.refresh()
            window._on_selection()
        finally:
            window.stage.viewport._pick_inside_group = False
        REPORT["inside_group"] = window.selection.bodies
        if window.selection.bodies != ["Lid"]:
            FAILURES.append("could not select one object inside a group")

        # -- ungroup ---------------------------------------------------------
        window.ungroup(group_name)
        QApplication.processEvents()
        REPORT["groups_after_ungroup"] = dict(window.document.groups)
        REPORT["bodies_after_ungroup"] = sorted(window.document.bodies)
        if window.document.groups:
            FAILURES.append("the group survived Ungroup")
        if sorted(window.document.bodies) != ["Base", "Lid", "Pin"]:
            FAILURES.append("Ungroup lost a body")

        # -- and it all undoes ------------------------------------------------
        window.undo()
        window.wait_for_rebuild()
        QApplication.processEvents()
        REPORT["groups_after_undo"] = {
            name: list(g.members) for name, g in window.document.groups.items()
        }
        if len(window.document.groups) != 1:
            FAILURES.append("undoing Ungroup did not bring the group back")

        viewport.clear_selection()
        viewport.fit_all()
        window.set_hint("Grouped two bodies, moved them together, ungrouped")

    def build(_app):
        window = MainWindow(Mode.DARK)
        window.stage.viewport.ready.connect(
            lambda: QTimer.singleShot(500, lambda: main_run(window))
        )
        return window

    run_and_capture(build, "docs/shots/group.png", settle_ms=6000, size=(1340, 860))
    print("--- grouping ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    for failure in FAILURES:
        print(f"  FAIL: {failure}")
    ok = bool(REPORT.get("groups")) and not FAILURES
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
