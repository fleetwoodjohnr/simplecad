"""Lightweight face-pattern and clearance guides over the CAD viewport."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget


class TextureOverlay(QWidget):
    def __init__(self, viewport, palette, parent=None) -> None:
        super().__init__(parent)
        self.viewport = viewport
        self.palette = palette
        self.layout = None
        self.clearances = {}
        self._projected = None
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        viewport.view_changed.connect(self._view_changed)

    def _view_changed(self) -> None:
        self._projected = None
        self.update()

    def set_layout(self, layout, clearances=None) -> None:
        self.layout = layout
        self.clearances = dict(clearances or {})
        self._projected = None
        self.update()

    def _project_ring(self, ring, *, limit=256, segmentize=False):
        if ring is None:
            return None
        line = ring.segmentize(2.5) if segmentize else ring
        coords = list(line.coords)
        if len(coords) > limit:
            step = math.ceil((len(coords) - 1) / (limit - 1))
            coords = coords[::step]
            if coords[-1] != line.coords[-1]:
                coords.append(line.coords[-1])
        points = []
        for u, v in coords:
            point = self.viewport.project(self.layout.mapping.point(u, v, 0.025))
            if point is not None:
                points.append(QPointF(*point))
        if len(points) < 2:
            return None
        return QPolygonF(points)

    def _project_lines(self):
        lines = []
        motifs = self.layout.motifs
        stride = max(1, math.ceil(len(motifs) / 240))
        for motif in motifs[::stride][:240]:
            polygon = self._project_ring(motif.exterior, limit=24)
            if polygon is not None:
                lines.append((polygon, self.palette.accent, 1.15, False))
        allowed = self.layout.allowed
        for polygon in allowed.geoms if hasattr(allowed, "geoms") else (allowed,):
            if polygon.geom_type == "Polygon":
                line = self._project_ring(polygon.exterior, segmentize=True)
                if line is not None:
                    lines.append((line, self.palette.grid_axis_y, 1.4, True))
        colors = {
            "hole": self.palette.grid_axis_x,
            "text": self.palette.grid_axis_y,
            "other": self.palette.warning,
        }
        for kind, source, buffered in self.layout.protected:
            for ring, color, width, dashed in (
                (source.exterior, self.palette.text_muted, 0.9, False),
                (buffered.exterior, colors[kind], 1.6, True),
            ):
                line = self._project_ring(ring, segmentize=True)
                if line is not None:
                    lines.append((line, color, width, dashed))
        return lines

    def paintEvent(self, _event) -> None:  # noqa: N802
        if self.layout is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(QFont(painter.font().family(), 9, QFont.DemiBold))
        mapping = self.layout.mapping
        # The exact kernel preview arrives later; these outlines are the
        # immediate, face-aware answer for every field edit.
        if self._projected is None:
            self._projected = self._project_lines()
        painter.setBrush(Qt.NoBrush)
        for line, color, width, dashed in self._projected:
            painter.setPen(QPen(QColor(color), width, Qt.DashLine if dashed else Qt.SolidLine))
            painter.drawPolyline(line)
        shown = set()
        for kind, source, buffered in self.layout.protected:
            if kind in shown:
                continue
            shown.add(kind)
            u, v = buffered.exterior.interpolate(buffered.exterior.length * 0.12).coords[0]
            point = self.viewport.project(mapping.point(u, v, 0.03))
            if point is None:
                continue
            label = f"{kind.title()} {self.clearances.get(kind, 0.0):.2f} mm"
            rect = painter.fontMetrics().boundingRect(label).adjusted(-7, -4, 7, 4)
            rect.moveCenter((QPointF(*point) + QPointF(0, -13)).toPoint())
            painter.setPen(QPen(QColor(self.palette.border), 1))
            painter.setBrush(QColor(self.palette.surface_raised))
            painter.drawRoundedRect(rect, 5, 5)
            painter.setPen(QColor(self.palette.text))
            painter.drawText(rect, Qt.AlignCenter, label)
