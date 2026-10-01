"""Choose which objects to bring in from another project or file."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem,
    QTreeWidgetItemIterator, QVBoxLayout,
)

KEY_ROLE = Qt.UserRole
KIND_ROLE = Qt.UserRole + 1


class ImportPicker(QDialog):
    """A checkable tree of a file's objects.

    Groups are listed with their members inside them; ticking a group takes
    the whole group -- and brings it in *as* a group -- while ticking only
    some members takes just those. :attr:`chosen` carries the keys of what was
    ticked (a group's key when all of it was), in the tree's order.
    """

    chosen = Signal(list)

    def __init__(self, parent, title: str, nodes: list[dict], *, thumbnail: bytes | None = None,
                 palette=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(460, 520)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        if thumbnail:
            picture = QPixmap()
            if picture.loadFromData(thumbnail):
                preview = QLabel()
                preview.setPixmap(picture.scaled(QSize(120, 90), Qt.KeepAspectRatio,
                                                 Qt.SmoothTransformation))
                header.addWidget(preview)
        explanation = QLabel(
            "Tick the objects to import. Each arrives as an ordinary, editable "
            "object in this project -- history, parameters, textures and "
            "colours included -- with no link back to the file."
        )
        explanation.setWordWrap(True)
        header.addWidget(explanation, 1)
        layout.addLayout(header)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        for node in nodes:
            self.tree.addTopLevelItem(self._item(node))
        self.tree.expandAll()
        self.tree.itemChanged.connect(self._changed)
        layout.addWidget(self.tree, 1)

        buttons = QHBoxLayout()
        every = QPushButton("Select all")
        every.clicked.connect(lambda: self._check_all(True))
        none = QPushButton("Select none")
        none.clicked.connect(lambda: self._check_all(False))
        buttons.addWidget(every)
        buttons.addWidget(none)
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.import_button = QPushButton("Import")
        self.import_button.setDefault(True)
        self.import_button.clicked.connect(self._accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.import_button)
        layout.addLayout(buttons)
        self._changed()

    def _item(self, node: dict) -> QTreeWidgetItem:
        label = node["name"]
        if node.get("detail"):
            label = f"{label}   ({node['detail']})"
        item = QTreeWidgetItem([label])
        item.setData(0, KEY_ROLE, node["key"])
        item.setData(0, KIND_ROLE, node["kind"])
        flags = item.flags() | Qt.ItemIsUserCheckable
        if node.get("children"):
            flags |= Qt.ItemIsAutoTristate
        item.setFlags(flags)
        item.setCheckState(0, Qt.Unchecked)
        for child in node.get("children", []):
            item.addChild(self._item(child))
        return item

    def _check_all(self, on: bool) -> None:
        for index in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(index).setCheckState(0, Qt.Checked if on else Qt.Unchecked)

    def _changed(self, *_args) -> None:
        self.import_button.setEnabled(bool(self.selected_keys()))

    def selected_keys(self) -> list:
        """What to import: whole groups where fully ticked, else single items."""
        keys = []

        def visit(item):
            state = item.checkState(0)
            if state == Qt.Checked:
                keys.append(item.data(0, KEY_ROLE))
                return
            if state == Qt.PartiallyChecked or item.childCount():
                for index in range(item.childCount()):
                    visit(item.child(index))

        for index in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(index))
        return keys

    def check(self, *keys) -> None:
        """Tick items by key -- for scripts and tests."""
        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value():
            item = iterator.value()
            if item.data(0, KEY_ROLE) in keys:
                item.setCheckState(0, Qt.Checked)
            iterator += 1

    def _accept(self) -> None:
        keys = self.selected_keys()
        if not keys:
            return
        self.accept()
        self.chosen.emit(keys)
