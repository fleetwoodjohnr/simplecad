"""Interactive placement for reusable internal clip joints."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget,
)

from ...core.document import BodyRef
from ...core.units import Dimension
from ...kernel.clips import (
    MAX_CONNECTORS, RETENTION_PRESETS, SIZE_PRESETS, ClipJointFeature,
    layout_points,
)
from ...kernel.align import planar_frame
from ..panels.clip_overlay import ClipPlacementOverlay
from ..theme import METRICS
from ..widgets.controls import GhostButton
from .base import FeaturePreviewController
from .modeling import _SelectionTool, _need
from .registry import register_tool


def _spin(minimum: int, maximum: int, value: int) -> QSpinBox:
    control = QSpinBox()
    control.setRange(minimum, maximum)
    control.setValue(value)
    control.setMinimumHeight(METRICS.control_height)
    return control


@register_tool("clip_joint")
class ClipJointPanel(_SelectionTool):
    """Place one or many removable double-ended clips between two parts."""

    title = "Clip joint"
    confirm_label = "Align and clip"
    width = 336

    def build(self) -> None:
        self.moving = None
        self.target = None
        self.anchors: list[tuple[float, float]] = []
        self._preview_ok = False
        self._preview_names: list[str] = []
        self._signals_connected = False

        self.marker = ClipPlacementOverlay(self._palette, self.window_.stage)
        self.marker.attach(self.window_.stage.viewport.project)
        self.window_.stage.add_overlay(self.marker, "full")
        self.marker.show()

        self.set_subtitle(
            "Select one flat face on each part. The first part moves; the second "
            "face receives your clip locations."
        )

        self.add_section("Placement")
        self.layout_mode = QComboBox()
        self.layout_mode.addItem("Manual points", "manual")
        self.layout_mode.addItem("Uniform row", "row")
        self.layout_mode.addItem("Uniform grid", "grid")
        self.layout_mode.currentIndexChanged.connect(self._layout_changed)
        self.add_widget(self.layout_mode)

        self.row_host = QWidget()
        row_grid = QGridLayout(self.row_host)
        row_grid.setContentsMargins(0, 0, 0, 0)
        row_grid.setSpacing(METRICS.space(1))
        row_grid.addWidget(QLabel("Count"), 0, 0)
        self.count = _spin(1, MAX_CONNECTORS, 3)
        row_grid.addWidget(self.count, 0, 1)
        self.spacing_mode = QComboBox()
        self.spacing_mode.addItem("Equal between anchors", "equal")
        self.spacing_mode.addItem("Fixed center spacing", "fixed")
        row_grid.addWidget(self.spacing_mode, 1, 0, 1, 2)
        self.add_widget(self.row_host)
        self.spacing = self.add_field("spacing", "Center spacing", 10.0)

        self.grid_host = QWidget()
        grid = QGridLayout(self.grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(METRICS.space(1))
        grid.addWidget(QLabel("Rows"), 0, 0)
        self.rows = _spin(1, 64, 2)
        grid.addWidget(self.rows, 0, 1)
        grid.addWidget(QLabel("Columns"), 1, 0)
        self.columns = _spin(1, 64, 2)
        grid.addWidget(self.columns, 1, 1)
        self.add_widget(self.grid_host)

        point_actions = QHBoxLayout()
        undo = GhostButton("Undo point")
        undo.clicked.connect(self._undo_point)
        clear = GhostButton("Clear points")
        clear.clicked.connect(self._clear_points)
        point_actions.addWidget(undo)
        point_actions.addWidget(clear)
        point_host = QWidget()
        point_host.setLayout(point_actions)
        self.add_widget(point_host)

        self.add_section("Clip")
        self.size = QComboBox()
        for key in ("small", "medium", "large"):
            self.size.addItem(key.title(), key)
        self.size.setCurrentIndex(1)
        self.add_widget(self.size)
        self.material = QComboBox()
        self.material.addItem("PLA", "pla")
        self.material.addItem("PETG", "petg")
        self.material.setCurrentIndex(1)
        self.add_widget(self.material)
        self.retention = QComboBox()
        for key in ("easy", "standard", "firm"):
            self.retention.addItem(key.title(), key)
        self.retention.setCurrentIndex(1)
        self.add_widget(self.retention)
        self.clearance = QComboBox()
        self.clearance.addItem("Press fit (calibrated)", "press")
        self.clearance.addItem("Snug fit (calibrated)", "snug")
        self.clearance.addItem("Sliding fit (calibrated)", "sliding")
        self.clearance.setCurrentIndex(1)
        self.add_widget(self.clearance)

        swap = GhostButton("Swap moving / target")
        swap.clicked.connect(self._swap)
        self.add_widget(swap)

        self.advanced = GhostButton("Advanced dimensions")
        self.advanced.setCheckable(True)
        self.advanced.clicked.connect(self._toggle_advanced)
        self.add_widget(self.advanced)
        self.advanced_host = QWidget()
        self.advanced_layout = QVBoxLayout(self.advanced_host)
        self.advanced_layout.setContentsMargins(0, 0, 0, 0)
        self.advanced_layout.setSpacing(METRICS.space(1.5))
        for key, label, value, dimension in (
            ("engagement", "Engagement / end", 8.0, Dimension.LENGTH),
            ("arm_width", "Arm width", 1.8, Dimension.LENGTH),
            ("slot", "Center slot", 1.0, Dimension.LENGTH),
            ("thickness", "Clip thickness", 3.0, Dimension.LENGTH),
            ("barb", "Barb projection", 0.6, Dimension.LENGTH),
            ("removal_angle", "Removal ramp", 35.0, Dimension.ANGLE),
            ("clearance_exact", "Exact clearance", 0.15, Dimension.LENGTH),
            ("rotation", "Connector rotation", 0.0, Dimension.ANGLE),
            ("align_x", "Alignment X offset", 0.0, Dimension.LENGTH),
            ("align_y", "Alignment Y offset", 0.0, Dimension.LENGTH),
        ):
            field = self.add_field(key, label, value, dimension, self.advanced_layout)
            field.edited_live.connect(lambda _value: self.preview())
        self.add_widget(self.advanced_host)
        self.advanced_host.hide()

        for combo in (
            self.size, self.material, self.retention, self.clearance,
        ):
            combo.currentIndexChanged.connect(self._preset_changed)
        self.count.valueChanged.connect(lambda _value: self.preview())
        self.rows.valueChanged.connect(lambda _value: self.preview())
        self.columns.valueChanged.connect(lambda _value: self.preview())
        self.spacing_mode.currentIndexChanged.connect(self._spacing_changed)
        self.spacing.edited_live.connect(lambda _value: self.preview())

        self._preview_controller = FeaturePreviewController(
            self, self._preview_answer, delay_ms=120
        )
        self._connect_viewport()
        self._latch_pair()
        self._layout_controls()
        self._sync_advanced()
        self.preview()

    # -- selected face pair --------------------------------------------
    def _latch_pair(self) -> bool:
        faces = self.selection.planar_faces()
        if len(faces) != 2 or faces[0].body == faces[1].body:
            self.confirm.setEnabled(False)
            return False
        self.moving, self.target = faces
        self.frame = planar_frame(self.target.shape)
        self.set_subtitle(
            f"{self.moving.body} will be seated onto {self.target.body}. "
            f"Click {self.target.body}'s selected face to place clips."
        )
        self.window_.stage.viewport.begin_point_pick()
        self.window_.set_hint(self._placement_hint())
        return True

    def on_selection_changed(self) -> None:
        if self.moving is None and self._latch_pair():
            self.preview()

    def _swap(self) -> None:
        if self.moving is None or self.target is None:
            return
        self.moving, self.target = self.target, self.moving
        self.frame = planar_frame(self.target.shape)
        self.anchors.clear()
        self.set_subtitle(
            f"{self.moving.body} will be seated onto {self.target.body}. "
            f"Click {self.target.body}'s selected face to place clips."
        )
        self.preview()

    # -- point collection ---------------------------------------------
    def _connect_viewport(self) -> None:
        if self._signals_connected:
            return
        viewport = self.window_.stage.viewport
        viewport.snap_hovered.connect(self._snap_hovered)
        viewport.snap_picked.connect(self._snap_picked)
        viewport.view_changed.connect(self.marker.update)
        self._signals_connected = True

    def _local_point(self, snap):
        if self.target is None or snap is None:
            return None
        delta = tuple(
            snap.position[index] - self.frame.center[index] for index in range(3)
        )
        distance = sum(delta[index] * self.frame.normal[index] for index in range(3))
        if abs(distance) > 0.25:
            return None
        return (
            sum(delta[index] * self.frame.x_axis[index] for index in range(3)),
            sum(delta[index] * self.frame.y_axis[index] for index in range(3)),
        )

    def _snap_hovered(self, snap) -> None:
        local = self._local_point(snap)
        self.marker.hover = self.frame.point(*local) if local is not None else None
        self.marker.update()

    def _snap_picked(self, snap) -> None:
        local = self._local_point(snap)
        if local is None:
            self.warn(f"Place clips on {self.target.body}'s selected flat face.")
            return
        mode = str(self.layout_mode.currentData())
        if mode == "manual":
            if len(self.anchors) >= MAX_CONNECTORS:
                self.warn(f"A joint supports at most {MAX_CONNECTORS} clips.")
                return
            self.anchors.append(local)
        elif len(self.anchors) < 2:
            self.anchors.append(local)
        else:
            self.anchors[-1] = local
        self.preview()
        self.window_.set_hint(self._placement_hint())

    def _placement_hint(self) -> str:
        mode = str(self.layout_mode.currentData())
        if mode == "manual":
            return "Click clip centers on the target face; use Clear or Undo to edit."
        if not self.anchors:
            return "Click the first layout anchor on the target face."
        if len(self.anchors) == 1:
            return "Click the opposite endpoint or corner."
        return "Move the anchors with Undo/Clear, or adjust the uniform spacing controls."

    def _undo_point(self) -> None:
        if self.anchors:
            self.anchors.pop()
        self.preview()

    def _clear_points(self) -> None:
        self.anchors.clear()
        self.preview()

    # -- controls and feature state -----------------------------------
    def _layout_changed(self, _index=0) -> None:
        self.anchors.clear()
        self._layout_controls()
        self.preview()

    def _layout_controls(self) -> None:
        mode = str(self.layout_mode.currentData())
        self.row_host.setVisible(mode == "row")
        self.grid_host.setVisible(mode == "grid")
        self.spacing.parentWidget().setVisible(mode == "row")
        self._spacing_changed()
        self.relayout()

    def _spacing_changed(self, _index=0) -> None:
        self.spacing.setEnabled(self.spacing_mode.currentData() == "fixed")
        self.preview()

    def _preset_changed(self, _index=0) -> None:
        if self.advanced.isChecked():
            self._sync_advanced()
        self.preview()

    def _toggle_advanced(self) -> None:
        self.advanced_host.setVisible(self.advanced.isChecked())
        if self.advanced.isChecked():
            self._sync_advanced()
        self.relayout()
        self.preview()

    def _sync_advanced(self) -> None:
        values = dict(SIZE_PRESETS[str(self.size.currentData())])
        retention = RETENTION_PRESETS[str(self.retention.currentData())]
        if self.material.currentData() == "pla":
            values["engagement"] *= 1.25
            values["barb"] *= 0.65
        values["barb"] *= retention["barb_scale"]
        values["removal_angle"] = retention["removal_angle"]
        for key in (
            "engagement", "arm_width", "slot", "thickness", "barb",
            "removal_angle",
        ):
            self.fields[key].set_value(values[key])
        from ...kernel.calibration import effective_fits
        from ...kernel.thread_specs import printer_profile

        clearance = effective_fits(printer_profile()["id"])[
            str(self.clearance.currentData())
        ]
        self.fields["clearance_exact"].set_value(clearance)

    def _points(self):
        return layout_points(
            str(self.layout_mode.currentData()), self.anchors,
            count=self.count.value(), rows=self.rows.value(),
            columns=self.columns.value(),
            spacing_mode=str(self.spacing_mode.currentData()),
            spacing=self.value("spacing", 10.0),
        )

    def _connector_names_for(self, count: int) -> list[str]:
        taken = set(self.window_.document.bodies) | set(self.window_.document.groups)
        names = []
        suffix = 1
        while len(names) < count:
            candidate = "Clip" if suffix == 1 else f"Clip{suffix}"
            suffix += 1
            if candidate not in taken:
                names.append(candidate)
                taken.add(candidate)
        return names

    def _feature(self) -> ClipJointFeature | None:
        if self.moving is None or self.target is None:
            return None
        points = self._points()
        names = self._connector_names_for(len(points))
        inputs = {
            "body_a": BodyRef(self.moving.body),
            "body_b": BodyRef(self.target.body),
            "face_a": self.moving.reference(self.window_.document),
            "face_b": self.target.reference(self.window_.document),
            "layout": str(self.layout_mode.currentData()),
            "anchors": [list(point) for point in self.anchors],
            "count": self.count.value(),
            "rows": self.rows.value(),
            "columns": self.columns.value(),
            "spacing_mode": str(self.spacing_mode.currentData()),
            "spacing": self.expression("spacing", "10"),
            "size": str(self.size.currentData()),
            "material": str(self.material.currentData()),
            "retention": str(self.retention.currentData()),
            "clearance": str(self.clearance.currentData()),
            "rotation": 0.0,
            "align_x": 0.0,
            "align_y": 0.0,
            "connector_names": names,
        }
        if self.advanced.isChecked():
            for key in (
                "engagement", "arm_width", "slot", "thickness", "barb",
                "removal_angle", "rotation", "align_x", "align_y",
            ):
                inputs[key] = self.expression(key)
            inputs["clearance"] = self.expression("clearance_exact")
        return ClipJointFeature(
            inputs=inputs,
            outputs=[self.moving.body, self.target.body, *names],
        )

    # -- preview / commit ---------------------------------------------
    def preview(self) -> None:
        if not hasattr(self, "_preview_controller"):
            return
        try:
            points = self._points()
        except Exception as exc:  # noqa: BLE001 - controls remain editable
            self._show_markers([], False)
            self.warn(str(exc))
            self.confirm.setEnabled(False)
            self._preview_controller.request(None)
            return
        self._show_markers(points, None)
        feature = self._feature()
        if feature is None or not points:
            self.confirm.setEnabled(False)
            self._preview_controller.request(None)
            self.warn("Click at least one clip location on the target face.")
            return
        self._preview_ok = False
        self.confirm.setEnabled(False)
        self.warn("Checking sockets…")
        self._preview_controller.request(feature.to_dict())

    def _show_markers(self, points, valid) -> None:
        if self.target is None:
            self.marker.set_points([])
            return
        world = [self.frame.point(point[0], point[1]) for point in points]
        states = (
            list(valid) if isinstance(valid, (list, tuple))
            else [valid] * len(world) if isinstance(valid, bool) else None
        )
        self.marker.set_points(world, states)

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
        if message.get("token") is None and error is None and blob is None:
            return
        if error or not blob:
            self._preview_ok = False
            self.confirm.setEnabled(False)
            points = self._points()
            states = [False] * len(points)
            # Kernel validation names the exact bad placement. Keep the valid
            # markers blue and turn only that one red, so a large grid remains
            # editable instead of becoming an undifferentiated failure.
            if error:
                import re

                match = re.search(r"Clip (\d+)", str(error))
                if match and 1 <= int(match.group(1)) <= len(points):
                    states = [True] * len(points)
                    states[int(match.group(1)) - 1] = False
            self._show_markers(points, states)
            self.warn(error or "The clip preview produced no geometry.")
            return
        from ...core.geometry_service import deserialise_shape

        shape = deserialise_shape(blob)
        if shape is None:
            self._preview_ok = False
            self.confirm.setEnabled(False)
            self.warn("The clip preview could not be displayed.")
            return
        viewport = self.window_.stage.viewport
        viewport.show_ghost(shape, self.window_.palette_.accent, transparency=0.1)
        self._preview_names = [self.moving.body, self.target.body]
        for name in self._preview_names:
            viewport.set_transparency(self.window_._presentations.get(name), 0.84)
        self._preview_ok = True
        self.confirm.setEnabled(True)
        count = len(self._points())
        self._show_markers(self._points(), True)
        self.warn(f"{count} clip{'s' if count != 1 else ''} ready to create.")

    def commit(self) -> None:
        if not _need(
            self.window_, self._preview_ok,
            "Place valid clip locations and wait for the preview check.",
        ):
            return
        feature = self._feature()
        if feature is None:
            return
        self._clear_preview()
        self.window_.add_feature(feature)
        self.window_.cancel_tool()

    def teardown(self) -> None:
        self._preview_controller.close()
        self._clear_preview()
        viewport = self.window_.stage.viewport
        viewport.end_point_pick()
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
