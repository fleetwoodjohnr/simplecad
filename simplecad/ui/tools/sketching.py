"""The Sketch tool.

Pick a plane, add a profile, set its dimensions, extrude. The sketch stays
parametric: its dimensions are real constraints solved on every rebuild, and
they accept expressions like any other field, so a profile can be driven by a
document parameter.

The profile shapes here (rectangle, circle, polygon, slot) come pre-constrained
-- a hand-drawn rectangle needs four alignments before the solver treats it as
one -- which is what lets a beginner get a constrained sketch without knowing
what a constraint is.
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QGridLayout, QLabel, QWidget

from ...core.units import Dimension
from ...kernel.sketch_features import ExtrudeFeature, SketchFeature
from ...sketch.sketch import Sketch, SketchPlane
from ...sketch.solver import SketchState, solve
from ..theme import METRICS
from ..widgets.controls import GhostButton, ToolTile
from .base import ToolPanel
from .registry import register_tool

#: Profile shapes, with the fields each one needs.
PROFILES = {
    "rectangle": (
        "rectangle", "Rectangle",
        (("width", "Width", 40.0), ("height", "Height", 25.0)),
    ),
    "circle": (
        "circle", "Circle", (("diameter", "Diameter", 30.0),),
    ),
    "polygon": (
        "polygon", "Polygon",
        (("sides", "Sides", 6.0), ("across_flats", "Across flats", 30.0)),
    ),
    "slot": (
        "slot", "Slot",
        (("length", "Length", 40.0), ("width", "Width", 12.0)),
    ),
}

PLANES = ("XY", "XZ", "YZ")


@register_tool("sketch")
class SketchPanel(ToolPanel):
    title = "Sketch"
    confirm_label = "Create"
    width = 300

    def build(self) -> None:
        self.set_subtitle(
            "Pick a plane and a profile. Dimensions become real constraints, "
            "so they can be edited later."
        )
        self.profile = "rectangle"

        self.add_section("Plane")
        self.plane = QComboBox()
        for name in PLANES:
            self.plane.addItem(f"{name} plane", name)
        self.add_widget(self.plane)

        draw = GhostButton("Draw it instead")
        draw.clicked.connect(self.start_drawing)
        self.add_widget(draw)

        self.add_section("Profile")
        chooser = QWidget()
        grid = QGridLayout(chooser)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(METRICS.space(1))
        self._tiles = {}
        icons = {"rectangle": "box", "circle": "sphere",
                 "polygon": "polygon", "slot": "tube"}
        for index, key in enumerate(PROFILES):
            tile = ToolTile(icons[key], PROFILES[key][1], self._palette)
            tile.setChecked(key == self.profile)
            tile.clicked.connect(lambda _=False, k=key: self.choose(k))
            grid.addWidget(tile, 0, index)
            self._tiles[key] = tile
        self.add_widget(chooser)

        self.add_section("Size")
        self.size_host = QWidget()
        self.size_layout = QGridLayout(self.size_host)
        self.size_layout.setContentsMargins(0, 0, 0, 0)
        self.size_layout.setSpacing(METRICS.space(1.5))
        self.add_widget(self.size_host)

        self.add_section("Extrude")
        self.add_field("distance", "Distance", 10.0)
        self.symmetric = GhostButton("Symmetric")
        self.symmetric.setCheckable(True)
        self.add_widget(self.symmetric)

        self.state = QLabel("")
        self.state.setWordWrap(True)
        self.add_widget(self.state)

        self.choose("rectangle")

    def start_drawing(self) -> None:
        """Switch from placing a profile to drawing one on the plane."""
        self.window_.begin_sketch(SketchPlane.named(self.plane.currentData()))

    def choose(self, key: str) -> None:
        self.profile = key
        for name, tile in self._tiles.items():
            tile.setChecked(name == key)
        self._rebuild_size_fields()

    def _rebuild_size_fields(self) -> None:
        from ..widgets.controls import LabeledField, ValueField

        while self.size_layout.count():
            item = self.size_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for key in list(self.fields):
            if key != "distance":
                del self.fields[key]

        _icon, _label, spec = PROFILES[self.profile]
        for row, (key, label, default) in enumerate(spec):
            dimension = Dimension.SCALAR if key == "sides" else Dimension.LENGTH
            field = ValueField(
                self._palette, self.window_.document.parameters, dimension, default
            )
            field.returnPressed.connect(self.commit)
            self.size_layout.addWidget(
                LabeledField(label, field, self._palette), row, 0
            )
            self.fields[key] = field
        self.adjustSize()
        self.window_.stage._layout_overlays()

    # -- building the sketch ---------------------------------------------
    def _make_sketch(self) -> Sketch:
        import math

        plane = SketchPlane.named(self.plane.currentData())
        sketch = Sketch("Sketch", plane)

        if self.profile == "rectangle":
            width = self.value("width", 40.0)
            height = self.value("height", 25.0)
            lines = sketch.add_rectangle(0, 0, width, height)
            sketch.constrain("fix", [sketch.points[lines[0].start].id])
            sketch.constrain("distance", [lines[0].start, lines[0].end], width)
            sketch.constrain("distance", [lines[1].start, lines[1].end], height)
        elif self.profile == "circle":
            diameter = self.value("diameter", 30.0)
            centre = sketch.add_point(0, 0)
            circle = sketch.add_circle(centre, diameter / 2.0)
            sketch.constrain("fix", [centre.id])
            sketch.constrain("diameter", [circle.id], diameter)
        elif self.profile == "polygon":
            sides = max(3, int(self.value("sides", 6.0)))
            across = self.value("across_flats", 30.0)
            radius = across / (2.0 * math.cos(math.pi / sides))
            sketch.add_polygon(0, 0, radius, sides)
        else:  # slot
            length = self.value("length", 40.0)
            width = self.value("width", 12.0)
            self._build_slot(sketch, length, width)
        return sketch

    @staticmethod
    def _build_slot_between(sketch: Sketch, start, end, width: float) -> None:
        """A slot along an arbitrary segment, used by the drawing canvas."""
        import math

        radius = width / 2.0
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        if length < 1e-9 or radius < 1e-9:
            return
        ux, uy = dx / length, dy / length
        nx, ny = -uy, ux
        left = sketch.add_point(*start)
        right = sketch.add_point(*end)
        top_left = sketch.add_point(start[0] + nx * radius, start[1] + ny * radius)
        top_right = sketch.add_point(end[0] + nx * radius, end[1] + ny * radius)
        bottom_right = sketch.add_point(end[0] - nx * radius, end[1] - ny * radius)
        bottom_left = sketch.add_point(start[0] - nx * radius, start[1] - ny * radius)
        sketch.add_line(top_left, top_right)
        sketch.add_line(bottom_right, bottom_left)
        base = math.atan2(uy, ux)
        sketch.add_arc(right, radius, base - math.pi / 2, base + math.pi / 2)
        sketch.add_arc(left, radius, base + math.pi / 2, base + 3 * math.pi / 2)

    @staticmethod
    def _build_slot(sketch: Sketch, length: float, width: float) -> None:
        """Two straight sides capped by half-circles."""
        import math

        radius = width / 2.0
        span = max(length - width, 1e-3) / 2.0
        left = sketch.add_point(-span, 0)
        right = sketch.add_point(span, 0)
        top_left = sketch.add_point(-span, radius)
        top_right = sketch.add_point(span, radius)
        bottom_left = sketch.add_point(-span, -radius)
        bottom_right = sketch.add_point(span, -radius)
        sketch.add_line(top_left, top_right)
        sketch.add_line(bottom_right, bottom_left)
        sketch.add_arc(right, radius, -math.pi / 2, math.pi / 2)
        sketch.add_arc(left, radius, math.pi / 2, 3 * math.pi / 2)
        for point in (left, right):
            sketch.constrain("fix", [point.id])

    def preview(self) -> None:
        sketch = self._make_sketch()
        result = solve(sketch)
        colour = {
            SketchState.FULLY: self._palette.success,
            SketchState.CONFLICTING: self._palette.danger,
            SketchState.OVER: self._palette.warning,
        }.get(result.state, self._palette.text_muted)
        self.state.setStyleSheet(f"color:{colour}; font-size:12px;")
        self.state.setText(f"{result.state.label()} — {result.message}")

    def commit(self) -> None:
        sketch = self._make_sketch()
        result = solve(sketch)
        if not result.ok:
            self.warn(result.message)
            return

        document = self.window_.document
        feature = SketchFeature(inputs={"sketch": sketch})
        feature.name = document.unique_name("Sketch")
        self.window_.history.record("Sketch")
        document.add_feature(feature)
        document.add_feature(
            ExtrudeFeature(
                inputs={
                    "sketch": feature.name,
                    "distance": self.expression("distance", "10"),
                    "symmetric": self.symmetric.isChecked(),
                }
            )
        )
        self.window_.mark_dirty()
        self.window_.rebuild()
        self.window_.stage.viewport.fit_all()
        self.window_.cancel_tool()
