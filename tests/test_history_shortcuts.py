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
