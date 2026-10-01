"""On-model guides for Join Parts: which face is which, and where joints go."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from ..theme import Palette


class ClipPlacementOverlay(QWidget):
    """A transparent, full-viewport layer drawn over the model.

    It shows, in screen space and so always legible:

    * the two picked faces, labelled A and B at their centres;
    * the connection direction -- an arrow out of the part that carries the
      tabs, into the part that receives them;
    * every joint's footprint on the seam, filled in the colour of the side
      that carries its male half, and red where it does not fit;
    * a dashed ring under the cursor while placing joints by hand.
    """

    def __init__(self, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._project = None
        self.faces: dict[str, tuple[float, float, float]] = {}
        self.arrow: tuple | None = None
        self.footprints: list[tuple[list, str, bool]] = []
        self.hover: tuple[float, float, float] | None = None
        # Kept for callers of the original marker API.
        self.points: list[tuple[float, float, float]] = []
        self.valid: list[bool | None] = []
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def attach(self, project) -> None:
        self._project = project

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.update()

    def set_scene(self, *, faces=None, arrow=None, footprints=()) -> None:
        self.faces = dict(faces or {})
        self.arrow = arrow
        self.footprints = list(footprints)
        self.update()

    def set_points(self, points, valid=None) -> None:
        self.points = list(points)
        self.valid = list(valid or [None] * len(self.points))
        self.update()

    def _colors(self) -> dict[str, str]:
        return {"a": self._palette.accent, "b": self._palette.warning,
                "bad": self._palette.danger}

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._project is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        colors = self._colors()
        for corners, role, ok in self.footprints:
            screen = [self._project(point) for point in corners]
            if any(point is None for point in screen):
                continue
            color = QColor(colors["bad"] if not ok else colors.get(role, colors["a"]))
            painter.setPen(QPen(color, 2.0))
            fill = QColor(color)
            fill.setAlpha(70)
            painter.setBrush(fill)
            painter.drawPolygon(QPolygonF([QPointF(*point) for point in screen]))
        if self.arrow is not None:
            start, end = (self._project(point) for point in self.arrow)
            if start is not None and end is not None:
                self._draw_arrow(painter, QPointF(*start), QPointF(*end), QColor(colors["a"]))
        placed: list[QPointF] = []
        for role, point in self.faces.items():
            screen = self._project(point)
            if screen is None:
                continue
            at = QPointF(*screen)
            # Mating faces share a centre; keep both labels readable.
            while any((at - other).manhattanLength() < 22.0 for other in placed):
                at = QPointF(at.x() + 24.0, at.y())
            placed.append(at)
            self._draw_marker(painter, at, role.upper(), colors.get(role, colors["a"]))
        for index, point in enumerate(self.points):
            screen = self._project(point)
            if screen is None:
                continue
            state = self.valid[index] if index < len(self.valid) else None
            color = (self._palette.danger if state is False else
                     self._palette.accent if state is True else self._palette.hover)
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

    def _draw_arrow(self, painter: QPainter, start: QPointF, end: QPointF, color: QColor) -> None:
        painter.setPen(QPen(color, 2.5))
        painter.drawLine(start, end)
        delta = end - start
        length = (delta.x() ** 2 + delta.y() ** 2) ** 0.5
        if length < 1.0:
            return
        ux, uy = delta.x() / length, delta.y() / length
        head = 10.0
        left = QPointF(end.x() - head * (ux - uy * 0.5), end.y() - head * (uy + ux * 0.5))
        right = QPointF(end.x() - head * (ux + uy * 0.5), end.y() - head * (uy - ux * 0.5))
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([end, left, right]))

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
