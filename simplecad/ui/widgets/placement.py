"""Camera-projected placement directions drawn over the modelling view."""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget


class PlacementOverlay(QWidget):
    """Label a face-stable U/V frame and its current offset origin."""

    def __init__(self, viewport, palette, parent=None) -> None:
        super().__init__(parent)
        self.viewport = viewport
        self.palette = palette
        self.frame = None
        self.origin = None
        self.u = 0.0
        self.v = 0.0
        self.snap = ""
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        viewport.view_changed.connect(self.update)

    def set_placement(self, frame, origin, u: float, v: float, snap: str = "") -> None:
        self.frame = frame
        self.origin = origin
        self.u = u
        self.v = v
        self.snap = snap
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        if self.frame is None or self.origin is None:
            return
        center = self.viewport.project(self.origin)
        if center is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(QFont(painter.font().family(), 9, QFont.DemiBold))
        c = QPointF(*center)
        radius = 48.0
        directions = (
            (self.frame.x_axis, "+U  RIGHT", self.palette.grid_axis_x),
            (tuple(-x for x in self.frame.x_axis), "−U  LEFT", self.palette.grid_axis_x),
            (self.frame.y_axis, "+V  UP", self.palette.grid_axis_y),
            (tuple(-x for x in self.frame.y_axis), "−V  DOWN", self.palette.grid_axis_y),
        )
        for axis, label, color in directions:
            projected = self.viewport.project(tuple(
                self.origin[i] + axis[i] for i in range(3)
            ))
            if projected is None:
                continue
            vector = QPointF(projected[0] - center[0], projected[1] - center[1])
            length = (vector.x() ** 2 + vector.y() ** 2) ** 0.5
            if length < 1.0:
                continue
            unit = QPointF(vector.x() / length, vector.y() / length)
            end = c + unit * radius
            normal = QPointF(-unit.y(), unit.x())
            painter.setPen(QPen(QColor(color), 2.2, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(c, end)
            painter.setBrush(QColor(color))
            painter.drawPolygon(QPolygonF([
                end, end - unit * 9.0 + normal * 4.5, end - unit * 9.0 - normal * 4.5,
            ]))
            label_at = end + unit * 8.0
            rect = painter.fontMetrics().boundingRect(label)
            painter.drawText(
                QPointF(label_at.x() - rect.width() / 2, label_at.y() + rect.height() / 3),
                label,
            )
        painter.setPen(QPen(QColor(self.palette.text), 2))
        painter.setBrush(QColor(self.palette.surface_raised))
        painter.drawEllipse(c, 5, 5)
        detail = f"U {self.u:+.2f} mm   V {self.v:+.2f} mm"
        if self.snap:
            detail += f"   • {self.snap}"
        metrics = painter.fontMetrics()
        rect = metrics.boundingRect(detail).adjusted(-10, -6, 10, 6)
        rect.moveCenter((c + QPointF(0, 82)).toPoint())
        painter.setPen(QPen(QColor(self.palette.border), 1))
        painter.setBrush(QColor(self.palette.surface_raised))
        painter.drawRoundedRect(rect, 8, 8)
        painter.setPen(QColor(self.palette.text))
        painter.drawText(rect, Qt.AlignCenter, detail)

