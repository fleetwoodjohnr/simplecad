"""Edit or remove one previously finished edge without replaying the timeline."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox

from ...core.units import Dimension
from ...kernel.operations import EdgeFinishFeature, edge_finish_specs
from ..widgets.controls import GhostButton
from .base import FeaturePreviewController, ToolPanel
from .registry import register_tool


@register_tool("edit_edge_finish")
class EdgeEditPanel(ToolPanel):
    title = "Edit edge finish"
    confirm_label = "Save edge"

    def build(self) -> None:
        self._source = None
        self._source_state = None
        self._index = 0
        self._loading = True
        self._valid = False
        self._remove = False
        self._remove_when_valid = False
        self._preview_revision = -1
        self._preview = FeaturePreviewController(self, self._answered, delay_ms=100)
        self.confirm.setEnabled(False)
        self.set_subtitle("Finding the feature that made this edge…")

        self.edge_choice = QComboBox()
        self.edge_choice.currentIndexChanged.connect(self._edge_changed)
        self.add_widget(self.edge_choice)
        self.kind = QComboBox()
        self.kind.addItem("Fillet", "fillet")
        self.kind.addItem("Chamfer", "chamfer")
        self.kind.currentIndexChanged.connect(self._mode_changed)
        self.add_widget(self.kind)
        self.mode = QComboBox()
        self.mode.addItem("Equal distance", "equal")
        self.mode.addItem("Two distances", "two_distances")
        self.mode.addItem("Distance and angle", "distance_angle")
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.add_widget(self.mode)
        self.add_field("radius", "Radius", 2.0, commit_on_return=False)
        self.add_field("distance", "Distance", 1.0, commit_on_return=False)
        self.add_field("distance2", "Second distance", 1.0, commit_on_return=False)
        self.add_field("angle", "Angle", 45.0, Dimension.ANGLE, commit_on_return=False)
        for field in self.fields.values():
            field.edited_live.connect(lambda _value: self.preview())
        remove = GhostButton("Remove from this edge")
        remove.clicked.connect(self.remove_edge)
        self.add_widget(remove)
        self._remove_button = remove
        self._mode_changed()

        chosen_id = getattr(self.window_, "_edge_edit_feature_id", None)
        self.window_._edge_edit_feature_id = None
        feature = self.window_.document.feature(chosen_id) if chosen_id else None
        if feature is not None and edge_finish_specs(feature):
            self._load_feature(feature, 0)
            return
        picks = self.window_.selection.picks
        if len(picks) != 1 or picks[0].kind not in {"edge", "face"}:
            self.warn("Select one finished edge or blend face.")
            return
        pick = picks[0]
        self._preview.request({"__inspect_edge": {
            "body": str(pick.body), "pick": pick.reference(self.window_.document).to_dict(),
        }})

    def _load_feature(self, feature, index: int) -> None:
        self._source = feature
        self._source_state = feature.to_dict()
        specs = edge_finish_specs(feature)
        self._loading = True
        self.edge_choice.clear()
        for position in range(len(specs)):
            self.edge_choice.addItem(f"Edge {position + 1}", position)
        self.edge_choice.setVisible(len(specs) > 1)
        self.edge_choice.setCurrentIndex(index)
        self._index = index
        self._load_spec(specs[index])
        self._loading = False
        self.set_subtitle(f"Editing edge {index + 1} of {feature.name}.")
        self.preview()

    def _load_spec(self, spec) -> None:
        self._loading = True
        self.kind.setCurrentIndex(0 if spec.get("type") == "fillet" else 1)
        mode = spec.get("mode", "equal")
        self.mode.setCurrentIndex(max(0, self.mode.findData(mode)))
        for key, default in (("radius", 2.0), ("distance", 1.0),
                             ("distance2", 1.0), ("angle", 45.0)):
            value = spec.get(key, default)
            self.fields[key].setText(str(value))
        self._loading = False
        self._mode_changed()

    def _edge_changed(self, index: int) -> None:
        if self._loading or self._source is None or index < 0:
            return
        self._index = index
        self._load_spec(edge_finish_specs(self._source)[index])
        self.preview()

    def _mode_changed(self, *_args) -> None:
        kind = self.kind.currentData()
        mode = self.mode.currentData()
        self.mode.setVisible(kind == "chamfer")
        self.fields["radius"].parentWidget().setVisible(kind == "fillet")
        self.fields["distance"].parentWidget().setVisible(kind == "chamfer")
        self.fields["distance2"].parentWidget().setVisible(kind == "chamfer" and mode == "two_distances")
        self.fields["angle"].parentWidget().setVisible(kind == "chamfer" and mode == "distance_angle")
        if not self._loading:
            self.preview()

    def _edited_feature(self, *, remove=False):
        source = self._source
        if source is None:
            return None
        specs = [dict(spec) for spec in edge_finish_specs(source)]
        if remove:
            del specs[self._index]
        else:
            old = specs[self._index]
            kind = self.kind.currentData()
            spec = {"edge": old["edge"], "type": kind}
            if kind == "fillet":
                spec["radius"] = self.expression("radius", "2")
            else:
                mode = self.mode.currentData()
                spec.update(mode=mode, distance=self.expression("distance", "1"))
                if mode == "two_distances":
                    spec["distance2"] = self.expression("distance2", "1")
                elif mode == "distance_angle":
                    spec["angle"] = self.expression("angle", "45")
                if mode != "equal" and old.get("side") is not None:
                    spec["side"] = old["side"]
            specs[self._index] = spec
        feature = EdgeFinishFeature(
            feature_id=source.id, name=source.name,
            inputs={"body": source.inputs["body"], "edge_specs": specs},
            outputs=list(source.outputs),
        )
        feature.bindings = dict(source.bindings)
        feature.output_ids = dict(source.output_ids)
        feature.suppressed = source.suppressed
        feature.dropped = list(source.dropped)
        return feature

    def preview(self) -> None:
        if self._loading or self._source is None:
            return
        self._valid = False
        self.confirm.setEnabled(False)
        feature = self._edited_feature()
        state = feature.to_dict()
        state["__edit_feature_id"] = self._source.id
        self._preview_revision = self.window_.geometry.latest_revision
        self.warn("Building the updated edge…")
        self._preview.request(state)

    def _answered(self, message: dict) -> None:
        if message.get("inspection"):
            info = message["inspection"]
            feature = self.window_.document.feature(info["feature_id"])
            if feature is not None:
                self._load_feature(feature, info["edge_index"])
            return
        if self._source is None:
            if message.get("error"):
                self.warn(message["error"])
            return
        from ...core.geometry_service import deserialise_shape

        self._valid = not message.get("error") and bool(message.get("shape"))
        if self._valid:
            shape = deserialise_shape(message["shape"])
            if shape is not None:
                self.window_.stage.viewport.show_ghost(shape, self._palette.accent, transparency=0.2)
            self.warn("")
            self.confirm.setEnabled(True)
            if self._remove_when_valid:
                self._remove_when_valid = False
                self._commit_remove()
        else:
            self.warn(message.get("error") or "This edge setting cannot be built.")

    def remove_edge(self) -> None:
        if self._source is None:
            return
        remaining = self._edited_feature(remove=True)
        self._remove = True
        self._remove_when_valid = True
        self._valid = False
        self.confirm.setEnabled(False)
        self._preview_revision = self.window_.geometry.latest_revision
        state = remaining.to_dict()
        state["__edit_feature_id"] = self._source.id
        if not remaining.inputs["edge_specs"]:
            state["__remove_edge_finish"] = True
        self.warn("Checking the edge without this finish…")
        self._preview.request(state)

    def _commit_remove(self) -> None:
        self._commit_feature(remove=True)

    def commit(self) -> None:
        self._commit_feature(remove=False)

    def _commit_feature(self, *, remove: bool) -> None:
        source = self._source
        if source is None or not self._valid:
            self.warn("Wait for a valid preview before saving.")
            return
        document = self.window_.document
        if (document.feature(source.id) is not source or source.to_dict() != self._source_state
                or self._preview_revision != self.window_.geometry.latest_revision):
            self.warn("The model changed while this edge was being edited. Reopen the editor.")
            return
        replacement = self._edited_feature(remove=remove)
        self.window_.history.record("Remove edge finish" if remove else "Edit edge finish")
        if remove and not replacement.inputs["edge_specs"]:
            document.remove_feature(source.id)
        else:
            document.features[document.index_of(source.id)] = replacement
        self.window_.invalidate({source.id})
        self.window_.mark_dirty()
        self.window_.rebuild()
        self.window_.cancel_tool()

    def teardown(self) -> None:
        self._preview.close()
        self.window_.stage.viewport.clear_ghost()
