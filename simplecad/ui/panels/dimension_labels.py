"""Dimension labels drawn over the viewport.

Each dimensional constraint gets a small label at its geometry, positioned by
projecting the anchor point to screen. Clicking one turns it into a text field:
type a new value, press Enter, and the sketch re-solves.

They are Qt widgets rather than OCCT annotations for two reasons -- this OCP
build does not wrap ``PrsDim``, and a widget can be clicked and typed into,
which an annotation cannot.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QLineEdit, QWidget

from ..theme import METRICS, Palette


class DimensionLabel(QLabel):
    """One dimension. Click to edit."""

    edit_requested = Signal(object)

    def __init__(self, anchor: dict, palette: Palette, parent=None) -> None:
        super().__init__(anchor["text"], parent)
        self.anchor = anchor
        self.setCursor(Qt.PointingHandCursor)
        self.setAlignment(Qt.AlignCenter)
        self.apply_palette(palette)
        self.adjustSize()

    def apply_palette(self, palette: Palette) -> None:
        self.setStyleSheet(
            f"""
            QLabel {{
                background: {palette.surface_raised};
                color: {palette.text};
                border: 1px solid {palette.border};
                border-radius: {METRICS.radius_sm}px;
                padding: 2px 7px;
                font-size: 12px;
                font-weight: 600;
            }}
            QLabel:hover {{ border-color: {palette.accent}; color: {palette.accent_hover}; }}
            """
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.edit_requested.emit(self.anchor)


class DimensionEditor(QLineEdit):
    """The inline field a dimension turns into."""

    committed = Signal(float)
    dismissed = Signal()

    def __init__(self, value: float, palette: Palette, parameters=None, parent=None):
        super().__init__(parent)
        self._parameters = parameters
        self.setText(f"{value:.2f}")
        self.selectAll()
        self.setFixedWidth(96)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            f"""
            QLineEdit {{
                background: {palette.surface_raised};
                color: {palette.text};
                border: 1px solid {palette.accent};
                border-radius: {METRICS.radius_sm}px;
                padding: 2px 6px;
                font-size: 12px;
                selection-background-color: {palette.accent};
            }}
            """
        )
        self.returnPressed.connect(self._commit)

    def _commit(self) -> None:
        from ...core.params import ExpressionError, evaluate

        try:
            names = self._parameters.values if self._parameters else {}
            value = evaluate(self.text(), names)
        except ExpressionError:
            self.setStyleSheet(self.styleSheet().replace("border: 1px solid", "border: 2px solid"))
            return
        self.committed.emit(value)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.dismissed.emit()
            return
        super().keyPressEvent(event)


class DimensionOverlay(QWidget):
    """Keeps a set of dimension labels positioned over the viewport."""

    value_changed = Signal(object, float)

    def __init__(self, stage, palette: Palette) -> None:
        super().__init__(stage)
        self.stage = stage
        self._palette = palette
        self._labels: list[DimensionLabel] = []
        self._editor: DimensionEditor | None = None
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setStyleSheet("background: transparent;")

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        for label in self._labels:
            label.apply_palette(palette)

    def clear(self) -> None:
        for label in self._labels:
            label.deleteLater()
        self._labels = []
        self._dismiss_editor()

    def rebuild(self, anchors: list[dict]) -> None:
        self.clear()
        for anchor in anchors:
            label = DimensionLabel(anchor, self._palette, self.stage)
            label.edit_requested.connect(self._begin_edit)
            label.show()
            self._labels.append(label)
        self.reposition()

    def reposition(self) -> None:
        """Project every anchor to screen and move its label there."""
        viewport = self.stage.viewport
        if not viewport.is_ready:
            return
        ratio = viewport.devicePixelRatioF()
        for label in self._labels:
            try:
                x, y = viewport.view.Convert(*label.anchor["point"])
            except Exception:  # noqa: BLE001
                continue
            label.move(
                int(x / ratio) - label.width() // 2,
                int(y / ratio) - label.height() // 2,
            )
            label.raise_()

    # -- editing ---------------------------------------------------------
    def _begin_edit(self, anchor: dict) -> None:
        self._dismiss_editor()
        editor = DimensionEditor(
            anchor["value"], self._palette,
            getattr(self.stage.window(), "document", None)
            and self.stage.window().document.parameters,
            self.stage,
        )
        viewport = self.stage.viewport
        ratio = viewport.devicePixelRatioF()
        x, y = viewport.view.Convert(*anchor["point"])
        editor.move(int(x / ratio) - editor.width() // 2, int(y / ratio) - 14)
        editor.committed.connect(
            lambda value, a=anchor: self._finish_edit(a, value)
        )
        editor.dismissed.connect(self._dismiss_editor)
        editor.show()
        editor.setFocus()
        editor.selectAll()
        self._editor = editor

    def _finish_edit(self, anchor: dict, value: float) -> None:
        self._dismiss_editor()
        self.value_changed.emit(anchor["entity"], value)

    def _dismiss_editor(self) -> None:
        if self._editor is not None:
            self._editor.hide()
            self._editor.deleteLater()
            self._editor = None

    @property
    def editing(self) -> bool:
        return self._editor is not None
