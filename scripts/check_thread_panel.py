#!/usr/bin/env python
"""Threading must ask what thread, and say when the answer will not print.

Two failures this exists for.

**"It makes the hole but there is still no thread."** The Hole tool's threaded
style used to ask for nothing, so the kernel matched the size itself and
answered a 5 mm hole with M5 -- a 0.25 mm tooth, real geometry that is invisible
on screen and finer than any nozzle resolves. The size is now the user's to
choose and the warning is said before Create.

**Opening Thread before picking the face.** Panels are built once, so the size
list was empty for good and the feature committed with no size at all.

Run:  .venv/bin/python scripts/check_thread_panel.py
"""

from __future__ import annotations

import sys

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}


def panel_named(window, name):
    return next(
        (w for w, _a in window.stage.overlays if type(w).__name__ == name), None
    )


def main() -> int:
    from PySide6.QtCore import QPoint, Qt, QTimer
    from PySide6.QtTest import QTest

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def build(_app):
        window = MainWindow(Mode.DARK)

        def populate() -> None:
            from simplecad.core.document import BodyRef
            from simplecad.kernel.operations import MoveFeature
            from simplecad.kernel.primitives import BoxFeature, CylinderFeature

            window.add_feature(
                BoxFeature(inputs={"width": 50, "depth": 40, "height": 30},
                           outputs=["Block"])
            )
            window.add_feature(
                CylinderFeature(inputs={"radius": 6, "height": 30},
                                outputs=["Post"])
            )
            window.add_feature(
                MoveFeature(inputs={"body": BodyRef("Post"), "dx": 90},
                            outputs=["Post"])
            )
            window.wait_for_rebuild()
            window.stage.viewport.fit_all()
            QTimer.singleShot(700, probe)

        def click(point) -> None:
            viewport = window.stage.viewport
            ratio = viewport.devicePixelRatioF()
            x, y = viewport.view.Convert(point[0], point[1], point[2])
            where = QPoint(int(x / ratio), int(y / ratio))
            QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, where)

        def probe() -> None:
            # -- the Hole tool, threaded ---------------------------------
            click((25.0, 20.0, 30.0))               # the top face of the block
            window.activate_tool("hole")
            hole = panel_named(window, "HolePanel")
            REPORT["hole_panel"] = hole is not None
            if hole is None:
                return
            REPORT["thread_group_hidden_when_simple"] = (
                not hole._thread_group.isVisible()
            )
            hole._choose("threaded")
            REPORT["thread_group_shown"] = hole._thread_group.isVisible()
            REPORT["sizes_for_6mm"] = hole.sizes.currentData()

            # A sub-nozzle size must say so up front, in the panel, rather than
            # in a hint line the next click wipes. ⌀5 is the diameter from the
            # report: its exact match is M5, whose printed tooth is 0.25 mm.
            hole.fields["diameter"].set_value(5.0)
            hole._refresh_sizes()
            offered = [hole.sizes.itemData(i) for i in range(hole.sizes.count())]
            REPORT["offered_for_5mm"] = offered
            # A printable way out has to be on the list beside the fine one.
            REPORT["printable_alternative_offered"] = any(
                str(name).startswith("P") for name in offered
            )
            hole.sizes.setCurrentIndex(hole.sizes.findData("M5"))
            REPORT["sizes_for_5mm"] = hole.sizes.currentData()
            REPORT["warns_before_create"] = hole.status.isVisible()
            REPORT["warning"] = hole.status.text()

            # And choosing a coarse one takes the warning away again, so it
            # reads as a live answer rather than a sticky complaint.
            hole.sizes.setCurrentIndex(hole.sizes.findData("P6"))
            REPORT["warning_clears_on_a_coarse_size"] = not hole.status.text()

            hole.fields["diameter"].set_value(6.0)
            hole._refresh_sizes()
            hole.from_end.setCurrentIndex(1)        # from the bottom
            hole.commit()
            window.wait_for_rebuild()
            feature = window.document.features[-1]
            REPORT["hole_inputs"] = {
                key: feature.inputs.get(key)
                for key in ("style", "designation", "from_end", "clearance", "form")
            }
            REPORT["hole_rebuilt_clean"] = feature.message in ("", None)
            # A clean feature is not the same as a threaded one. Reporting done
            # while handing back an untouched body is the exact failure this
            # round of work was about, so look at the geometry rather than at
            # what the feature says about itself.
            from simplecad.core.naming import sub_shapes
            from simplecad.kernel.detect import analyse_cylinder
            from simplecad.kernel.occ import volume

            block = window.document.bodies["Block"].shape
            REPORT["block_volume_mm3"] = round(volume(block), 1)
            REPORT["block_lost_material"] = volume(block) < 50 * 40 * 30 - 1.0
            faces = sub_shapes(block, "face")
            REPORT["block_faces"] = len(faces)
            # A plain box has six. A bore adds at least one round face, and the
            # thread cut into it adds its flanks.
            bores = [
                info for info in (analyse_cylinder(face) for face in faces)
                if info is not None and info.internal
            ]
            REPORT["internal_round_faces"] = len(bores)
            # -- the Thread tool, opened before the face is picked --------
            window.stage.viewport.clear_selection()
            window.activate_tool("thread")
            thread = panel_named(window, "ThreadPanel")
            REPORT["thread_panel"] = thread is not None
            if thread is None:
                return
            REPORT["empty_before_a_face_is_picked"] = thread.sizes.count() == 0

            click((90.0 + 4.243, -4.243, 15.0))     # the post's wall
            REPORT["fills_in_when_the_face_is_picked"] = thread.sizes.count() > 0
            REPORT["thread_size_offered"] = thread.sizes.currentData()
            REPORT["subtitle"] = thread.subtitle.text()
            QTimer.singleShot(8000, lambda: record_thread_preview(thread))
            window.set_hint("Thread, opened before the face was picked")

        def record_thread_preview(thread) -> None:
            REPORT["thread_preview_valid"] = thread._preview_valid
            REPORT["thread_preview_visible"] = (
                window.stage.viewport._ghost is not None
            )
            REPORT["thread_create_enabled"] = thread.confirm.isEnabled()

        window.stage.viewport.ready.connect(populate)
        return window

    # Long enough that the capture never races the work: a threaded rebuild is
    # ~3 s of OCCT in the geometry process, and the shot timer quitting in the
    # middle of it tore the child down and left the block undrilled -- which
    # this script then cheerfully reported as a clean feature.
    run_and_capture(build, "docs/shots/thread_panel.png",
                    settle_ms=30000, size=(1200, 820))
    print("--- threading panels ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    inputs = REPORT.get("hole_inputs") or {}
    ok = (
        REPORT.get("hole_panel") is True
        and REPORT.get("thread_group_hidden_when_simple") is True
        and REPORT.get("thread_group_shown") is True
        and REPORT.get("sizes_for_6mm") == "P6"
        and REPORT.get("sizes_for_5mm") == "M5"
        and REPORT.get("printable_alternative_offered") is True
        and REPORT.get("warns_before_create") is True
        and "0.25 mm" in str(REPORT.get("warning"))
        and REPORT.get("warning_clears_on_a_coarse_size") is True
        and inputs.get("style") == "threaded"
        and inputs.get("designation") == "P6"
        and inputs.get("from_end") == "bottom"
        and inputs.get("clearance") and inputs.get("form")
        and REPORT.get("hole_rebuilt_clean") is True
        and REPORT.get("block_lost_material") is True
        and REPORT.get("block_faces", 0) > 7
        and REPORT.get("internal_round_faces", 0) >= 1
        and REPORT.get("thread_panel") is True
        and REPORT.get("empty_before_a_face_is_picked") is True
        and REPORT.get("fills_in_when_the_face_is_picked") is True
        and REPORT.get("thread_size_offered") == "P12"
        and REPORT.get("thread_preview_valid") is True
        and REPORT.get("thread_preview_visible") is True
        and REPORT.get("thread_create_enabled") is True
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
