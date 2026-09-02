"""The Shape tool: drop a primitive without drawing a sketch first.

This is the fastest path to geometry in SimpleCAD and the reason a beginner can
make something useful in the first minute. Pick a shape, adjust the two or three
numbers that matter, press Create. Everything stays parametric afterwards.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ...core.units import Dimension
from ...kernel.decorative import (
    CrescentFeature, CrossFeature, HeartFeature, LightningFeature, StarFeature,
)
from ...kernel.primitives import PRIMITIVES
from ...kernel.vent import VentPlateFeature
from ..icons import icon
from ..theme import METRICS, Palette
from ..widgets.controls import (
    FloatingCard, GhostButton, IconButton, LabeledField, PrimaryButton,
    SectionLabel, ToolTile, ValueField,
)
from .registry import register_tool

#: Which numbers each shape exposes up front. Anything not listed here stays
#: behind More Options rather than crowding the panel.
FIELDS: dict[str, tuple[tuple[str, str, float], ...]] = {
    "box": (("width", "Width", 40.0), ("depth", "Depth", 30.0), ("height", "Height", 20.0)),
    "cylinder": (("radius", "Radius", 12.0), ("height", "Height", 30.0)),
    "sphere": (("radius", "Radius", 15.0),),
    "cone": (("bottom_radius", "Bottom", 15.0), ("top_radius", "Top", 0.0), ("height", "Height", 30.0)),
    "torus": (("major_radius", "Ring", 25.0), ("minor_radius", "Tube", 6.0)),
    "tube": (("outer_radius", "Outer", 15.0), ("inner_radius", "Inner", 10.0), ("height", "Height", 30.0)),
    "wedge": (("width", "Width", 40.0), ("depth", "Depth", 30.0), ("height", "Height", 20.0)),
    "polygon_prism": (("sides", "Sides", 6.0), ("across_flats", "Across flats", 17.0), ("height", "Height", 8.0)),
    "star": (("size", "Size", 30.0), ("inner_ratio", "Inset", 0.45), ("height", "Height", 10.0)),
    "heart": (("size", "Size", 30.0), ("height", "Height", 10.0)),
    "cross": (("size", "Size", 30.0), ("arm_width", "Arm width", 10.0), ("height", "Height", 10.0)),
    "crescent": (("size", "Size", 30.0), ("thickness", "Thickness", 8.0), ("height", "Height", 10.0)),
    "lightning": (("size", "Size", 30.0), ("height", "Height", 10.0)),
    # Polygon presets: the side count is fixed, so only size and height are asked
    # for. Even-sided ones are measured across flats, because that is how a
    # spanner, a nut and a caliper all measure one. Odd-sided ones have no
    # opposite flats *or* opposite corners to measure between, so they are sized
    # by the circle their corners sit on -- the one number that is unambiguous
    # for them.
    "triangle": (("across_corners", "Corner circle", 30.0), ("height", "Height", 10.0)),
    "pentagon": (("across_corners", "Corner circle", 25.0), ("height", "Height", 10.0)),
    "hexagon": (("across_flats", "Across flats", 17.0), ("height", "Height", 8.0)),
    "octagon": (("across_flats", "Across flats", 20.0), ("height", "Height", 10.0)),
    "vent_plate": (
        ("width", "Width", 60.0), ("depth", "Depth", 60.0),
        ("thickness", "Thickness", 3.0), ("across_flats", "Hole size", 5.0),
        ("wall", "Wall", 1.2), ("margin", "Border", 2.0),
    ),
}

#: Presets that reuse another feature with some inputs pinned. A hexagon is a
#: polygon prism with ``sides`` fixed at 6 -- worth its own tile because that is
#: what people look for, not worth its own feature type.
FIXED_INPUTS: dict[str, dict[str, float]] = {
    "triangle": {"sides": 3},
    "pentagon": {"sides": 5},
    "hexagon": {"sides": 6},
    "octagon": {"sides": 8},
}

#: UI tile -> the feature class it builds.
FEATURES: dict[str, type] = {
    **PRIMITIVES,
    "star": StarFeature,
    "heart": HeartFeature,
    "cross": CrossFeature,
    "crescent": CrescentFeature,
    "lightning": LightningFeature,
    "triangle": PRIMITIVES["polygon_prism"],
    "pentagon": PRIMITIVES["polygon_prism"],
    "hexagon": PRIMITIVES["polygon_prism"],
    "octagon": PRIMITIVES["polygon_prism"],
    "vent_plate": VentPlateFeature,
}

LABELS = (
    ("box", "box", "Box"),
    ("cylinder", "cylinder", "Cylinder"),
    ("sphere", "sphere", "Sphere"),
    ("cone", "cone", "Cone"),
    ("torus", "torus", "Torus"),
    ("tube", "tube", "Tube"),
    ("wedge", "wedge", "Wedge"),
    ("vent_plate", "vent", "Vent"),
    ("star", "star", "Star"),
    ("heart", "heart", "Heart"),
    ("cross", "cross", "Cross"),
    ("crescent", "crescent", "Crescent"),
    ("lightning", "lightning", "Lightning"),
    ("triangle", "triangle", "Triangle"),
    ("pentagon", "pentagon", "Pentagon"),
    ("hexagon", "hexagon", "Hexagon"),
    ("octagon", "octagon", "Octagon"),
    # Last, and on its own row: the escape hatch for any other side count.
    ("polygon_prism", "polygon", "Polygon"),
)

#: What a new body gets called. Falls back to the feature's own label, which for
#: every preset would otherwise be the unhelpful "Polygon".
BODY_NAMES = {kind: label for kind, _icon, label in LABELS}


class ShapePanel(FloatingCard):
    """Floating panel: pick a shape, set a few numbers, create it.

    Follows the same activate/commit/cancel contract as the tools in
    ``tools.base``, even though its layout is a picker grid rather than a field
    list -- so Enter, Esc and the registry treat it identically.
    """

    is_tool_panel = True

    def __init__(self, window, palette: Palette) -> None:
        super().__init__(palette, window.stage)
        self.window_ = window
        self._palette = palette
        self._kind = "box"
        self.fields: dict[str, ValueField] = {}
        self.setFixedWidth(292)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            METRICS.space(4), METRICS.space(3.5), METRICS.space(4), METRICS.space(3.5)
        )
        layout.setSpacing(METRICS.space(3))

        header = QHBoxLayout()
        title = QLabel("Add a shape")
        title.setObjectName("PanelTitle")
        close = IconButton("close", palette, "Cancel  (Esc)", size=30)
        close.clicked.connect(window.cancel_tool)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(close)
        layout.addLayout(header)

        self._tiles: dict[str, ToolTile] = {}
        grid = QGridLayout()
        grid.setSpacing(METRICS.space(1))
        for index, (kind, icon_name, label) in enumerate(LABELS):
            tile = ToolTile(icon_name, label, palette)
            tile.clicked.connect(lambda _=False, k=kind: self.choose(k))
            grid.addWidget(tile, index // 4, index % 4)
            self._tiles[kind] = tile
        layout.addLayout(grid)

        layout.addWidget(SectionLabel("Size", palette))
        self.fields_host = QWidget()
        self.fields_layout = QVBoxLayout(self.fields_host)
        self.fields_layout.setContentsMargins(0, 0, 0, 0)
        self.fields_layout.setSpacing(METRICS.space(1.5))
        layout.addWidget(self.fields_host)

        buttons = QHBoxLayout()
        buttons.setSpacing(METRICS.space(2))
        cancel = GhostButton("Cancel")
        cancel.clicked.connect(window.cancel_tool)
        create = PrimaryButton("Create")
        create.clicked.connect(self.commit)
        buttons.addWidget(cancel)
        buttons.addWidget(create, 1)
        layout.addLayout(buttons)

        self.choose("box")

    def choose(self, kind: str) -> None:
        self._kind = kind
        for name, tile in self._tiles.items():
            tile.setChecked(name == kind)
        self._rebuild_fields()
        label = BODY_NAMES[kind]
        self.window_.set_hint(f"{label}: set the size, then press Create.")

    def _rebuild_fields(self) -> None:
        while self.fields_layout.count():
            item = self.fields_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.fields = {}
        for key, label, default in FIELDS[self._kind]:
            dimension = (
                Dimension.SCALAR
                if key in {"sides", "inner_ratio"}
                else Dimension.LENGTH
            )
            field = ValueField(
                self._palette,
                self.window_.document.parameters,
                dimension,
                default,
            )
            field.returnPressed.connect(self.commit)
            row = LabeledField(label, field, self._palette)
            self.fields_layout.addWidget(row)
            self.fields[key] = field
        self.adjustSize()
        self.window_.stage._layout_overlays()

    def commit(self) -> None:
        feature_class = FEATURES[self._kind]
        inputs = {key: field.expression() for key, field in self.fields.items()}
        # Preset tiles pin inputs the panel deliberately does not ask about.
        inputs.update(FIXED_INPUTS.get(self._kind, {}))
        feature = feature_class(inputs=inputs)
        # Name the body here rather than letting the rebuild assign it: the
        # rebuild now happens in another process, and the caller wants to know
        # what it made straight away.
        feature.name = self.window_.document.unique_name(
            BODY_NAMES.get(self._kind, feature_class.label)
        )
        feature.outputs = [feature.name]
        self.window_.add_feature(feature)
        self.window_.stage.viewport.fit_all()


@register_tool("shapes")
def open_shape_panel(window) -> ShapePanel:
    """The registry places and shows whatever a tool factory returns."""
    return ShapePanel(window, window.palette_)


def _preset_factory(kind: str):
    """A tool that opens the shape panel already on *kind*.

    This is what lets search jump straight to Hexagon instead of leaving the
    user to find the tile -- the panel is the same one either way, so there is
    no second code path to keep in step.
    """

    def open_preset(window) -> ShapePanel:
        panel = ShapePanel(window, window.palette_)
        panel.choose(kind)
        return panel

    open_preset.__name__ = f"open_{kind}_panel"
    return open_preset


# Every tile is reachable by name from command search.
for _kind, _icon_name, _label in LABELS:
    register_tool(f"shape_{_kind}")(_preset_factory(_kind))
