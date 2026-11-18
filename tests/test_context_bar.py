"""The contextual action bar must neither float stray windows nor take focus."""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication, QWidget

from simplecad.ui.panels.context_bar import ActionButton, ContextBar
from simplecad.ui.theme import DARK


def test_back_to_back_selections_do_not_pop_up_orphan_buttons(monkeypatch):
    # Duplicate changes the selection twice before the event loop turns. The
    # first selection's buttons, discarded at once, used to appear as stray
    # top-level windows that stole activation -- and Delete -- from the viewport.
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    stage = QWidget()
    stage.resize(900, 600)
    bar = ContextBar(DARK, stage)
    stage.show()
    actions = [("move", "Move", "move"), ("delete", "Delete", "trash")]
    bar.show_actions("1 body", actions)
    app.processEvents()
    bar.show_actions("1 body", actions)
    bar.show_actions("1 body", actions)
    app.processEvents()
    try:
        assert not [w for w in QApplication.topLevelWidgets()
                    if isinstance(w, ActionButton) and w.isVisible()]
        assert len(bar.visible_keys()) == 2
    finally:
        stage.deleteLater()
        app.sendPostedEvents(None, QEvent.DeferredDelete)


def test_clicking_the_bar_leaves_keyboard_focus_where_it_was(monkeypatch):
    # A clicked button took focus, the bar was rebuilt under it, and focus fell
    # to an unrelated widget: Delete then never reached the view.
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    QApplication.instance() or QApplication([])
    bar = ContextBar(DARK)
    bar.show_actions("1 body", [("move", "Move", "move"), ("delete", "Delete", "trash")])
    try:
        assert [b.focusPolicy() for b in bar._buttons] == [Qt.TabFocus, Qt.TabFocus]
        assert bar.overflow.focusPolicy() == Qt.TabFocus
    finally:
        bar.deleteLater()


def test_overflow_menu_opens_upward_inside_the_window(monkeypatch):
    # The bar sits on the window's bottom edge. Its "..." menu used to drop
    # *down* from the button, so all but its first rows ran off the bottom.
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPoint, QRect, QSize

    app = QApplication.instance() or QApplication([])
    stage = QWidget()
    stage.resize(900, 600)
    bar = ContextBar(DARK, stage)
    stage.show()
    actions = [(f"a{i}", f"Action {i}", "move") for i in range(10)]
    bar.show_actions("1 body", actions, max_width=420)
    bar.move(stage.width() - bar.width(), stage.height() - bar.height())
    app.processEvents()
    try:
        assert bar.overflow.isVisible()
        size = QSize(240, 300)
        menu = QRect(bar._overflow_position(size), size)
        window = QRect(stage.mapToGlobal(QPoint(0, 0)), stage.size())
        button_top = bar.overflow.mapToGlobal(QPoint(0, 0)).y()
        assert menu.bottom() < button_top
        assert window.left() <= menu.left() and menu.right() <= window.right()
    finally:
        stage.deleteLater()
        app.sendPostedEvents(None, QEvent.DeferredDelete)
