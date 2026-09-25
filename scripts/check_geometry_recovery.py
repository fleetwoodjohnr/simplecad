#!/usr/bin/env python
"""Real-window boolean latency, cancellation, recovery and theme checks."""
from __future__ import annotations

import json
import sys
import time

from _harness import grab_composited


def main():
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from simplecad.core.document import BodyRef
    from simplecad.core.geometry_service import deserialise_shape
    from simplecad.kernel.occ import bounding_box, is_valid, volume
    from simplecad.kernel.operations import BooleanFeature
    from simplecad.kernel.primitives import BoxFeature
    from simplecad.kernel.vent import VentPlateFeature
    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import DARK, Mode
    from simplecad.ui.tools.shapes import ShapePanel
    from simplecad.ui.viewport.occt_view import default_surface_format

    QSurfaceFormat.setDefaultFormat(default_surface_format())
    app = QApplication(sys.argv[:1])
    window = MainWindow(Mode.DARK)
    window.resize(1100, 760)
    window.show()
    results, replies, failures = {}, {}, []
    window.geometry.previewed.connect(lambda message: replies.update({message['token']: message}))
    stalls = []
    last_tick = [time.perf_counter()]

    def heartbeat():
        now = time.perf_counter()
        stalls.append((now - last_tick[0]) * 1000)
        last_tick[0] = now

    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(heartbeat)

    def preview(operation, token, target='A', tool='B'):
        feature = BooleanFeature(inputs={
            'body': BodyRef(target), 'tools': [BodyRef(tool)], 'operation': operation,
        }, outputs=[target])
        started = time.perf_counter()
        assert window.geometry.preview(feature.to_dict(), token)
        while token not in replies and time.perf_counter() - started < 20:
            QTest.qWait(5)
        answer = replies[token]
        assert answer.get('shape'), answer.get('error')
        shape = deserialise_shape(answer['shape'])
        assert is_valid(shape)
        window.stage.viewport.show_ghost(shape)
        window.stage.viewport.repaint()
        return (time.perf_counter() - started) * 1000, shape

    def run():
        try:
            for name, x in (('A', 0), ('B', 10)):
                window.add_feature(BoxFeature(inputs={
                    'width': 20, 'depth': 20, 'height': 20, 'x': x,
                }, outputs=[name]))
            assert window.wait_for_rebuild(20000)
            assert set(window.document.bodies) == {'A', 'B'}
            window.stage.viewport.fit_all()
            QTest.qWait(100)
            timer.start()
            last_tick[0] = time.perf_counter()
            for token, (operation, expected) in enumerate((('cut', 4000), ('join', 12000), ('intersect', 4000)), 900):
                elapsed, shape = preview(operation, token)
                results[operation + '_ms'] = round(elapsed, 1)
                assert elapsed < 1000, (operation, elapsed)
                assert abs(volume(shape) - expected) < 1e-5
            original = {name: bounding_box(body.shape) for name, body in window.document.bodies.items()}
            feature = BooleanFeature(inputs={'body': BodyRef('A'), 'tools': [BodyRef('B')], 'operation': 'join'}, outputs=['A'])
            assert window.geometry.preview(feature.to_dict(), 910)
            started = time.perf_counter()
            window.geometry.cancel_preview(910)
            elapsed = (time.perf_counter() - started) * 1000
            results['cancel_ms'] = round(elapsed, 1)
            assert elapsed < 250
            assert replies[910]['shape'] is None and not replies[910]['retryable']
            assert window.wait_for_rebuild(20000)
            assert {name: bounding_box(body.shape) for name, body in window.document.bodies.items()} == original
            results['recovered_preview_ms'], _ = preview('cut', 911)
            window.add_feature(VentPlateFeature(inputs={
                'width': 40, 'depth': 40, 'thickness': 2,
                'across_flats': 5, 'wall': 1.2, 'margin': 2,
                'x': 10, 'y': 10, 'z': 19,
            }, outputs=['Vent']))
            assert window.wait_for_rebuild(20000)
            results['vent_join_ms'], _ = preview('join', 912, tool='Vent')
            timer.stop()
            results['worst_ui_tick_ms'] = round(max(stalls, default=0), 1)
            assert results['worst_ui_tick_ms'] < 250
            window.stage.viewport.clear_ghost()
            panel = ShapePanel(window, DARK)
            window.stage.add_overlay(panel, 'right')
            panel.show()
            window.stage._layout_overlays()
            QTest.qWait(100)
            grab_composited(window).save('/tmp/simplecad-geometry-recovery.png')
        except Exception as exc:
            import traceback
            traceback.print_exc()
            failures.append(str(exc))
        finally:
            timer.stop()
            window.geometry.stop()
            app.quit()

    QTimer.singleShot(300, run)
    app.exec()
    print(json.dumps(results, indent=2))
    print('VERDICT:', 'FAIL' if failures else 'PASS')
    return bool(failures)


if __name__ == '__main__':
    sys.exit(main())
