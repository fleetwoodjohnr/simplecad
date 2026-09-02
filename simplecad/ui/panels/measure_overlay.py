"""What a point-to-point measurement looks like on the model.

An indicator where the cursor will land, markers where the two points went, a
line between them, and the distance in the middle. All drawn as Qt over the
viewport rather than as OCCT annotations -- this OCP build does not wrap
``PrsDim``, and the same trick already carries the sketch dimension labels.

Every indicator is a **circle**, so there is one consistent thing to look for,
and what kind of place the cursor has found is drawn *inside* it: a square is a
corner, a ring is a centre, a diamond is a midpoint, a dot is a point on a face,
a bar is a point along an edge. So you can tell a corner from a midpoint without
reading the hint line, and you can always tell that the tool is tracking you at
all -- which is the thing that was actually missing.

The first picked point is filled rather than outlined. Two identical markers
leave you working out which end you started from; one filled and one hollow
does not.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from ..theme import METRICS, Palette

#: Radius of the circular indicator, in pixels.
RING = 6.5
#: Size of the glyph drawn inside it.
GLYPH = 5.0


class MeasureOverlay(QWidget):
    """Draws the snap indicator, the picked points and the line between them."""

    def __init__(self, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        self._palette = palette
        # Purely a display: every click has to reach the viewport underneath.
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.hover = None            # SnapPoint under the cursor
        self.picked: list = []       # SnapPoints confirmed, at most two
        self.reading = ""            # what to write on the finished line
        self._project = None

    def attach(self, project) -> None:
        """*project* maps a 3D point to widget pixels (the viewport's own)."""
        self._project = project

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.update()

    def clear(self) -> None:
        self.hover = None
        self.picked = []
        self.reading = ""
        self.update()

    # ------------------------------------------------------------------
    def _at(self, snap):
        if self._project is None or snap is None:
            return None
        screen = self._project(snap.position)
        return QPointF(*screen) if screen else None

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._project is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        points = [self._at(snap) for snap in self.picked]
        points = [p for p in points if p is not None]
        hover_at = self._at(self.hover)

        if len(points) == 2:
            self._draw_line(painter, points[0], points[1], self.reading, False)
        elif len(points) == 1 and hover_at is not None:
            if getattr(self.hover, "inference", None):
                self._draw_inference(
                    painter, points[0], hover_at, self.hover.inference
                )
            # A rubber band to wherever the cursor has snapped, with the length
            # updating as it moves: the measurement is readable before it is
            # committed, so a wrong second point is obvious before you click it.
            self._draw_line(
                painter, points[0], hover_at, self._live_reading(), True
            )

        for index, (snap, point) in enumerate(zip(self.picked, points)):
            self._draw_marker(
                painter, point, snap.kind, self._palette.accent,
                filled=index == 0,
            )
        if hover_at is not None and len(self.picked) < 2:
            self._draw_marker(painter, hover_at, self.hover.kind, self._palette.hover)
        painter.end()

    def _draw_inference(
        self, painter: QPainter, start: QPointF, end: QPointF, label: str
    ) -> None:
        """Draw the magnetic alignment line beyond the measured segment."""
        dx, dy = end.x() - start.x(), end.y() - start.y()
        length = math.hypot(dx, dy)
        if length < 1e-6:
            return
        ux, uy = dx / length, dy / length
        reach = math.hypot(self.width(), self.height())
        a = QPointF(start.x() - ux * reach, start.y() - uy * reach)
        b = QPointF(end.x() + ux * reach, end.y() + uy * reach)
        pen = QPen(QColor(self._palette.accent))
        pen.setWidthF(1.0)
        pen.setStyle(Qt.DotLine)
        painter.setPen(pen)
        painter.drawLine(a, b)

        badge = QRectF(end.x() + 10.0, end.y() - 20.0, 58.0, 18.0)
        painter.setPen(QPen(QColor(self._palette.border), 1.0))
        painter.setBrush(QColor(self._palette.surface_raised))
        painter.drawRoundedRect(badge, 5.0, 5.0)
        painter.setPen(QPen(QColor(self._palette.accent)))
        painter.drawText(badge, Qt.AlignCenter, label)

    def _live_reading(self) -> str:
        """The running distance from the first point to the cursor."""
        if not self.picked or self.hover is None:
            return ""
        a, b = self.picked[0].position, self.hover.position
        distance = math.dist(a, b)
        return f"{distance:.3f}".rstrip("0").rstrip(".") + " mm"

    def _draw_line(
        self, painter: QPainter, start: QPointF, end: QPointF,
        reading: str, provisional: bool,
    ) -> None:
        colour = self._palette.hover if provisional else self._palette.accent
        pen = QPen(QColor(colour))
        pen.setWidthF(1.6)
        pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(start, end)

        if not reading:
            return
        # The label sits on the line's midpoint, in a chip so it stays readable
        # over the model rather than over whatever colour happens to be there.
        middle = (start + end) / 2.0
        painter.setFont(self.font())
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(reading) + METRICS.space(3)
        height = metrics.height() + METRICS.space(1.5)
        box = QRectF(middle.x() - width / 2, middle.y() - height / 2, width, height)
        painter.setPen(QPen(QColor(self._palette.border), 1.0))
        painter.setBrush(QColor(self._palette.surface_raised))
        painter.drawRoundedRect(box, METRICS.radius_sm, METRICS.radius_sm)
        painter.setPen(
            QPen(QColor(self._palette.text_muted if provisional
                        else self._palette.text))
        )
        painter.drawText(box, Qt.AlignCenter, reading)

    def _draw_marker(
        self, painter: QPainter, at: QPointF, kind: str, color: str,
        filled: bool = False,
    ) -> None:
        """The circular indicator, with the kind of place drawn inside it."""
        pen = QPen(QColor(color))
        pen.setWidthF(2.0)
        painter.setPen(pen)
        painter.setBrush(QColor(color) if filled else Qt.NoBrush)
        painter.drawEllipse(at, RING, RING)

        # A filled marker needs its glyph knocked out of the fill to be legible.
        glyph = QPen(
            QColor(self._palette.surface_raised if filled else color)
        )
        glyph.setWidthF(1.6)
        painter.setPen(glyph)
        painter.setBrush(Qt.NoBrush)
        half = GLYPH / 2.0

        if kind == "vertex":
            painter.drawRect(QRectF(at.x() - half, at.y() - half, GLYPH, GLYPH))
        elif kind == "center":
            painter.drawEllipse(at, half, half)
        elif kind == "midpoint":
            painter.drawPolygon(QPolygonF([
                QPointF(at.x(), at.y() - half),
                QPointF(at.x() + half, at.y()),
                QPointF(at.x(), at.y() + half),
                QPointF(at.x() - half, at.y()),
            ]))
        elif kind == "on_edge":
            painter.drawLine(
                QPointF(at.x() - half, at.y()), QPointF(at.x() + half, at.y())
            )
        elif kind == "inference":
            painter.drawLine(
                QPointF(at.x() - half, at.y()), QPointF(at.x() + half, at.y())
            )
            painter.drawLine(
                QPointF(at.x(), at.y() - half), QPointF(at.x(), at.y() + half)
            )
        else:                                   # face centre, or a point on one
            glyph.setWidthF(2.6)
            painter.setPen(glyph)
            painter.drawPoint(at)
