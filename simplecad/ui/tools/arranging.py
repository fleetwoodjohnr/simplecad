"""Auto-Arrange: lay several objects out on a face, or line them up in a row.

The workflow is the one the brief asks for -- select the objects, open
Arrange, click the face they go on, choose a pattern, adjust, Apply -- and
every change previews live in the geometry process. The objects are latched
when the panel opens, so the clicks that pick a face, a contact face or a
boundary edge never change *what* is being arranged; only the explicit
"Add or remove objects" pick does.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget,
)

from ...core.document import BodyRef
from ...core.units import Dimension
from ...kernel.arrange import ArrangeFeature
from ..theme import METRICS
from ..viewport.occt_view import SelectionMode
from ..widgets.controls import AnchorGrid, GhostButton
from .base import FeaturePreviewController
from .modeling import _SelectionTool, _need
from .registry import register_tool

PATTERNS = (
    ("grid", "Grid"), ("linear", "Linear"), ("radial", "Radial"),
    ("staggered", "Staggered"), ("packed", "Packed"), ("custom", "Custom"),
)
#: What a fresh panel, and Reset, start from.
DEFAULTS = {
    "pattern": "grid", "spacing": "auto", "boundary": "auto", "align": "center",
    "fill": "rows", "distribute": "between", "rows": 0, "columns": 0,
    "contain": True, "avoid_holes": True, "avoid_bodies": True, "auto_rotate": False,
    "face_center": False, "orient": True,
    "gap": 3.0, "gap_y": 3.0, "margin": 2.0, "inset_left": 0.0, "inset_right": 0.0,
    "inset_top": 0.0, "inset_bottom": 0.0, "direction": 0.0, "rotation": 0.0,
    "radius": 0.0, "start_angle": 0.0, "normal_offset": 0.0,
}
LENGTHS = ("gap", "gap_y", "margin", "inset_left", "inset_right", "inset_top",
           "inset_bottom", "radius", "normal_offset")
ANGLES = ("direction", "rotation", "start_angle")
SWITCHES = ("contain", "avoid_holes", "avoid_bodies", "auto_rotate", "face_center", "orient")
WHOLE = {"body", "solid", "shell", "compound"}
#: Viewport pick modes for each thing the panel can be waiting for.
ROLE_MODES = {
    "target": (SelectionMode.FACE,),
    "objects": (SelectionMode.BODY,),
    "contact": (SelectionMode.FACE,),
    "edges": (SelectionMode.EDGE,),
}


@register_tool("arrange")
class ArrangePanel(_SelectionTool):
    title = "Arrange objects"
    confirm_label = "Apply"
    width = 336

    # -- construction ----------------------------------------------------
    def build(self) -> None:
        window = self.window_
        self._overlays = []
        self._dimmed = []
        self._offset = (0.0, 0.0, 0.0)
        self._role = None
        self._fits = False
        self._names: list[str] = []
        self._target = None          # Picked, for describing it and its axes
        self._target_ref = None      # what the feature stores
        self._contacts: dict = {}    # body -> SubShapeRef
        self._edges: list = []       # SubShapeRefs of picked boundary edges
        self._edge_picks: list = []
        self._source = None
        self._source_state = None
        self._suggestion_buttons = []
        self.checks: dict[str, QCheckBox] = {}
        self._modes = window.stage.viewport.selection_modes
        self._preview_controller = FeaturePreviewController(self, self._previewed, delay_ms=80)

        editing = getattr(window, "_arrange_edit_feature_id", None)
        window._arrange_edit_feature_id = None
        source = window.document.feature(editing) if editing else None
        if source is not None:
            self._source = source
            self._source_state = source.to_dict()
            self._load_subjects(source)
        else:
            self._latch_selection()

        self._build_subjects()
        self._build_pattern()
        self._build_row()
        self._build_spacing()
        self._build_boundary()
        self._build_layout()
        self._build_orientation()
        self._build_actions()

        self._apply(DEFAULTS)
        if source is not None:
            self._apply(self._recorded(source))
            self.confirm.setText("Save changes")
        elif self._target is None:
            self._start("target")
        self._refresh()
        self._attach_group_gizmo()
        self.preview()

    def _row_widget(self, *widgets) -> QWidget:
        host = QWidget()
        layout = QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(METRICS.space(1))
        for widget in widgets:
            layout.addWidget(widget, 1)
        return host

    def _choices(self, options, changed, columns=3) -> tuple[QWidget, dict]:
        """A segmented row of checkable buttons, the house pattern for a choice."""
        host = QWidget()
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(METRICS.space(1))
        buttons = {}
        for index, (key, label) in enumerate(options):
            button = GhostButton(label)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, k=key: changed(k))
            grid.addWidget(button, index // columns, index % columns)
            buttons[key] = button
        return host, buttons

    def _group(self) -> tuple[QWidget, QVBoxLayout]:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(METRICS.space(1.5))
        self.add_widget(host)
        return host, layout

    def _field(self, key, label, into, dimension=Dimension.LENGTH):
        field = self.add_field(key, label, DEFAULTS.get(key, 0.0), dimension, into=into,
                               commit_on_return=False)
        field.edited_live.connect(lambda _v: self.preview())
        return field

    def _check(self, key, label, into):
        box = QCheckBox(label)
        box.setCursor(Qt.PointingHandCursor)
        box.stateChanged.connect(lambda _v: self.preview())
        into.addWidget(box)
        self.checks[key] = box
        return box

    def _spin(self, label, into):
        spin = QSpinBox()
        spin.setRange(0, 999)
        spin.setSpecialValueText("Auto")
        spin.valueChanged.connect(lambda _v: self.preview())
        caption = QLabel(label)
        caption.setStyleSheet(f"color:{self._palette.text_muted};")
        into.addWidget(self._row_widget(caption, spin))
        return spin

    def _build_subjects(self) -> None:
        self.objects_button = GhostButton("")
        self.objects_button.setCheckable(True)
        self.objects_button.clicked.connect(lambda: self._toggle_role("objects"))
        self.target_button = GhostButton("")
        self.target_button.setCheckable(True)
        self.target_button.clicked.connect(lambda: self._toggle_role("target"))
        self.add_widget(self.objects_button)
        self.add_widget(self.target_button)

    def _build_pattern(self) -> None:
        self.add_section("Pattern")
        host, self.pattern_buttons = self._choices(
            PATTERNS + (("row", "Row (no face)"),), self._choose_pattern)
        self.pattern_buttons["row"].setToolTip(
            "Line the objects up where they are, with equal or fixed gaps.")
        self.add_widget(host)

    def _build_row(self) -> None:
        self.row_box, into = self._group()
        self.row_mode = QComboBox()
        self.row_mode.addItem("Equal clear gaps", "equal_gaps")
        self.row_mode.addItem("Fixed clear gap", "fixed_gap")
        self.row_mode.currentIndexChanged.connect(lambda _i: self._refresh())
        into.addWidget(self.row_mode)
        self.row_gap = self.add_field("row_gap", "Clear gap", 10.0, into=into,
                                      commit_on_return=False)
        self.row_gap.edited_live.connect(lambda _v: self.preview())
        self.row_axis = QComboBox()
        self.row_axis.currentIndexChanged.connect(lambda _i: self.preview())
        into.addWidget(self.row_axis)

    def _build_spacing(self) -> None:
        self.spacing_box, into = self._group()
        self.add_section("Spacing", into=into)
        host, self.spacing_buttons = self._choices(
            (("auto", "Automatic"), ("fixed", "Fixed")), self._choose_spacing, columns=2)
        into.addWidget(host)
        self._field("gap", "Spacing", into)
        self._field("gap_y", "Row spacing", into)

    def _build_boundary(self) -> None:
        self.boundary_box, into = self._group()
        self.add_section("Boundary", into=into)
        host, self.boundary_buttons = self._choices(
            (("auto", "Automatic"), ("manual", "Manual")), self._choose_boundary, columns=2)
        into.addWidget(host)
        self._field("margin", "Edge margin", into)
        self.manual_box = QWidget()
        manual = QVBoxLayout(self.manual_box)
        manual.setContentsMargins(0, 0, 0, 0)
        manual.setSpacing(METRICS.space(1))
        for key, label in (("inset_left", "Left inset"), ("inset_right", "Right inset"),
                           ("inset_top", "Top inset"), ("inset_bottom", "Bottom inset")):
            self._field(key, label, manual)
        self.edges_button = GhostButton("")
        self.edges_button.setCheckable(True)
        self.edges_button.setToolTip(
            "In Manual, the edge margin is kept only from the edges you pick.")
        self.edges_button.clicked.connect(lambda: self._toggle_role("edges"))
        manual.addWidget(self.edges_button)
        into.addWidget(self.manual_box)
        self._check("contain", "Keep objects entirely inside", into)
        self._check("avoid_holes", "Avoid holes and cutouts", into)
        self._check("avoid_bodies", "Avoid other objects on the face", into)

    def _build_layout(self) -> None:
        self.layout_box, into = self._group()
        self.add_section("Layout", into=into)
        self.direction_field = self._field("direction", "Row direction", into, Dimension.ANGLE)
        self.rows = self._spin("Rows", into)
        self.columns = self._spin("Columns", into)
        self.custom_box = QWidget()
        custom = QVBoxLayout(self.custom_box)
        custom.setContentsMargins(0, 0, 0, 0)
        self.fill = QComboBox()
        self.fill.addItem("Fill rows first", "rows")
        self.fill.addItem("Fill columns first", "columns")
        self.fill.currentIndexChanged.connect(lambda _i: self.preview())
        custom.addWidget(self.fill)
        self.distribute = QComboBox()
        self.distribute.addItem("Spread to the margins", "between")
        self.distribute.addItem("Even gaps, edges included", "evenly")
        self.distribute.currentIndexChanged.connect(lambda _i: self.preview())
        custom.addWidget(self.distribute)
        into.addWidget(self.custom_box)
        self.radial_box = QWidget()
        radial = QVBoxLayout(self.radial_box)
        radial.setContentsMargins(0, 0, 0, 0)
        radial.setSpacing(METRICS.space(1))
        self._field("radius", "Radius (0 auto)", radial)
        self._field("start_angle", "Start angle", radial, Dimension.ANGLE)
        self._check("face_center", "Turn each to face the centre", radial)
        into.addWidget(self.radial_box)
        self.align_caption = QLabel("Alignment within the boundary")
        self.align_caption.setStyleSheet(f"color:{self._palette.text_muted};")
        into.addWidget(self.align_caption)
        self.align = AnchorGrid(self._palette, "center")
        self.align.changed.connect(lambda _v: self.preview())
        into.addWidget(self.align)

    def _build_orientation(self) -> None:
        self.orientation_box, into = self._group()
        self.add_section("Orientation", into=into)
        self._check("orient", "Stand objects on a flat face", into)
        self.contact_button = GhostButton("")
        self.contact_button.setCheckable(True)
        self.contact_button.setToolTip("Click a face of an object to stand it on that face.")
        self.contact_button.clicked.connect(lambda: self._toggle_role("contact"))
        into.addWidget(self.contact_button)
        self._field("rotation", "Rotate each", into, Dimension.ANGLE)
        self._check("auto_rotate", "Turn objects to fit better", into)
        self._field("normal_offset", "Stand-off", into)

    def _build_actions(self) -> None:
        reset = GhostButton("Reset")
        reset.setToolTip("Back to the default settings, and undo any drag.")
        reset.clicked.connect(self._reset)
        again = GhostButton("Recalculate")
        again.setToolTip("Rebuild the preview from the objects as they are now.")
        again.clicked.connect(self.preview)
        self.add_widget(self._row_widget(reset, again))
        # The outcome sits in the panel's fixed chrome, next to Apply, so a
        # layout that does not fit -- and what would -- is never scrolled away.
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.suggestion_host = QWidget()
        self.suggestion_layout = QVBoxLayout(self.suggestion_host)
        self.suggestion_layout.setContentsMargins(0, 0, 0, 0)
        self.suggestion_layout.setSpacing(METRICS.space(1))
        at = self.root.indexOf(self.status)
        self.root.insertWidget(at, self.suggestion_host)
        self.root.insertWidget(at, self.summary)

    # -- subjects --------------------------------------------------------
    def _latch_selection(self) -> None:
        picks = list(self.selection.picks)
        faces = [p for p in picks if p.kind == "face" and (p.is_planar_face or p.is_round_face)]
        whole = list(dict.fromkeys(str(p.body) for p in picks if p.kind in WHOLE))
        target = next((p for p in reversed(faces) if str(p.body) not in whole), None)
        self._names = [str(name) for name in self.selection.bodies
                       if str(name) in whole]
        if target is not None:
            self._set_target(target)
        for pick in faces:
            if pick is not target and str(pick.body) in self._names and pick.is_planar_face:
                self._contacts[str(pick.body)] = pick.reference(self.window_.document)

    def _load_subjects(self, feature) -> None:
        from ...core.naming import resolve

        inputs = feature.inputs
        self._names = [str(name) for name in inputs.get("bodies", [])]
        self._contacts = {ref.body: ref for ref in inputs.get("contact_faces") or []}
        self._edges = list(inputs.get("boundary_edges") or [])
        ref = inputs.get("target_face")
        if ref is None:
            return
        self._target_ref = ref
        body = self.window_.document.body(ref.body)
        try:
            shape = resolve(ref, body.shape)
        except Exception:  # noqa: BLE001 - reported, and the face is picked again
            shape = None
        if shape is None:
            self._target_ref = None
            self.warn("The face this arrangement was on is gone. Click a new one.")
            return
        self._target = self._picked(ref.body, shape)

    def _picked(self, body, shape):
        from ...kernel.detect import analyse_cylinder, analyse_plane
        from ..selection import Picked

        return Picked(body=str(body), kind="face", shape=shape,
                      presentation=self.window_._presentations.get(str(body)),
                      info=analyse_plane(shape) or analyse_cylinder(shape))

    def _set_target(self, pick) -> None:
        self._target = pick
        self._target_ref = pick.reference(self.window_.document)
        self._edges, self._edge_picks = [], []

    def _toggle_role(self, role: str) -> None:
        self._start(None if self._role == role else role)

    def _start(self, role) -> None:
        viewport = self.window_.stage.viewport
        # No role while the selection is being set up, so the panel does not
        # read its own clearing as the user deselecting everything.
        self._role = None
        viewport.set_selection_modes(ROLE_MODES.get(role, self._modes))
        viewport.set_additive_selection(role in ("objects", "edges"))
        viewport.clear_selection()
        if role == "objects":
            viewport.select_presentations(
                [self.window_._presentations.get(n) for n in self._names])
        elif role == "edges":
            for pick in self._edge_picks:
                viewport.select_subshape(pick.presentation, pick.shape, replace=False)
        self._role = role
        self._refresh()

    def on_selection_changed(self) -> None:
        role = self._role
        if role is None:
            return
        picks = list(self.selection.picks)
        if role == "objects":
            target_body = self._target.body if self._target else None
            chosen = [str(b) for b in self.selection.bodies if str(b) != target_body]
            names = [n for n in self._names if n in chosen] + [n for n in chosen if n not in self._names]
            late = self._made_too_late(names)
            if late:
                self.warn(f"{', '.join(late)} was made after this arrangement; arrange it separately.")
                names = [n for n in names if n not in late]
            if names == self._names:
                return
            self._names = names
            self._contacts = {k: v for k, v in self._contacts.items() if k in names}
            self._attach_group_gizmo()
        elif not picks:
            return
        elif role == "target":
            pick = picks[-1]
            if pick.kind != "face" or not (pick.is_planar_face or pick.is_round_face):
                self.warn("Pick a flat face, or the round side of a cylinder.")
                return
            if str(pick.body) in self._names:
                self.warn("That face is on one of the objects being arranged. Pick another object's face.")
                return
            self._set_target(pick)
            self._start(None)
            if self.pattern == "row" and self._source is None:
                self._choose_pattern("grid")
        elif role == "contact":
            pick = picks[-1]
            if str(pick.body) not in self._names or not pick.is_planar_face:
                self.warn("Click a flat face on one of the objects being arranged.")
                return
            self._contacts[str(pick.body)] = pick.reference(self.window_.document)
        elif role == "edges":
            edges = [p for p in picks if p.kind == "edge" and self._on_target(p)]
            if len(edges) < len([p for p in picks if p.kind == "edge"]):
                self.warn("Only edges of the target face can bound it.")
            self._edge_picks = edges
            self._edges = [p.reference(self.window_.document) for p in edges]
        self._refresh()
        self.preview()

    def _on_target(self, pick) -> bool:
        from OCP.TopAbs import TopAbs_EDGE
        from OCP.TopExp import TopExp_Explorer

        if self._target is None or str(pick.body) != str(self._target.body):
            return False
        explorer = TopExp_Explorer(self._target.shape, TopAbs_EDGE)
        while explorer.More():
            if explorer.Current().IsSame(pick.shape):
                return True
            explorer.Next()
        return False

    def _made_too_late(self, names) -> list[str]:
        """Bodies that do not exist yet where the edited arrangement sits."""
        if self._source is None:
            return []
        document = self.window_.document
        earlier = document.features[:document.index_of(self._source.id)]
        made = {name for feature in earlier for name in feature.outputs}
        return [n for n in names if n not in made]

    # -- choices ---------------------------------------------------------
    @property
    def pattern(self) -> str:
        return next((k for k, b in self.pattern_buttons.items() if b.isChecked()), "grid")

    def _pick_one(self, buttons, key) -> None:
        for name, button in buttons.items():
            button.setChecked(name == key)

    def _choose_pattern(self, key) -> None:
        self._pick_one(self.pattern_buttons, key)
        self._refresh()
        self.preview()

    def _choose_spacing(self, key) -> None:
        self._pick_one(self.spacing_buttons, key)
        self._refresh()
        self.preview()

    def _choose_boundary(self, key) -> None:
        self._pick_one(self.boundary_buttons, key)
        if key != "manual" and self._role == "edges":
            self._start(None)
        self._refresh()
        self.preview()

    def _apply(self, values: dict) -> None:
        """Set controls from feature-style values; quietly, then one refresh."""
        for key, value in values.items():
            if key == "pattern":
                self._pick_one(self.pattern_buttons, value)
            elif key == "spacing":
                self._pick_one(self.spacing_buttons, value)
            elif key == "boundary":
                self._pick_one(self.boundary_buttons, value)
            elif key == "align":
                self.align.set_value(str(value), emit=False)
            elif key in ("rows", "columns"):
                getattr(self, key).setValue(int(value or 0))
            elif key in ("fill", "distribute"):
                combo = getattr(self, key)
                combo.setCurrentIndex(max(0, combo.findData(value)))
            elif key in self.checks:
                self.checks[key].setChecked(bool(value))
            elif key in self.fields:
                field = self.fields[key]
                if isinstance(value, (int, float)):
                    field.set_value(float(value))
                else:
                    field.setText(str(value))
                    field._commit()
            elif key == "mode":
                self.row_mode.setCurrentIndex(max(0, self.row_mode.findData(value)))
            elif key == "axis":
                self._row_axis_wanted = value

    def _recorded(self, feature) -> dict:
        values = dict(feature.inputs)
        values["pattern"] = values.get("pattern", "row")
        if values["pattern"] == "row":
            values["row_gap"] = values.pop("gap", 10.0)
        axes = self._axes()
        if "move_x" in values and axes is not None:
            # Stored along the face's U, V and normal; the drag works in world terms.
            amounts = [float(values.get(k, 0.0)) for k in ("move_x", "move_y", "move_normal")]
            self._offset = tuple(sum(a * axis[i] for a, axis in zip(amounts, axes)) for i in range(3))
        elif "dx" in values:
            self._offset = tuple(float(values.get(k, 0.0)) for k in ("dx", "dy", "dz"))
        return values

    def _reset(self) -> None:
        self._offset = (0.0, 0.0, 0.0)
        self._contacts = {}
        self._edges, self._edge_picks = [], []
        self._apply(DEFAULTS)
        self._start(None)
        self._attach_group_gizmo()
        self.preview()

    def _apply_suggestion(self, patch: dict) -> None:
        self._apply(patch)
        self._refresh()
        self.preview()

    # -- showing state ---------------------------------------------------
    def _refresh(self) -> None:
        pattern = self.pattern
        row = pattern == "row"
        target = self._target
        self.pattern_buttons["row"].setVisible(target is None or row)
        for key, button in self.pattern_buttons.items():
            if key != "row":
                button.setEnabled(not (key == "radial" and target is not None and target.is_round_face))
        n = len(self._names)
        self.objects_button.setText(
            "Click objects to add or remove…" if self._role == "objects"
            else f"●  {n} object{'s' if n != 1 else ''}  ·  Add or remove")
        self.objects_button.setChecked(self._role == "objects")
        if self._role == "target":
            self.target_button.setText("Click the face to arrange on…")
        elif target is not None:
            kind = "round face" if target.is_round_face else "flat face"
            self.target_button.setText(f"◆  {target.body}: {kind}  ·  Pick another")
        else:
            self.target_button.setText("◆  Pick the target face")
        self.target_button.setChecked(self._role == "target")

        self.row_box.setVisible(row)
        for box in (self.spacing_box, self.boundary_box, self.layout_box):
            box.setVisible(not row)
        self.orientation_box.setVisible(not row or target is not None)
        self._show_field("rotation", not row)
        self.checks["auto_rotate"].setVisible(not row)
        self.contact_button.setVisible(not row)
        self.contact_button.setText(
            "Click a face on an object…" if self._role == "contact"
            else f"Pick contact faces ({len(self._contacts)} chosen)")
        self.contact_button.setChecked(self._role == "contact")

        if row:
            self._refresh_row_axis()
            self.row_gap.setEnabled(self.row_mode.currentData() == "fixed_gap")
        else:
            auto = self.spacing_buttons["auto"].isChecked()
            self._label("gap", "Minimum spacing" if auto else "Spacing")
            self._show_field("gap_y", pattern == "custom")
            manual = self.boundary_buttons["manual"].isChecked()
            self.manual_box.setVisible(manual)
            self._label("margin", "Margin from picked edges" if manual else "Edge margin")
            self.edges_button.setText(
                "Click edges of the target face…" if self._role == "edges"
                else f"Pick edges to keep the margin from ({len(self._edges)})")
            self.edges_button.setChecked(self._role == "edges")
            self._show_field("direction", pattern != "radial")
            self._label("direction", "Row direction" if pattern == "linear" else "Pattern turn")
            gridded = pattern in ("grid", "staggered", "custom")
            for spin in (self.rows, self.columns):
                spin.parentWidget().setVisible(gridded)
            self.custom_box.setVisible(pattern == "custom")
            self.radial_box.setVisible(pattern == "radial")
            self.align_caption.setVisible(pattern != "radial")
            self.align.setVisible(pattern != "radial")
        if target is not None:
            self.set_subtitle(f"Arrange {n} object{'s' if n != 1 else ''} on {target.body}.")
        elif row:
            self.set_subtitle(f"Line {n} objects up in a straight row.")
        else:
            self.set_subtitle("Select the objects, then click the face to arrange them on.")
        self.relayout()

    def _label(self, key, text) -> None:
        holder = self.fields[key].parentWidget()
        holder.label.setText(text)

    def _show_field(self, key, visible) -> None:
        self.fields[key].parentWidget().setVisible(visible)

    def _refresh_row_axis(self) -> None:
        wanted = getattr(self, "_row_axis_wanted", None) or self.row_axis.currentData() or "x"
        on_face = self._target is not None
        items = ([("Face U — toward Right", "x"), ("Face V — toward Up", "y")] if on_face
                 else [("World X", "x"), ("World Y", "y"), ("World Z", "z")])
        if [self.row_axis.itemData(i) for i in range(self.row_axis.count())] != [d for _l, d in items]:
            self.row_axis.blockSignals(True)
            self.row_axis.clear()
            for label, data in items:
                self.row_axis.addItem(label, data)
            self.row_axis.blockSignals(False)
        self.row_axis.setCurrentIndex(max(0, self.row_axis.findData(wanted)))
        self._row_axis_wanted = None

    def _show_suggestions(self, suggestions) -> None:
        for button in self._suggestion_buttons:
            self.suggestion_layout.removeWidget(button)
            button.deleteLater()
        self._suggestion_buttons = []
        for offer in suggestions or ():
            button = GhostButton(f"Try: {offer['label']}")
            button.clicked.connect(lambda _=False, patch=dict(offer["patch"]): self._apply_suggestion(patch))
            self.suggestion_layout.addWidget(button)
            self._suggestion_buttons.append(button)
        self.suggestion_host.setVisible(bool(self._suggestion_buttons))

    # -- the feature -----------------------------------------------------
    def _required(self) -> int:
        if self.pattern != "row":
            return 1
        return 3 if self.row_mode.currentData() == "equal_gaps" else 2

    def _axes(self):
        """U, V and normal of the target, for turning a drag into face terms."""
        if self._target is None:
            return None
        from ...kernel.arrange import face_axes

        return face_axes(self._target.shape)

    def _inputs(self) -> dict:
        from ...kernel.align import _dot

        pattern = self.pattern
        inputs = {"bodies": [BodyRef(name) for name in self._names]}
        axes = self._axes()
        if pattern == "row":
            inputs.update({
                "mode": str(self.row_mode.currentData()),
                "axis": str(self.row_axis.currentData() or "x"),
                "gap": self.expression("row_gap", "10"),
                "orient": self.checks["orient"].isChecked() if axes else False,
                "normal_offset": self.expression("normal_offset", "0"),
            })
        else:
            spacing = "auto" if self.spacing_buttons["auto"].isChecked() else "fixed"
            boundary = "manual" if self.boundary_buttons["manual"].isChecked() else "auto"
            inputs.update({
                "pattern": pattern, "spacing": spacing, "boundary": boundary,
                "gap": self.expression("gap", "3"), "margin": self.expression("margin", "2"),
                "rotation": self.expression("rotation", "0"),
                "normal_offset": self.expression("normal_offset", "0"),
            })
            inputs.update({key: self.checks[key].isChecked()
                           for key in ("contain", "avoid_holes", "avoid_bodies", "auto_rotate", "orient")})
            if pattern != "radial":
                inputs["direction"] = self.expression("direction", "0")
                inputs["align"] = self.align.value()
            if pattern in ("grid", "staggered", "custom"):
                inputs.update(rows=self.rows.value(), columns=self.columns.value())
            if pattern == "custom":
                inputs.update(gap_y=self.expression("gap_y", "3"), fill=self.fill.currentData(),
                              distribute=self.distribute.currentData())
            if pattern == "radial":
                inputs.update(radius=self.expression("radius", "0"),
                              start_angle=self.expression("start_angle", "0"),
                              face_center=self.checks["face_center"].isChecked())
            if boundary == "manual":
                inputs.update({key: self.expression(key, "0") for key in
                               ("inset_left", "inset_right", "inset_top", "inset_bottom")})
                if self._edges:
                    inputs["boundary_edges"] = list(self._edges)
            contacts = [ref for name, ref in self._contacts.items() if name in self._names]
            if contacts:
                inputs["contact_faces"] = contacts
        if axes is not None:
            u, v, n = axes
            inputs.update({
                "target_face": self._target_ref,
                "move_x": _dot(self._offset, u), "move_y": _dot(self._offset, v),
                "move_normal": _dot(self._offset, n), "frame_mode": "reference",
            })
        else:
            inputs.update(dict(zip(("dx", "dy", "dz"), self._offset)))
        return inputs

    def _feature(self) -> ArrangeFeature:
        source = self._source
        feature = ArrangeFeature(
            feature_id=source.id if source is not None else None,
            name=source.name if source is not None else "",
            inputs=self._inputs(), outputs=list(self._names),
        )
        if source is not None:
            feature.bindings = dict(source.bindings)
            feature.output_ids = dict(source.output_ids)
            feature.dropped = list(source.dropped)
        return feature

    def _ready(self) -> str:
        """Why the arrangement cannot be previewed yet, or ''."""
        if len(self._names) < self._required():
            need = self._required()
            return f"Select at least {need} object{'s' if need != 1 else ''} to arrange."
        if self.pattern != "row" and self._target_ref is None:
            return "Click the face to arrange the objects on."
        return ""

    # -- preview ---------------------------------------------------------
    def preview(self) -> None:
        if not hasattr(self, "_preview_controller") or not hasattr(self, "summary"):
            return
        self._fits = False
        self.confirm.setEnabled(False)
        reason = self._ready()
        if reason:
            self._clear_preview(keep_gizmo=True)
            self.summary.setText("")
            self.summary.setMinimumHeight(0)
            self._show_suggestions(())
            self.warn(reason if self._role is None else self._role_hint())
            self._preview_controller.request(None)
            return
        viewport = self.window_.stage.viewport
        for name in self._names:
            if name not in self._dimmed:
                viewport.set_transparency(self.window_._presentations.get(name), 0.82)
                self._dimmed.append(name)
        for name in [n for n in self._dimmed if n not in self._names]:
            viewport.set_transparency(self.window_._presentations.get(name), 0.0)
            self._dimmed.remove(name)
        self.warn(self._role_hint() if self._role else "Updating arrangement…")
        state = self._feature().to_dict()
        if self._source is not None:
            state["__edit_feature_id"] = self._source.id
        self._preview_controller.request(state)

    def _role_hint(self) -> str:
        return {
            "target": "Click the face to arrange the objects on.",
            "objects": "Click objects to add or remove them; click the button again when done.",
            "contact": "Click the face each object should stand on.",
            "edges": "Click the edges the margin should be kept from.",
        }.get(self._role, "")

    def _previewed(self, message: dict) -> None:
        from ...core.geometry_service import deserialise_shape

        viewport = self.window_.stage.viewport
        self._erase_overlays()
        viewport.clear_ghost()
        error = message.get("error")
        notes = message.get("annotations") or {}
        if self._ready():
            return
        if error or not message.get("shape"):
            self._fits = False
            self.summary.setText("")
            self.summary.setMinimumHeight(0)
            self._show_suggestions(())
            if error:
                self.warn(error)
            self.confirm.setEnabled(False)
            return
        parts = message.get("parts") or {}
        palette = self.window_.palette_
        if self.pattern == "row" or not parts:
            viewport.show_ghost(deserialise_shape(message["shape"]), palette.accent, transparency=0.12)
            fits = True
            summary = message.get("message") or "Arrangement ready."
        else:
            for key, colour, alpha in (("ok", palette.accent, 0.12), ("problem", palette.danger, 0.05),
                                       ("boundary", palette.text_muted, 0.0)):
                if parts.get(key):
                    shown = viewport.show_overlay_shape(deserialise_shape(parts[key]), colour, alpha)
                    if shown is not None:
                        self._overlays.append(shown)
            fits = bool(notes.get("fits"))
            summary = notes.get("summary") or message.get("message") or ""
            if not fits:
                problems = notes.get("problems") or []
                summary = "Doesn't fit: " + summary + "".join(
                    f"\n• {p['name']} {p['reason']}" for p in problems[:2])
                if len(problems) > 2:
                    summary += f"\n• and {len(problems) - 2} more"
        self._fits = fits
        colour = palette.text if fits else palette.danger
        self.summary.setStyleSheet(f"color:{colour}; font-size:12px;")
        self.summary.setText(summary)
        # A wrapped label under-reports its height to the layout; say it.
        margins = self.root.contentsMargins()
        self.summary.setMinimumHeight(
            self.summary.heightForWidth(self.width - margins.left() - margins.right()))
        self._show_suggestions(notes.get("suggestions") if not fits else ())
        warnings = message.get("warnings") or []
        self.warn(self._role_hint() if self._role else " · ".join(dict.fromkeys(warnings)))
        self.confirm.setEnabled(fits)
        # The outcome rows change height here, and Qt only reports their new
        # size hints once the event loop has laid them out; budget the panel
        # after that, or the scroll area keeps its old height and they overlap.
        QTimer.singleShot(0, self, self.relayout)

    # -- moving the whole arrangement -----------------------------------
    def _attach_group_gizmo(self) -> None:
        presentations = [
            self.window_._presentations.get(name) for name in self._names
            if self.window_._presentations.get(name) is not None
        ]
        if not presentations:
            self.window_.detach_gizmo()
            return
        gizmo = self.window_.attach_gizmo(presentations, allow_rotation=False, allow_planes=True)
        if gizmo is not None:
            gizmo.changed.connect(self._on_group_drag)
            gizmo.committed.connect(self._on_group_drag)

    def _on_group_drag(self, dx, dy, dz, _rx, _ry, _rz) -> None:
        self._offset = (dx, dy, dz)
        self.preview()

    # -- cleaning up -----------------------------------------------------
    def _erase_overlays(self) -> None:
        viewport = self.window_.stage.viewport
        for presentation in self._overlays:
            viewport.erase(presentation)
        self._overlays = []

    def _clear_preview(self, *, keep_gizmo: bool = False) -> None:
        viewport = self.window_.stage.viewport
        viewport.clear_ghost()
        self._erase_overlays()
        for name in self._dimmed:
            viewport.set_transparency(self.window_._presentations.get(name), 0.0)
        self._dimmed = []
        if not keep_gizmo:
            self.window_.detach_gizmo()

    def teardown(self) -> None:
        self._preview_controller.close()
        self._clear_preview()
        viewport = self.window_.stage.viewport
        viewport.set_additive_selection(False)
        viewport.set_selection_modes(self._modes)

    def commit(self) -> None:
        if not _need(self.window_, not self._ready(), self._ready() or "Nothing to arrange."):
            return
        if not _need(self.window_, self._fits,
                     "The arrangement doesn't fit yet. Try one of the suggestions."):
            return
        feature = self._feature()
        document = self.window_.document
        source = self._source
        if source is not None and (document.feature(source.id) is not source
                                   or source.to_dict() != self._source_state):
            self.warn("The model changed while this arrangement was open. Reopen it from History.")
            return
        self._clear_preview()
        if source is None:
            self.window_.add_feature(feature)
        else:
            self.window_.history.record("Edit arrangement")
            document.features[document.index_of(source.id)] = feature
            self.window_.invalidate({source.id})
            self.window_.mark_dirty()
            self.window_.rebuild()
        self.window_.cancel_tool()
