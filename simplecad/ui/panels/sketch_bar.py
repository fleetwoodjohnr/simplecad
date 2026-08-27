"""The sketching toolbar and status.

Appears only while a sketch is open. It carries the drawing tools, the live
constraint state, and the two buttons that end the session -- because while
sketching, those are the only things that matter, and everything else would be
in the way.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..icons import icon
from ..theme import METRICS, Palette
from ..widgets.controls import FloatingCard, GhostButton, PrimaryButton, ToolTile

#: (key, icon, label) for each drawing tool.
DRAW_TOOLS = (
    ("line", "extrude", "Line"),
    ("polyline", "sketch", "Polyline"),
    ("rectangle", "box", "Rect"),
    ("center_rectangle", "box", "C-Rect"),
    ("circle", "sphere", "Circle"),
    ("arc", "concentric", "Arc"),
    ("ellipse", "torus", "Ellipse"),
    ("spline", "sketch", "Spline"),
    ("polygon", "polygon", "Polygon"),
    ("slot", "tube", "Slot"),
    ("point", "measure", "Point"),
    ("dimension", "measure", "Dim  D"),
)


class SketchBar(FloatingCard):
    """Drawing tools, live state, and the way out."""

    tool_chosen = Signal(str)
    finished = Signal()
    cancelled = Signal()
    undo_requested = Signal()

    def __init__(self, palette: Palette, parent=None) -> None:
        super().__init__(palette, parent)
        self._palette = palette
        self._tiles: dict[str, ToolTile] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            METRICS.space(2), METRICS.space(2), METRICS.space(2), METRICS.space(2)
        )
        layout.setSpacing(METRICS.space(1.5))

        tools = QWidget()
        row = QHBoxLayout(tools)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(METRICS.space(0.5))
        for key, icon_name, label in DRAW_TOOLS:
            tile = ToolTile(icon_name, label, palette)
            tile.clicked.connect(lambda _=False, k=key: self.choose(k))
            row.addWidget(tile)
            self._tiles[key] = tile
        layout.addWidget(tools)

        bottom = QWidget()
        bottom_row = QHBoxLayout(bottom)
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(METRICS.space(2))

        self.state = QLabel("")
        self.state.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        bottom_row.addWidget(self.state, 1)

        undo = GhostButton("Undo last")
        undo.clicked.connect(self.undo_requested.emit)
        bottom_row.addWidget(undo)

        cancel = GhostButton("Discard")
        cancel.clicked.connect(self.cancelled.emit)
        bottom_row.addWidget(cancel)

        done = PrimaryButton("Finish sketch")
        done.clicked.connect(self.finished.emit)
        bottom_row.addWidget(done)
        layout.addWidget(bottom)

        self.choose("line")
        self.apply_palette(palette)

    def choose(self, key: str) -> None:
        for name, tile in self._tiles.items():
            tile.setChecked(name == key)
        self.tool_chosen.emit(key)

    def apply_palette(self, palette: Palette) -> None:
        super().apply_palette(palette)
        self._palette = palette
        for tile in self._tiles.values():
            tile.apply_palette(palette)

    def show_state(self, result, hint: str = "") -> None:
        """Report the solver's verdict, coloured by how much it matters."""
        from ...sketch.solver import SketchState

        if result is None:
            self.state.setText(hint)
            self.state.setStyleSheet(f"color:{self._palette.text_muted};")
            return
        colour = {
            SketchState.FULLY: self._palette.success,
            SketchState.CONFLICTING: self._palette.danger,
            SketchState.OVER: self._palette.warning,
        }.get(result.state, self._palette.text_muted)
        text = result.state.label()
        if hint:
            text = f"{text}  ·  {hint}"
        self.state.setText(text)
        self.state.setStyleSheet(f"color:{colour}; font-size:12.5px; font-weight:600;")
