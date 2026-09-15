"""Interactive equal-gap and fixed-gap arrangement of independent bodies."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QLabel

from ...core.document import BodyRef
from ...kernel.arrange import ArrangeFeature
from ..widgets.controls import GhostButton
from .base import FeaturePreviewController
from .modeling import _SelectionTool, _need
from .registry import register_tool


@register_tool("arrange")
class ArrangePanel(_SelectionTool):
    title = "Arrange objects"
    confirm_label = "Arrange"
    width = 316

    def build(self) -> None:
        self._preview_names = []
        self._group_offset = (0.0, 0.0, 0.0)
        self._preview_controller = FeaturePreviewController(
            self, self._previewed, delay_ms=60
        )
        names, target = self._subjects()
        if target is not None:
            self.set_subtitle(
                f"Arrange {len(names)} objects on {target.body}'s selected face."
            )
        elif names:
            self.set_subtitle(f"Arrange {len(names)} objects in a straight row.")
        else:
            self.set_subtitle("Select at least three objects, optionally with a target face.")

        self.add_section("Spacing")
        self.mode = QComboBox()
        self.mode.addItem("Equal clear gaps", "equal_gaps")
        self.mode.addItem("Fixed clear gap", "fixed_gap")
        self.mode.currentIndexChanged.connect(lambda _i: self._mode_changed())
        self.add_widget(self.mode)
        self.add_field("gap", "Clear gap", 10.0)
        self.fields["gap"].edited_live.connect(lambda _value: self.preview())

        self.add_section("Row direction")
        self.axis = QComboBox()
        self.axis.addItem("Face U — toward Right" if target is not None else "World X", "x")
        self.axis.addItem("Face V — toward Up" if target is not None else "World Y", "y")
        if target is None:
            self.axis.addItem("World Z", "z")
        self.axis.currentIndexChanged.connect(lambda _i: self.preview())
        self.add_widget(self.axis)
        if target is not None:
            directions = QLabel("← −U / Left     +U / Right →\n↓ −V / Down       +V / Up ↑")
            directions.setAlignment(Qt.AlignCenter)
            directions.setStyleSheet(
                f"color:{self._palette.text_muted}; padding:5px;"
            )
            self.add_widget(directions)

        self.orient = GhostButton("Orient support faces to target")
        self.orient.setCheckable(True)
        self.orient.setChecked(target is not None)
        self.orient.setVisible(target is not None)
        self.orient.clicked.connect(lambda: self.preview())
        self.add_widget(self.orient)
        self.add_field("normal_offset", "Stand-off", 0.0)
        normal_field = self.fields["normal_offset"]
        if normal_field.parentWidget() is not None:
            normal_field.parentWidget().setVisible(target is not None)
        self.fields["normal_offset"].edited_live.connect(lambda _value: self.preview())

        self._mode_changed()
        self.preview()
        self._attach_group_gizmo()

    def _subjects(self):
        targets = self.selection.planar_faces()
        target = targets[0] if len(targets) == 1 else None
        names = [
            name for name in self.selection.bodies
            if target is None or name != target.body
        ]
        return names, target

    def _mode_changed(self) -> None:
        self.fields["gap"].setEnabled(self.mode.currentData() == "fixed_gap")
        self.preview()

    def _inputs(self):
        names, target = self._subjects()
        inputs = {
            "bodies": [BodyRef(name) for name in names],
            "mode": str(self.mode.currentData()),
            "axis": str(self.axis.currentData()),
            "gap": self.expression("gap", "10"),
            "orient": self.orient.isChecked() if target is not None else False,
            "normal_offset": self.expression("normal_offset", "0"),
        }
        if target is not None:
            from ...kernel.align import _dot, reference_frame

            frame = reference_frame(target.shape)
            inputs.update({
                "target_face": target.reference(self.window_.document),
                "move_x": _dot(self._group_offset, frame.x_axis),
                "move_y": _dot(self._group_offset, frame.y_axis),
                "move_normal": _dot(self._group_offset, frame.normal),
                "frame_mode": "reference",
            })
        else:
            inputs.update(dict(zip(("dx", "dy", "dz"), self._group_offset)))
        return names, inputs

    def preview(self) -> None:
        self._clear_preview(keep_gizmo=True)
        names, inputs = self._inputs()
        required = 3 if self.mode.currentData() == "equal_gaps" else 2
        if len(names) < required:
            if hasattr(self, "confirm"):
                self.confirm.setEnabled(False)
            self.warn(f"Select at least {required} moving objects.")
            self._preview_controller.request(None)
            return
        viewport = self.window_.stage.viewport
        self._preview_names = list(names)
        for name in self._preview_names:
            viewport.set_transparency(self.window_._presentations.get(name), 0.82)
        if hasattr(self, "confirm"):
            self.confirm.setEnabled(False)
        self.warn("Updating arrangement…")
        feature = ArrangeFeature(inputs=inputs, outputs=list(names))
        self._preview_controller.request(feature.to_dict())

    def _previewed(self, message: dict) -> None:
        from ...core.geometry_service import deserialise_shape

        blob = message.get("shape")
        error = message.get("error")
        if not blob:
            self.window_.stage.viewport.clear_ghost()
            if error:
                self.warn(error)
            if hasattr(self, "confirm"):
                self.confirm.setEnabled(False)
            return
        self.window_.stage.viewport.show_ghost(
            deserialise_shape(blob), self.window_.palette_.accent, transparency=0.12
        )
        warnings = message.get("warnings") or []
        note = message.get("message") or "Arrangement ready."
        if warnings:
            note += " · " + " · ".join(warnings)
        self.warn(note)
        if hasattr(self, "confirm"):
            self.confirm.setEnabled(True)

    def _attach_group_gizmo(self) -> None:
        names, _target = self._subjects()
        presentations = [
            self.window_._presentations.get(name) for name in names
            if self.window_._presentations.get(name) is not None
        ]
        if not presentations:
            return
        gizmo = self.window_.attach_gizmo(
            presentations, allow_rotation=False, allow_planes=True
        )
        if gizmo is not None:
            gizmo.changed.connect(self._on_group_drag)
            gizmo.committed.connect(self._on_group_drag)

    def _on_group_drag(self, dx, dy, dz, _rx, _ry, _rz) -> None:
        self._group_offset = (dx, dy, dz)
        self.preview()

    def _clear_preview(self, *, keep_gizmo: bool = False) -> None:
        viewport = self.window_.stage.viewport
        viewport.clear_ghost()
        for name in self._preview_names:
            viewport.set_transparency(self.window_._presentations.get(name), 0.0)
        self._preview_names = []
        if not keep_gizmo:
            self.window_.detach_gizmo()

    def teardown(self) -> None:
        self._preview_controller.close()
        self._clear_preview()

    def commit(self) -> None:
        names, target = self._subjects()
        required = 3 if self.mode.currentData() == "equal_gaps" else 2
        if not _need(
            self.window_, len(names) >= required,
            f"Select at least {required} moving objects.",
        ):
            return
        _names, inputs = self._inputs()
        self._clear_preview()
        self.window_.add_feature(ArrangeFeature(inputs=inputs, outputs=list(names)))
        self.window_.cancel_tool()
