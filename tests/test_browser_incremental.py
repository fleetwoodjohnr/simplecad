"""A geometry-only edit must retain browser rows and their selection."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from simplecad.core.document import Document, FeatureState
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.primitives import BoxFeature
from simplecad.ui.panels.model_browser import ModelBrowser
from simplecad.ui.theme import DARK


def test_refresh_retains_unchanged_rows_and_updates_only_history_state(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    doc = Document()
    feature = doc.add_feature(BoxFeature(outputs=["A"]))
    other = doc.add_feature(BoxFeature(outputs=["B"]))
    builder = Rebuilder(doc)
    assert builder.rebuild().ok
    browser = ModelBrowser(DARK, doc)
    body_row = browser.bodies_tree.topLevelItem(0)
    history_row = browser.history_list.item(0)
    body_row.setSelected(True)
    feature.inputs["width"] = 50
    assert builder.rebuild().ok
    browser.refresh()
    assert browser.bodies_tree.topLevelItem(0) is body_row
    assert body_row.isSelected()
    assert browser.history_list.item(0) is history_row
    feature.state = FeatureState.NEEDS_ATTENTION
    feature.message = "Missing input"
    browser.refresh()
    assert browser.history_list.item(0) is history_row
    assert history_row.toolTip() == "Missing input"
    doc.remove_feature(other.id)
    browser.refresh()
    assert browser.history_list.count() == 1
    assert browser.history_list.item(0).data(Qt.UserRole) == feature.id
    browser.close()
