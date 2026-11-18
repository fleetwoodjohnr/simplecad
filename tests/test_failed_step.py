"""A step that fails as it is made is taken back, so the body is not poisoned.

Kept, the failed Pull became the box's newest producer and every later push or
pull on that box failed behind it with "An earlier feature failed".
Runs the real geometry process; only drawing is stubbed out.
"""

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from simplecad.core.document import BodyRef
from simplecad.core.naming import fingerprint, make_ref, sub_shapes
from simplecad.kernel.occ import bounding_box
from simplecad.kernel.operations import PushPullFeature
from simplecad.kernel.primitives import BoxFeature


@pytest.fixture
def window(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "_start_autosave", lambda self: None)
    monkeypatch.setattr(MainWindow, "refresh_view", lambda self: None)
    window = MainWindow(Mode.DARK)
    yield window
    window.geometry.stop()
    window.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)


def _settle(window):
    for _ in range(3):  # a retraction asks for one more rebuild
        assert window.wait_for_rebuild(120_000)
        QApplication.processEvents()


def _push_top(window, box, distance):
    shape = window.document.body("Box").shape
    top = next(f for f in sub_shapes(shape, "face")
               if fingerprint(f, "face").direction == (0.0, 0.0, 1.0))
    window.add_feature(PushPullFeature(
        inputs={"body": BodyRef("Box"), "face": make_ref(shape, top, box.id, body="Box"),
                "distance": distance},
        outputs=["Box"],
    ))
    _settle(window)


def test_a_failed_pull_is_taken_back_and_the_next_one_works(window):
    box = BoxFeature(inputs={"width": 10, "depth": 10, "height": 10}, outputs=["Box"])
    window.add_feature(box)
    _settle(window)

    _push_top(window, box, -20.0)  # straight through: refused by the kernel
    assert [f.type_name for f in window.document.features] == ["box"]
    assert not window.history.can_redo
    assert "Nothing was changed" in window.stage.hint.full_text()

    _push_top(window, box, -2.0)
    assert [f.type_name for f in window.document.features] == ["box", "push_pull"]
    low, high = bounding_box(window.document.body("Box").shape)
    assert high[2] - low[2] == pytest.approx(8.0, abs=1e-6)
