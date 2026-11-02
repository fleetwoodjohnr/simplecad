"""Properties: where the selected object is, how big it is, how it is turned.

Sits under the Bodies tree. Every number is the live model's, read from its
bounding box, and every edit is an ordinary history step: a position becomes a
Move, a size becomes a Scale about the reference point. So the card is a view
onto the same features the gizmo makes, not a second source of truth.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout,
)

from ..theme import METRICS, Palette
from ..widgets.controls import GhostButton, IconButton, SectionLabel, ValueField

#: (key, label, tooltip) for the point that Position reports and Size keeps.
REFERENCES = (
    ("bottom", "Bottom", "Centre of the bottom face: Z 0 sits on the plate"),
    ("centre", "Centre", "Centre of the bounding box"),
    ("min", "Corner", "The lowest X, Y and Z corner"),
)

AXIS_COLOURS = ("#E5484D", "#46A758", "#3E8EF7")


def reference_point(low, high, reference: str) -> tuple[float, float, float]:
    """The point *reference* names on the box ``low``..``high``."""
    centre = tuple((a + b) / 2.0 for a, b in zip(low, high))
    if reference == "min":
        return tuple(low)
    if reference == "bottom":
        return (centre[0], centre[1], low[2])
    return centre


def euler_xyz(transform) -> tuple[float, float, float]:
    """Degrees about X, then Y, then Z that make up a rotation ``gp_Trsf``."""
    import math

    m = transform.VectorialPart()
    r = [[m.Value(i, j) for j in (1, 2, 3)] for i in (1, 2, 3)]
    ry = math.asin(max(-1.0, min(1.0, -r[2][0])))
    if abs(math.cos(ry)) > 1e-9:
        rx = math.atan2(r[2][1], r[2][2])
        rz = math.atan2(r[1][0], r[0][0])
    else:  # gimbal lock: fold everything into X
        rx = math.atan2(-r[1][2], r[1][1])
        rz = 0.0
    clean = [round(math.degrees(a), 6) + 0.0 for a in (rx, ry, rz)]
    return tuple(0.0 if abs(a) < 1e-6 else a for a in clean)


class PropertiesCard(QFrame):
    """Position, size and rotation of the selection, editable in place."""

    #: (axis, new coordinate of the reference point)
    move_requested = Signal(int, float)
    #: (axis, new size, keep proportions)
    size_requested = Signal(int, float, bool)
    rotate_requested = Signal()

    def __init__(self, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("PropertiesCard")
        self._palette = palette
        self._low = (0.0, 0.0, 0.0)
        self._high = (0.0, 0.0, 0.0)
        self._filling = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, METRICS.space(2), 0, 0)
        root.setSpacing(METRICS.space(1.5))

        header = QHBoxLayout()
        self.heading = SectionLabel("Properties", palette)
        self.title = QLabel("")
        self.title.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        header.addWidget(self.heading)
        header.addStretch(1)
        header.addWidget(self.title)
        root.addLayout(header)

        grid = QGridLayout()
        grid.setHorizontalSpacing(METRICS.space(1.5))
        grid.setVerticalSpacing(METRICS.space(1))
        self.reference = QComboBox()
        for index, (key, label, tip) in enumerate(REFERENCES):
            self.reference.addItem(label, key)
            self.reference.setItemData(index, tip, Qt.ToolTipRole)
        self.reference.setToolTip("Which point Position reports, and which point Size keeps still")
        self.reference.currentIndexChanged.connect(lambda _i: self._fill())
        grid.addWidget(self.reference, 0, 0, 1, 2)
        size_header = QHBoxLayout()
        size_header.setContentsMargins(0, 0, 0, 0)
        self.size_label = QLabel("Size")
        self.proportional = IconButton("lock", palette, "Keep proportions", size=26, checkable=True)
        size_header.addWidget(self.size_label)
        size_header.addStretch(1)
        size_header.addWidget(self.proportional)
        grid.addLayout(size_header, 0, 2, 1, 2)

        self.position: list[ValueField] = []
        self.size: list[ValueField] = []
        self._axis_labels: list[QLabel] = []
        for axis, (pos_name, size_name) in enumerate(zip("XYZ", "WDH")):
            for column, (name, store, signal) in enumerate((
                (pos_name, self.position, self._moved),
                (size_name, self.size, self._resized),
            )):
                label = QLabel(name)
                label.setFixedWidth(12)
                self._axis_labels.append(label)
                field = ValueField(palette)
                field.setMinimumWidth(0)
                field.committed.connect(lambda value, a=axis, handler=signal: handler(a, value))
                store.append(field)
                grid.addWidget(label, axis + 1, column * 2)
                grid.addWidget(field, axis + 1, column * 2 + 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        root.addLayout(grid)

        rotation = QHBoxLayout()
        self.rotation = QLabel("")
        self.rotate_button = GhostButton("Rotate…")
        self.rotate_button.clicked.connect(self.rotate_requested.emit)
        rotation.addWidget(self.rotation, 1)
        rotation.addWidget(self.rotate_button)
        root.addLayout(rotation)
        self.apply_palette(palette)
        self.hide()

    # ------------------------------------------------------------------
    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.heading.apply_palette(palette)
        self.proportional.apply_palette(palette)
        self.setStyleSheet(
            f"QFrame#PropertiesCard {{ border:none; border-top:1px solid {palette.border}; }}"
        )
        muted = f"color:{palette.text_muted}; font-size:{METRICS.font_size_ui}px;"
        self.title.setStyleSheet(muted + "font-weight:600;")
        self.size_label.setStyleSheet(muted)
        self.rotation.setStyleSheet(
            f"color:{palette.text_muted}; font-family:{METRICS.font_mono};"
            f"font-size:{METRICS.font_size_sm}px;"
        )
        for index, label in enumerate(self._axis_labels):
            colour = AXIS_COLOURS[(index // 2) % 3] if index % 2 == 0 else palette.text_faint
            label.setStyleSheet(f"color:{colour}; font-weight:700; font-size:{METRICS.font_size_ui}px;")

    def reference_key(self) -> str:
        return self.reference.currentData() or "bottom"

    def show_for(self, title: str | None, low=None, high=None, rotation=None,
                 locked: bool = False) -> None:
        """Show the numbers for one object or several; hide when *title* is None."""
        if title is None:
            self.hide()
            return
        self._low, self._high = tuple(low), tuple(high)
        self.title.setText(title)
        self.title.setToolTip(title)
        for field in self.position + self.size:
            field.setReadOnly(locked)
        self.rotate_button.setEnabled(not locked)
        if rotation is None:
            self.rotation.setText("Rotation: several objects")
        else:
            self.rotation.setText("Rotation  " + "  ".join(
                f"{axis} {value:g}°" for axis, value in zip("XYZ", rotation)))
        self._fill()
        self.show()

    def _fill(self) -> None:
        point = reference_point(self._low, self._high, self.reference_key())
        self._filling = True
        try:
            for axis in range(3):
                if not self.position[axis].hasFocus():
                    self.position[axis].set_value(point[axis])
                if not self.size[axis].hasFocus():
                    self.size[axis].set_value(self._high[axis] - self._low[axis])
        finally:
            self._filling = False

    # ------------------------------------------------------------------
    def _moved(self, axis: int, value: float) -> None:
        current = reference_point(self._low, self._high, self.reference_key())[axis]
        if self._filling or abs(value - current) < 1e-9:
            return
        self.move_requested.emit(axis, value)

    def _resized(self, axis: int, value: float) -> None:
        current = self._high[axis] - self._low[axis]
        if self._filling or abs(value - current) < 1e-9:
            return
        if value <= 0:
            self.size[axis].set_value(current)
            return
        self.size_requested.emit(axis, value, self.proportional.isChecked())
