#!/usr/bin/env python
"""Stack & Place on a large real project: the window must not freeze.

On the Atlas Box (455 steps, an embedded 344-face board) placing the board
froze the window and then said the align had failed. This replays that
placement in a real window -- the saved sides and gaps typed key by key, the
saved quarter turns, Done -- timing how long the event loop is blocked and
recording every message.

    xvfb-run -a .venv/bin/python scripts/check_align_large.py COPY.scad3 [ALIGN]

COPY must be a scratch copy, never an original. The project's own history is
undone to just before the Align step named ALIGN (default Align64), which is
then made again through the panel with the saved choices; Done must put the
part exactly where the preview showed it.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
import zipfile

os.environ["SIMPLECAD_NO_GRID"] = "1"

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {"blocks": [], "messages": [], "solves": []}
#: The longest the event loop may stall on a change made in the panel. Opening
#: the panel and Done draw the part afresh, which 0.4's viewport takes about two
#: seconds to do for a 2944-face part; those are reported, not limited.
LIMIT_MS = 500
UNLIMITED = ("opening", "open Stack & Place", "measuring (the check's own work)", "Done", "finished")


def main() -> int:
    from PySide6.QtCore import QElapsedTimer, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from simplecad.core.document import SubShapeRef
    from simplecad.core.naming import resolve
    from simplecad.kernel.align import frame_from_basis
    from simplecad.kernel.occ import transformed

    def exact_box(shape):
        """Bounds from the geometry itself, not from whatever mesh is on it."""
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib

        box = Bnd_Box()
        BRepBndLib.AddOptimal_s(shape, box, False, True)
        low, high = box.CornerMin(), box.CornerMax()
        return ((low.X(), low.Y(), low.Z()), (high.X(), high.Y(), high.Z()))
    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    path = sys.argv[1]
    wanted = sys.argv[2] if len(sys.argv) > 2 else "Align64"
    model = json.loads(zipfile.ZipFile(path).read("model.json"))
    saved = next(f for f in model["features"] if f["name"] == wanted)
    made = saved["inputs"]
    moving_name = made["body"]["__body__"]
    target_name = made["target_face"]["__ref__"]["body"]
    REPORT["align"] = (wanted, moving_name, target_name)
    action = {"name": "opening"}

    def build(_app):
        window = MainWindow(Mode.DARK)
        clock, last = QElapsedTimer(), [0]
        clock.start()

        def tick():
            now = clock.elapsed()
            gap = now - last[0] - 10
            if last[0] and gap > LIMIT_MS:
                REPORT["blocks"].append((action["name"], gap))
            last[0] = now

        meter = QTimer(window)
        meter.timeout.connect(tick)
        meter.start(10)

        def doing(name):
            QApplication.processEvents()
            action["name"] = name

        def settle():
            window.wait_for_rebuild()
            QApplication.processEvents()

        def run():
            try:
                populate()
            except Exception:  # noqa: BLE001 - reported, then the check fails
                REPORT["error"] = traceback.format_exc()
            finally:
                QTimer.singleShot(200, QApplication.instance().quit)

        def populate():
            window._open_path(path)
            settle()
            # Back through the project's own history until the Align is gone.
            while any(f.name == wanted for f in window.document.features):
                window.undo()
                settle()
            document = window.document
            REPORT["moving_box_after_undo"] = exact_box(document.body(moving_name).shape)
            REPORT["bodies_after_undo"] = {
                name: [body.visible, body.id, body.producer,
                       exact_box(body.shape) if body.shape is not None else None]
                for name, body in document.bodies.items()}
            for name in (moving_name, target_name):
                if not document.body(name).visible:
                    window._set_body_visible(name, True)
            settle()
            moving_body = document.body(moving_name).shape
            target_body = document.body(target_name).shape
            REPORT["moving_box_shown"] = exact_box(moving_body)
            moving = resolve(SubShapeRef.from_dict(made["moving_face"]["__ref__"]), moving_body)
            target = resolve(SubShapeRef.from_dict(made["target_face"]["__ref__"]), target_body)
            viewport = window.stage.viewport
            viewport.select_subshape(window._presentations.get(moving_name), moving, replace=True)
            viewport.select_subshape(window._presentations.get(target_name), target, replace=False)
            window.selection.refresh()
            REPORT["picked"] = len(window.selection.faces()) == 2

            profile = None
            if os.environ.get("SIMPLECAD_PROFILE"):
                import cProfile

                profile = cProfile.Profile()
                profile.enable()
            doing("open Stack & Place")
            window.activate_tool("align")
            QApplication.processEvents()
            panel = next(w for w, _a in window.stage.overlays if getattr(w, "is_tool_panel", False))
            warn, solve = panel.warn, panel._solve

            def recorded_warn(text, *args, **kwargs):
                if text:
                    REPORT["messages"].append((action["name"], str(text)))
                return warn(text, *args, **kwargs)

            last = [None]

            def timed_solve(*args, **kwargs):
                start, failure = time.perf_counter(), ""
                try:
                    last[0] = solve(*args, **kwargs)
                    return last[0]
                except Exception as exc:
                    failure = f"{type(exc).__name__}: {exc}"
                    raise
                finally:
                    REPORT["solves"].append(
                        (action["name"], round(time.perf_counter() - start, 3), failure))

            panel.warn, panel._solve = recorded_warn, timed_solve
            # The saved Align64 latched this frame from the camera of the day;
            # use the same one so Done can be compared with what it made.
            frame = frame_from_basis(target, made["target_basis"], world_u=made["target_u"],
                                     world_normal=made["target_normal"])
            panel._target_frames = [{"face": target, "frame": frame, "basis": made["target_basis"],
                                     "ref": SubShapeRef.from_dict(made["target_face"]["__ref__"])}]
            panel._cached_frames = None

            sides = {"left": 0, "center": 1, "right": 2, "bottom": 0, "top": 2}
            rotation = float(str(made.get("rotation", "0")).split()[0].replace("°", "") or 0)
            doing("edge mode, saved sides")
            panel.horizontal_side.setCurrentIndex(sides[made.get("horizontal", "center")])
            panel.vertical_side.setCurrentIndex(sides[made.get("vertical", "center")])
            panel._edge_changed()
            for key in ("horizontal_gap", "vertical_gap"):
                doing(f"type {key}")
                field = panel.fields[key]
                field.setFocus()
                QTest.keyClick(field, Qt.Key_A, Qt.ControlModifier)
                QTest.keyClicks(field, str(made.get(key, "0")).split()[0])
                QTest.keyClick(field, Qt.Key_Return)
            for _ in range(round(rotation / 90.0) % 4):
                doing("Rotate 90°")
                panel.rotate_quarter.click()
            panel.preview()
            # Done must put the part exactly where the last preview showed it.
            doing("measuring (the check's own work)")
            expected = exact_box(transformed(moving_body, last[0].transform))
            doing("Done")
            panel.confirm.click()
            settle()
            doing("finished")
            if profile is not None:
                import io
                import pstats

                profile.disable()
                text = io.StringIO()
                pstats.Stats(profile, stream=text).sort_stats("cumulative").print_stats(45)
                REPORT["profile"] = text.getvalue()[-9000:]
            align = document.features[-1]
            REPORT["align_state"] = (align.type_name, align.state.name, align.message)
            REPORT["inputs"] = {"made": align.to_dict()["inputs"], "saved": made}
            placed = exact_box(document.body(moving_name).shape)
            REPORT["placed_as_previewed"] = all(
                abs(a - b) < 1e-6 for low_high in zip(placed, expected) for a, b in zip(*low_high))
            REPORT["placed"], REPORT["expected"] = placed, expected

        QTimer.singleShot(0, run)
        return window

    run_and_capture(build, os.path.join(os.environ.get("SIMPLECAD_SHOTS_DIR", "/tmp"), "align_large.png"),
                    settle_ms=600_000)
    print(json.dumps(REPORT, indent=1, default=str))
    worst = max((gap for name, gap in REPORT["blocks"] if name not in UNLIMITED), default=0)
    ok = (not REPORT.get("error") and REPORT.get("picked") and worst <= LIMIT_MS
          and REPORT.get("align_state", ("", ""))[1] == "OK" and REPORT.get("placed_as_previewed"))
    print("PASS" if ok else f"FAIL (worst block {worst} ms)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
