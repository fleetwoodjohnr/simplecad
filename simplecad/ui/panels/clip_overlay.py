"""Numbered on-model markers for interactive clip placement."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..theme import Palette


class ClipPlacementOverlay(QWidget):
    """A transparent, full-viewport layer showing clip centres and validity."""

    def __init__(self, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._project = None
        self.points: list[tuple[float, float, float]] = []
        self.valid: list[bool | None] = []
        self.hover: tuple[float, float, float] | None = None
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def attach(self, project) -> None:
        self._project = project

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.update()

    def set_points(self, points, valid=None) -> None:
        self.points = list(points)
        self.valid = list(valid or [None] * len(self.points))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._project is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        for index, point in enumerate(self.points):
            screen = self._project(point)
            if screen is None:
                continue
            state = self.valid[index] if index < len(self.valid) else None
            color = (
                self._palette.danger if state is False else
                self._palette.accent if state is True else self._palette.hover
            )
            self._draw_marker(painter, QPointF(*screen), str(index + 1), color)
        if self.hover is not None:
            screen = self._project(self.hover)
            if screen is not None:
                pen = QPen(QColor(self._palette.hover))
                pen.setWidthF(1.5)
                pen.setStyle(Qt.DashLine)
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(QPointF(*screen), 9.0, 9.0)
        painter.end()

    def _draw_marker(self, painter: QPainter, at: QPointF, label: str, color: str) -> None:
        radius = 10.0
        painter.setPen(QPen(QColor(color), 2.0))
        painter.setBrush(QColor(self._palette.surface_raised))
        painter.drawEllipse(at, radius, radius)
        painter.setPen(QPen(QColor(color)))
        painter.drawText(
            QRectF(at.x() - radius, at.y() - radius, radius * 2, radius * 2),
            Qt.AlignCenter,
            label,
        )
