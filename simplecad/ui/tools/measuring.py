"""The Measure tool.

Two ways of asking, because there are two questions.

**Entities** is the original: select things and it tells you about them. There
is no sub-mode to choose within it -- picking two holes reports the centre
distance because that is what two holes means, and picking one reports its
diameter.

**Point to point** is for the question entities cannot answer: how far is *this
corner* from *that hole centre*. The cursor snaps to the places that mean
something -- corners, edge midpoints, arc and hole centres, face centres --
because measuring between two arbitrary points on a surface is almost never
what anyone means.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ...kernel.measure import Measurement, Reading, measure, minimum_distance
from ..theme import METRICS, Palette
from ..widgets.controls import GhostButton
from .base import ToolPanel
from .registry import register_tool


def _fmt(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".") + " mm"


def _point_measurement(first, second) -> Measurement:
    """Two snap points, read out as a distance and its three components."""
    import math

    a, b = first.position, second.position
    dx, dy, dz = (b[i] - a[i] for i in range(3))
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    return Measurement(
        subject=f"{first.label} to {second.label}",
        readings=[
            Reading("Distance", distance, _fmt(distance), primary=True),
            Reading("ΔX", dx, _fmt(abs(dx))),
            Reading("ΔY", dy, _fmt(abs(dy))),
            Reading("ΔZ", dz, _fmt(abs(dz))),
        ],
    )


class ReadingRow(QWidget):
    """One measured quantity: label on the left, value on the right."""

    def __init__(self, reading, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, METRICS.space(0.5), 0, METRICS.space(0.5))
        layout.setSpacing(METRICS.space(2))

        name = QLabel(reading.label)
        name.setStyleSheet(
            f"color:{palette.text_muted}; "
            f"font-size:{'13' if reading.primary else '12'}px;"
        )
        value = QLabel(reading.text)
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        value.setStyleSheet(
            f"color:{palette.text}; font-family:{METRICS.font_mono};"
            f"font-size:{'14' if reading.primary else '12.5'}px;"
            f"font-weight:{'700' if reading.primary else '500'};"
        )
        layout.addWidget(name)
        layout.addStretch(1)
        layout.addWidget(value)


@register_tool("measure")
class MeasurePanel(ToolPanel):
    title = "Measure"
    confirm_label = "Done"
    width = 320

    #: Which mode Measure opens in, remembered across uses.
    #:
    #: Point-to-point used to be reachable only by opening Measure and then
    #: noticing a toggle, which is a poor place for the answer to "how far is
    #: this corner from that hole". It now has its own command, and having used
    #: it once you get it back without hunting for the toggle again.
    last_mode = "entities"

    def build(self) -> None:
        self.selection = self.window_.selection
        self.points: list = []
        self._mode = "entities"
        self._closing = False
        self.measurement = Measurement()

        self.set_subtitle(
            "Select geometry to measure. Pick two things for a distance or an "
            "angle."
        )

        modes = QHBoxLayout()
        modes.setSpacing(METRICS.space(1))
        self._mode_buttons = {}
        for key, label in (("entities", "Entities"), ("points", "Point to point")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(METRICS.control_height)
            button.clicked.connect(
                lambda _=False, k=key: self.set_mode(k, defer=True)
            )
            modes.addWidget(button, 1)
            self._mode_buttons[key] = button
        host = QWidget()
        host.setLayout(modes)
        self.add_widget(host)

        self.readings_host = QWidget()
        self.readings_layout = QVBoxLayout(self.readings_host)
        self.readings_layout.setContentsMargins(0, 0, 0, 0)
        self.readings_layout.setSpacing(0)
        self.add_widget(self.readings_host)

        copy = GhostButton("Copy to clipboard")
        copy.clicked.connect(self.copy)
        self.add_widget(copy)

        clear = GhostButton("Clear points")
        clear.clicked.connect(self.clear_points)
        clear.hide()
        self.add_widget(clear)
        self._clear_button = clear

        # Keep measuring as the user picks: the panel is a live readout, not a
        # snapshot taken when it opened.
        viewport = self.window_.stage.viewport
        viewport.selection_changed.connect(self._on_selection_changed)
        viewport.snap_hovered.connect(self._on_snap_hover)
        viewport.snap_picked.connect(self._on_snap_picked)
        viewport.view_changed.connect(self._reposition_overlay)
        self.set_mode(self.start_mode())

    def start_mode(self) -> str:
        """Which mode to open in. Overridden by the point-to-point command."""
        return type(self).last_mode

    # -- modes -----------------------------------------------------------
    def set_mode(self, mode: str, defer: bool = False) -> None:
        """Switch between the two ways of asking.

        *defer* holds the viewport half back by one turn of the event loop. Qt
        cannot repaint a button until the slot that checked it returns, so a
        click on the toggle that goes straight on to clear the selection, empty
        the context bar and re-lay the overlays leaves "Entities" still looking
        selected until all of that is done -- and while the window was busy that
        read as the toggle being dead. Opening the tool does *not* defer: there
        the panel is not on screen yet, and callers reasonably expect point mode
        to be live the moment the tool is up.
        """
        previous = getattr(self, "_mode", None)
        self._mode = mode
        MeasurePanel.last_mode = mode
        for key, button in self._mode_buttons.items():
            button.setChecked(key == mode)
        self._clear_button.setVisible(mode == "points")

        if mode == "points":
            self.clear_points()
            self.set_subtitle(
                "Click two points. The cursor snaps to corners, midpoints, "
                "centres, edges and faces."
            )
            self.window_.set_hint("Click the first point.")

        if defer:
            QTimer.singleShot(0, lambda: self._enter_mode(mode, previous))
        else:
            self._enter_mode(mode, previous)
        self.refresh()

    def _enter_mode(self, mode: str, previous) -> None:
        """The viewport half of a mode switch."""
        if self._closing or self._mode != mode:
            return
        viewport = self.window_.stage.viewport
        overlay = self.window_.stage.measure_overlay

        if mode == "points":
            viewport.clear_selection()
            viewport.clear_snap_cache()
            viewport.begin_point_pick()
            overlay.attach(viewport.project)
            overlay.setGeometry(self.window_.stage.rect())
            overlay.show()
            overlay.raise_()
        else:
            viewport.end_point_pick()
            overlay.clear()
            overlay.hide()
            # Only re-read on the way *back* from point mode, which cleared the
            # viewport selection. Doing it when the panel first opens would
            # discard a selection the caller set up before opening it.
            if previous == "points":
                self.selection.refresh()
        self.refresh()

    def clear_points(self) -> None:
        self.points = []
        overlay = self.window_.stage.measure_overlay
        overlay.picked = []
        overlay.reading = ""
        overlay.update()
        self.refresh()

    def _reposition_overlay(self) -> None:
        overlay = self.window_.stage.measure_overlay
        if overlay.isVisible():
            overlay.setGeometry(self.window_.stage.rect())
            overlay.update()

    def _on_snap_hover(self, snap) -> None:
        """Show where the cursor has landed. Nothing here may be expensive.

        In particular this does *not* write the hint line. ``set_hint`` now
        ignores a repeat of what the line already says, and ``_layout_overlays``
        no longer re-raises every floating panel unless the stacking actually
        changed -- between them the per-move cost is gone -- but a snap hover
        reports a *different* place on every motion event, so this is the one
        caller the repeat guard could never help.

        Nothing is lost by it. The running length is drawn on the rubber band
        by the overlay, and which *kind* of place the cursor has found is drawn
        inside the indicator -- a square for a corner, a diamond for a
        midpoint -- which is what the glyphs are for.
        """
        if self._mode != "points":
            return
        overlay = self.window_.stage.measure_overlay
        overlay.hover = snap
        overlay.update()

    def _on_snap_picked(self, snap) -> None:
        if self._mode != "points" or snap is None:
            return
        # A third click starts a fresh measurement rather than doing nothing.
        if len(self.points) >= 2:
            self.points = []
        self.points.append(snap)
        overlay = self.window_.stage.measure_overlay
        overlay.picked = list(self.points)
        overlay.update()
        self.refresh()
        # Written on a click, not on a move: once per gesture is affordable and
        # is when the user is actually looking for confirmation.
        self.window_.set_hint(
            f"First point on a {snap.label}. Click the second."
            if len(self.points) == 1
            else f"{self.measurement.headline.text}."
            if self.measurement.headline is not None
            else "Click to start a new measurement."
        )

    def _on_selection_changed(self) -> None:
        """Re-read the viewport, then measure. Deciding *when* to re-read is
        the signal's job; ``refresh`` only measures what is already selected."""
        self.selection.refresh()
        self.refresh()

    def refresh(self) -> None:
        if self._mode == "points":
            self.measurement = (
                _point_measurement(*self.points[:2]) if len(self.points) == 2
                else Measurement()
            )
            overlay = self.window_.stage.measure_overlay
            headline = self.measurement.headline
            overlay.reading = headline.text if headline is not None else ""
            overlay.update()
            self.warn("")
        else:
            picks = [(pick.kind, pick.shape) for pick in self.selection.picks]
            try:
                self.measurement = measure(picks)
            except Exception as exc:  # noqa: BLE001 - must not break the app
                from ...core.errors import translate

                self.warn(str(translate(exc, "measure")))
                return
            self.warn("")

        while self.readings_layout.count():
            item = self.readings_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if self.measurement.is_empty():
            empty = QLabel(
                f"Click {'two points' if not self.points else 'one more point'} "
                "on the model." if self._mode == "points"
                else "Select an edge, a face or a body."
            )
            empty.setStyleSheet(
                f"color:{self._palette.text_faint}; font-size:12px; "
                f"padding:{METRICS.space(3)}px 0;"
            )
            self.readings_layout.addWidget(empty)
            self.readings_host.setMinimumHeight(48)
        else:
            height = 0
            for reading in self.measurement.readings:
                row = ReadingRow(reading, self._palette, self.readings_host)
                row.adjustSize()
                height += row.sizeHint().height()
                self.readings_layout.addWidget(row)
            self.readings_host.setMinimumHeight(height)

        if not self.measurement.is_empty():
            self.set_subtitle(self.measurement.subject)
        elif self._mode == "points":
            self.set_subtitle(
                "Click two points. The cursor snaps to corners, midpoints, "
                "centres, edges and faces."
            )
        else:
            self.set_subtitle("Select geometry to measure.")
        self.adjustSize()
        self.window_.stage._layout_overlays()

        headline = self.measurement.headline
        if headline is not None:
            self.window_.set_hint(f"{headline.label}: {headline.text}")

    def copy(self) -> None:
        from PySide6.QtWidgets import QApplication

        if self.measurement.is_empty():
            return
        text = "\n".join(
            f"{r.label}: {r.text}" for r in self.measurement.readings
        )
        QApplication.clipboard().setText(text)
        self.window_.set_hint("Measurements copied.")

    def commit(self) -> None:
        self.window_.cancel_tool()

    def teardown(self) -> None:
        self._closing = True
        viewport = self.window_.stage.viewport
        viewport.end_point_pick()
        viewport.clear_snap_cache()
        overlay = self.window_.stage.measure_overlay
        overlay.clear()
        overlay.hide()
        for signal, slot in (
            (viewport.selection_changed, self._on_selection_changed),
            (viewport.snap_hovered, self._on_snap_hover),
            (viewport.snap_picked, self._on_snap_picked),
            (viewport.view_changed, self._reposition_overlay),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass


class PointMeasurePanel(MeasurePanel):
    """Measure, opened straight into point-to-point."""

    def start_mode(self) -> str:
        return "points"


@register_tool("measure_points")
def open_point_measure(window):
    """Two points on a model is a question of its own, so it gets its own way in.

    Registered as a factory rather than a second decorator on the class,
    because the registry hands a tool only the window -- the mode has to be
    bound here or this would open showing Entities, which is the very thing
    that made point-to-point hard to find.
    """
    return PointMeasurePanel(window, window.palette_)
