"""Real Qt key events and real feature rebuilds, with display and IPC replaced.

The GUI smoke script covers OCCT picking. Here rebuild completion is controlled
so keystrokes arriving during slow geometry can be tested deterministically.
"""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from simplecad.core.naming import fingerprint, sub_shapes
from simplecad.kernel.occ import bounding_box
from simplecad.kernel.primitives import BoxFeature


@pytest.fixture
def model(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    from simplecad.ui.geometry_client import GeometryClient
    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode
    from simplecad.ui.viewport import camera

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(GeometryClient, "start", lambda self: False)
    monkeypatch.setattr(MainWindow, "_start_autosave", lambda self: None)
    monkeypatch.setattr(camera, "read_state", lambda view: (
        (0, 0, 10), (0, 0, 0), (0, 1, 0), 20,
    ))
    window = MainWindow(Mode.DARK)
    viewport = window.stage.viewport
    entries = []

    def clear():
        entries.clear()
        viewport.selection_changed.emit()

    def select_presentations(presentations):
        entries.clear()
        for presentation in presentations:
            name = next(n for n, p in window._presentations.items() if p is presentation)
            entries.append(dict(presentation=presentation, kind="solid",
                                shape=window.document.body(name).shape))
        viewport.selection_changed.emit()

    def select_face(presentation, face):
        entries[:] = [dict(presentation=presentation, kind="face", shape=face)]
        viewport.selection_changed.emit()
        return True

    def refresh():
        clear()
        window._presentations.clear()
        window._presentations.update({name: object() for name in window.document.bodies})

    def request(*args, **kwargs):
        window.geometry._busy = True
        return True

    def finish():
        report = window.rebuilder.rebuild()
        window.geometry._busy = False
        window._refresh_rebuilt_model(report.ok)
        return report

    monkeypatch.setattr(viewport, "selected_entries", lambda: list(entries))
    monkeypatch.setattr(viewport, "clear_selection", clear)
    monkeypatch.setattr(viewport, "select_presentations", select_presentations)
    monkeypatch.setattr(viewport, "select_subshape", select_face)
    monkeypatch.setattr(window, "refresh_view", refresh)
    monkeypatch.setattr(window.geometry, "request", request)
    window.geometry.available = True
    for name, x in (("A", 0), ("B", 20)):
        window.document.add_feature(BoxFeature(
            inputs={"width": 10, "depth": 10, "height": 10, "x": x}, outputs=[name],
        ))
    assert finish().ok

    def select(*names, face=False):
        if face:
            shape = window.document.body(names[0]).shape
            top = next(f for f in sub_shapes(shape, "face")
                       if fingerprint(f, "face").direction == (0.0, 0.0, 1.0))
            select_face(window._presentations[names[0]], top)
        else:
            select_presentations(window._presentations[n] for n in names)

    yield SimpleNamespace(window=window, viewport=viewport, select=select, finish=finish)
    window._cancel_nudge()
    window.geometry.stop()
    window.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)


def test_holding_an_arrow_waits_for_real_release_and_groups_repeats(model):
    window, viewport = model.window, model.viewport
    model.select("A")
    QTest.keyPress(viewport, Qt.Key_Right)
    QTest.qWait(300)  # Longer than the quiet timer, shorter than many repeat delays.
    assert len(window.document.features) == 2
    for _ in range(3):
        QApplication.sendEvent(viewport, QKeyEvent(
            QEvent.KeyRelease, Qt.Key_Right, Qt.NoModifier, "", True,
        ))
        QApplication.sendEvent(viewport, QKeyEvent(
            QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier, "", True,
        ))
    QTest.keyRelease(viewport, Qt.Key_Right)
    QTest.qWait(300)
    assert len(window.document.features) == 3
    assert model.finish().ok
    assert bounding_box(window.document.body("A").shape)[0][0] == pytest.approx(1, abs=1e-6)
    window.undo()
    assert model.finish().ok
    assert bounding_box(window.document.body("A").shape)[0][0] == pytest.approx(0, abs=1e-6)
    window.redo()
    assert model.finish().ok
    assert bounding_box(window.document.body("A").shape)[0][0] == pytest.approx(1, abs=1e-6)


@pytest.mark.parametrize("face", [False, True])
def test_arrows_during_rebuild_resume_on_the_same_geometry(model, face):
    window, viewport = model.window, model.viewport
    model.select("A", face=face)
    key = Qt.Key_Up if face else Qt.Key_Right
    QTest.keyClick(viewport, key)
    window._commit_nudge()
    assert window.geometry.busy
    QTest.keyClick(viewport, key)
    QTest.keyClick(viewport, key)
    assert len(window.document.features) == 3
    assert model.finish().ok
    assert window._nudge_state is not None
    window._commit_nudge()
    assert model.finish().ok
    low, high = bounding_box(window.document.body("A").shape)
    assert (high[2] if face else low[0]) == pytest.approx(10.75 if face else .75, abs=1e-6)
    assert window.selection.bodies == ["A"]
    assert len(window.document.features) == 4


def test_screen_and_depth_steps_move_all_selected_bodies(model):
    window, viewport = model.window, model.viewport
    model.select("A", "B")
    QTest.keyClick(viewport, Qt.Key_Left)
    QTest.keyClick(viewport, Qt.Key_Down)
    QTest.keyClick(viewport, Qt.Key_Up, Qt.ControlModifier)
    window._commit_nudge()
    assert model.finish().ok
    for name, x in (("A", 0), ("B", 20)):
        assert bounding_box(window.document.body(name).shape)[0] == pytest.approx(
            (x - .25, -.25, -.25), abs=1e-6,
        )


@pytest.mark.parametrize("internal", [False, True])
def test_round_face_steps_change_radius_and_restore_the_same_surface(model, internal):
    from simplecad.kernel.detect import cylindrical_faces
    from simplecad.kernel.primitives import TubeFeature

    window, viewport = model.window, model.viewport
    window.document.add_feature(TubeFeature(
        inputs={"outer_radius": 5, "inner_radius": 3, "height": 10}, outputs=["Tube"],
    ))
    assert model.finish().ok
    face = next(f for f, info in cylindrical_faces(window.document.body("Tube").shape)
                if info.internal == internal)
    viewport.select_subshape(window._presentations["Tube"], face)
    for key, change in ((Qt.Key_Up, .25), (Qt.Key_Down, 0)):
        QTest.keyClick(viewport, key)
        window._commit_nudge()
        assert model.finish().ok
        radii = {info.internal: info.radius
                 for _, info in cylindrical_faces(window.document.body("Tube").shape)}
        assert radii[internal] == pytest.approx(3 - change if internal else 5 + change)
        assert radii[not internal] == pytest.approx(5 if internal else 3)
        assert window.selection.round_faces()[0].info.internal == internal


def test_a_held_queued_arrow_still_waits_for_release_after_rebuild(model):
    window, viewport = model.window, model.viewport
    model.select("A")
    QTest.keyClick(viewport, Qt.Key_Right)
    window._commit_nudge()
    QTest.keyPress(viewport, Qt.Key_Right)
    assert model.finish().ok
    QTest.qWait(300)
    assert len(window.document.features) == 3
    QTest.keyRelease(viewport, Qt.Key_Right)
    QTest.qWait(300)
    assert model.finish().ok
    assert bounding_box(window.document.body("A").shape)[0][0] == pytest.approx(.5, abs=1e-6)


def test_in_process_rebuild_also_preserves_arrows_received_while_working(model, monkeypatch):
    window, viewport = model.window, model.viewport
    window.geometry.available = False
    model.select("A", face=True)
    original = window.rebuilder.rebuild

    def rebuilding(*args, **kwargs):
        QTest.keyClick(viewport, Qt.Key_Up)
        return original(*args, **kwargs)

    monkeypatch.setattr(window.rebuilder, "rebuild", rebuilding)
    QTest.keyClick(viewport, Qt.Key_Up)
    window._commit_nudge()
    monkeypatch.setattr(window.rebuilder, "rebuild", original)
    window._commit_nudge()
    assert bounding_box(window.document.body("A").shape)[1][2] == pytest.approx(10.5, abs=1e-6)
    assert window._nudge_state is None


@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("cancel", ["escape", "selection", "tool", "failure", "document"])
def test_pending_arrows_cannot_outlive_their_target(model, monkeypatch, queued, cancel):
    from simplecad.core.document import Document
    from simplecad.ui.tools import registry

    window, viewport = model.window, model.viewport
    model.select("A")
    QTest.keyClick(viewport, Qt.Key_Right)
    if queued:
        window._commit_nudge()
        QTest.keyClick(viewport, Qt.Key_Right)
    if cancel == "escape":
        QTest.keyClick(viewport, Qt.Key_Escape)
    elif cancel == "selection":
        model.select("B")
    elif cancel == "tool":
        monkeypatch.setattr(registry, "activate", lambda *args: None)
        window.activate_tool("move")
    elif cancel == "failure":
        window._on_geometry_failed("Test failure")
    else:
        window.set_document(Document("New"))
    assert window._nudge_state is None
    assert window._nudge_restore is None
    assert not window._nudge_queue
    assert not window._nudge_timer.isActive()
    before = len(window.document.features)
    assert model.finish().ok
    window._commit_nudge()
    assert len(window.document.features) == before
    if cancel == "selection":
        assert bounding_box(window.document.body("B").shape)[0][0] == pytest.approx(20, abs=1e-6)


def test_editors_and_unsupported_face_keys_keep_their_normal_behavior(model):
    window, viewport = model.window, model.viewport
    model.select("A", face=True)
    for key, modifiers in ((Qt.Key_Left, Qt.NoModifier), (Qt.Key_Up, Qt.ControlModifier)):
        QTest.keyClick(viewport, key, modifiers)
        assert window._nudge_state is None
    editor = QLineEdit("123", window)
    editor.setCursorPosition(2)
    QTest.keyClick(editor, Qt.Key_Left)
    assert editor.cursorPosition() == 1
    assert window._nudge_state is None
