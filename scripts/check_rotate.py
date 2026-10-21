#!/usr/bin/env python
"""Rotation through the real window: every ring, past a quarter turn.

Drags the X, Y and Z rings more than 90 degrees in the iso view (the Y ring
used to fold back at 90 and could never get there), makes a 5 degree drag (it
used to snap back to nothing), clicks empty space mid-tool and drags again
(the gizmo used to lose its subject), rotates a second time without reopening
the tool, drags a ring seen edge-on in the Top view (it used to do nothing),
and turns one half of a split cylinder.
"""

from __future__ import annotations

import math
import sys

from _harness import run_and_capture  # noqa: E402

REPORT: dict[str, object] = {}


def _ring_hits(viewport, want_axis: int):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication
    from OCP.AIS import AIS_Manipulator, AIS_MM_Rotation

    context = viewport.context
    manipulator = viewport.gizmo._manipulator
    cx, cy = viewport.project(viewport.gizmo._pivot)
    hits = []
    for dx in range(-170, 171, 3):
        for dy in range(-170, 171, 3):
            point = QPoint(round(cx + dx), round(cy + dy))
            if QApplication.widgetAt(viewport.mapToGlobal(point)) is not viewport:
                continue
            context.MoveTo(*viewport._device_pos(point), viewport.view, False)
            if (context.HasDetected()
                    and isinstance(context.DetectedInteractive(), AIS_Manipulator)
                    and manipulator.HasActiveMode()
                    and manipulator.ActiveMode() == AIS_MM_Rotation
                    and manipulator.ActiveAxisIndex() == want_axis):
                hits.append(point)
    return (cx, cy), hits


def _drag_ring(window, axis: int, sweep_degrees: float, *, linear=False, miss=False):
    """Press on ring *axis* and sweep the cursor around the pivot on screen.

    *miss* presses just outside the ring instead -- on a pixel OCCT does not
    report as the ring -- which must still take that ring.
    """
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    viewport = window.stage.viewport
    if viewport.gizmo is None:
        return None
    (cx, cy), hits = _ring_hits(viewport, axis)
    REPORT.setdefault("diag", []).append(f"axis{axis}:hits={len(hits)}")
    if not hits:
        return None
    # The hit furthest from the pivot is on the ring, not near the hub.
    start = max(hits, key=lambda p: (p.x() - cx) ** 2 + (p.y() - cy) ** 2)
    if miss:
        reach = math.hypot(start.x() - cx, start.y() - cy)
        ux, uy = (start.x() - cx) / reach, (start.y() - cy) / reach
        start = next(
            (QPoint(round(start.x() + ux * push), round(start.y() + uy * push))
             for push in range(6, 16)
             if not viewport.gizmo._handle_under(*viewport._device_pos(
                 QPoint(round(start.x() + ux * push), round(start.y() + uy * push))))),
            None,
        )
        if start is None:
            return None
    QTest.mouseMove(viewport, start)
    QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, start)
    if viewport._nav.name != "GIZMO":
        REPORT["diag"].append(f"axis{axis}:nav={viewport._nav.name}")
        QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, start)
        return None
    radius = math.hypot(start.x() - cx, start.y() - cy)
    begin = math.atan2(start.y() - cy, start.x() - cx)
    steps = 24
    end = start
    along = ((start.x() - cx) / max(radius, 1e-6), (start.y() - cy) / max(radius, 1e-6))
    for index in range(1, steps + 1):
        if linear:
            # Edge-on, the ring is a line through the pivot: push along it.
            end = QPoint(round(start.x() - along[0] * index * 4),
                         round(start.y() - along[1] * index * 4))
        else:
            angle = begin - math.radians(sweep_degrees) * index / steps
            end = QPoint(round(cx + radius * math.cos(angle)), round(cy + radius * math.sin(angle)))
        QTest.mouseMove(viewport, end)
        QApplication.processEvents()
    QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, end)
    window.wait_for_rebuild()
    QApplication.processEvents()
    return window.document.features[-1]


def _drag_part(window, sweep_degrees: float):
    """Press on the part well clear of every ring and sweep around the pivot."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    viewport = window.stage.viewport
    rings = [p for axis in range(3) for p in _ring_hits(viewport, axis)[1]]
    cx, cy = viewport.project(viewport.gizmo._pivot)
    part = viewport.gizmo._attached[0]
    start = None
    for step in range(24):
        angle = math.radians(step * 15)
        point = QPoint(round(cx + 160 * math.cos(angle)), round(cy + 160 * math.sin(angle)))
        viewport.context.MoveTo(*viewport._device_pos(point), viewport.view, False)
        clear = all(math.hypot(p.x() - point.x(), p.y() - point.y()) > 40 for p in rings)
        if (clear and viewport.context.HasDetected()
                and viewport.context.DetectedInteractive() is part):
            start = point
            break
    if start is None:
        return None
    QTest.mouseMove(viewport, start)
    QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, start)
    if viewport._nav.name != "GIZMO":
        QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, start)
        return None
    begin = math.atan2(start.y() - cy, start.x() - cx)
    end = start
    for index in range(1, 25):
        angle = begin - math.radians(sweep_degrees) * index / 24
        end = QPoint(round(cx + 160 * math.cos(angle)), round(cy + 160 * math.sin(angle)))
        QTest.mouseMove(viewport, end)
        QApplication.processEvents()
    QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, end)
    window.wait_for_rebuild()
    QApplication.processEvents()
    return window.document.features[-1]


def _angle(feature, key):
    from simplecad.core.params import evaluate

    if feature is None or feature.type_name != "move":
        return 0.0
    raw = feature.inputs.get(key, 0)
    return float(raw) if isinstance(raw, (int, float)) else float(evaluate(str(raw), {}))


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from simplecad.core.document import BodyRef
    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode
    from simplecad.ui.viewport.occt_view import StandardView

    def build(_app):
        window = MainWindow(Mode.DARK)

        def select(name):
            viewport = window.stage.viewport
            viewport.clear_selection()
            viewport.select_shape(window._presentations[name])
            window.selection.refresh()

        def run() -> None:
            from simplecad.kernel.primitives import BoxFeature, CylinderFeature
            from simplecad.kernel.split import SplitFeature

            window.add_feature(BoxFeature(inputs={"width": 30, "depth": 20, "height": 10},
                                          outputs=["Block"]))
            window.wait_for_rebuild()
            viewport = window.stage.viewport
            viewport.fit_all()
            select("Block")
            window.activate_tool("rotate")
            QApplication.processEvents()
            REPORT["gizmo_attached"] = viewport.gizmo is not None

            for axis, key in ((0, "rx"), (1, "ry"), (2, "rz")):
                feature = _drag_ring(window, axis, 120.0)
                REPORT[f"ring_{key}"] = round(_angle(feature, key), 1)
                REPORT[f"panel_open_after_{key}"] = any(
                    getattr(w, "title", "") == "Rotate" for w, _a in window.stage.overlays)
                REPORT[f"gizmo_back_after_{key}"] = viewport.gizmo is not None

            count = len(window.document.features)
            feature = _drag_ring(window, 2, 5.0)
            REPORT["small_drag_rz"] = round(_angle(feature, "rz"), 1)
            REPORT["small_drag_committed"] = len(window.document.features) == count + 1

            # A click on empty space clears the selection; the panel keeps its subject.
            viewport.clear_selection()
            window._on_selection()
            feature = _drag_ring(window, 2, 45.0)
            REPORT["after_empty_click_body"] = str(feature.inputs.get("body")) if feature else None

            # A press a few pixels outside a ring still takes that ring.
            feature = _drag_ring(window, 0, 60.0, miss=True)
            REPORT["near_miss_rx"] = round(_angle(feature, "rx"), 1)

            # Grabbing the part itself turns it about Z, like a turntable.
            feature = _drag_part(window, 60.0)
            REPORT["part_drag"] = tuple(round(_angle(feature, k), 1) for k in ("rx", "ry", "rz"))

            # Clicking a ring without dragging changes nothing.
            from PySide6.QtTest import QTest
            from PySide6.QtCore import Qt
            count = len(window.document.features)
            _centre, hits = _ring_hits(viewport, 1)
            QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, hits[len(hits) // 2])
            window.wait_for_rebuild()
            REPORT["click_adds_nothing"] = len(window.document.features) == count

            # Edge-on: in the Top view the X ring is a line through the pivot.
            viewport.set_standard_view(StandardView.TOP, animate=False)
            QApplication.processEvents()
            window.cancel_tool()
            select("Block")
            window.activate_tool("rotate")
            QApplication.processEvents()
            count = len(window.document.features)
            feature = _drag_ring(window, 0, 0.0, linear=True)
            REPORT["edge_on_rx"] = round(_angle(feature, "rx"), 1)
            REPORT["edge_on_committed"] = len(window.document.features) == count + 1
            window.cancel_tool()

            # A split half rotates like anything else.
            viewport.set_standard_view(StandardView.ISO, animate=False)
            window.add_feature(CylinderFeature(inputs={"radius": 10, "height": 30, "x": 60},
                                               outputs=["Cylinder"]))
            window.wait_for_rebuild()
            window.add_feature(SplitFeature(inputs={
                "body": BodyRef("Cylinder"), "normal": [1, 0, 0], "origin": [60, 0, 0],
                "position": 0.0, "names": ["Cylinder A", "Cylinder B"],
            }, outputs=["Cylinder A", "Cylinder B"]))
            window.wait_for_rebuild()
            viewport.fit_all()
            QApplication.processEvents()
            select("Cylinder A")
            window.activate_tool("rotate")
            QApplication.processEvents()
            feature = _drag_ring(window, 1, 120.0)
            REPORT["split_half_ry"] = round(_angle(feature, "ry"), 1)
            REPORT["split_half_body"] = str(feature.inputs.get("body")) if feature else None
            REPORT["failed_features"] = [f.name for f in window.document.features
                                         if f.state.value not in ("ok", "suppressed")]
            window.cancel_tool()
            viewport.fit_all()
            # The scenario outlasts any fixed settle time; end the run here.
            QTimer.singleShot(0, QApplication.instance().quit)

        window.stage.viewport.ready.connect(lambda: QTimer.singleShot(500, run))
        return window

    run_and_capture(build, "/tmp/simplecad-check-rotate.png", settle_ms=600000, size=(1300, 860))
    print("--- rotate ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    ok = (
        REPORT.get("gizmo_attached")
        and all(abs(REPORT.get(f"ring_{k}", 0)) > 95 for k in ("rx", "ry", "rz"))
        and all(REPORT.get(f"panel_open_after_{k}") and REPORT.get(f"gizmo_back_after_{k}")
                for k in ("rx", "ry", "rz"))
        and REPORT.get("small_drag_committed") and 1 <= abs(REPORT.get("small_drag_rz", 0)) <= 15
        and REPORT.get("after_empty_click_body") == "Block"
        and abs(REPORT.get("near_miss_rx", 0)) > 40
        and REPORT.get("part_drag", (1, 1, 0))[:2] == (0.0, 0.0)
        and 45 < abs(REPORT.get("part_drag", (0, 0, 0))[2]) < 75
        and REPORT.get("click_adds_nothing")
        and REPORT.get("edge_on_committed") and abs(REPORT.get("edge_on_rx", 0)) > 1
        and abs(REPORT.get("split_half_ry", 0)) > 95
        and REPORT.get("split_half_body") == "Cylinder A"
        and not REPORT.get("failed_features")
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
