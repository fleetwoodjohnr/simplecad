"""Select a face, adjust printable relief, inspect it, then confirm."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QSlider, QWidget

from ...core.document import BodyRef
from ...core.units import Dimension
from ...kernel.face_texture import (
    PACKAGES, FaceTextureFeature, layout_for_face, mapping_for_face,
)
from ..widgets.texture import TextureOverlay
from .base import FeaturePreviewController, ToolPanel
from .registry import register_tool

@register_tool("face_texture")
class FaceTexturePanel(ToolPanel):
    title = "Face texture"
    confirm_label = "Apply texture"
    width = 346

    def build(self) -> None:
        self.selection = self.window_.selection
        self._preview = FeaturePreviewController(
            self, self._preview_answered, delay_ms=120, allow_in_process=False,
        )
        self._preview_body = None
        self._preview_valid = False
        self._guide_valid = False
        self._exact_failed = False
        self._mapping = None
        self._mapped_face = None
        self._edit_base_face = None
        self._editing = None
        self._loading = False
        editing_id = getattr(self.window_, "_texture_edit_feature_id", None)
        self.window_._texture_edit_feature_id = None

        self.add_section("Pattern")
        self.pattern = QComboBox()
        for package_id, definitions in PACKAGES.items():
            for pattern_id, definition in definitions.items():
                self.pattern.addItem(definition.label, (package_id, pattern_id))
        self.pattern.currentIndexChanged.connect(self._pattern_changed)
        self.add_widget(self.pattern)
        self.mode = QComboBox()
        self.add_widget(self.mode)
        self.mode.currentIndexChanged.connect(lambda _i: self.preview())

        self.add_section("Shape and placement")
        for key, label, default, dimension in (
            ("size", "Pattern size", 3.0, Dimension.LENGTH),
            ("spacing", "Spacing", 2.0, Dimension.LENGTH),
            ("depth", "Height / depth", 0.6, Dimension.LENGTH),
            ("rotation", "Rotation", 0.0, Dimension.ANGLE),
            ("offset_u", "Horizontal offset", 0.0, Dimension.LENGTH),
            ("offset_v", "Vertical offset", 0.0, Dimension.LENGTH),
        ):
            field = self.add_field(key, label, default, dimension, commit_on_return=False)
            field.edited_live.connect(lambda _value: self._field_changed())

        density_row = QWidget()
        density_layout = QHBoxLayout(density_row)
        density_layout.setContentsMargins(0, 0, 0, 0)
        self.density_label = QLabel("Density 60%")
        self.density = QSlider(Qt.Horizontal)
        self.density.setRange(10, 95)
        self.density.setValue(60)
        self.density.valueChanged.connect(self._density_changed)
        density_layout.addWidget(self.density_label)
        density_layout.addWidget(self.density, 1)
        self.add_widget(density_row)

        self.add_section("Clearances")
        for key, label, default in (
            ("edge_clearance", "Edge / border", 3.0),
            ("hole_clearance", "Holes", 2.0),
            ("text_clearance", "Text", 1.5),
            ("other_clearance", "Other detail", 2.0),
        ):
            field = self.add_field(key, label, default, commit_on_return=False)
            field.edited_live.connect(lambda _value: self.preview())

        self.overlay = TextureOverlay(self.window_.stage.viewport, self._palette, self.window_.stage)
        self.window_.stage.add_overlay(self.overlay, "full")
        self.overlay.show()
        self._pattern_changed()
        feature = self.window_.document.feature(editing_id) if editing_id else None
        if feature is not None and feature.type_name == "face_texture":
            self.load_feature(feature)
        else:
            self.on_selection_changed()

    def load_feature(self, feature) -> None:
        self._editing = feature
        self._editing_snapshot = feature.to_dict()
        self.title_label.setText("Edit texture")
        self.confirm_label = "Save changes"
        self.confirm.setText(self.confirm_label)
        self._loading = True
        choice = (str(feature.inputs.get("package", "builtin.v1")),
                  str(feature.inputs.get("pattern", "horizontal_lines")))
        # Qt stores the tuple as a Python object; QVariant matching can compare
        # its identity rather than its contents. Match package/pattern values.
        index = next((i for i in range(self.pattern.count())
                      if self.pattern.itemData(i) == choice), -1)
        if index >= 0:
            self.pattern.setCurrentIndex(index)
        self._pattern_changed()
        mode = str(feature.inputs.get("mode", "raised"))
        index = self.mode.findData(mode)
        if index >= 0:
            self.mode.setCurrentIndex(index)
        for key, field in self.fields.items():
            if key in feature.inputs:
                field.setText(str(feature.inputs[key]))
        self._loading = False
        self.set_subtitle(f"Editing texture on {feature.inputs.get('body')}.")
        self._field_changed()

    def _pick(self):
        if self._editing is not None:
            return None
        faces = self.selection.planar_faces() + self.selection.round_faces()
        return faces[0] if len(faces) == 1 and self.selection.count == 1 else None

    def _pattern_changed(self, _index=None) -> None:
        choice = self.pattern.currentData()
        if choice is None:
            return
        definition = PACKAGES[choice[0]][choice[1]]
        self.mode.blockSignals(True)
        self.mode.clear()
        self.mode.addItem("Raised", "raised")
        self.mode.addItem("Recessed", "recessed")
        if choice[1] == "vent":
            self.mode.addItem("Through wall", "through")
        selected = definition.default_mode
        self.mode.setCurrentIndex(next(i for i in range(self.mode.count()) if self.mode.itemData(i) == selected))
        self.mode.blockSignals(False)
        if not self._loading:
            self.preview()

    def _field_changed(self) -> None:
        size, spacing = self.value("size", 3.0), self.value("spacing", 2.0)
        density = round(100 * size / (size + spacing)) if size + spacing > 0 else 10
        self.density.blockSignals(True)
        self.density.setValue(max(10, min(95, density)))
        self.density.blockSignals(False)
        self.density_label.setText(f"Density {self.density.value()}%")
        self.preview()

    def _density_changed(self, value: int) -> None:
        self.density_label.setText(f"Density {value}%")
        size = self.value("size", 3.0)
        self.fields["spacing"].set_value(max(0.0, size * (100 / value - 1)))
        self.preview()

    def _settings(self) -> dict:
        package, pattern = self.pattern.currentData()
        return {
            "package": package,
            "pattern": pattern,
            "mode": self.mode.currentData(),
            **{key: self.value(key) for key in (
                "size", "spacing", "depth", "rotation", "offset_u", "offset_v",
                "edge_clearance", "hole_clearance", "text_clearance", "other_clearance",
            )},
        }

    def _inputs(self, pick) -> dict:
        package, pattern = self.pattern.currentData()
        body = BodyRef(pick.body) if pick is not None else self._editing.inputs["body"]
        face = pick.reference(self.window_.document) if pick is not None else self._editing.inputs["face"]
        return {
            "body": body, "face": face, "package": package, "pattern": pattern,
            "mode": self.mode.currentData(),
            **{key: self.expression(key) for key in (
                "size", "spacing", "depth", "rotation", "offset_u", "offset_v",
                "edge_clearance", "hole_clearance", "text_clearance", "other_clearance",
            )},
        }

    def on_selection_changed(self) -> None:
        if self._editing is not None:
            return
        pick = self._pick()
        if pick is None:
            self.set_subtitle("Select one flat or cylindrical face to texture.")
            self._guide_valid = False
            self._preview.request(None)
            self._clear_preview()
            self.overlay.set_layout(None)
            self.confirm.setEnabled(False)
            return
        self.set_subtitle(f"Texture the selected face of {pick.body}.")
        if self._mapped_face is None or not self._mapped_face.IsSame(pick.shape):
            self._mapping = None
            self._mapped_face = pick.shape
        self.preview()

    def preview(self) -> None:
        if not hasattr(self, "overlay") or self._loading:
            return
        pick = self._pick()
        if pick is None and self._editing is None:
            self._guide_valid = False
            self._preview.request(None)
            self._clear_preview()
            self.overlay.set_layout(None)
            self.confirm.setEnabled(False)
            return
        self._preview_valid = False
        self._guide_valid = False
        self._exact_failed = False
        self._clear_preview()
        self.confirm.setEnabled(False)
        self.confirm.setText(self.confirm_label)
        settings = self._settings()
        active_face = pick.shape if pick is not None else self._edit_base_face
        if active_face is None and self._editing is not None:
            state = FaceTextureFeature(
                inputs=self._inputs(None), outputs=[str(self._editing.inputs["body"])],
            ).to_dict()
            state["__edit_feature_id"] = self._editing.id
            state["__base_face_only"] = True
            self.warn("Loading the face beneath this texture…")
            self._preview.request(state)
            return
        if active_face is not None:
            try:
                self._mapping = self._mapping or mapping_for_face(active_face)
                layout = layout_for_face(
                    active_face, settings, self.window_.document,
                    pick.body if pick is not None else str(self._editing.inputs["body"]),
                    mapping=self._mapping,
                    feature_id=self._editing.id if self._editing else None,
                )
                self.overlay.set_layout(layout, {
                    "hole": settings["hole_clearance"],
                    "text": settings["text_clearance"],
                    "other": settings["other_clearance"],
                })
                self._guide_valid = True
                self.confirm.setEnabled(False)
            except Exception as exc:  # noqa: BLE001 - invalid dimensions stay editable
                self.overlay.set_layout(None)
                self._preview.request(None)
                self._clear_preview()
                self.confirm.setText(self.confirm_label)
                self.warn(str(exc))
                return
        state = FaceTextureFeature(
            inputs=self._inputs(pick), outputs=[pick.body if pick is not None else str(self._editing.inputs["body"])],
        ).to_dict()
        if self._editing is not None:
            state["__edit_feature_id"] = self._editing.id
        self.warn("Pattern guide ready; building the exact preview…")
        self._preview.request(state)

    def _preview_answered(self, message: dict) -> None:
        from ...core.geometry_service import deserialise_shape

        face_blob = (message.get("parts") or {}).get("base_face")
        if self._editing is not None and self._edit_base_face is None and face_blob:
            face = deserialise_shape(face_blob)
            if face is not None:
                self._edit_base_face = face
                self._mapping = mapping_for_face(face)
                self.preview()
                return
        if message.get("token") is None:
            return
        blob = message.get("shape")
        shape = deserialise_shape(blob) if blob else None
        if shape is None:
            self._clear_preview()
            self._exact_failed = not bool(message.get("timeout"))
            self.confirm.setEnabled(False)
            self.confirm.setText(self.confirm_label)
            if message.get("error"):
                self.warn(message["error"])
            return
        self._clear_preview()
        viewport = self.window_.stage.viewport
        name = str(self._editing.inputs["body"]) if self._editing is not None else self._pick().body
        self._preview_body = name
        viewport.show_ghost(shape, self.window_.palette_.accent, transparency=0.12)
        viewport.set_transparency(self.window_._presentations.get(name), 0.88)
        self._preview_valid = True
        self._preview_revision = message.get("revision", self.window_.geometry.latest_revision)
        self._exact_failed = False
        self.confirm.setEnabled(True)
        self.confirm.setText(self.confirm_label)
        self.warn(" · ".join(message.get("warnings") or []))
        if self._editing is not None and face_blob:
            face = deserialise_shape(face_blob)
            if face is not None:
                self._edit_base_face = face
                self._mapping = mapping_for_face(face)
                try:
                    layout = layout_for_face(face, self._settings(), self.window_.document, name, mapping=self._mapping, feature_id=self._editing.id if self._editing else None)
                    self.overlay.set_layout(layout, {
                        "hole": self.value("hole_clearance"),
                        "text": self.value("text_clearance"),
                        "other": self.value("other_clearance"),
                    })
                except Exception:
                    self.overlay.set_layout(None)

    def _clear_preview(self) -> None:
        viewport = self.window_.stage.viewport
        viewport.clear_ghost()
        if self._preview_body is not None:
            viewport.set_transparency(self.window_._presentations.get(self._preview_body), 0.0)
        self._preview_body = None

    def teardown(self) -> None:
        self._preview.close()
        self._clear_preview()
        self.window_.stage.remove_overlay(self.overlay)
        self.overlay.deleteLater()

    def commit(self) -> None:
        pick = self._pick()
        if self._editing is None and pick is None:
            self.warn("Select one flat or cylindrical face.")
            return
        if not self._guide_valid or not self._preview_valid or self._exact_failed:
            self.warn("Choose a valid texture layout before applying it.")
            return
        if not self.window_.geometry.available:
            self.warn("The geometry engine must be running to build this texture.")
            return
        if getattr(self, "_preview_revision", -1) != self.window_.geometry.latest_revision:
            self.preview()
            self.warn("The model changed; updating the texture preview.")
            return
        if self._editing is not None:
            feature = self.window_.document.feature(self._editing.id)
            if (feature is not self._editing or feature.type_name != "face_texture"
                    or feature.to_dict() != self._editing_snapshot):
                self._preview.request(None)
                self._clear_preview()
                self.confirm.setEnabled(False)
                self.warn("This texture changed or was removed. Close the editor and select it again.")
                return
            with self.window_.document.transaction(f"Edit Texture on {self._editing.inputs['body']}"):
                self._editing.inputs = self._inputs(None)
            self.window_.invalidate({self._editing.id})
            self.window_.mark_dirty()
            self.window_.rebuild()
        else:
            self.window_.add_feature(FaceTextureFeature(inputs=self._inputs(pick), outputs=[pick.body]))
        self.window_.cancel_tool()
