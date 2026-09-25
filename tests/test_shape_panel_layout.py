"""Switching shape choices must not compress or overlap the picker tiles."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QWidget

from simplecad.core.document import Document
from simplecad.ui.theme import DARK
from simplecad.ui.tools.shapes import FIELDS, ShapePanel


@pytest.mark.parametrize("width,height", [(260, 320), (700, 420), (1100, 800)])
def test_box_to_vent_keeps_every_size_field_editable(monkeypatch, width, height):
    from PySide6.QtCore import QPoint
    from simplecad.ui.theme import stylesheet

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(stylesheet(DARK))
    stage = QWidget()
    stage.resize(width, height)
    stage._layout_overlays = lambda: None
    window = SimpleNamespace(
        stage=stage, document=Document(), set_hint=lambda _: None,
        cancel_tool=lambda: None,
    )
    panel = ShapePanel(window, DARK)
    stage.show()
    panel.show()
    for _ in range(3):
        panel.choose("box")
        panel.choose("vent_plate")
        app.processEvents()
        rows = [panel.fields_layout.itemAt(index).widget()
                for index in range(panel.fields_layout.count())]
        assert len(rows) == len(FIELDS["vent_plate"])
        assert all(row.height() >= row.field.height() for row in rows)
        assert all(first.geometry().bottom() < second.geometry().top()
                   for first, second in zip(rows, rows[1:]))
        assert panel.fields_host.height() >= rows[-1].geometry().bottom() + 1
        for key, field in panel.fields.items():
            field.setText("72")
            assert field.expression() == "72"
            bottom = field.mapTo(panel.content, QPoint()).y() + field.height()
            panel.scroll.verticalScrollBar().setValue(
                max(0, bottom - panel.scroll.viewport().height())
            )
            app.processEvents()
            top = field.mapTo(panel.scroll.viewport(), QPoint()).y()
            assert top >= 0
            assert top + field.height() <= panel.scroll.viewport().height()
        assert panel.root.itemAt(2).layout().itemAt(1).widget().isVisible()
    panel.close()
    stage.close()
    app.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.mark.parametrize("height", [420, 760])
def test_repeated_box_vent_switches_keep_tiles_full_size(monkeypatch, height):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    stage = QWidget()
    stage.resize(700, height)
    stage._layout_overlays = lambda: None
    window = SimpleNamespace(
        stage=stage, document=Document(), set_hint=lambda _message: None,
        cancel_tool=lambda: None,
    )
    panel = ShapePanel(window, DARK)
    panel.show()
    for kind in ("box", "vent_plate", "cylinder", "box", "vent_plate") * 4:
        panel.choose(kind)
        app.processEvents()
        assert len(panel.fields) == len(FIELDS[kind])
        tiles = list(panel._tiles.values())
        assert all(tile.width() == 58 and tile.height() == 52 for tile in tiles)
        rectangles = [tile.geometry() for tile in tiles]
        assert all(
            not first.intersects(second)
            for index, first in enumerate(rectangles)
            for second in rectangles[index + 1:]
        )
        assert panel.height() <= max(360, height - 32)
    panel.close()
    stage.close()
    app.sendPostedEvents(None, QEvent.DeferredDelete)


def test_shape_panel_background_and_children_follow_live_theme(monkeypatch):
    from simplecad.ui.theme import LIGHT, stylesheet
    from PySide6.QtCore import QPoint
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    stage = QWidget()
    stage.resize(700, 760)
    stage._layout_overlays = lambda: None
    window = SimpleNamespace(stage=stage, document=Document(), set_hint=lambda _: None, cancel_tool=lambda: None)
    panel = ShapePanel(window, DARK)
    stage.show()
    panel.show()
    for palette in (DARK, LIGHT, DARK):
        app.setStyleSheet(stylesheet(palette))
        panel.apply_palette(palette)
        app.processEvents()
        assert not panel.content.autoFillBackground()
        assert not panel.scroll.viewport().autoFillBackground()
        assert all(tile._palette is palette for tile in panel._tiles.values())
        # The gutter is actual rendered scroll content, between the tiles.
        position = panel.content.mapTo(stage, QPoint(0, 54))
        pixel = stage.grab().toImage().pixelColor(position)
        assert pixel.name().lower() == palette.surface_raised.lower()
    panel.close()
    stage.close()
    app.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.mark.parametrize("width,height", [(260, 320), (700, 420), (1100, 800)])
def test_create_then_switch_and_resize_keeps_fields_readable(monkeypatch, width, height):
    from simplecad.core.rebuild import Rebuilder
    from PySide6.QtCore import QPoint
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    stage = QWidget()
    stage.resize(width, height)
    stage._layout_overlays = lambda: None
    stage.viewport = SimpleNamespace(fit_all=lambda: None)
    doc = Document()
    builder = Rebuilder(doc)
    def create(feature):
        doc.add_feature(feature)
        assert builder.rebuild().ok
    window = SimpleNamespace(stage=stage, document=doc, set_hint=lambda _: None,
                             cancel_tool=lambda: None, add_feature=create)
    panel = ShapePanel(window, DARK)
    stage.show()
    panel.show()
    for kind in ("box", "cylinder", "sphere", "cone", "box") * 3:
        panel.choose(kind)
        app.processEvents()
        panel.commit()
        app.processEvents()
        assert panel.width() <= width - 32
        assert panel.height() <= height - 32
        for field in panel.fields.values():
            assert field.width() >= 80
            assert field.height() >= 24
            left = field.mapTo(panel.content, QPoint()).x()
            assert left + field.width() <= panel.content.width()
        tiles = list(panel._tiles.values())
        assert all(t.width() == 58 and t.height() == 52 for t in tiles)
    panel.close()
    stage.close()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
