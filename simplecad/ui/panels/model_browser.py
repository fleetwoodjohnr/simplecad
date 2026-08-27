"""Model browser: the bodies in the document and the history that made them.

Two lists in one floating card. Bodies is what most users look at; History is
the parametric timeline -- edit, rename, suppress, delete, reorder -- and it is
one click away rather than a separate workspace.

Bodies is a **tree** rather than a list, because groups exist. A group is a row
with a disclosure triangle and its members are the rows underneath it, which is
the whole of "clear visual indication showing which objects belong to a group"
and is also what makes nesting legible: a sub-assembly is simply a group inside
a group, indented once more, and needs no explaining.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from ..icons import icon
from ..theme import METRICS, Palette
from ..widgets.controls import FloatingCard

#: Item data roles: the name, and whether the row is a group or a body.
NAME_ROLE = Qt.UserRole
KIND_ROLE = Qt.UserRole + 1


class _Tab(QLabel):
    """A quiet text tab. Underlined when current."""

    clicked = Signal()

    def __init__(self, text: str, palette: Palette, parent=None) -> None:
        super().__init__(text, parent)
        self._palette = palette
        self._current = False
        self.setCursor(Qt.PointingHandCursor)
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(30)
        self.refresh()

    def set_current(self, current: bool) -> None:
        self._current = current
        self.refresh()

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.refresh()

    def refresh(self) -> None:
        p = self._palette
        color = p.text if self._current else p.text_faint
        border = p.accent if self._current else "transparent"
        self.setStyleSheet(
            f"color:{color}; font-size:12.5px; font-weight:600;"
            f"border-bottom:2px solid {border}; padding:0 2px;"
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked.emit()


class ModelBrowser(FloatingCard):
    """Floating panel listing bodies, groups and feature history."""

    visibility_toggled = Signal(str, bool)
    feature_selected = Signal(str)
    #: A row was clicked: a body name or a group name.
    body_selected = Signal(str)
    #: (body name, new name)
    rename_requested = Signal(str, str)
    isolate_requested = Signal(str)
    show_all_requested = Signal()
    duplicate_requested = Signal(str)
    delete_requested = Signal(str)
    #: (feature id, action) — suppress, unsuppress, delete, rename
    feature_action = Signal(str, str)
    #: Grouping, from the tree's own context menu.
    group_requested = Signal(list)
    ungroup_requested = Signal(str)
    group_visibility_toggled = Signal(str, bool)
    group_rename_requested = Signal(str, str)
    group_delete_requested = Signal(str)
    group_duplicate_requested = Signal(str)

    def __init__(self, palette: Palette, document, parent=None) -> None:
        super().__init__(palette, parent)
        self.document = document

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            METRICS.space(2), METRICS.space(2), METRICS.space(2), METRICS.space(2)
        )
        layout.setSpacing(METRICS.space(1.5))

        tabs = QHBoxLayout()
        tabs.setSpacing(METRICS.space(3))
        self.tab_bodies = _Tab("Bodies", palette)
        self.tab_history = _Tab("History", palette)
        tabs.addWidget(self.tab_bodies)
        tabs.addWidget(self.tab_history)
        tabs.addStretch(1)
        layout.addLayout(tabs)

        self.stack = QStackedWidget()
        self.bodies_tree = QTreeWidget()
        self.bodies_tree.setHeaderHidden(True)
        self.bodies_tree.setRootIsDecorated(True)
        self.bodies_tree.setIndentation(14)
        self.bodies_tree.setFrameShape(QTreeWidget.NoFrame)
        # Extended, so several rows can be picked and grouped from here.
        self.bodies_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.bodies_tree.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.bodies_tree.setMinimumHeight(160)
        self.bodies_tree.setMaximumHeight(320)
        self.history_list = QListWidget()
        self.history_list.setFrameShape(QListWidget.NoFrame)
        self.history_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.history_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.history_list.setMinimumHeight(160)
        self.history_list.setMaximumHeight(320)
        self.stack.addWidget(self.bodies_tree)
        self.stack.addWidget(self.history_list)
        layout.addWidget(self.stack)

        self.empty = QLabel("No bodies yet")
        self.empty.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.empty)

        self.tab_bodies.clicked.connect(lambda: self.show_tab(0))
        self.tab_history.clicked.connect(lambda: self.show_tab(1))
        self.bodies_tree.itemClicked.connect(self._row_clicked)
        self.history_list.itemClicked.connect(self._feature_clicked)
        self.bodies_tree.itemDoubleClicked.connect(self._rename_row)
        self.history_list.itemDoubleClicked.connect(self._rename_feature)
        self.bodies_tree.itemExpanded.connect(self._remember_expansion)
        self.bodies_tree.itemCollapsed.connect(self._remember_expansion)
        for widget in (self.bodies_tree, self.history_list):
            widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.bodies_tree.customContextMenuRequested.connect(self._body_menu)
        self.history_list.customContextMenuRequested.connect(self._feature_menu)

        self.show_tab(0)
        self.apply_palette(palette)

    def show_tab(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.tab_bodies.set_current(index == 0)
        self.tab_history.set_current(index == 1)
        self.refresh()

    def apply_palette(self, palette: Palette) -> None:
        super().apply_palette(palette)
        self._palette = palette
        for tab in (self.tab_bodies, self.tab_history):
            tab.apply_palette(palette)
        self.empty.setStyleSheet(
            f"color:{palette.text_faint}; font-size:12px; padding:{METRICS.space(4)}px 0;"
        )
        self.refresh()

    # -- contents -------------------------------------------------------
    def refresh(self) -> None:
        self._fill_bodies()
        self._fill_history()
        showing_bodies = self.stack.currentIndex() == 0
        count = (
            self.bodies_tree.topLevelItemCount() if showing_bodies
            else self.history_list.count()
        )
        self.empty.setText("No bodies yet" if showing_bodies else "No features yet")
        self.empty.setVisible(count == 0)
        self.stack.setVisible(count > 0)
        self.adjustSize()

    def _fill_bodies(self) -> None:
        self.bodies_tree.clear()
        for kind, name in self.document.root_items():
            self.bodies_tree.addTopLevelItem(self._row(kind, name))

    def _row(self, kind: str, name: str) -> QTreeWidgetItem:
        """One tree row, with its children when it is a group."""
        palette = self._palette
        item = QTreeWidgetItem([name])
        item.setData(0, NAME_ROLE, name)
        item.setData(0, KIND_ROLE, kind)
        item.setSizeHint(0, QSize(0, 30))

        if kind == "group":
            group = self.document.group(name)
            members = self.document.expand([name])
            item.setIcon(0, icon("group", palette.text_muted, 18))
            item.setText(
                0, f"{name}  ({len(members)})"
            )
            if group is not None and not group.visible:
                item.setForeground(0, _color(palette.text_faint))
            for child_kind, child_name in self.document.group_members(name):
                item.addChild(self._row(child_kind, child_name))
            item.setExpanded(group.expanded if group is not None else True)
        else:
            body = self.document.body(name)
            item.setIcon(0, icon("body", palette.text_muted, 18))
            if body is not None and not body.visible:
                item.setForeground(0, _color(palette.text_faint))
        return item

    def _remember_expansion(self, item: QTreeWidgetItem) -> None:
        if item.data(0, KIND_ROLE) != "group":
            return
        group = self.document.group(item.data(0, NAME_ROLE))
        if group is not None:
            group.expanded = item.isExpanded()

    def _fill_history(self) -> None:
        from ...core.document import FeatureState

        palette = self._palette
        self.history_list.clear()
        icons = {
            "box": "box", "cylinder": "cylinder", "sphere": "sphere",
            "cone": "cone", "torus": "torus", "tube": "tube",
            "wedge": "wedge", "polygon_prism": "polygon",
            "split": "split", "import": "import",
        }
        for feature in self.document.features:
            item = QListWidgetItem(feature.name)
            item.setData(Qt.UserRole, feature.id)
            colour = palette.text_muted
            if feature.state is FeatureState.FAILED:
                colour = palette.danger
                item.setToolTip(feature.message)
            elif feature.state is FeatureState.NEEDS_ATTENTION:
                colour = palette.warning
                item.setToolTip(feature.message)
            elif feature.state is FeatureState.SUPPRESSED:
                colour = palette.text_faint
            name = icons.get(feature.type_name, "history")
            if feature.state in (FeatureState.FAILED, FeatureState.NEEDS_ATTENTION):
                name = "warning"
            item.setIcon(icon(name, colour, 18))
            item.setSizeHint(QSize(0, 32))
            if feature.state is not FeatureState.OK:
                item.setForeground(_color(colour))
            self.history_list.addItem(item)

    def _row_clicked(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        self.body_selected.emit(item.data(0, NAME_ROLE))

    # -- context menus ---------------------------------------------------
    def _selected_names(self) -> list[str]:
        return [
            item.data(0, NAME_ROLE) for item in self.bodies_tree.selectedItems()
        ]

    def _body_menu(self, position) -> None:
        """Only actions that apply to this row -- the spec's rule for menus."""
        from PySide6.QtWidgets import QMenu

        item = self.bodies_tree.itemAt(position)
        if item is None:
            return
        name = item.data(0, NAME_ROLE)
        kind = item.data(0, KIND_ROLE)
        chosen = self._selected_names()
        if name not in chosen:
            chosen = [name]

        menu = QMenu(self)
        if kind == "group":
            group = self.document.group(name)
            if group is None:
                return
            menu.addAction(
                "Hide" if group.visible else "Show",
                lambda: self.group_visibility_toggled.emit(name, not group.visible),
            )
            menu.addSeparator()
            menu.addAction("Rename…", lambda: self._rename_row(item))
            menu.addAction("Duplicate", lambda: self.group_duplicate_requested.emit(name))
            menu.addAction("Ungroup", lambda: self.ungroup_requested.emit(name))
            if len(chosen) > 1:
                menu.addAction("Group selection", lambda: self.group_requested.emit(chosen))
            menu.addSeparator()
            menu.addAction("Delete group", lambda: self.group_delete_requested.emit(name))
            menu.exec(self.bodies_tree.mapToGlobal(position))
            return

        body = self.document.body(name)
        if body is None:
            return
        menu.addAction(
            "Hide" if body.visible else "Show",
            lambda: self.visibility_toggled.emit(name, not body.visible),
        )
        menu.addAction("Isolate", lambda: self.isolate_requested.emit(name))
        if any(not b.visible for b in self.document.bodies.values()):
            menu.addAction("Show all", self.show_all_requested.emit)
        menu.addSeparator()
        menu.addAction("Rename…", lambda: self._rename_row(item))
        menu.addAction("Duplicate", lambda: self.duplicate_requested.emit(name))
        if len(chosen) > 1:
            menu.addAction(
                f"Group these {len(chosen)}",
                lambda: self.group_requested.emit(chosen),
            )
        holder = self.document.group_of(name)
        if holder is not None:
            menu.addAction(
                f"Ungroup {holder.name}",
                lambda: self.ungroup_requested.emit(holder.name),
            )
        menu.addSeparator()
        menu.addAction("Delete", lambda: self.delete_requested.emit(name))
        menu.exec(self.bodies_tree.mapToGlobal(position))

    def _feature_menu(self, position) -> None:
        from PySide6.QtWidgets import QMenu

        item = self.history_list.itemAt(position)
        if item is None:
            return
        feature_id = item.data(Qt.UserRole)
        feature = self.document.feature(feature_id)
        if feature is None:
            return

        menu = QMenu(self)
        menu.addAction("Rename…", lambda: self._rename_feature(item))
        if feature.suppressed:
            menu.addAction(
                "Unsuppress",
                lambda: self.feature_action.emit(feature_id, "unsuppress"),
            )
        else:
            menu.addAction(
                "Suppress", lambda: self.feature_action.emit(feature_id, "suppress")
            )
        menu.addSeparator()
        menu.addAction(
            "Delete", lambda: self.feature_action.emit(feature_id, "delete")
        )
        menu.exec(self.history_list.mapToGlobal(position))

    # -- renaming --------------------------------------------------------
    def _rename_row(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        from PySide6.QtWidgets import QInputDialog

        old = item.data(0, NAME_ROLE)
        kind = item.data(0, KIND_ROLE)
        title = "Rename group" if kind == "group" else "Rename body"
        new, accepted = QInputDialog.getText(self, title, "Name:", text=old)
        if not accepted or not new.strip() or new.strip() == old:
            return
        if kind == "group":
            self.group_rename_requested.emit(old, new.strip())
        else:
            self.rename_requested.emit(old, new.strip())

    def _rename_feature(self, item: QListWidgetItem) -> None:
        from PySide6.QtWidgets import QInputDialog

        feature_id = item.data(Qt.UserRole)
        feature = self.document.feature(feature_id)
        if feature is None:
            return
        new, accepted = QInputDialog.getText(
            self, "Rename feature", "Name:", text=feature.name
        )
        if accepted and new.strip() and new.strip() != feature.name:
            self.feature_action.emit(f"{feature_id}:{new.strip()}", "rename")

    def _feature_clicked(self, item: QListWidgetItem) -> None:
        self.feature_selected.emit(item.data(Qt.UserRole))

    def toggle_current_body(self) -> None:
        item = self.bodies_tree.currentItem()
        if item is None:
            return
        name = item.data(0, NAME_ROLE)
        if item.data(0, KIND_ROLE) == "group":
            group = self.document.group(name)
            if group is not None:
                self.group_visibility_toggled.emit(name, not group.visible)
            return
        body = self.document.body(name)
        if body is not None:
            self.visibility_toggled.emit(name, not body.visible)


def _color(hex_color: str):
    from PySide6.QtGui import QColor

    return QColor(hex_color)
