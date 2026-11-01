"""Hinge: pick the edge it runs along and the part that swings, check it opens.

The edge is on the part that stays put -- a box's top back edge -- and the
swinging part is clicked anywhere. Everything else is a number with a sensible
default. The preview turns the swinging part through its range on a slider (or
Play), and the kernel has already refused anything that would collide, naming
the angle where it would.
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QSlider, QWidget

from ...core.document import BodyRef
from ...core.units import Dimension
from ...kernel.hinge import DEFAULTS, TYPES, HingeFeature
from ..widgets.controls import GhostButton
from .base import FeaturePreviewController
from .clips import FITS, _combo, _select, _spin
from .modeling import _SelectionTool, _need
from .registry import register_tool

FIELDS = (
    ("knuckle_od", "Knuckle diameter", DEFAULTS["knuckle_od"], Dimension.LENGTH),
    ("pin", "Pin diameter", DEFAULTS["pin"], Dimension.LENGTH),
    ("length", "Length (0 = whole edge)", DEFAULTS["length"], Dimension.LENGTH),
    ("axial_clearance", "Gap between knuckles", DEFAULTS["axial_clearance"], Dimension.LENGTH),
    ("open_max", "Opens to", DEFAULTS["open_max"], Dimension.ANGLE),
    ("flex", "Strip thickness", DEFAULTS["flex"], Dimension.LENGTH),
    ("flex_length", "Bending length", DEFAULTS["flex_length"], Dimension.LENGTH),
)
LIVING_ONLY = {"flex", "flex_length"}


@register_tool("hinge")
class HingePanel(_SelectionTool):
    title = "Hinge"
    confirm_label = "Apply"
    width = 340

    def build(self) -> None:
        self._edge = None          # (body, reference)
        self._swing = None         # body name
        self._source = None
        self._preview_ok = False
        self._annotations = None
        self._moving = None
        self._dimmed = []
        self.roles = QLabel("")
        self.roles.setWordWrap(True)
        self.add_widget(self.roles)
        self.add_section("Hinge")
        self.kind = _combo(list(TYPES.items()), 1)
        self.kind.setToolTip(
            "Pin hinge: both parts bored, a separate pin goes through.\n"
            "Print-in-place: prints assembled; the pin is part of the fixed part.\n"
            "Snap-on: the swinging part's knuckles clip onto the pin and come off again.\n"
            "Living hinge: a thin strip joins them into one print (PP, PETG or TPU)."
        )
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.add_widget(self.kind)
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Knuckles"))
        self.knuckles = _spin(2, 15, int(DEFAULTS["knuckles"]))
        self.knuckles.valueChanged.connect(lambda _v: self.preview())
        layout.addWidget(self.knuckles)
        self.knuckle_row = row
        self.add_widget(row)
        self.fit = _combo([(k, label) for k, label in FITS if k != "press"], 1)
        self.fit.currentIndexChanged.connect(lambda _i: self.preview())
        self.add_widget(self.fit)
        for key, label, value, dimension in FIELDS:
            self.add_field(key, label, value, dimension)
            self.fields[key].edited_live.connect(lambda _v: self.preview())
        self.stop = QCheckBox("Stop at the opening limit")
        self.flip = QCheckBox("Put the knuckles on the other face")
        for box in (self.stop, self.flip):
            box.toggled.connect(lambda _on: self.preview())
            self.add_widget(box)
        self.add_section("Check it opens")
        motion = QWidget()
        row = QHBoxLayout(motion)
        row.setContentsMargins(0, 0, 0, 0)
        self.angle = QSlider(Qt.Horizontal)
        self.angle.setRange(0, int(DEFAULTS["open_max"]))
        self.angle.valueChanged.connect(self._show_angle)
        self.angle_label = QLabel("0°")
        self.play = GhostButton("Play")
        self.play.clicked.connect(self._play)
        row.addWidget(self.angle, 1)
        row.addWidget(self.angle_label)
        row.addWidget(self.play)
        self.add_widget(motion)
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._play_step)
        self._preview_controller = FeaturePreviewController(self, self._answer, delay_ms=140)
        editing = getattr(self.window_, "_hinge_edit_feature_id", None)
        self.window_._hinge_edit_feature_id = None
        if editing:
            self._load(editing)
        else:
            self._take_selection()
        self._kind_changed(preview=False)
        self._describe()
        self.preview()

    # -- picking ---------------------------------------------------------
    def _take_selection(self) -> None:
        document = self.window_.document
        for pick in self.selection.picks:
            if pick.kind == "edge" and self._edge is None:
                self._edge = (str(pick.body), pick.reference(document))
            elif self._edge is not None and str(pick.body) != self._edge[0]:
                self._swing = str(pick.body)
        if self._edge is not None and self._swing is None:
            others = [p for p in self.selection.picks if str(p.body) != self._edge[0]]
            if others:
                self._swing = str(others[0].body)

    def on_selection_changed(self) -> None:
        if not self.selection.picks or self._source is not None:
            return
        pick = self.selection.picks[-1]
        if pick.kind == "edge" and (self._edge is None or str(pick.body) == self._edge[0]):
            self._edge = (str(pick.body), pick.reference(self.window_.document))
        elif self._edge is not None and str(pick.body) != self._edge[0]:
            self._swing = str(pick.body)
        else:
            self.warn("First click the straight edge the hinge runs along.")
            return
        self.warn("")
        self._describe()
        self.preview()

    def _describe(self) -> None:
        fixed = self._edge[0] if self._edge else None
        if fixed is None:
            text = ("1. Click the straight edge the hinge runs along, on the part that stays "
                    "put (a box's top back edge).")
        elif self._swing is None:
            text = f"Fixed: {fixed}. 2. Now click the part that swings (the lid)."
        else:
            text = f"Fixed: {fixed}  ·  Swings: {self._swing}"
        self.roles.setText(text)
        self.set_subtitle("Knuckles go just outside the face behind the edge; drag the slider "
                          "to check it opens.")
        self.window_.set_hint(text)

    def _kind_changed(self, _index=0, preview: bool = True) -> None:
        living = self.kind.currentData() == "living"
        for key, *_rest in FIELDS:
            self.fields[key].parentWidget().setVisible(
                (key in LIVING_ONLY) == living or key in ("length", "open_max"))
        for widget in (self.knuckle_row, self.fit, self.stop, self.flip):
            widget.setVisible(not living)
        self.relayout()
        if preview:
            self.preview()

    def _load(self, feature_id: str) -> None:
        feature = self.window_.document.feature(feature_id)
        if feature is None or feature.type_name != "hinge":
            return
        self._source = feature
        self._source_state = feature.to_dict()
        self.title_label.setText("Edit hinge")
        inputs = feature.inputs
        self._edge = (str(inputs.get("body_a")), inputs.get("edge"))
        self._swing = str(inputs.get("body_b"))
        _select(self.kind, inputs.get("type", "knuckle"))
        if isinstance(inputs.get("clearance"), str):
            _select(self.fit, inputs.get("clearance"))
        self.knuckles.setValue(int(inputs.get("knuckles", DEFAULTS["knuckles"])))
        self.stop.setChecked(bool(inputs.get("stop", False)))
        self.flip.setChecked(bool(inputs.get("flip", False)))
        for key, *_rest in FIELDS:
            if key in inputs:
                self.fields[key].setText(str(inputs[key]))
                self.fields[key]._commit()

    # -- the feature -------------------------------------------------------
    def _pin_name(self) -> str:
        if self._source is not None and self._source.inputs.get("names"):
            return str(self._source.inputs["names"][0])
        taken = set(self.window_.document.bodies) | set(self.window_.document.groups)
        name, index = "Hinge pin", 2
        while name in taken:
            name, index = f"Hinge pin {index}", index + 1
        return name

    def _feature(self):
        if self._edge is None or self._swing is None:
            return None
        kind = str(self.kind.currentData())
        fixed, swing = self._edge[0], self._swing
        pin = self._pin_name()
        inputs = {
            "body_a": BodyRef(fixed), "body_b": BodyRef(swing), "edge": self._edge[1],
            "type": kind, "clearance": str(self.fit.currentData()),
            "knuckles": self.knuckles.value(), "stop": self.stop.isChecked(),
            "flip": self.flip.isChecked(), "names": [pin],
            **{key: self.expression(key, str(value)) for key, _l, value, _d in FIELDS},
        }
        outputs = ([fixed] if kind == "living" else
                   [fixed, swing, pin] if kind == "pin" else [fixed, swing])
        source = self._source
        feature = HingeFeature(feature_id=source.id if source else None,
                               name=source.name if source else "", inputs=inputs,
                               outputs=outputs)
        if source is not None:
            feature.bindings = dict(source.bindings)
            feature.output_ids = dict(source.output_ids)
            feature.dropped = list(source.dropped)
        return feature

    def preview(self) -> None:
        if not hasattr(self, "_preview_controller"):
            return
        feature = self._feature()
        self._preview_ok = False
        self.confirm.setEnabled(False)
        if feature is None:
            self._preview_controller.request(None)
            return
        self.warn("Building the hinge and swinging it through its range…")
        state = feature.to_dict()
        if self._source is not None:
            state["__edit_feature_id"] = self._source.id
        self._preview_controller.request(state)

    def _clear(self) -> None:
        viewport = self.window_.stage.viewport
        viewport.clear_ghost()
        if self._moving is not None:
            viewport.erase(self._moving)
            self._moving = None
        for name in self._dimmed:
            viewport.set_transparency(self.window_._presentations.get(name), 0.0)
        self._dimmed = []

    def _answer(self, message: dict) -> None:
        from ...core.geometry_service import deserialise_shape
        from ...kernel.occ import compound

        self._clear()
        if message.get("token") is None and message.get("error") is None:
            return
        error = message.get("error")
        parts = message.get("parts") or {}
        if error or not parts.get("a"):
            self.warn(error or "The hinge preview produced no geometry.")
            return
        viewport = self.window_.stage.viewport
        fixed = [deserialise_shape(parts["a"])]
        if parts.get("pin"):
            fixed.append(deserialise_shape(parts["pin"]))
        viewport.show_ghost(compound(fixed), self.window_.palette_.accent, transparency=0.5)
        if parts.get("b"):
            self._moving = viewport.show_overlay_shape(deserialise_shape(parts["b"]),
                                                       self.window_.palette_.accent, 0.35)
        self._annotations = message.get("annotations") or {}
        self.angle.setMaximum(int(round(self._annotations.get("open_max", 120))))
        self._dimmed = [self._edge[0], self._swing]
        for name in self._dimmed:
            viewport.set_transparency(self.window_._presentations.get(name), 0.9)
        self._show_angle(self.angle.value())
        self._preview_ok = True
        self.confirm.setEnabled(True)
        self.warn(" ".join([message.get("message") or "", *(message.get("warnings") or [])]))

    def _show_angle(self, value: int) -> None:
        from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf
        from OCP.TopLoc import TopLoc_Location

        self.angle_label.setText(f"{value}°")
        notes = self._annotations or {}
        if self._moving is None or "axis" not in notes:
            return
        turn = gp_Trsf()
        turn.SetRotation(gp_Ax1(gp_Pnt(*notes["axis"]), gp_Dir(*notes["direction"])),
                         -math.radians(value))
        self.window_.stage.viewport.place(self._moving, TopLoc_Location(turn))

    def _play(self) -> None:
        self._phase = 0
        self._timer.start()

    def _play_step(self) -> None:
        self._phase += 1
        top = self.angle.maximum()
        steps = 30
        value = (self._phase / steps if self._phase <= steps
                 else max(0.0, 2.0 - self._phase / steps)) * top
        self.angle.setValue(int(round(value)))
        if self._phase >= 2 * steps:
            self._timer.stop()
            self.angle.setValue(0)

    def commit(self) -> None:
        if not _need(self.window_, self._preview_ok, "Pick the edge and the swinging part, "
                     "and wait for the preview."):
            return
        feature = self._feature()
        document = self.window_.document
        source = self._source
        if source is not None and (document.feature(source.id) is not source
                                   or source.to_dict() != self._source_state):
            self.warn("The model changed while this hinge was open. Reopen it from History.")
            return
        self._clear()
        if source is None:
            self.window_.add_feature(feature)
        else:
            self.window_.history.record("Edit hinge")
            document.features[document.index_of(source.id)] = feature
            self.window_.invalidate({source.id})
            self.window_.mark_dirty()
            self.window_.rebuild()
        self.window_.cancel_tool()

    def teardown(self) -> None:
        self._timer.stop()
        self._preview_controller.close()
        self._clear()
