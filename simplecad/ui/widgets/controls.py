"""Reusable controls: large, quiet, and consistent.

Everything here follows the same rules -- generous hit areas, hairline borders,
one accent colour, no bevels or gradients. Tool affordances are big enough to be
read at a glance, which is what keeps the interface from turning into rows of
tiny toolbar buttons.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from ..icons import icon
from ..theme import METRICS, Palette


class IconButton(QToolButton):
    """A square, quiet icon button. Used throughout the top bar."""

    def __init__(
        self,
        name: str,
        palette: Palette,
        tooltip: str = "",
        size: int = 36,
        checkable: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._name = name
        self._palette = palette
        self._size = size
        self.setCheckable(checkable)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(size, size)
        self.setIconSize(QSize(int(size * 0.55), int(size * 0.55)))
        self.setToolTip(tooltip)
        self.setAutoRaise(True)
        self.apply_palette(palette)

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.setIcon(icon(self._name, palette.text_muted, int(self._size * 0.55)))
        radius = METRICS.radius_sm
        self.setStyleSheet(
            f"""
            QToolButton {{
                border: none; border-radius: {radius}px; background: transparent;
            }}
            QToolButton:hover   {{ background: {palette.surface_raised}; }}
            QToolButton:pressed {{ background: {palette.accent_soft}; }}
            QToolButton:checked {{ background: {palette.accent_soft}; }}
            """
        )

    def enterEvent(self, event) -> None:  # noqa: N802
        self.setIcon(icon(self._name, self._palette.text, int(self._size * 0.55)))
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.setIcon(icon(self._name, self._palette.text_muted, int(self._size * 0.55)))
        super().leaveEvent(event)


class ToolTile(QToolButton):
    """A large icon-over-label tile for the tool rail."""

    def __init__(self, name: str, label: str, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        self._name = name
        self._palette = palette
        self.setText(label)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.setIconSize(QSize(24, 24))
        self.setFixedSize(64, 62)
        self.apply_palette(palette)

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.setIcon(icon(self._name, palette.text_muted, 24))
        self.setStyleSheet(
            f"""
            QToolButton {{
                border: none;
                border-radius: {METRICS.radius}px;
                background: transparent;
                color: {palette.text_muted};
                font-size: 10.5px;
                padding-top: 6px;
            }}
            QToolButton:hover   {{ background: {palette.surface_raised}; color: {palette.text}; }}
            QToolButton:checked {{ background: {palette.accent_soft}; color: {palette.accent_hover}; }}
            """
        )

    def enterEvent(self, event) -> None:  # noqa: N802
        self.setIcon(icon(self._name, self._palette.text, 24))
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        color = self._palette.accent_hover if self.isChecked() else self._palette.text_muted
        self.setIcon(icon(self._name, color, 24))
        super().leaveEvent(event)


class FloatingCard(QFrame):
    """A rounded panel that sits over the viewport.

    This is the shape almost every SimpleCAD panel takes: properties, the model
    browser, contextual actions. Panels float rather than dock so the viewport
    stays the focus and the layout never fragments into fixed regions.
    """

    def __init__(self, palette: Palette, parent=None, shadow: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("FloatingPanel")
        self._palette = palette
        # Deliberately not apply_palette(): subclasses override it and would run
        # against attributes their own __init__ has not created yet.
        self._apply_card_style(palette)
        if shadow:
            effect = QGraphicsDropShadowEffect(self)
            effect.setBlurRadius(36)
            effect.setOffset(0, 8)
            effect.setColor(QColor(0, 0, 0, 90))
            self.setGraphicsEffect(effect)

    def _apply_card_style(self, palette: Palette) -> None:
        self.setStyleSheet(
            f"""
            QFrame#FloatingPanel {{
                background: {palette.surface_raised};
                border: 1px solid {palette.border};
                border-radius: {METRICS.radius_lg}px;
            }}
            """
        )

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        self._apply_card_style(palette)


class SectionLabel(QLabel):
    """A small, quiet heading used inside panels."""

    def __init__(self, text: str, palette: Palette, parent=None) -> None:
        super().__init__(text.upper(), parent)
        self.apply_palette(palette)

    def apply_palette(self, palette: Palette) -> None:
        self.setStyleSheet(
            f"color:{palette.text_faint}; font-size:10.5px;"
            f"font-weight:600; letter-spacing:0.7px;"
        )


class ValueField(QLineEdit):
    """A dimension input that accepts expressions as readily as numbers.

    ``12``, ``12mm``, ``0.5in``, ``wall``, ``width / 2``, ``hole + 0.4mm`` are
    all valid. Every dimension in SimpleCAD is an expression, so this is the one
    numeric control the whole app uses.
    """

    committed = Signal(float)
    edited_live = Signal(float)

    def __init__(
        self,
        palette: Palette,
        parameters=None,
        dimension=None,
        value: float = 0.0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        from ...core.units import Dimension

        self._palette = palette
        self._parameters = parameters
        self._dimension = dimension or Dimension.LENGTH
        self._value = value
        self.setAlignment(Qt.AlignRight)
        self.setMinimumHeight(METRICS.control_height)
        self.set_value(value)
        self.editingFinished.connect(self._commit)
        self.textEdited.connect(self._preview)
        self.apply_palette(palette)

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette

    def set_parameters(self, parameters) -> None:
        self._parameters = parameters

    def value(self) -> float:
        return self._value

    def set_value(self, value: float) -> None:
        from ...core.units import format_quantity

        self._value = float(value)
        self.setText(format_quantity(self._value, self._dimension))
        self._mark_valid(True)

    def expression(self) -> str:
        return self.text().strip()

    def _evaluate(self, text: str) -> float | None:
        from ...core.params import ExpressionError, evaluate

        try:
            names = self._parameters.values if self._parameters else {}
            return evaluate(text, names, self._dimension)
        except ExpressionError:
            return None

    def _preview(self, text: str) -> None:
        result = self._evaluate(text)
        self._mark_valid(result is not None)
        if result is not None:
            self.edited_live.emit(result)

    def _commit(self) -> None:
        result = self._evaluate(self.text())
        if result is None:
            self.set_value(self._value)  # revert to the last good value
            return
        self._value = result
        self._mark_valid(True)
        self.committed.emit(result)

    def _mark_valid(self, valid: bool) -> None:
        self.setProperty("invalid", "false" if valid else "true")
        self.style().unpolish(self)
        self.style().polish(self)


class LabeledField(QWidget):
    """A label paired with a :class:`ValueField`, laid out consistently."""

    def __init__(self, label: str, field: ValueField, palette: Palette, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(METRICS.space(3))
        self.label = QLabel(label)
        self.label.setStyleSheet(f"color:{palette.text_muted};")
        self.label.setMinimumWidth(72)
        self.field = field
        layout.addWidget(self.label)
        layout.addWidget(field, 1)


class PrimaryButton(QPushButton):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self.setProperty("variant", "primary")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(METRICS.control_height)


class GhostButton(QPushButton):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self.setProperty("variant", "ghost")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(METRICS.control_height)


class Hint(QLabel):
    """The quiet line that tells the user what to do next.

    Elides rather than growing without limit. It shares the bottom edge with the
    contextual toolbar, and between a status line and a row of controls it is
    the status line that should give way -- so it is told how much room is left
    and trims itself to fit, instead of running underneath the buttons.
    """

    def __init__(self, palette: Palette, parent=None) -> None:
        super().__init__("", parent)
        self._full = ""
        self._limit = 0
        self.apply_palette(palette)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt naming
        self._full = text
        self._relayout()

    def full_text(self) -> str:
        return self._full

    def elide_to(self, width: int) -> None:
        self._limit = max(0, int(width))
        self._relayout()

    def _relayout(self) -> None:
        from PySide6.QtGui import QFontMetrics

        if self._limit <= 0:
            super().setText(self._full)
            return
        metrics = QFontMetrics(self.font())
        super().setText(
            metrics.elidedText(self._full, Qt.ElideRight, max(80, self._limit))
        )

    def apply_palette(self, palette: Palette) -> None:
        self.setStyleSheet(
            f"color:{palette.text_muted}; font-size:{METRICS.font_size_sm}px;"
        )
