"""Align: line whole objects up along X, Y or Z, or spread them out evenly.

The first tab of Align & Arrange. Stack & Place (one object onto a face of
another) and Arrange (patterns on a face or the build plate) are the other two;
:data:`POSITION_TABS` is the strip that joins them.

Every button applies at once as one undo step -- a Move per object, folded
into the object's previous move where it can be -- because lining things up is
a click-and-look gesture, and Undo is the preview.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
)

from ...core.document import BodyRef
from ...core.units import Dimension
from ...kernel.align import align_offsets, distribute_offsets
from ..icons import icon
from ..theme import METRICS
from ..widgets.controls import ValueField
from .base import ToolPanel, ToolTabs
from .registry import register_tool

#: The Align & Arrange strip, shared by all three panels.
POSITION_TABS = (("align_bodies", "Align"), ("align", "Stack & Place"), ("arrange", "Arrange"))

REFERENCES = (
    ("selection", "All of them together"),
    ("first", "The first one selected"),
    ("last", "The last one selected"),
)
SIDE_TIPS = {
    0: ("Left edges (lowest X)", "Centres in X", "Right edges (highest X)"),
    1: ("Front edges (lowest Y)", "Centres in Y", "Back edges (highest Y)"),
    2: ("Bottoms (lowest Z)", "Centres in Z", "Tops (highest Z)"),
}
AXIS_COLOURS = ("#E5484D", "#46A758", "#3E8EF7")


@register_tool("align_bodies")
class AlignBodiesPanel(ToolPanel):
    title = "Align"
    confirm_label = "Done"

    def build(self) -> None:
        palette = self._palette
        self.add_widget(ToolTabs(self.window_, POSITION_TABS, "align_bodies"))
        self.set_subtitle(
            "Line the selected objects up, or spread them out evenly. "
            "Each click is one step you can undo."
        )

        self.add_section("Line up with")
        self.reference = QComboBox()
        for key, label in REFERENCES:
            self.reference.addItem(label, key)
        self.reference.setToolTip(
            "All of them together: the outermost edge or the overall centre. "
            "First or last selected: that object stays put and the others meet it."
        )
        self.add_widget(self.reference)

        self.add_section("Align")
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(METRICS.space(1))
        grid.setVerticalSpacing(METRICS.space(1))
        self.align_buttons = {}
        for axis, name in enumerate("XYZ"):
            label = QLabel(name)
            label.setStyleSheet(f"color:{AXIS_COLOURS[axis]}; font-weight:700;")
            label.setFixedWidth(16)
            grid.addWidget(label, axis, 0)
            for column, side in enumerate(("min", "centre", "max")):
                button = QPushButton()
                button.setIcon(icon(f"align_{'center' if side == 'centre' else side}",
                                    palette.text_muted, 18))
                button.setIconSize(QSize(18, 18))
                button.setToolTip(f"Align {SIDE_TIPS[axis][column].lower()}")
                button.setCursor(Qt.PointingHandCursor)
                button.clicked.connect(lambda _=False, a=axis, s=side: self.align(a, s))
                grid.addWidget(button, axis, column + 1)
                self.align_buttons[(axis, side)] = button
        self.add_widget(grid_host)

        self.add_section("Spread out evenly")
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(METRICS.space(1))
        self.spread_buttons = {}
        for axis, name in enumerate("XYZ"):
            button = QPushButton(f"Along {name}")
            button.setIcon(icon("distribute", palette.text_muted, 16))
            button.setToolTip(f"Equal gaps between the objects along {name}")
            button.clicked.connect(lambda _=False, a=axis: self.distribute(a))
            layout.addWidget(button)
            self.spread_buttons[axis] = button
        self.add_widget(row)
        gap_row = QWidget()
        gap_layout = QHBoxLayout(gap_row)
        gap_layout.setContentsMargins(0, 0, 0, 0)
        self.fixed_gap = QCheckBox("Fixed gap")
        self.fixed_gap.setToolTip(
            "Off: the outermost two stay put and share the space between them.\n"
            "On: the first stays put and the rest follow at exactly this clearance."
        )
        self.gap = ValueField(palette, self.window_.document.parameters, Dimension.LENGTH, 5.0)
        self.gap.setEnabled(False)
        self.fixed_gap.toggled.connect(self.gap.setEnabled)
        gap_layout.addWidget(self.fixed_gap)
        gap_layout.addWidget(self.gap, 1)
        self.add_widget(gap_row)

    # ------------------------------------------------------------------
    def _subject(self):
        """(items, boxes, bodies per item) for the top-level selected objects."""
        from ...kernel.occ import bounding_box

        document = self.window_.document
        items, boxes, members = [], [], []
        for item in self.window_.selection.items:
            names = [n for n in document.expand([item])
                     if document.body(n) is not None and document.body(n).shape is not None]
            if not names:
                continue
            lows, highs = zip(*(bounding_box(document.body(n).shape) for n in names))
            boxes.append((tuple(min(v[i] for v in lows) for i in range(3)),
                          tuple(max(v[i] for v in highs) for i in range(3))))
            items.append(item)
            members.append(names)
        return items, boxes, members

    def align(self, axis: int, side: str) -> bool:
        reference = self.reference.currentData() or "selection"
        return self._apply(
            f"Align {SIDE_TIPS[axis][('min', 'centre', 'max').index(side)].lower()}", axis,
            lambda boxes: align_offsets(boxes, axis, side, reference), minimum=2)

    def distribute(self, axis: int) -> bool:
        gap = self.gap.value() if self.fixed_gap.isChecked() else None
        return self._apply(
            f"Spread out along {'XYZ'[axis]}", axis,
            lambda boxes: distribute_offsets(boxes, axis, gap),
            minimum=2 if gap is not None else 3)

    def _apply(self, label: str, axis: int, solve, *, minimum: int) -> bool:
        from ...kernel.operations import MoveFeature

        window = self.window_
        items, boxes, members = self._subject()
        if len(items) < minimum:
            self.warn(f"Select at least {minimum} objects (click, Shift-click or drag a box).")
            return False
        if window.refuse_locked([n for names in members for n in names]):
            return False
        if window.is_rebuilding:
            self.warn("Still updating the last change — try again in a moment.")
            return False
        offsets = solve(boxes)
        if all(abs(d) < 1e-9 for d in offsets):
            self.warn("")
            window.set_hint("Already lined up.")
            return False
        self.warn("")
        window.history.record(label)
        for names, distance in zip(members, offsets):
            if abs(distance) < 1e-9:
                continue
            vector = [0.0, 0.0, 0.0]
            vector[axis] = distance
            for name in names:
                if not window.document.fold_translation([name], tuple(vector)):
                    window.document.add_feature(MoveFeature(
                        inputs={"body": BodyRef(name), "dx": vector[0], "dy": vector[1],
                                "dz": vector[2]},
                        outputs=[name]))
        window.mark_dirty()
        window.rebuild()
        window.set_hint(f"{label}: {len(items)} objects.")
        return True

    def commit(self) -> None:
        self.window_.cancel_tool()


@register_tool("align_arrange")
def open_align_arrange(window):
    """The rail tile and search entry: open whichever tab fits the selection."""
    window.activate_tool(window._align_arrange_tool())
    return None
