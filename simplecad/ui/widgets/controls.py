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
    QButtonGroup, QFrame, QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
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
    """A compact icon-over-label tile for navigation and sketch tools."""

    def __init__(self, name: str, label: str, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        self._name = name
        self._palette = palette
        self.setText(label)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.setIconSize(QSize(22, 22))
        self.setFixedSize(58, 52)
        self.apply_palette(palette)

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        colour = palette.accent_hover if self.isChecked() else palette.text_muted
        self.setIcon(icon(self._name, colour, 22))
        self.setStyleSheet(
            f"""
            QToolButton {{
                border: none;
                border-radius: {METRICS.radius}px;
                background: transparent;
                color: {palette.text_muted};
                font-size: 9.5px;
                font-weight: 600;
                padding-top: 3px;
            }}
            QToolButton:hover   {{ background: {palette.surface_raised}; color: {palette.text}; }}
            QToolButton:checked {{ background: {palette.accent_soft}; color: {palette.accent_hover}; }}
            """
        )

    def enterEvent(self, event) -> None:  # noqa: N802
        self.setIcon(icon(self._name, self._palette.text, 22))
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        color = self._palette.accent_hover if self.isChecked() else self._palette.text_muted
        self.setIcon(icon(self._name, color, 22))
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
            effect.setBlurRadius(24)
            effect.setOffset(0, 6)
            effect.setColor(QColor(0, 0, 0, 72))
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
        self.setObjectName("ValueField")
        self.setAlignment(Qt.AlignRight)
        self.setMinimumHeight(METRICS.control_height)
        self.set_value(value)
        self.editingFinished.connect(self._commit)
        self.textEdited.connect(self._preview)
        self.apply_palette(palette)

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.setStyleSheet(
            'QLineEdit#ValueField { font-family:"JetBrains Mono"; '
            "font-size:12px; font-weight:600; }"
        )

    def set_parameters(self, parameters) -> None:
        self._parameters = parameters

    def value(self) -> float:
        # Live previews read while the editor still has focus, before
        # ``editingFinished`` promotes the text to ``_value``. Return the
        # current valid expression so typing a gap or placement offset moves
        # the preview immediately; invalid partial text keeps the last value.
        current = self._evaluate(self.text())
        return self._value if current is None else current

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


class AnchorGrid(QWidget):
    """A compact 3×3 face-anchor picker with real, generous hit targets."""

    changed = Signal(str)
    _keys = (
        ("top_left", "↖"), ("top", "↑"), ("top_right", "↗"),
        ("left", "←"), ("center", "●"), ("right", "→"),
        ("bottom_left", "↙"), ("bottom", "↓"), ("bottom_right", "↘"),
    )

    def __init__(self, palette: Palette, value: str = "center", parent=None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._value = value
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(5)
        layout.setVerticalSpacing(5)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        for index, (key, glyph) in enumerate(self._keys):
            button = QToolButton(self)
            button.setText(glyph)
            button.setToolTip(key.replace("_", " ").title())
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedSize(36, 32)
            button.setProperty("anchor", key)
            button.setChecked(key == value)
            button.clicked.connect(lambda _checked=False, selected=key: self.set_value(selected))
            self.group.addButton(button)
            self.buttons[key] = button
            layout.addWidget(button, index // 3, index % 3)
        self.apply_palette(palette)

    def value(self) -> str:
        return self._value

    def set_value(self, value: str, *, emit: bool = True) -> None:
        if value not in self.buttons:
            return
        changed = value != self._value
        self._value = value
        self.buttons[value].setChecked(True)
        if changed and emit:
            self.changed.emit(value)

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.setStyleSheet(
            f"""
            QToolButton {{
                border:1px solid {palette.border}; border-radius:8px;
                background:{palette.surface}; color:{palette.text_muted};
                font-size:16px;
            }}
            QToolButton:hover {{ border-color:{palette.accent}; color:{palette.text}; }}
            QToolButton:checked {{
                background:{palette.accent_soft}; border-color:{palette.accent};
                color:{palette.accent_hover}; font-weight:700;
            }}
            """
        )


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
        self.setVisible(bool(text))
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
            f"background:{palette.surface_raised}; border:1px solid {palette.border};"
            f"border-radius:{METRICS.radius_sm}px; padding:6px 10px;"
        )
