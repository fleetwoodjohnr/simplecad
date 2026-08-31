"""The offer to restore work from a session that did not exit cleanly.

This used to be a ``QMessageBox.question``, and that was the wrong shape for it
in a way that cost a user most of an evening. A message box is *application
modal*: while it is up the main window accepts no clicks, no menu, no tool. It
was also raised 400 ms after startup, before the window had settled, which under
XWayland is exactly when a dialog can end up positioned behind the window it is
modal for. The result is an application that draws perfectly, runs a healthy
event loop, and ignores everything the user does -- indistinguishable, from the
outside, from a hang. Worse, the usual response is to force-quit, which leaves
another recovery file, which shows the dialog again on the next launch.

So the offer floats over the stage like every other panel here: it cannot take
focus away from the model, it cannot hide behind the window, and ignoring it
costs nothing.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton

from ..theme import METRICS, Palette
from ..widgets.controls import FloatingCard


class RecoveryBar(FloatingCard):
    """"Recover the work from last time?" -- offered, never insisted on."""

    #: Restore the recovered document.
    recover_requested = Signal()
    #: Throw the leftovers away.
    discard_requested = Signal()

    def __init__(self, palette: Palette, age: str, parent=None) -> None:
        super().__init__(palette, parent)
        self._age = age

        row = QHBoxLayout(self)
        row.setContentsMargins(
            METRICS.space(4), METRICS.space(2.5),
            METRICS.space(2.5), METRICS.space(2.5),
        )
        row.setSpacing(METRICS.space(2))

        self.message = QLabel(
            f"SimpleCAD closed unexpectedly. There is unsaved work from {age}."
        )
        row.addWidget(self.message)

        self.recover = QPushButton("Recover")
        self.recover.setProperty("variant", "primary")
        self.recover.setCursor(Qt.PointingHandCursor)
        self.recover.clicked.connect(self.recover_requested.emit)
        row.addWidget(self.recover)

        self.discard = QPushButton("Discard")
        self.discard.setCursor(Qt.PointingHandCursor)
        self.discard.clicked.connect(self.discard_requested.emit)
        row.addWidget(self.discard)

        self.apply_palette(palette)

    def apply_palette(self, palette: Palette) -> None:
        self._apply_card_style(palette)
        # The buttons pick their look up from the application stylesheet, which
        # already styles QPushButton and the "primary" variant. Only the label
        # needs saying, because it is the one thing here with no rule of its own.
        self.message.setStyleSheet(f"color: {palette.text}; background: transparent;")
