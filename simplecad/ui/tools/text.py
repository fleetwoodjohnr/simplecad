"""The face-attached raised/engraved text tool."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QGridLayout, QLineEdit, QWidget

from ...core.document import BodyRef
from ...core.units import Dimension
from ...kernel.text import TextFeature
from ..theme import METRICS
from ..widgets.controls import GhostButton
from .base import FeaturePreviewController, ToolPanel
from .registry import register_tool


@register_tool("text")
class TextPanel(ToolPanel):
    title = "Add text"
    confirm_label = "Create"
    width = 310

    def build(self) -> None:
        self.selection = self.window_.selection
        self.mode = "raised"
        self._preview_body: str | None = None
        self._preview_valid = False
        self._preview = FeaturePreviewController(
            self, self._preview_answered, delay_ms=180
        )

        self.add_section("Text")
        self.content = QLineEdit("Text")
        self.content.setMinimumHeight(METRICS.control_height)
        self.content.setMaxLength(120)
        self.content.textEdited.connect(lambda _text: self.preview())
        self.content.returnPressed.connect(self.commit)
        self.add_widget(self.content)

        self.family = QComboBox()
        self.family.addItem("Sans serif", "sans-serif")
        self.family.addItem("Serif", "serif")
        self.family.addItem("Monospace", "monospace")
        self.family.currentIndexChanged.connect(lambda _index: self.preview())
        self.add_widget(self.family)

        self.add_section("Operation")
        chooser = QWidget()
        grid = QGridLayout(chooser)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(METRICS.space(1))
        self._mode_buttons = {}
        for column, (key, label) in enumerate((
            ("raised", "Raised"), ("engraved", "Engraved"),
        )):
            button = GhostButton(label)
            button.setCheckable(True)
            button.setChecked(key == self.mode)
            button.clicked.connect(lambda _=False, value=key: self._choose_mode(value))
            grid.addWidget(button, 0, column)
            self._mode_buttons[key] = button
        self.add_widget(chooser)

        height = self.add_field("text_height", "Height", 8.0)
        depth = self.add_field("depth", "Depth", 1.0)
        self.add_section("Placement")
        offset_x = self.add_field("offset_x", "Horizontal", 0.0)
        offset_y = self.add_field("offset_y", "Vertical", 0.0)
        rotation = self.add_field("rotation", "Rotation", 0.0, Dimension.ANGLE)
        for field in (height, depth, offset_x, offset_y, rotation):
            field.edited_live.connect(lambda _value: self.preview())

        self.on_selection_changed()
        self.content.selectAll()

    def _choose_mode(self, mode: str) -> None:
        self.mode = mode
        for key, button in self._mode_buttons.items():
            button.setChecked(key == mode)
        self.preview()

    def _pick(self):
        faces = self.selection.planar_faces()
        return faces[0] if len(faces) == 1 and self.selection.count == 1 else None

    def on_selection_changed(self) -> None:
        pick = self._pick()
        if pick is None:
            self.set_subtitle("Select one flat face for the text.")
            self._preview.request(None)
            self._clear_preview()
            self._preview_valid = False
            self.confirm.setEnabled(False)
        else:
            self.set_subtitle(
                f"Centered on {pick.body}. Use offsets and rotation to place it."
            )
            self.preview()
        self.adjustSize()
        self.window_.stage._layout_overlays()

    def _inputs(self, pick) -> dict:
        return {
            "body": BodyRef(pick.body),
            "face": pick.reference(self.window_.document),
            "text": self.content.text(),
            "font_family": self.family.currentData(),
            "mode": self.mode,
            "text_height": self.expression("text_height", "8"),
            "depth": self.expression("depth", "1"),
            "offset_x": self.expression("offset_x", "0"),
            "offset_y": self.expression("offset_y", "0"),
            "rotation": self.expression("rotation", "0"),
        }

    def _feature_state(self) -> dict | None:
        pick = self._pick()
        if pick is None or not self.content.text().strip():
            return None
        return TextFeature(
            inputs=self._inputs(pick), outputs=[pick.body]
        ).to_dict()

    def preview(self) -> None:
        state = self._feature_state()
        self._preview_valid = False
        self.confirm.setEnabled(False)
        if state is None:
            self.confirm.setText(self.confirm_label)
            self._clear_preview()
            self.warn("Enter some text." if self._pick() is not None else "")
            self._preview.request(None)
            return
        self.confirm.setText("Building preview…")
        self._preview.request(state)

    def _preview_answered(self, message: dict) -> None:
        from ...core.geometry_service import deserialise_shape

        blob = message.get("shape")
        shape = deserialise_shape(blob) if blob else None
        pick = self._pick()
        if shape is None or pick is None:
            self._clear_preview()
            self.confirm.setEnabled(False)
            self.confirm.setText(self.confirm_label)
            self.warn(message.get("error") or "The text could not be built here.")
            return
        self._clear_preview()
        viewport = self.window_.stage.viewport
        self._preview_body = pick.body
        viewport.show_ghost(shape, self.window_.palette_.accent, transparency=0.12)
        viewport.set_transparency(
            self.window_._presentations.get(self._preview_body), 0.88
        )
        self._preview_valid = True
        self.confirm.setEnabled(True)
        self.confirm.setText(self.confirm_label)
        self.warn(" · ".join(message.get("warnings") or []))

    def _clear_preview(self) -> None:
        viewport = self.window_.stage.viewport
        viewport.clear_ghost()
        if self._preview_body:
            viewport.set_transparency(
                self.window_._presentations.get(self._preview_body), 0.0
            )
        self._preview_body = None

    def teardown(self) -> None:
        self._preview.close()
        self._clear_preview()

    def commit(self) -> None:
        pick = self._pick()
        if pick is None:
            self.warn("Select one flat face for the text.")
            return
        if not self._preview_valid:
            self.warn("Wait for a valid text preview before creating it.")
            return
        self.window_.add_feature(
            TextFeature(inputs=self._inputs(pick), outputs=[pick.body])
        )
        self.window_.cancel_tool()
