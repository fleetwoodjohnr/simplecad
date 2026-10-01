"""Keyboard undo and redo reach the project history in the main window."""

from simplecad.kernel.primitives import BoxFeature
from tests.test_texture_editing import model  # noqa: F401 - shared Qt fixture


def test_sketch_undo_and_redo_restore_a_drawing_step():
    from simplecad.sketch.sketch import Sketch
    from simplecad.ui.viewport.sketch_canvas import SketchCanvas

    canvas = SketchCanvas(Sketch("Profile"), object(), None)
    canvas.refresh = lambda: None
    canvas._commit("rectangle", [(0, 0), (10, 10)])
    assert len(canvas.sketch.entities) == 4
    canvas.undo_last()
    assert len(canvas.sketch.entities) == 0
    canvas.redo_last()
    assert len(canvas.sketch.entities) == 4


def test_ctrl_z_and_ctrl_shift_z_shortcuts_step_one_project_action(model):
    window = model.window
    shortcuts = {shortcut.key().toString(): shortcut for shortcut in window._shortcuts}
    assert "Ctrl+Z" in shortcuts
    assert "Ctrl+Shift+Z" in shortcuts

    window.history.record("Add C")
    window.document.add_feature(BoxFeature(
        inputs={"width": 4, "depth": 5, "height": 6}, outputs=["C"],
    ))
    window.rebuild()
    assert window.document.body("C") is not None

    shortcuts["Ctrl+Z"].activated.emit()
    assert window.document.body("C") is None

    shortcuts["Ctrl+Shift+Z"].activated.emit()
    assert window.document.body("C") is not None


def _real_key(widget, key, modifiers):
    """Deliver a keystroke the way Qt does: ShortcutOverride first, then press."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    override = QKeyEvent(QEvent.ShortcutOverride, key, modifiers)
    override.ignore()
    QApplication.sendEvent(widget, override)
    return not override.isAccepted()


def test_a_focused_empty_field_does_not_swallow_undo_or_redo(model):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLineEdit

    window = model.window
    field = QLineEdit(window)
    field.show()
    field.setFocus()
    for key, modifiers in ((Qt.Key_Z, Qt.ControlModifier),
                           (Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier),
                           (Qt.Key_Y, Qt.ControlModifier)):
        assert _real_key(field, key, modifiers), "the window shortcut must get this key"


def test_a_field_with_its_own_edit_keeps_undo(model):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLineEdit

    window = model.window
    field = QLineEdit(window)
    field.show()
    field.setFocus()
    QTest.keyClicks(field, "12")
    assert field.isUndoAvailable()
    assert not _real_key(field, Qt.Key_Z, Qt.ControlModifier), "typing is undone first"


def test_ctrl_y_is_bound_to_redo(model):
    window = model.window
    shortcuts = {shortcut.key().toString(): shortcut for shortcut in window._shortcuts}
    assert "Ctrl+Y" in shortcuts
    window.history.record("Add D")
    window.document.add_feature(BoxFeature(
        inputs={"width": 4, "depth": 5, "height": 6}, outputs=["D"],
    ))
    window.rebuild()
    window.undo()
    assert window.document.body("D") is None
    shortcuts["Ctrl+Y"].activated.emit()
    assert window.document.body("D") is not None
