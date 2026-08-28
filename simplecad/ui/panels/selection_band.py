"""The marquee: the rectangle you drag to pick out several things at once.

Drawn as a Qt widget over the viewport rather than into it. OCCT owns the GL
context, and painting into a ``QOpenGLWidget`` behind its back is the one thing
this viewport must not do -- the measurement overlay solved the same problem the
same way, and this rides on that.

Deliberately a *filled* rectangle and not just an outline. An outline alone
reads as a crop marker; a translucent wash reads as "everything under here",
which is what the gesture means.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..theme import METRICS, Palette

#: How solid the wash is. Enough to read the rectangle's extent against a busy
#: model, faint enough to see what is being caught inside it.
FILL_ALPHA = 42
#: The dashes that say "this is a gesture in progress", not a drawn shape.
DASH = (4.0, 3.0)


class SelectionBand(QWidget):
    """Draws the drag rectangle. Owns no state beyond the rectangle itself."""

    def __init__(self, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        self._palette = palette
        # Purely a display: every event has to reach the viewport underneath.
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.rect_: QRect | None = None
        self.hide()

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.update()

    def show_rect(self, rect) -> None:
        """Show *rect*, or hide the band when it is None."""
        self.rect_ = rect
        if rect is None:
            self.hide()
            return
        if self.parent() is not None:
            self.setGeometry(self.parent().rect())
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self.rect_ is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        colour = QColor(self._palette.accent)
        fill = QColor(colour)
        fill.setAlpha(FILL_ALPHA)
        pen = QPen(colour)
        pen.setWidthF(1.4)
        pen.setStyle(Qt.CustomDashLine)
        pen.setDashPattern(list(DASH))
        painter.setPen(pen)
        painter.setBrush(fill)
        painter.drawRoundedRect(self.rect_, METRICS.radius_sm, METRICS.radius_sm)
        painter.end()
