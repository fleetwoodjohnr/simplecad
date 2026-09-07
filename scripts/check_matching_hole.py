#!/usr/bin/env python
"""The two-part workflow, through the real window.

Thread one part, select it together with where the mate goes, and ask for the
matching thread. Four shapes of target, because "where the mate goes" is not one
thing:

* a **flat face** -- drill a threaded hole into it;
* an **existing bore** that is already the right size -- thread it;
* a **whole tube**, selected as an object rather than a face -- find its one
  bore and thread that, which is how a cap gets made;
* a **bore that is the wrong size** -- refuse until the user says to open it,
  then open it and thread it.

The existing ``check_matching_thread.py`` covers the free-standing bolt and nut,
which is the one path that always worked; none of these were covered at all,
which is how they came to be broken.
"""

from __future__ import annotations

import sys

from _harness import grab_composited, run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}
CASES = ("flat_face", "existing_bore", "whole_tube", "undersize_bore")
M24 = "--m24" in sys.argv
OUT_PATH = "/tmp/simplecad-matching-hole-m24.png" if M24 else "/tmp/simplecad-matching-hole.png"


def main() -> int:
    from PySide6.QtCore import QCoreApplication, QEventLoop
    from PySide6.QtWidgets import QApplication

    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    def spin(ms: int = 30) -> None:
        QCoreApplication.processEvents(QEventLoop.AllEvents, ms)

    def wait_until(test, seconds: float = 90.0) -> bool:
        import time

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if test():
                return True
            spin()
        return test()

    def build(_app):
        window = MainWindow(Mode.DARK)

        def panel():
            return next(
                (w for w, _a in window.stage.overlays
                 if type(w).__name__ == "MatchingPartPanel"),
                None,
            )

        def finish() -> None:
            """Capture and quit as soon as the work is done.

            ``run_and_capture`` waits out its whole settle window before it
            grabs anything, which for a script that builds five threads would
            mean sitting idle for minutes after the last assertion. The settle
            time stays as a backstop for a hang; on a normal run this gets there
            first.
            """
            try:
                grab_composited(window).save(OUT_PATH)
            except Exception:  # noqa: BLE001 - the picture is a courtesy
                pass
            QApplication.instance().quit()

        def run() -> None:
            from simplecad.core.document import BodyRef
            from simplecad.core.naming import make_ref, sub_shapes
            from simplecad.kernel.detect import (
                analyse_cylinder, analyse_plane, cylindrical_faces,
            )
            from simplecad.kernel.operations import HoleFeature, ThreadFeature
            from simplecad.kernel.primitives import BoxFeature, CylinderFeature, TubeFeature
            from simplecad.ui.selection import Picked, available_actions

            document = window.document

            def top_face(shape):
                best, height = None, -1e9
                for face in sub_shapes(shape, "face"):
                    info = analyse_plane(face)
                    if info and abs(info.normal[2]) > 0.99 and info.center[2] > height:
                        best, height = face, info.center[2]
                return best

            def bore(shape):
                for face in sub_shapes(shape, "face"):
                    info = analyse_cylinder(face)
                    if info is not None and info.internal:
                        return face, info
                return None, None

            def picked(name, shape=None, kind=None):
                body = document.body(name)
                shape = body.shape if shape is None else shape
                info = None
                if kind == "face":
                    info = analyse_plane(shape) or analyse_cylinder(shape)
                return Picked(
                    body=name, kind=kind or "solid", shape=shape,
                    presentation=window._presentations.get(name), info=info,
                )

            # -- the source thread, and three parts to mate with it ----------
            radius = 12 if M24 else 6
            diameter = 2 * radius
            post = document.add_feature(
                CylinderFeature(inputs={"radius": radius, "height": 20}, outputs=["Post"])
            )
            plates = {}
            for name, x, hole in (
                ("Plate", 60, None), ("Bored", 120, diameter), ("Small", 180, diameter - 4)
            ):
                plates[name] = document.add_feature(BoxFeature(
                    inputs={"width": 40, "depth": 40, "height": 10, "x": x},
                    outputs=[name],
                ))
            cap = document.add_feature(TubeFeature(
                inputs={"outer_radius": radius + 4, "inner_radius": radius, "height": 14, "x": 240},
                outputs=["Cap"],
            ))
            window.rebuild()
            window.wait_for_rebuild()

            for name, bore_diameter in (("Bored", diameter), ("Small", diameter - 4)):
                face = top_face(document.body(name).shape)
                document.add_feature(HoleFeature(
                    inputs={
                        "body": BodyRef(name),
                        "face": make_ref(
                            document.body(name).shape, face, plates[name].id, body=name
                        ),
                        "diameter": bore_diameter,
                        "depth_mode": "through",
                        "position": tuple(analyse_plane(face).center),
                    },
                    outputs=[name],
                ))
            window.rebuild()
            window.wait_for_rebuild()

            face = cylindrical_faces(document.body("Post").shape)[0][0]
            document.add_feature(ThreadFeature(
                inputs={
                    **({"designation": "M24"} if M24 else {}),
                    "body": BodyRef("Post"),
                    "face": make_ref(
                        document.body("Post").shape, face, post.id, body="Post"
                    ),
                    "length": 12, "clearance": "normal",
                    "form": "printed", "from_end": "top",
                },
                outputs=["Post"],
            ))
            window.rebuild()
            window.wait_for_rebuild()
            REPORT["source_thread"] = bool(document.threads_on("Post"))
            if M24:
                REPORT["source_m24"] = document.threads_on("Post")[0]["designation"] == "M24"

            # -- one pass per shape of target --------------------------------
            def attempt(case: str, target: Picked, kind: str, allow_resize=False):
                window.selection.picks = [picked("Post"), target]
                offered = [k for k, _l, _i in available_actions(window.selection)]
                window.activate_tool("matching_part")
                spin()
                tool = panel()
                if tool is None:
                    REPORT[case] = "no panel"
                    return
                REPORT[f"{case}_offered"] = "matching_part" in offered
                REPORT[f"{case}_default_kind"] = tool.kind
                if allow_resize:
                    # It must refuse first, and say why, before it is told to.
                    wait_until(lambda: tool.status.text() != "", 60.0)
                    REPORT[f"{case}_refused"] = (
                        not tool._preview_valid
                        and "resize" in tool.status.text().lower()
                    )
                    REPORT[f"{case}_asks_visible"] = tool.resize.isVisible()
                    REPORT[f"{case}_asks"] = tool.resize.text()
                    tool.resize.setChecked(True)
                if tool.kind != kind:
                    tool.choose(kind)
                wait_until(lambda: tool._preview_valid, 120.0)
                REPORT[f"{case}_preview"] = tool._preview_valid
                REPORT[f"{case}_status"] = tool.status.text()[:90]
                if not tool._preview_valid:
                    REPORT[case] = False
                    window.cancel_tool()
                    return
                threaded = target.body
                tool.commit()
                window.wait_for_rebuild()
                spin()
                REPORT[case] = bool(document.threads_on(threaded))

            plate_face = top_face(document.body("Plate").shape)
            attempt("flat_face", picked("Plate", plate_face, "face"), "hole")
            from simplecad.kernel.thread_specs import by_designation
            from simplecad.kernel.threads import required_bore

            source = document.threads_on("Post")[0]
            wanted = required_bore(by_designation(source["designation"]), source["clearance"])
            bores = [info.diameter for _face, info in cylindrical_faces(document.body("Plate").shape)
                     if info.internal]
            REPORT["flat_face_clearance"] = bool(bores) and abs(max(bores) - wanted) < .01

            bored_face, _info = bore(document.body("Bored").shape)
            attempt("existing_bore", picked("Bored", bored_face, "face"), "apply")

            attempt("whole_tube", picked("Cap"), "apply")

            small_face, _info = bore(document.body("Small").shape)
            attempt(
                "undersize_bore", picked("Small", small_face, "face"), "apply",
                allow_resize=True,
            )

            window.stage.viewport.fit_all()
            window.set_hint("Matching threads created on a face, a bore and a tube")

        from PySide6.QtCore import QTimer

        def run_then_finish() -> None:
            try:
                run()
            except BaseException as exc:  # noqa: BLE001 - report, never hang
                REPORT["error"] = f"{type(exc).__name__}: {exc}"
            QTimer.singleShot(300, finish)

        window.stage.viewport.ready.connect(
            lambda: QTimer.singleShot(200, run_then_finish)
        )
        return window

    run_and_capture(
        build, OUT_PATH, settle_ms=600000, size=(1300, 820)
    )
    print("--- matching hole ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    expected = {
        "source_thread": True,
        "flat_face_clearance": True,
        "flat_face": True, "flat_face_offered": True, "flat_face_default_kind": "hole",
        "existing_bore": True, "existing_bore_offered": True,
        "existing_bore_default_kind": "apply",
        "whole_tube": True, "whole_tube_offered": True,
        "whole_tube_default_kind": "apply",
        "undersize_bore": True, "undersize_bore_refused": True,
        "undersize_bore_asks_visible": True,
    }
    if M24:
        expected["source_m24"] = True
    bad = [k for k, want in expected.items() if REPORT.get(k) != want]
    for key in bad:
        print(f"  MISMATCH {key}: {REPORT.get(key)!r} (wanted {expected[key]!r})")
    print("VERDICT:", "PASS" if not bad else "FAIL")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
