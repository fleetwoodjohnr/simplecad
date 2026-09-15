"""The live dimension shown while a face is dragged, nudged, or typed.

Dragging a face used to report only a delta, in the hint at the far corner of
the window -- a long way from where the user is looking, and the wrong quantity.
What you are steering toward when you push a wall in is the size the part ends
up, not how far you have moved so far. So this shows both: the resulting extent
large, the delta underneath. Mouse drags follow the cursor; keyboard and typed
changes stay beside the selected face.

A Qt widget rather than an OCCT annotation, for the same reason the dimension
labels are: this OCP build does not wrap ``PrsDim``.

Typing an exact number *during* a drag is deliberately not offered here. It
would mean holding a mouse button down while typing, and the two ways of hitting
an exact value that already exist are better ones: the drag snaps to 0.25 mm, and
the Pull panel takes a number or an expression outright.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..theme import METRICS, Palette

#: How far from the cursor the readout sits, in px. Far enough not to be under
#: the pointer, close enough to read without looking away.
OFFSET = QPoint(18, 18)


class DragReadout(QWidget):
    """Resulting size on top, delta below."""

    def __init__(self, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        self._palette = palette
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            METRICS.space(2), METRICS.space(1.5), METRICS.space(2), METRICS.space(1.5)
        )
        layout.setSpacing(1)

        self.size_label = QLabel("")
        self.size_label.setAlignment(Qt.AlignCenter)
        self.delta_label = QLabel("")
        self.delta_label.setAlignment(Qt.AlignCenter)

        layout.addWidget(self.size_label)
        layout.addWidget(self.delta_label)
        self.apply_palette(palette)
        self.hide()

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.setStyleSheet(
            f"""
            QWidget {{
                background: {palette.surface_raised};
                border: 1px solid {palette.border};
                border-radius: {METRICS.radius_sm}px;
            }}
            """
        )
        self.size_label.setStyleSheet(
            f"border:none; background:transparent; color:{palette.text}; "
            f"font-family:{METRICS.font_mono}; font-size:15px; font-weight:700;"
        )

    # ------------------------------------------------------------------
    def show_text(
        self, at: QPoint, headline: str, sub: str = "", tone: str | None = None
    ) -> None:
        """Place the readout at *at* showing arbitrary text.

        The general form. Push/Pull came first and hard-wired its own wording
        into the widget, but a fillet radius and a split position want exactly
        the same thing -- a number at the cursor with a smaller qualifier under
        it -- so the widget takes the strings and the caller decides what they
        mean. *tone* colours the sub-line: accent for adding, danger for cutting
        or for a value the kernel has refused.
        """
        accent = tone or self._palette.accent
        self.delta_label.setStyleSheet(
            f"border:none; background:transparent; color:{accent}; "
            f"font-family:{METRICS.font_mono}; font-size:12px; font-weight:600;"
        )
        self.size_label.setText(headline)
        self.delta_label.setText(sub)
        self.delta_label.setVisible(bool(sub))
        self.adjustSize()
        position = at + OFFSET
        parent = self.parentWidget()
        if parent is not None:
            # Keyboard-driven tools anchor this label to geometry rather than
            # to a moving pointer. Keep that projected point useful even when
            # the face is close to an edge of the stage.
            padding = METRICS.space(2)
            position.setX(max(
                padding,
                min(position.x(), parent.width() - self.width() - padding),
            ))
            position.setY(max(
                padding,
                min(position.y(), parent.height() - self.height() - padding),
            ))
        self.move(position)
        # Raised on the way in only. This runs on every motion event of a drag,
        # and ``raise_`` over the GL viewport re-composites it and re-blurs the
        # drop shadow -- per frame, for a widget that is already on top.
        if not self.isVisible():
            self.show()
            self.raise_()

    def show_drag(
        self,
        at: QPoint,
        distance: float,
        resulting: float | None,
        label: str,
        material_change: float | None = None,
    ) -> None:
        """Update and place the readout. *resulting* may be None if unknown.

        ``distance`` is the signed travel of the surface, which is the useful
        delta to display. ``material_change`` may have the opposite sign for
        the inside of a hole: moving that surface outward makes the hole larger
        and therefore removes material.
        """
        cutting = (distance if material_change is None else material_change) < 0
        tone = self._palette.danger if cutting else self._palette.accent
        if resulting is None:
            headline = f"{abs(distance):.2f} mm"
            sub = "Cutting" if cutting else "Adding"
        else:
            headline = f"{label} {resulting:.2f} mm"
            sign = "−" if distance < 0 else "+"
            sub = f"{sign}{abs(distance):.2f} mm"
        self.show_text(at, headline, sub, tone)

    def finish(self) -> None:
        self.hide()
