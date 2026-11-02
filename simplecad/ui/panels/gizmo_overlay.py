"""The gizmo's plane squares and the angle wedge, drawn over the viewport.

OCCT draws the arrows and rings. The XY / XZ / YZ squares are drawn here and
hit-tested by the gizmo itself (``TransformGizmo.plane_handles``), because
OCCT's own plane parts were invisible until hovered and took presses meant for
the arrows. The wedge shows how far a ring drag has turned, with the angle.

Like the marquee, this is a transparent Qt widget over the GL view: it owns no
state, and repaints after each 3D frame so it tracks the camera exactly.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from ..theme import METRICS, Palette

#: Plane squares take the colour of their normal axis, as the arrows do.
AXIS_COLOURS = ("#E5484D", "#46A758", "#3E8EF7")
WEDGE_RADIUS_PX = 64


class GizmoOverlay(QWidget):
    def __init__(self, palette: Palette, viewport, parent=None) -> None:
        super().__init__(parent)
        self._palette = palette
        self.viewport = viewport
        self._drawn = None
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        viewport.painted.connect(self.sync, Qt.QueuedConnection)

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.update()

    def _state(self):
        gizmo = self.viewport.gizmo
        if gizmo is None or not gizmo.active:
            return None
        planes = tuple((n, tuple(c)) for n, c in gizmo.plane_handles())
        active = gizmo.constraint[1] if gizmo.dragging and gizmo.constraint and \
            gizmo.constraint[0] == "plane" else None
        wedge = gizmo.wedge()
        return planes, gizmo.hover_plane, active, wedge

    def sync(self) -> None:
        """Repaint only when what is drawn would change."""
        state = self._state()
        if state == self._drawn:
            return
        self._drawn = state
        if state is None:
            self.hide()
            return
        stage = self.parent()
        if stage is not None:
            self.setGeometry(stage.rect())
        if self.isHidden():
            self.show()
            # Just above the 3D view, below every card: the squares belong to
            # the model, not over a panel.
            controls = getattr(stage, "view_controls", None)
            if controls is not None:
                self.stackUnder(controls)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if not self._drawn:
            return
        planes, hovered, active, wedge = self._drawn
        ratio = self.viewport.devicePixelRatioF() or 1.0
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        for normal, corners in planes:
            colour = QColor(AXIS_COLOURS[normal])
            lit = normal in (hovered, active)
            fill = QColor(colour)
            fill.setAlpha(150 if lit else 64)
            pen = QPen(colour)
            pen.setWidthF(1.6 if lit else 1.0)
            painter.setPen(pen)
            painter.setBrush(fill)
            painter.drawPolygon(QPolygonF([QPointF(x / ratio, y / ratio) for x, y in corners]))
        if wedge is not None:
            self._paint_wedge(painter, wedge, ratio)
        painter.end()

    def _paint_wedge(self, painter: QPainter, wedge, ratio: float) -> None:
        (px, py), start, sweep, angle = wedge
        centre = QPointF(px / ratio, py / ratio)
        radius = WEDGE_RADIUS_PX
        box = QRectF(centre.x() - radius, centre.y() - radius, radius * 2, radius * 2)
        accent = QColor(self._palette.accent)
        fill = QColor(accent)
        fill.setAlpha(56)
        path = QPainterPath(centre)
        path.arcTo(box, start, max(-360.0, min(360.0, sweep)))
        path.closeSubpath()
        pen = QPen(accent)
        pen.setWidthF(1.4)
        painter.setPen(pen)
        painter.setBrush(fill)
        painter.drawPath(path)
        middle = math.radians(start + sweep / 2.0)
        label = QPointF(centre.x() + math.cos(middle) * (radius + 18),
                        centre.y() - math.sin(middle) * (radius + 18))
        font = QFont(METRICS.font_mono.split(",")[0].strip("' "))
        font.setPixelSize(METRICS.font_size_ui)
        font.setBold(True)
        painter.setFont(font)
        text = f"{angle:+.1f}°".replace("+", "") if abs(angle) >= 0.05 else "0°"
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 12
        height = metrics.height() + 6
        tag = QRectF(label.x() - width / 2, label.y() - height / 2, width, height)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self._palette.surface_raised))
        painter.drawRoundedRect(tag, METRICS.radius_sm, METRICS.radius_sm)
        painter.setPen(QColor(self._palette.text))
        painter.drawText(tag, Qt.AlignCenter, text)
