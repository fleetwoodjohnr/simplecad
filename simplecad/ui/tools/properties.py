"""Edit parameters: change any feature's numbers after the fact.

Every feature keeps its dimensions in ``inputs``, and the rebuild engine
re-runs whatever depends on a changed feature -- but only textures, fillets,
joints and arrangements used to have an editor. This panel is the generic one:
it shows a feature's numeric and on/off inputs, previews the change, and
replaces the feature in place as one undo step. Everything downstream (the
hole in the box, the fillet on the hole) rebuilds from it.
"""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QVBoxLayout, QWidget

from ...core.units import Dimension
from .base import FeaturePreviewController, ToolPanel
from .registry import register_tool

#: Inputs that are plain counts, not lengths, when a feature does not say.
COUNT_KEYS = {"count", "rows", "columns", "sides", "copies", "number", "points", "segments",
              "turns", "teeth", "quantity", "instances", "n", "count_x", "count_y"}
#: Inputs that are structure rather than dimensions.
HIDDEN_KEYS = {"order", "clip_version", "pivot", "axis", "names", "connector_names"}
#: Features with an editor of their own.
BESPOKE = {"face_texture", "fillet", "chamfer", "edge_finish", "clip_joint", "arrange", "hinge"}
#: Friendlier names for terse input keys.
LABELS = {
    "dx": "Move X", "dy": "Move Y", "dz": "Move Z",
    "rx": "Turn about X", "ry": "Turn about Y", "rz": "Turn about Z",
    "sx": "Scale X", "sy": "Scale Y", "sz": "Scale Z",
    "x": "Position X", "y": "Position Y", "z": "Position Z",
}


def editable_inputs(feature, parameters) -> list[tuple[str, object]]:
    """``(key, Dimension | "bool")`` for each input a person can sensibly edit.

    Numbers and expressions that evaluate become value fields, booleans become
    checkboxes. References, body names, lists and choice strings are left to
    the feature's own tool.
    """
    from ...core.document import BodyRef

    found = []
    for key, raw in feature.inputs.items():
        if key.startswith("_") or key in HIDDEN_KEYS or isinstance(raw, BodyRef):
            continue
        if isinstance(raw, bool):
            found.append((key, "bool"))
            continue
        declared = feature.dimensions.get(key)
        if isinstance(raw, (int, float)):
            if declared is not None:
                found.append((key, declared))
            elif key in COUNT_KEYS or key.endswith("_count"):
                found.append((key, Dimension.SCALAR))
            else:
                found.append((key, Dimension.LENGTH))
            continue
        if not isinstance(raw, str) or not raw.strip():
            continue
        for dimension in ([declared] if declared is not None else
                          ([Dimension.SCALAR] if key in COUNT_KEYS else []) +
                          [Dimension.LENGTH, Dimension.ANGLE]):
            try:
                parameters.evaluate(raw, dimension)
            except Exception:  # noqa: BLE001 - not an expression of this kind
                continue
            found.append((key, dimension))
            break
    return found


def label_for(key: str) -> str:
    return LABELS.get(key, key.replace("_", " ").capitalize())


def _literal(text: str):
    """A plain number with no unit or name in it, or None."""
    try:
        return float(text)
    except ValueError:
        return None


@register_tool("edit_parameters")
class PropertiesPanel(ToolPanel):
    """The numbers behind one feature, editable in place."""

    title = "Edit parameters"
    confirm_label = "Apply"

    def build(self) -> None:
        window = self.window_
        document = window.document
        feature_id = getattr(window, "_parameters_feature_id", None)
        window._parameters_feature_id = None
        self._chain = []
        if feature_id is None:
            bodies = list(window.selection.bodies)
            if len(bodies) == 1:
                graph = document.prepare_graph()
                head = graph.latest.get(bodies[0])
                if head is not None:
                    self._chain = [graph.features[fid]
                                   for fid in graph.writers.get(head.object_id, ())
                                   if fid in graph.features]
            editable = [f for f in self._chain
                        if f.type_name not in BESPOKE
                        and editable_inputs(f, document.parameters)]
            self._chain = editable
            feature_id = editable[-1].id if editable else None
        self._preview_controller = FeaturePreviewController(self, self._preview_answer)
        self._preview_ok = True
        self._ghosted = []
        if len(self._chain) > 1:
            self.add_section("Step")
            self.step = QComboBox()
            for feature in self._chain:
                self.step.addItem(feature.name or feature.label, feature.id)
            self.step.setCurrentIndex(len(self._chain) - 1)
            self.step.currentIndexChanged.connect(
                lambda _i: self._load(self.step.currentData())
            )
            self.add_widget(self.step)
        self._host = None
        self._load(feature_id)

    # -- the form ----------------------------------------------------------
    def _load(self, feature_id) -> None:
        document = self.window_.document
        self._source = document.feature(feature_id) if feature_id else None
        if self._host is not None:
            for key in list(self.fields):
                self.fields.pop(key)
            self._host.setParent(None)
            self._host.deleteLater()
        self._host = QWidget()
        layout = QVBoxLayout(self._host)
        layout.setContentsMargins(0, 0, 0, 0)
        self.add_widget(self._host)
        self._checks = {}
        self._original_text = {}
        if self._source is None:
            self.set_subtitle("Select a body, or right-click a step in History, "
                              "to change its dimensions.")
            self.confirm.setEnabled(False)
            self.relayout()
            return
        self._source_state = self._source.to_dict()
        self.set_subtitle(
            f"{self._source.name or self._source.label} — change a value and everything "
            "built from it updates. Expressions and parameter names work too."
        )
        inputs = editable_inputs(self._source, document.parameters)
        if not inputs:
            layout.addWidget(QLabel("This step has no dimensions to edit here."))
        for key, kind in inputs:
            raw = self._source.inputs[key]
            if kind == "bool":
                box = QCheckBox(label_for(key))
                box.setChecked(bool(raw))
                box.toggled.connect(lambda _c: self.preview())
                layout.addWidget(box)
                self._checks[key] = box
                continue
            value = (float(raw) if isinstance(raw, (int, float))
                     else document.parameters.evaluate(raw, kind))
            field = self.add_field(key, label_for(key), value, kind, into=layout)
            if isinstance(raw, str):
                field.setText(raw)           # keep the expression, not its value
            field.edited_live.connect(lambda _v: self.preview())
            self._original_text[key] = field.text()
        self.confirm.setEnabled(True)
        self.warn("")
        self.relayout()

    def _edited_feature(self):
        """A copy of the source with the edited inputs, or None if invalid."""
        from ...core.document import Feature

        source = self._source
        if source is None:
            return None
        state = source.to_dict()
        feature = Feature.from_dict(state)
        parameters = self.window_.document.parameters
        for key, box in self._checks.items():
            feature.inputs[key] = box.isChecked()
        for key, field in self.fields.items():
            text = field.text().strip()
            if text == self._original_text.get(key):
                continue
            original = source.inputs[key]
            dimension = dict(editable_inputs(source, parameters)).get(key, Dimension.LENGTH)
            try:
                parameters.evaluate(text, dimension)
            except Exception as exc:  # noqa: BLE001 - shown to the user
                self.warn(f"{label_for(key)}: {exc}")
                return None
            number = _literal(text)
            if number is not None and isinstance(original, (int, float)):
                # A plain number stays a number, of the kind it was.
                feature.inputs[key] = int(number) if isinstance(original, int) and \
                    number.is_integer() else number
            else:
                feature.inputs[key] = text
        return feature

    def preview(self) -> None:
        if not hasattr(self, "_preview_controller"):
            return
        feature = self._edited_feature()
        if feature is None:
            self._preview_ok = False
            self.confirm.setEnabled(False)
            self._preview_controller.request(None)
            return
        self._preview_ok = False
        self.confirm.setEnabled(False)
        state = feature.to_dict()
        state["__edit_feature_id"] = self._source.id
        self._preview_controller.request(state)

    def _clear_preview(self) -> None:
        viewport = self.window_.stage.viewport
        viewport.clear_ghost()
        for name in self._ghosted:
            viewport.set_transparency(self.window_._presentations.get(name), 0.0)
        self._ghosted = []

    def _preview_answer(self, message: dict) -> None:
        from ...core.geometry_service import deserialise_shape

        self._clear_preview()
        if message.get("token") is None and message.get("error") is None:
            self._preview_ok = self._edited_feature() is not None
            self.confirm.setEnabled(self._preview_ok)
            return
        error = message.get("error")
        shape = deserialise_shape(message["shape"]) if message.get("shape") else None
        if error or shape is None:
            self._preview_ok = False
            self.confirm.setEnabled(False)
            self.warn(error or "This change produces no solid.")
            return
        viewport = self.window_.stage.viewport
        viewport.show_ghost(shape, self.window_.palette_.accent, transparency=0.35)
        self._ghosted = [str(name) for name in self._source.outputs]
        for name in self._ghosted:
            viewport.set_transparency(self.window_._presentations.get(name), 0.8)
        self._preview_ok = True
        self.confirm.setEnabled(True)
        self.warn(" ".join(message.get("warnings") or []))

    def commit(self) -> None:
        document = self.window_.document
        source = self._source
        if source is None:
            return
        if document.feature(source.id) is not source or source.to_dict() != self._source_state:
            self.warn("The model changed while this was open. Open it again from History.")
            return
        feature = self._edited_feature()
        if feature is None:
            return
        if feature.to_dict() == self._source_state:
            self.window_.cancel_tool()
            return
        if not self._preview_ok:
            self.warn("Wait for the preview — or fix the value it reports.")
            return
        self._clear_preview()
        self.window_.history.record(f"Edit {source.name or source.label}")
        document.features[document.index_of(source.id)] = feature
        self.window_.invalidate({source.id})
        self.window_.mark_dirty()
        self.window_.rebuild()
        self.window_.set_hint(f"Updated {source.name or source.label}.")
        self.window_.cancel_tool()

    def teardown(self) -> None:
        self._preview_controller.close()
        self._clear_preview()
