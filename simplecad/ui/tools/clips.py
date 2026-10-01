"""Join Parts: pick two touching faces, get aligned printable joints.

The workflow is the one the Align tools taught: click a face on one part,
click the face it meets on the other. From there SimpleCAD works out which
way the seam runs, where joints fit clear of edges, holes and text, and which
side carries the tabs -- and shows the result live. Everything the panel
chooses is an input of one ``clip_joint`` feature, so the whole connection is
one undo step, can be reopened from History and edited, and deleting it puts
both parts back as they were.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget,
)

from ...core.document import BodyRef
from ...core.units import Dimension
from ...kernel.clips import (
    DEFAULTS, MAX_CONNECTORS, STYLES, ClipJointFeature, facing, find_seam, seat_offsets,
)
from ...kernel.align import planar_frame
from ..panels.clip_overlay import ClipPlacementOverlay
from ..theme import METRICS
from ..widgets.controls import GhostButton
from .base import FeaturePreviewController
from .modeling import _SelectionTool, _need
from .registry import register_tool


FITS = (("snug", "Snug fit"), ("sliding", "Sliding fit"), ("press", "Press fit"))


def _spin(minimum: int, maximum: int, value: int) -> QSpinBox:
    control = QSpinBox()
    control.setRange(minimum, maximum)
    control.setValue(value)
    control.setMinimumHeight(METRICS.control_height)
    return control


def _combo(items, current=0) -> QComboBox:
    combo = QComboBox()
    for data, label in items:
        combo.addItem(label, data)
    combo.setCurrentIndex(current)
    return combo


def _select(combo: QComboBox, data) -> None:
    index = combo.findData(data)
    if index >= 0:
        combo.setCurrentIndex(index)


def calibrated_fit(key: str) -> float:
    """The printer's measured clearance for a fit, in mm (diametral)."""
    from ...kernel.calibration import effective_fits
    from ...kernel.thread_specs import printer_profile

    return float(effective_fits(printer_profile()["id"])[key])


@register_tool("clip_joint")
class ClipJointPanel(_SelectionTool):
    """Join two parts of a split print with snap-together joints."""

    title = "Join parts"
    confirm_label = "Apply"
    width = 348

    # -- construction --------------------------------------------------
    def build(self) -> None:
        self.picks = {"a": None, "b": None}
        self._refs = {"a": None, "b": None}
        self._bodies = {"a": None, "b": None}
        self._role: str | None = "a"
        self.anchors: list[tuple[float, float]] = []
        self._preview_ok = False
        self._preview_names: list[str] = []
        self._annotations = None
        self._signals_connected = False
        self._picking_points = False
        self._source = None
        self._source_state = None
        self._seat_offsets = (0.0, 0.0)

        self.marker = ClipPlacementOverlay(self._palette, self.window_.stage)
        self.marker.attach(self.window_.stage.viewport.project)
        self.window_.stage.add_overlay(self.marker, "full")
        self.marker.show()

        self.add_section("Faces")
        roles = QWidget()
        grid = QGridLayout(roles)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(METRICS.space(1))
        self.role_buttons = {}
        for row, role in enumerate(("a", "b")):
            button = GhostButton("")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, r=role: self._start_pick(r))
            grid.addWidget(button, row, 0, 1, 2)
            self.role_buttons[role] = button
        self.swap_button = GhostButton("Swap parts")
        self.swap_button.clicked.connect(self._swap)
        grid.addWidget(self.swap_button, 2, 0)
        self.flip_button = GhostButton("Tabs on A")
        self.flip_button.setCheckable(True)
        self.flip_button.setToolTip("Which part carries the tabs; the other gets the pockets.")
        self.flip_button.clicked.connect(self._flip)
        grid.addWidget(self.flip_button, 2, 1)
        self.add_widget(roles)
        self.seat = QCheckBox("Bring the parts together")
        self.seat.setToolTip("Set A down onto B first, keeping its side-to-side position.")
        self.seat.toggled.connect(lambda _on: self.preview())
        self.seat.hide()
        self.add_widget(self.seat)

        self.add_section("Joint")
        self.style = _combo([(key, label) for key, label in STYLES.items()])
        self.style.setToolTip(
            "Snap tab: flexible hooks on one part catch in pockets in the other.\n"
            "Printed connector: a separate double-ended clip, pockets in both parts.\n"
            "Tab & slot: a straight key with detent bumps, for thin walls."
        )
        self.add_widget(self.style)
        # Not ``size``: this is a QWidget, and shadowing QWidget.size() breaks
        # the stage's layout of the panel.
        self.clip_size = _combo([("small", "Small"), ("medium", "Medium"), ("large", "Large")], 1)
        self.retention = _combo([("easy", "Easy to separate"), ("standard", "Standard hold"),
                                 ("firm", "Firm hold")], 1)
        self.material = _combo([("petg", "PETG"), ("pla", "PLA")])
        self.fit = _combo(FITS)
        for widget in (self.clip_size, self.retention, self.material, self.fit):
            self.add_widget(widget)

        self.add_section("Placement")
        self.placement = _combo([("auto", "Automatic along the seam"), ("manual", "Click to place")])
        self.add_widget(self.placement)
        counts = QWidget()
        row = QHBoxLayout(counts)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel("Number of joints"))
        self.count = _spin(0, MAX_CONNECTORS, 0)
        self.count.setSpecialValueText("Auto")
        row.addWidget(self.count)
        self.add_widget(counts)
        self.add_field("spacing", "Spacing (auto)", DEFAULTS["spacing"])
        self.add_field("edge_offset", "Edge offset", DEFAULTS["edge_offset"])
        self.add_field("shift", "Slide along seam", 0.0)
        point_host = QWidget()
        point_actions = QHBoxLayout(point_host)
        point_actions.setContentsMargins(0, 0, 0, 0)
        self.undo_point = GhostButton("Undo point")
        self.undo_point.clicked.connect(self._undo_point)
        self.clear_points = GhostButton("Clear points")
        self.clear_points.clicked.connect(self._clear_points)
        point_actions.addWidget(self.undo_point)
        point_actions.addWidget(self.clear_points)
        self.point_host = point_host
        self.add_widget(point_host)

        self.advanced = GhostButton("Fit and printing details")
        self.advanced.setCheckable(True)
        self.advanced.clicked.connect(self._toggle_advanced)
        self.add_widget(self.advanced)
        self.advanced_host = QWidget()
        advanced = QVBoxLayout(self.advanced_host)
        advanced.setContentsMargins(0, 0, 0, 0)
        advanced.setSpacing(METRICS.space(1.5))
        for key, label, value, dimension in (
            ("clearance", "Clearance (total)", calibrated_fit("snug"), Dimension.LENGTH),
            ("depth_clearance", "Depth clearance", DEFAULTS["depth_clearance"], Dimension.LENGTH),
            ("min_wall", "Minimum wall", DEFAULTS["min_wall"], Dimension.LENGTH),
            ("lead_in", "Lead-in chamfer", DEFAULTS["lead_in"], Dimension.LENGTH),
            ("rotation", "Turn joints", 0.0, Dimension.ANGLE),
        ):
            self.add_field(key, label, value, dimension, advanced)
        self.add_widget(self.advanced_host)
        self.advanced_host.hide()

        for combo in (self.style, self.clip_size, self.retention, self.material):
            combo.currentIndexChanged.connect(lambda _i: self.preview())
        self.fit.currentIndexChanged.connect(self._fit_changed)
        self.placement.currentIndexChanged.connect(self._placement_changed)
        self.count.valueChanged.connect(lambda _v: self.preview())
        for key in ("spacing", "edge_offset", "shift", "clearance", "depth_clearance",
                    "min_wall", "lead_in", "rotation"):
            self.fields[key].edited_live.connect(lambda _v: self.preview())

        self._preview_controller = FeaturePreviewController(self, self._preview_answer, delay_ms=140)
        self._connect_viewport()
        editing = getattr(self.window_, "_clip_edit_feature_id", None)
        self.window_._clip_edit_feature_id = None
        if editing:
            self._load(editing)
        else:
            self._latch_selection()
        self._placement_changed()
        self._refresh_roles()
        self.preview()

    # -- faces -----------------------------------------------------------
    def _latch_selection(self) -> None:
        picks = [p for p in self.selection.picks]
        bodies = list(dict.fromkeys(p.body for p in picks))
        if len(bodies) == 2:
            for role, body in zip(("a", "b"), bodies):
                self._take(role, next(p for p in picks if p.body == body))
            self._role = None
            self._pair_faces()
        elif len(bodies) == 1:
            self._take("a", picks[-1])
            self._role = "b"

    def _take(self, role: str, pick) -> None:
        """Remember what was clicked on a part; faces are settled once both are."""
        self._clicked = getattr(self, "_clicked", {"a": None, "b": None})
        self._clicked[role] = pick
        self._bodies[role] = pick.body
        if pick.is_planar_face:
            self._assign(role, pick)
        else:
            self.picks[role] = None
            self._refs[role] = None

    def _pair_faces(self) -> None:
        """Settle the two faces: the clicked ones if they meet, else the seam.

        Split parts meet on faces hidden inside the assembly, so a click
        anywhere on each part is enough. When the clicked faces are not the
        ones that touch, the touching pair is found and used instead.
        """
        clicked = getattr(self, "_clicked", {})
        a, b = clicked.get("a"), clicked.get("b")
        if a is None or b is None:
            return
        if a.is_planar_face and b.is_planar_face:
            opposed, gap = facing(a.shape, b.shape)
            if opposed and abs(gap) <= 0.05:
                return
        document = self.window_.document
        body_a, body_b = document.body(a.body), document.body(b.body)
        if body_a is None or body_b is None or body_a.shape is None or body_b.shape is None:
            return
        seam = find_seam(body_a.shape, body_b.shape)
        if seam is None:
            if not (a.is_planar_face and b.is_planar_face):
                self.warn("These parts do not touch. Click the two flat faces that should "
                          "meet, and they can be brought together.")
            return
        from ...kernel.detect import analyse_plane
        from ..selection import Picked

        for role, pick, face in (("a", a, seam[0]), ("b", b, seam[1])):
            self._assign(role, Picked(pick.body, "face", face,
                                      self.window_._presentations.get(pick.body),
                                      analyse_plane(face)))
        self.warn("")

    def _assign(self, role: str, pick) -> None:
        self.picks[role] = pick
        self._bodies[role] = pick.body
        self._refs[role] = pick.reference(self.window_.document)
        self.anchors.clear()
        self._update_seat()

    def _start_pick(self, role: str) -> None:
        self._role = role
        self._set_point_picking(False)
        self._refresh_roles()

    def on_selection_changed(self) -> None:
        if self._role is None or not self.selection.picks:
            return
        pick = self.selection.picks[-1]
        other = self._bodies["b" if self._role == "a" else "a"]
        if pick.body == other:
            self.warn("Pick the face on the other part.")
            return
        self._take(self._role, pick)
        self._role = "b" if self._role == "a" and self._bodies["b"] is None else None
        self.warn("")
        if self._role is None:
            self._pair_faces()
        self._refresh_roles()
        self._placement_changed()
        self.preview()

    def _refresh_roles(self) -> None:
        for role, label in (("a", "First face"), ("b", "Second face")):
            button = self.role_buttons[role]
            name = self._bodies[role]
            picking = self._role == role
            text = f"{role.upper()}   {name}" if name else f"{role.upper()}   {label}"
            button.setText(("Click a face…  " if picking else "") + text)
            button.setChecked(picking)
        male = "b" if self.flip_button.isChecked() else "a"
        self.flip_button.setText(f"Tabs on {male.upper()}")
        ready = all(self._refs.values())
        self.swap_button.setEnabled(ready)
        self.flip_button.setEnabled(ready and self.style.currentData() != "connector")
        if self._role == "a":
            self.set_subtitle("Click the face on the first part where the parts meet.")
        elif self._role == "b":
            self.set_subtitle("Now click the matching face on the other part.")
        else:
            self.set_subtitle("Joints are placed automatically; adjust them below, then Apply.")
        self.window_.set_hint(self.subtitle.text())

    def _swap(self) -> None:
        clicked = getattr(self, "_clicked", {"a": None, "b": None})
        for store in (self.picks, self._refs, self._bodies, clicked):
            store["a"], store["b"] = store["b"], store["a"]
        self.anchors.clear()
        self._update_seat()
        self._refresh_roles()
        self.preview()

    def _flip(self) -> None:
        self._refresh_roles()
        self.preview()

    def _update_seat(self) -> None:
        a, b = self.picks["a"], self.picks["b"]
        if a is None or b is None:
            return
        opposed, gap = facing(a.shape, b.shape)
        apart = opposed and abs(gap) > 0.05
        self.seat.setVisible(apart)
        if apart:
            self._seat_offsets = seat_offsets(a.shape, b.shape)
            self.seat.setText(f"Bring the parts together (they are {abs(gap):.1f} mm apart)")
            self.seat.setChecked(True)
        else:
            self.seat.setChecked(False)
        self.relayout()

    # -- editing an existing joint -------------------------------------
    def _load(self, feature_id: str) -> None:
        feature = self.window_.document.feature(feature_id)
        if feature is None or feature.type_name != "clip_joint":
            return
        if not feature.inputs.get("clip_version"):
            self.warn("This joint was made by an earlier version. Delete it from History "
                      "and join the parts again to use the new joints.")
            self.confirm.setEnabled(False)
            self._role = None
            return
        self._source = feature
        self._source_state = feature.to_dict()
        self.title_label.setText("Edit joint")
        inputs = feature.inputs
        for role in ("a", "b"):
            self._bodies[role] = str(inputs.get(f"body_{role}", ""))
            self._refs[role] = inputs.get(f"face_{role}")
        self._role = None
        _select(self.style, inputs.get("style", "snap_tab"))
        _select(self.clip_size, inputs.get("size", "medium"))
        _select(self.retention, inputs.get("retention", "standard"))
        _select(self.material, inputs.get("material", "petg"))
        _select(self.placement, inputs.get("placement", "auto"))
        preset = inputs.get("clearance_preset")
        if preset:
            self.fit.blockSignals(True)
            _select(self.fit, preset)
            self.fit.blockSignals(False)
        self.count.setValue(int(inputs.get("count", 0) or 0))
        self.flip_button.setChecked(str(inputs.get("male", "a")) == "b")
        self.anchors = [tuple(point) for point in inputs.get("anchors") or []]
        for key in ("spacing", "edge_offset", "shift", "clearance", "depth_clearance",
                    "min_wall", "lead_in", "rotation"):
            if key in inputs:
                field = self.fields[key]
                field.setText(str(inputs[key]))
                field._commit()
        if inputs.get("seat"):
            self.seat.show()
            self.seat.setChecked(True)
            self._seat_offsets = (float(inputs.get("seat_x", 0.0)), float(inputs.get("seat_y", 0.0)))

    # -- manual placement ------------------------------------------------
    def _connect_viewport(self) -> None:
        if self._signals_connected:
            return
        viewport = self.window_.stage.viewport
        viewport.snap_hovered.connect(self._snap_hovered)
        viewport.snap_picked.connect(self._snap_picked)
        viewport.view_changed.connect(self.marker.update)
        self._signals_connected = True

    def _set_point_picking(self, on: bool) -> None:
        viewport = self.window_.stage.viewport
        if on and not self._picking_points:
            viewport.begin_point_pick()
        elif not on and self._picking_points:
            viewport.end_point_pick()
            self.marker.hover = None
        self._picking_points = on

    def _target_frame(self):
        pick = self.picks["b"]
        if pick is not None:
            return planar_frame(pick.shape)
        return None

    def _local_point(self, snap):
        frame = self._target_frame()
        if frame is None or snap is None:
            return None, frame
        delta = tuple(snap.position[i] - frame.center[i] for i in range(3))
        if abs(sum(delta[i] * frame.normal[i] for i in range(3))) > 0.25:
            return None, frame
        return (sum(delta[i] * frame.x_axis[i] for i in range(3)),
                sum(delta[i] * frame.y_axis[i] for i in range(3))), frame

    def _snap_hovered(self, snap) -> None:
        if not self._picking_points:
            return
        local, frame = self._local_point(snap)
        self.marker.hover = frame.point(*local) if local is not None else None
        self.marker.update()

    def _snap_picked(self, snap) -> None:
        if not self._picking_points:
            return
        local, _frame = self._local_point(snap)
        if local is None:
            self.warn(f"Place joints on the face of {self._bodies['b']}.")
            return
        if len(self.anchors) >= MAX_CONNECTORS:
            self.warn(f"A connection supports at most {MAX_CONNECTORS} joints.")
            return
        self.anchors.append(local)
        self.preview()

    def _undo_point(self) -> None:
        if self.anchors:
            self.anchors.pop()
            self.preview()

    def _clear_points(self) -> None:
        self.anchors.clear()
        self.preview()

    # -- controls --------------------------------------------------------
    def _placement_changed(self, _index=0) -> None:
        manual = self.placement.currentData() == "manual"
        self.point_host.setVisible(manual)
        for key in ("spacing", "shift"):
            self.fields[key].parentWidget().setVisible(not manual)
        self.count.parentWidget().setVisible(not manual)
        # Clicking points needs the picked target face, not just a reference.
        self._set_point_picking(manual and self._role is None and self.picks["b"] is not None)
        if manual and self.picks["b"] is None and self._source is not None:
            self.warn("Pick face B again to place joints by hand.")
        self.relayout()
        self.preview()

    def _fit_changed(self, _index=0) -> None:
        self.fields["clearance"].set_value(calibrated_fit(str(self.fit.currentData())))
        self.preview()

    def _toggle_advanced(self) -> None:
        self.advanced_host.setVisible(self.advanced.isChecked())
        self.relayout()

    def _connector_names_for(self, count: int) -> list[str]:
        document = self.window_.document
        taken = set(document.bodies) | set(document.groups)
        if self._source is not None:
            existing = [str(n) for n in self._source.inputs.get("connector_names", [])]
            taken -= set(existing)
        else:
            existing = []
        names = existing[:count]
        taken |= set(names)
        suffix = 1
        while len(names) < count:
            candidate = "Clip" if suffix == 1 else f"Clip{suffix}"
            suffix += 1
            if candidate not in taken:
                names.append(candidate)
                taken.add(candidate)
        return names

    def _feature(self) -> ClipJointFeature | None:
        if not all(self._refs.values()):
            return None
        style = str(self.style.currentData())
        manual = self.placement.currentData() == "manual"
        count = len(self.anchors) if manual else self.count.value()
        names = []
        if style == "connector":
            # Enough names for any automatic count; unused ones are ignored.
            names = self._connector_names_for(count or MAX_CONNECTORS)
        a, b = self._bodies["a"], self._bodies["b"]
        inputs = {
            "clip_version": 2,
            "body_a": BodyRef(a), "body_b": BodyRef(b),
            "face_a": self._refs["a"], "face_b": self._refs["b"],
            "style": style,
            "size": str(self.clip_size.currentData()),
            "retention": str(self.retention.currentData()),
            "material": str(self.material.currentData()),
            "clearance": self.expression("clearance", "0.15"),
            "clearance_preset": str(self.fit.currentData()),
            "depth_clearance": self.expression("depth_clearance", "0.3"),
            "min_wall": self.expression("min_wall", "0.8"),
            "lead_in": self.expression("lead_in", "0.4"),
            "rotation": self.expression("rotation", "0"),
            "placement": "manual" if manual else "auto",
            "count": 0 if manual else self.count.value(),
            "spacing": self.expression("spacing", "80"),
            "edge_offset": self.expression("edge_offset", "1"),
            "shift": self.expression("shift", "0"),
            "anchors": [list(point) for point in self.anchors],
            "male": "b" if self.flip_button.isChecked() and style != "connector" else "a",
            "connector_names": names,
        }
        if not self.seat.isHidden() and self.seat.isChecked():
            inputs.update(seat=True, seat_x=self._seat_offsets[0], seat_y=self._seat_offsets[1])
        source = self._source
        feature = ClipJointFeature(
            feature_id=source.id if source is not None else None,
            name=source.name if source is not None else "",
            inputs=inputs, outputs=[a, b, *names],
        )
        if source is not None:
            feature.bindings = dict(source.bindings)
            feature.output_ids = dict(source.output_ids)
            feature.dropped = list(source.dropped)
        return feature

    # -- preview / commit ----------------------------------------------
    def preview(self) -> None:
        if not hasattr(self, "_preview_controller"):
            return
        self._refresh_roles()
        feature = self._feature()
        if feature is None:
            self.confirm.setEnabled(False)
            self._preview_controller.request(None)
            self._draw_scene(None)
            return
        if self.placement.currentData() == "manual" and not self.anchors:
            self.confirm.setEnabled(False)
            self._preview_controller.request(None)
            self._draw_scene(None)
            self.warn(f"Click on the face of {self._bodies['b']} to place each joint.")
            return
        self._preview_ok = False
        self.confirm.setEnabled(False)
        self.warn("Working out the joints…")
        state = feature.to_dict()
        if self._source is not None:
            state["__edit_feature_id"] = self._source.id
        self._preview_controller.request(state)

    def _draw_scene(self, annotations) -> None:
        faces = {}
        for role in ("a", "b"):
            pick = self.picks[role]
            if pick is not None and pick.info is not None:
                faces[role] = tuple(pick.info.center)
        if not annotations:
            self.marker.set_scene(faces=faces)
            return
        male = annotations.get("male", "a")
        width, across = annotations.get("footprint", (6.0, 3.0))
        normal = annotations.get("normal", (0.0, 0.0, 1.0))
        footprints = []
        centre = [0.0, 0.0, 0.0]
        clips = annotations.get("clips") or []
        for clip in clips:
            at, x = clip["at"], clip["x"]
            y = (normal[1] * x[2] - normal[2] * x[1], normal[2] * x[0] - normal[0] * x[2],
                 normal[0] * x[1] - normal[1] * x[0])
            corners = [tuple(at[i] + sx * width / 2.0 * x[i] + sy * across / 2.0 * y[i]
                             for i in range(3))
                       for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
            footprints.append((corners, male, bool(clip.get("ok", True))))
            centre = [centre[i] + at[i] / len(clips) for i in range(3)]
        arrow = None
        if clips:
            reach = max(width * 1.5, 8.0)
            start = tuple(centre[i] - normal[i] * reach * 0.5 for i in range(3))
            end = tuple(centre[i] + normal[i] * reach for i in range(3))
            arrow = (start, end)
        self.marker.set_scene(faces=faces, arrow=arrow, footprints=footprints)

    def _clear_preview(self) -> None:
        viewport = self.window_.stage.viewport
        viewport.clear_ghost()
        for name in self._preview_names:
            viewport.set_transparency(self.window_._presentations.get(name), 0.0)
        self._preview_names = []

    def _preview_answer(self, message: dict) -> None:
        self._clear_preview()
        error = message.get("error")
        blob = message.get("shape")
        annotations = message.get("annotations")
        self._annotations = annotations
        if message.get("token") is None and error is None and blob is None:
            return
        self._draw_scene(annotations)
        if error or not blob:
            self._preview_ok = False
            self.confirm.setEnabled(False)
            self.warn(error or "The joint preview produced no geometry.")
            return
        from ...core.geometry_service import deserialise_shape

        shape = deserialise_shape(blob)
        if shape is None:
            self._preview_ok = False
            self.confirm.setEnabled(False)
            self.warn("The joint preview could not be displayed.")
            return
        viewport = self.window_.stage.viewport
        # See-through, so the tabs inside their pockets are visible.
        viewport.show_ghost(shape, self.window_.palette_.accent, transparency=0.55)
        self._preview_names = [self._bodies["a"], self._bodies["b"]]
        for name in self._preview_names:
            viewport.set_transparency(self.window_._presentations.get(name), 0.9)
        self._preview_ok = True
        self.confirm.setEnabled(True)
        clips = (annotations or {}).get("clips") or []
        notes = [w for w in message.get("warnings") or []]
        count = len(clips)
        summary = message.get("message") or f"{count} joint{'s' if count != 1 else ''} ready."
        self.warn(" ".join([summary, *notes]))

    def commit(self) -> None:
        if not _need(self.window_, self._preview_ok,
                     "Pick both faces and wait for the joint preview."):
            return
        feature = self._feature()
        if feature is None:
            return
        document = self.window_.document
        source = self._source
        if source is not None and (document.feature(source.id) is not source
                                   or source.to_dict() != self._source_state):
            self.warn("The model changed while this joint was open. Reopen it from History.")
            return
        self._clear_preview()
        if source is None:
            self.window_.add_feature(feature)
        else:
            self.window_.history.record("Edit joint")
            document.features[document.index_of(source.id)] = feature
            self.window_.invalidate({source.id})
            self.window_.mark_dirty()
            self.window_.rebuild()
        self.window_.cancel_tool()

    def teardown(self) -> None:
        self._preview_controller.close()
        self._clear_preview()
        viewport = self.window_.stage.viewport
        self._set_point_picking(False)
        viewport.clear_snap_cache()
        for signal, slot in (
            (viewport.snap_hovered, self._snap_hovered),
            (viewport.snap_picked, self._snap_picked),
            (viewport.view_changed, self.marker.update),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self.window_.stage.remove_overlay(self.marker)
        self.marker.hide()
        self.marker.deleteLater()

