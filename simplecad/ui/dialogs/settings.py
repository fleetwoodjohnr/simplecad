"""Settings: appearance, units, modelling numbers, keyboard shortcuts, updates.

Every control applies as soon as it changes and is saved at once -- there is
no OK to forget. Shortcut edits that clash with another action are refused
where they are typed, naming the action that already has the key.
"""

from __future__ import annotations

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QHeaderView, QKeySequenceEdit,
    QLabel, QPushButton, QTableWidget, QTabWidget, QVBoxLayout, QWidget,
)

THEMES = (("system", "Follow the desktop"), ("light", "Light"), ("dark", "Dark"))
UNITS = (("mm", "Millimetres (mm)"), ("cm", "Centimetres (cm)"), ("m", "Metres (m)"),
         ("in", "Inches (in)"))


class SettingsDialog(QDialog):
    def __init__(self, window) -> None:
        super().__init__(window)
        self.window_ = window
        self.setWindowTitle("Settings")
        self.setModal(False)
        self.resize(620, 560)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.tabs.addTab(self._general(), "General")
        self.tabs.addTab(self._modelling(), "Modelling")
        self.tabs.addTab(self._shortcuts(), "Shortcuts")
        updates = getattr(window, "updates_tab", None)
        if callable(updates):
            self.tabs.addTab(updates(self), "Updates")
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        layout.addLayout(row)

    def show_tab(self, name: str) -> None:
        for index in range(self.tabs.count()):
            if self.tabs.tabText(index).lower() == name.lower():
                self.tabs.setCurrentIndex(index)

    # -- General ---------------------------------------------------------
    def _general(self) -> QWidget:
        from ...core.settings import display_unit, theme_choice

        page = QWidget()
        form = QFormLayout(page)
        self.theme = QComboBox()
        for key, label in THEMES:
            self.theme.addItem(label, key)
        self.theme.setCurrentIndex(
            next(i for i, (key, _l) in enumerate(THEMES) if key == (theme_choice() or "system"))
        )
        self.theme.currentIndexChanged.connect(
            lambda _i: self.window_.set_theme(self.theme.currentData())
        )
        form.addRow("Theme", self.theme)
        self.unit = QComboBox()
        for key, label in UNITS:
            self.unit.addItem(label, key)
        self.unit.setCurrentIndex(next(i for i, (k, _l) in enumerate(UNITS) if k == display_unit()))
        self.unit.currentIndexChanged.connect(self._unit_changed)
        form.addRow("Show lengths in", self.unit)
        note = QLabel("Any unit can still be typed: 12mm, 1.5cm, 0.5in, width / 2.")
        note.setWordWrap(True)
        form.addRow("", note)
        return page

    def _unit_changed(self, _index: int) -> None:
        from ...core.settings import set_display_unit

        set_display_unit(self.unit.currentData())
        self.window_.set_hint(f"Lengths are now shown in {self.unit.currentData()}.")

    # -- Modelling -------------------------------------------------------
    def _modelling(self) -> QWidget:
        from ...core.settings import number_preference, ui_preference
        from ...core.units import Dimension
        from ..widgets.controls import ValueField

        page = QWidget()
        form = QFormLayout(page)
        palette = self.window_.palette_
        self.numbers = {}
        for key, label, dimension in (
            ("rotation_snap", "Rotation snaps every", Dimension.ANGLE),
            ("move_step", "Move step", Dimension.LENGTH),
            ("history_depth", "Undo steps kept", Dimension.SCALAR),
        ):
            field = ValueField(palette, None, dimension, number_preference(key))
            field.committed.connect(lambda value, k=key: self._number_changed(k, value))
            self.numbers[key] = field
            form.addRow(label, field)
        self.compact = QCheckBox("Smaller files: keep only the finished bodies")
        self.compact.setToolTip(
            "Intermediate steps are not saved. The file can be many times smaller and "
            "still reopens at once; an edit far back in the timeline rebuilds the steps "
            "it needs."
        )
        self.compact.setChecked(ui_preference("compact_saves", False))
        self.compact.toggled.connect(self._compact_changed)
        form.addRow("Project files", self.compact)
        return page

    def _number_changed(self, key: str, value: float) -> None:
        from ...core.settings import number_preference, set_number_preference

        set_number_preference(key, value)
        stored = number_preference(key)
        if abs(stored - value) > 1e-9:
            self.numbers[key].set_value(stored)       # clamped to its range
        if key == "history_depth":
            history = self.window_.history
            history.limit = int(stored)
            history._undo[:] = history._undo[-history.limit:]
        self.window_.set_hint("Saved. Snapping applies to the next drag.")

    def _compact_changed(self, checked: bool) -> None:
        from ...core.settings import set_ui_preference

        set_ui_preference("compact_saves", bool(checked))

    # -- Shortcuts -------------------------------------------------------
    def _shortcuts(self) -> QWidget:
        from ...core.settings import DEFAULT_SHORTCUTS, SHORTCUT_LABELS

        page = QWidget()
        layout = QVBoxLayout(page)
        self.shortcut_status = QLabel("Click a shortcut and press the new keys.")
        self.shortcut_status.setWordWrap(True)
        layout.addWidget(self.shortcut_status)
        self.table = QTableWidget(len(DEFAULT_SHORTCUTS), 3)
        self.table.setHorizontalHeaderLabels(("Action", "Shortcut", ""))
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.editors = {}
        for row, action in enumerate(DEFAULT_SHORTCUTS):
            label = QLabel(SHORTCUT_LABELS.get(action, action))
            self.table.setCellWidget(row, 0, label)
            editor = QKeySequenceEdit()
            editor.setMaximumSequenceLength(1)
            editor.editingFinished.connect(lambda a=action: self._shortcut_edited(a))
            self.editors[action] = editor
            self.table.setCellWidget(row, 1, editor)
            reset = QPushButton("Default")
            reset.setToolTip(f"Back to {DEFAULT_SHORTCUTS[action]}")
            reset.clicked.connect(lambda _checked=False, a=action: self.reset_shortcut(a))
            self.table.setCellWidget(row, 2, reset)
        layout.addWidget(self.table, 1)
        reset_all = QPushButton("Restore all defaults")
        reset_all.clicked.connect(self.reset_all_shortcuts)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(reset_all)
        layout.addLayout(row)
        self._load_shortcuts()
        return page

    def _load_shortcuts(self) -> None:
        from ...core.settings import shortcuts

        for action, binding in shortcuts().items():
            editor = self.editors.get(action)
            if editor is not None:
                editor.blockSignals(True)
                editor.setKeySequence(QKeySequence(binding))
                editor.blockSignals(False)

    def _shortcut_edited(self, action: str) -> None:
        self.set_shortcut(action, self.editors[action].keySequence().toString(
            QKeySequence.PortableText))

    def set_shortcut(self, action: str, binding: str) -> bool:
        """Bind *action* to *binding*, refusing one another action already uses."""
        from ...core.settings import SHORTCUT_LABELS, set_shortcut, shortcuts

        binding = binding.strip()
        current = shortcuts()
        if not binding or binding == current.get(action):
            self._load_shortcuts()
            return False
        normal = QKeySequence(binding).toString(QKeySequence.PortableText)
        for other, existing in current.items():
            if other != action and QKeySequence(existing).toString(
                    QKeySequence.PortableText) == normal:
                self.shortcut_status.setText(
                    f"{normal} is already {SHORTCUT_LABELS.get(other, other)}. "
                    "Change that one first."
                )
                self._load_shortcuts()
                return False
        set_shortcut(action, normal)
        self.window_.reload_shortcuts()
        self.shortcut_status.setText(f"{SHORTCUT_LABELS.get(action, action)} is now {normal}.")
        self._load_shortcuts()
        return True

    def reset_shortcut(self, action: str) -> None:
        from ...core.settings import DEFAULT_SHORTCUTS, SHORTCUT_LABELS, reset_shortcut, shortcuts

        default = DEFAULT_SHORTCUTS[action]
        clash = next((other for other, binding in shortcuts().items()
                      if other != action and binding == default), None)
        if clash is not None:
            self.shortcut_status.setText(
                f"{default} is in use by {SHORTCUT_LABELS.get(clash, clash)}. "
                "Change that one first."
            )
            return
        reset_shortcut(action)
        self.window_.reload_shortcuts()
        self._load_shortcuts()
        self.shortcut_status.setText(f"{SHORTCUT_LABELS.get(action, action)} is back on {default}.")

    def reset_all_shortcuts(self) -> None:
        from ...core.settings import reset_shortcuts

        reset_shortcuts()
        self.window_.reload_shortcuts()
        self._load_shortcuts()
        self.shortcut_status.setText("Every shortcut is back on its default.")
