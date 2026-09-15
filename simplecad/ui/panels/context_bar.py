"""The contextual action bar.

Appears over the viewport only when something is selected, and offers only the
operations that make sense for that selection. This is what replaces the wall of
always-visible toolbar buttons: the user selects geometry, and the things they
might plausibly want are right there.

Two things about how it rebuilds, both of them fixes for the bar arriving
half-formed.

**Old buttons go synchronously.** ``deleteLater`` does not run until the event
loop turns, so a layout measured immediately afterwards still contains every
button of the *previous* selection. The bar was sized and positioned against
that stale layout and then jumped a frame later, which is what read as the
toolbar loading in pieces. ``setParent(None)`` removes them there and then.

**Nothing is silently dropped.** The bar used to keep the first four matches and
discard the rest, so selecting a body offered a fraction of what applied to it
with no indication that anything was missing. Now every applicable action is
built; as many as fit are shown, and the remainder go behind a trailing "..."
which is visibly there.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QToolButton,
)

from ..icons import icon
from ..theme import METRICS, Palette
from ..widgets.controls import FloatingCard

#: Never show fewer than this, however cramped the window is -- past this point
#: the bar has stopped being a shortcut and hiding more does not help.
MIN_VISIBLE = 2
#: Width to reserve for the overflow button when there is going to be one.
OVERFLOW_WIDTH = 40


class ActionButton(QPushButton):
    """A pill with an icon and a label, sized to be easy to hit."""

    def __init__(self, key: str, label: str, icon_name: str, palette: Palette, primary: bool):
        super().__init__(label)
        self.key = key
        self._icon_name = icon_name
        self._primary = primary
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(METRICS.control_height_lg)
        self.apply_palette(palette)

    def apply_palette(self, palette: Palette) -> None:
        colour = palette.text_on_accent if self._primary else palette.text
        self.setIcon(icon(self._icon_name, colour, 18))
        background = palette.accent if self._primary else "transparent"
        hover = palette.accent_hover if self._primary else palette.surface_sunken
        border = palette.accent if self._primary else palette.border
        self.setStyleSheet(
            f"""
            QPushButton {{
                background:{background}; color:{colour};
                border:1px solid {border};
                border-radius:{METRICS.control_height_lg // 2}px;
                padding:0 {METRICS.space(3.5)}px;
                font-weight:{600 if self._primary else 500};
            }}
            QPushButton:hover {{ background:{hover}; }}
            """
        )


class ContextBar(FloatingCard):
    """Floating bar of actions for the current selection."""

    action_triggered = Signal(str)

    def __init__(self, palette: Palette, parent=None) -> None:
        super().__init__(palette, parent)
        self._palette = palette
        self._buttons: list[ActionButton] = []
        self._actions: list[tuple[str, str, str]] = []
        self._overflow_actions: list[tuple[str, str, str]] = []

        self.layout_ = QHBoxLayout(self)
        self.layout_.setContentsMargins(
            METRICS.space(2), METRICS.space(2), METRICS.space(2), METRICS.space(2)
        )
        self.layout_.setSpacing(METRICS.space(1.5))

        self.summary = QLabel("")
        self.layout_.addWidget(self.summary)
        self.separator = QFrame()
        self.separator.setFrameShape(QFrame.VLine)
        self.separator.setFixedWidth(1)
        self.layout_.addWidget(self.separator)

        self.overflow = QToolButton()
        self.overflow.setText("⋯")
        self.overflow.setCursor(Qt.PointingHandCursor)
        self.overflow.setToolTip("More actions")
        self.overflow.setFixedHeight(METRICS.control_height_lg)
        self.overflow.clicked.connect(self._show_overflow)
        self.layout_.addWidget(self.overflow)
        self.overflow.hide()

        self.apply_palette(palette)
        self.hide()

    def apply_palette(self, palette: Palette) -> None:
        super().apply_palette(palette)
        self._palette = palette
        self.summary.setStyleSheet(
            f"color:{palette.text_muted}; font-size:12.5px;"
            f"background:{palette.surface_sunken}; border:1px solid {palette.border};"
            f"border-radius:{METRICS.radius_sm}px; padding:6px {METRICS.space(2)}px;"
        )
        self.separator.setStyleSheet(f"background:{palette.border}; border:none;")
        self.overflow.setStyleSheet(
            f"""
            QToolButton {{
                background:transparent; color:{palette.text};
                border:1px solid {palette.border};
                border-radius:{METRICS.control_height_lg // 2}px;
                padding:0 {METRICS.space(2)}px; font-size:15px;
            }}
            QToolButton:hover {{ background:{palette.surface_sunken}; }}
            """
        )
        for button in self._buttons:
            button.apply_palette(palette)

    # ------------------------------------------------------------------
    def keys(self) -> list[str]:
        """Every action currently offered, shown or in the overflow.

        Read from the action list rather than assembled from the buttons and
        the overflow: an overflowed button is hidden, not destroyed, so adding
        the two together counts it twice.
        """
        return [key for key, _label, _icon in self._actions]

    def visible_keys(self) -> list[str]:
        return [b.key for b in self._buttons if not b.isHidden()]

    def overflow_keys(self) -> list[str]:
        return [key for key, _label, _icon in self._overflow_actions]

    def _discard_buttons(self) -> None:
        """Remove the previous selection's buttons, now rather than later."""
        for button in self._buttons:
            self.layout_.removeWidget(button)
            # Not deleteLater alone: it leaves the widget parented and sized
            # into this layout until the event loop turns, so anything that
            # measures the bar before then measures the *old* selection.
            button.setParent(None)
            button.deleteLater()
        self._buttons = []
        self._actions = []
        self._overflow_actions = []

    def show_actions(
        self,
        summary: str,
        actions: list[tuple[str, str, str]],
        max_width: int | None = None,
    ) -> None:
        """Rebuild the bar for a new selection. Hides itself when empty."""
        self._discard_buttons()
        if not actions:
            self.overflow.hide()
            self.hide()
            return

        self._actions = list(actions)
        self.summary.setText(summary)
        self.summary.adjustSize()

        for index, (key, label, icon_name) in enumerate(actions):
            button = ActionButton(key, label, icon_name, self._palette, index == 0)
            button.clicked.connect(
                lambda _=False, k=key: self.action_triggered.emit(k)
            )
            # Inserted before the overflow button, which always sits last.
            self.layout_.insertWidget(self.layout_.count() - 1, button)
            # Shown explicitly. A widget added to the layout of an *already
            # visible* parent stays hidden until the layout next runs, and a
            # hidden widget contributes nothing to the layout's size hint -- so
            # measuring here without this reports a bar just wide enough for the
            # summary, and every button gets squeezed to a sliver to fit it.
            button.show()
            self._buttons.append(button)

        self._apply_overflow(actions, max_width)
        self._resize_to_fit()
        # Only on the way in: see DragReadout for the same reasoning. The bar is
        # rebuilt on every selection change, and re-raising something already on
        # top costs a full viewport re-composite for nothing.
        if not self.isVisible():
            self.show()
            self.raise_()

    def _resize_to_fit(self) -> None:
        """Size the bar to its contents, now rather than a frame later.

        ``adjustSize()`` alone is not enough and the failure is invisible until
        you look at it: adding and hiding child widgets only *posts* a layout
        request, so the cached size hint ``adjustSize`` reads is still the
        previous selection's. The bar gets the wrong width, the layout squeezes
        every button down to a sliver to fit it, and it stays that way -- there
        is no second pass, because nothing else resizes a manually-placed
        overlay. Invalidating the cache first makes the measurement the real one.
        """
        self.layout_.invalidate()
        self.layout_.activate()
        self.resize(self.sizeHint())

    def _fits(self) -> bool:
        """Whether the bar is currently at its natural width. For tests."""
        return self.width() >= self.sizeHint().width()

    def _apply_overflow(self, actions, max_width: int | None) -> None:
        """Hide the buttons that will not fit, and offer them from a menu."""
        self.overflow.hide()
        self._overflow_actions = []
        if max_width is None:
            return

        margins = self.layout_.contentsMargins()
        spacing = self.layout_.spacing()
        used = (
            margins.left() + margins.right()
            + self.summary.sizeHint().width()
            + self.separator.width() + spacing * 2
        )
        widths = [button.sizeHint().width() for button in self._buttons]

        # Work out how many fit, leaving room for the overflow button itself
        # whenever there is going to be one.
        keep = len(self._buttons)
        while keep > MIN_VISIBLE:
            reserve = OVERFLOW_WIDTH + spacing if keep < len(self._buttons) else 0
            total = used + sum(widths[:keep]) + spacing * keep + reserve
            if total <= max_width:
                break
            keep -= 1

        if keep >= len(self._buttons):
            return
        for button in self._buttons[keep:]:
            button.hide()
        self._overflow_actions = list(actions[keep:])
        self.overflow.show()

    def _show_overflow(self) -> None:
        menu = QMenu(self)
        for key, label, icon_name in self._overflow_actions:
            action = menu.addAction(
                icon(icon_name, self._palette.text, 18), label
            )
            action.triggered.connect(
                lambda _=False, k=key: self.action_triggered.emit(k)
            )
        menu.exec(self.overflow.mapToGlobal(self.overflow.rect().topLeft()))
