"""Closing the window: never held up by an autosave, and never losing unsaved work.

Clicking X while the 90-second autosave was writing (99 MB on the Atlas box)
did nothing for seconds at a time, then deleted that same recovery file. And a
window with unsaved changes closed without a word.
"""

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from simplecad.kernel.primitives import BoxFeature


@pytest.fixture
def window(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    from simplecad.ui.geometry_client import GeometryClient
    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(GeometryClient, "start", lambda self, timeout=20.0: False)
    monkeypatch.setattr(MainWindow, "_start_autosave", lambda self: None)
    win = MainWindow(Mode.DARK)
    # Not shown: offscreen Qt has no window for the OCCT view. A close that goes
    # through stops the geometry worker, which is what these tests watch.
    win.closed = []
    stop = win.geometry.stop
    monkeypatch.setattr(win.geometry, "stop", lambda: (win.closed.append(True), stop()))
    yield win
    win.mark_clean()
    win.close()


def _unsaved(win):
    win.document.add_feature(BoxFeature(inputs={"width": 10, "depth": 10, "height": 10}, outputs=["B"]))
    win.mark_dirty()


def _answer(monkeypatch, button):
    asked = []

    def question(*args, **kwargs):
        asked.append(args[2] if len(args) > 2 else "")
        return button

    monkeypatch.setattr(QMessageBox, "question", question)
    return asked


def test_an_autosave_in_progress_does_not_hold_the_window_open(window):
    window.geometry._active_save = {"token": {"autosave": True}}
    window.geometry._pending_saves = [{"token": {"autosave": True}}]
    assert window.close()
    assert window.closed


def test_a_save_the_user_started_is_finished_before_closing(window):
    window.geometry._active_save = {"token": {"autosave": False}, "path": "/tmp/x.scad3"}
    assert not window.close()
    assert "Finishing the save" in window.stage.hint.full_text()
    window.geometry._active_save = None
    assert window.close()


def test_unsaved_work_asks_first_and_cancel_keeps_the_window(window, monkeypatch):
    _unsaved(window)
    asked = _answer(monkeypatch, QMessageBox.Cancel)
    assert not window.close()
    assert not window.closed
    assert asked and "unsaved changes" in asked[0]


def test_dont_save_closes_without_saving(window, monkeypatch):
    _unsaved(window)
    _answer(monkeypatch, QMessageBox.Discard)
    saves = []
    monkeypatch.setattr(window, "save_document", lambda ask=False: saves.append(ask))
    assert window.close()
    assert not saves


def test_save_closes_once_the_save_succeeds_and_a_failed_save_keeps_the_window(window, monkeypatch):
    _unsaved(window)
    _answer(monkeypatch, QMessageBox.Save)

    def start_save(ask=False):
        window.geometry._active_save = {"token": {"autosave": False}}
        return True

    monkeypatch.setattr(window, "save_document", start_save)
    assert not window.close()
    assert not window.closed

    # The save fails: the window stays, with the reason.
    window.geometry._active_save = None
    window._on_project_saved({"token": {"autosave": False}, "error": "disk full"})
    for _ in range(5):
        QApplication.processEvents()
    assert not window.closed
    assert "disk full" in window.stage.hint.full_text()

    # Asked again and saved this time: the window closes when the save reports back.
    assert not window.close()
    window.geometry._active_save = None
    window._dirty = False
    window._on_project_saved({"token": {"autosave": False, "epoch": window._edit_epoch},
                              "generation": window.geometry._generation, "path": "/tmp/x.scad3"})
    for _ in range(5):
        QApplication.processEvents()
    assert window.closed
