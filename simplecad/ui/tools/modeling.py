"""The tools that do the modelling: Pull, Move, Align, Hole, Thread, Fillet.

Each reads the current selection, offers only the numbers that matter, and
commits a parametric feature. Nothing here opens a modal dialog and nothing
asks the user for information the geometry already carries -- a thread tool that
has been handed a cylindrical face knows the diameter and whether it is a hole.
"""

from __future__ import annotations

import itertools
import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel,
    QVBoxLayout, QWidget,
)

from ...core.document import BodyRef
from ...core.units import Dimension
from ...kernel.operations import (
    AlignFeature, ChamferFeature, FilletFeature, HoleFeature, MoveFeature,
    PushPullFeature, RoundPushPullFeature, ScaleFeature, ShellFeature,
    ThreadedConnectionFeature, ThreadFeature,
)
from ...kernel.section_replace import SectionReplaceFeature
from ...kernel.thread_specs import (
    clearance_presets, effective_clearance_for, recommend,
)
from ..theme import METRICS
from ..widgets.controls import AnchorGrid, GhostButton
from .base import FeaturePreviewController, ToolPanel
from ...kernel.vent import VentCutFeature
from .registry import register_tool


#: Preview requests are numbered across the whole session rather than per panel.
#: A panel closed mid-drag and reopened would otherwise start counting again and
#: could accept the previous panel's late answer as its own.
_preview_tokens = itertools.count(1)


def _need(window, test: bool, message: str) -> bool:
    if not test:
        window.set_hint(message)
        return False
    return True


def _resize_text(size, diameter: float, internal: bool) -> str:
    """A size, and what picking it would do to the face it lands on.

    The second half is the part that matters: a size the face does not already
    suit is still a perfectly good answer, but only if the user can see what it
    costs before choosing it.
    """
    change = size.diameter - diameter
    what = "hole" if internal else "shaft"
    if change > 0.05:
        effect = (
            f"opens the {what} {change:.2f} mm" if internal
            else f"builds the {what} up {change:.2f} mm"
        )
    elif change < -0.05:
        effect = (
            f"fills the {what} back {abs(change):.2f} mm" if internal
            else f"turns the {what} down {abs(change):.2f} mm"
        )
    else:
        effect = "fits as it is"
    return f"{size.designation}  ·  {size.pitch:.2f} mm pitch  ·  {effect}"


def _clearance_text(key: str, entry: dict, *, hint: bool = False) -> str:
    """The active printer value, stated in both useful conventions."""
    value = effective_clearance_for(key)
    text = f"{entry['label']} — {value:.2f} mm diameter · {value / 2.0:.2f} per side"
    return f"{text} · {entry['hint']}" if hint else text


class _SelectionTool(ToolPanel):
    """A tool that acts on whatever is selected when it opens -- or after.

    Panels are built once, at activation (see ``registry.activate``), so a tool
    opened *before* its subject was picked used to sit there reading "select a
    face" for as long as it was open, and then commit whatever that amounted to
    -- which for Thread was a feature carrying no size at all. The selection is
    a live input, so :meth:`on_selection_changed` is called whenever it moves.

    The window drives it (``MainWindow._on_selection``) rather than the panel
    connecting to the viewport itself: ``close_tool_panels`` takes a panel out
    of the overlay list before tearing it down, so notification stops at exactly
    the right moment without every subclass having to remember to disconnect.
    """

    def __init__(self, window, palette=None) -> None:
        self.selection = window.selection
        super().__init__(window, palette)

    def on_selection_changed(self) -> None:
        """Re-read the selection into the panel. Subclasses override."""


# ----------------------------------------------------------------------
@register_tool("pushpull")
class PushPullPanel(_SelectionTool):
    """Pull a face. One command, because there is one gesture.

    A flat face moves along its normal and asks for a distance. A round face
    moves along its radius and asks for a diameter, because nobody thinks about
    a shaft in terms of how far its surface travelled -- they think about how
    thick it ends up. Both are the same idea and the same panel, and asking the
    user which one they meant would be asking about something the selection
    already says.
    """

    title = "Pull face"
    confirm_label = "Pull"

    def build(self) -> None:
        self._direction_added = False
        self.pick = self._pick()
        if self.pick is None:
            self.set_subtitle("Select a flat face, or the side of a shaft or hole.")
            self.add_field("distance", "Distance", 5.0)
            self.fields["distance"].edited_live.connect(lambda _value: self.preview())
            return
        if self.pick.is_round_face:
            info = self.pick.info
            self.set_subtitle(
                f"{self.pick.describe()} — set the diameter it should end up."
                + self._wall_note(info)
            )
            self.add_field("diameter", "Diameter", round(info.diameter, 3))
            self.fields["diameter"].edited_live.connect(lambda _value: self.preview())
        else:
            self.set_subtitle(
                f"{self.pick.describe()} — positive adds material, negative cuts."
            )
            self.add_field("distance", "Distance", 5.0)
            self.fields["distance"].edited_live.connect(lambda _value: self.preview())
        self._ensure_direction()
        self.preview()

    def _ensure_direction(self) -> None:
        if self._direction_added:
            return
        self._direction_added = True
        self.direction = QLabel(
            "↑ increases along the face normal by 0.25 mm\n"
            "↓ decreases by 0.25 mm"
        )
        self.direction.setAlignment(Qt.AlignCenter)
        self.direction.setStyleSheet(f"color:{self._palette.text_muted}; padding:5px;")
        self.add_widget(self.direction)

    def _remove_field(self, key: str) -> None:
        field = self.fields.pop(key, None)
        if field is None:
            return
        row = field.parentWidget()
        if row is not None:
            self.body.removeWidget(row)
            row.deleteLater()

    def on_selection_changed(self) -> None:
        pick = self._pick()
        if pick is None:
            self._clear_preview()
            self.pick = None
            self.set_subtitle("Select a flat face, or the side of a shaft or hole.")
            return
        changed = self.pick is None or not pick.shape.IsSame(self.pick.shape)
        if not changed:
            return
        self._clear_preview()
        self.pick = pick
        if pick.is_round_face:
            self._remove_field("distance")
            if "diameter" not in self.fields:
                field = self.add_field("diameter", "Diameter", round(pick.info.diameter, 3))
                field.edited_live.connect(lambda _value: self.preview())
            else:
                self.fields["diameter"].set_value(round(pick.info.diameter, 3))
            self.set_subtitle(
                f"{pick.describe()} — set the diameter it should end up."
                + self._wall_note(pick.info)
            )
        else:
            self._remove_field("diameter")
            if "distance" not in self.fields:
                field = self.add_field("distance", "Distance", 5.0)
                field.edited_live.connect(lambda _value: self.preview())
            else:
                self.fields["distance"].set_value(5.0)
            self.set_subtitle(
                f"{pick.describe()} — positive adds material, negative cuts."
            )
        self._ensure_direction()
        self.relayout()
        self.preview()

    def handle_arrow_key(self, key: int) -> bool:
        if self.pick is None or key not in (Qt.Key_Up, Qt.Key_Down):
            return False
        sign = 1.0 if key == Qt.Key_Up else -1.0
        if self.pick.is_round_face:
            # The editor displays diameter; 0.25 mm of radial surface travel is
            # exactly 0.50 mm of diameter.
            field = self.fields["diameter"]
            field.set_value(field.value() + sign * 0.5)
        else:
            field = self.fields["distance"]
            field.set_value(field.value() + sign * 0.25)
        self.preview()
        return True

    def preview(self) -> None:
        pick = getattr(self, "pick", None)
        if pick is None:
            return
        if pick.is_round_face:
            surface_delta = (
                self.value("diameter", pick.info.diameter) - pick.info.diameter
            ) / 2.0
            material_delta = (
                -surface_delta if pick.info.internal else surface_delta
            )
        else:
            surface_delta = self.value("distance", 0.0)
            material_delta = surface_delta
        try:
            shape = self.window_._pull_preview(pick, surface_delta)
        except BaseException:  # noqa: BLE001 - OCCT raises non-Exceptions
            return
        viewport = self.window_.stage.viewport
        viewport.show_ghost(
            shape,
            self.window_.palette_.danger
            if material_delta < 0 else self.window_.palette_.accent,
            transparency=0.42,
        )
        viewport.set_transparency(
            self.window_._presentations.get(pick.body),
            0.62 if material_delta < 0 else 0.0,
        )
        self.window_._show_drag_readout(
            pick,
            surface_delta,
            at=self.window_._pull_readout_anchor(pick),
            material_change=material_delta,
        )
        self.window_.set_hint(
            "Face normal: ↑ +0.25 mm  ↓ −0.25 mm  ·  "
            f"current {surface_delta:+.2f} mm"
        )

    def _clear_preview(self) -> None:
        viewport = self.window_.stage.viewport
        viewport.clear_ghost()
        self.window_.stage.drag_readout.finish()
        pick = getattr(self, "pick", None)
        if pick is not None:
            viewport.set_transparency(
                self.window_._presentations.get(pick.body), 0.0
            )

    def teardown(self) -> None:
        self._clear_preview()

    def _pick(self):
        faces = self.selection.planar_faces() or self.selection.round_faces()
        return faces[0] if faces else None

    def _wall_note(self, info) -> str:
        """How thick the wall would be, when there is a wall to speak of."""
        from ...kernel.operations import _coaxial_faces

        body = self.window_.document.body(self.pick.body)
        if body is None or body.shape is None:
            return ""
        others = _coaxial_faces(body.shape, info, internal=not info.internal)
        if not others:
            return ""
        wall = abs(others[0].radius - info.radius)
        return f" Wall is {wall:.2f} mm now."

    def commit(self) -> None:
        pick = self._pick()
        if not _need(
            self.window_, pick is not None,
            "Select a flat face, or the side of a shaft or hole.",
        ):
            return
        document = self.window_.document
        if pick.is_round_face:
            wanted = self.value("diameter", pick.info.diameter)
            delta = (wanted - pick.info.diameter) / 2.0
            if not _need(
                self.window_, abs(delta) > 1e-9,
                "That is the diameter it already has.",
            ):
                return
            # Stored as the radial move rather than the diameter, so the feature
            # still means the same thing after an upstream edit resizes the
            # face -- exactly as Pull stores a distance and not a height.
            self.window_.add_feature(
                RoundPushPullFeature(
                    inputs={
                        "body": BodyRef(pick.body),
                        "face": pick.reference(document),
                        "delta": round(
                            delta if not pick.info.internal else -delta, 4
                        ),
                    },
                    outputs=[pick.body],
                )
            )
            self.window_.cancel_tool()
            return
        self.window_.add_feature(
            PushPullFeature(
                inputs={
                    "body": BodyRef(pick.body),
                    "face": pick.reference(document),
                    "distance": self.expression("distance", "5"),
                },
                outputs=[pick.body],
            )
        )
        self.window_.cancel_tool()


class _TransformPanel(_SelectionTool):
    """Shared implementation for the deliberately separate Move and Rotate tools."""

    mode = "move"
    field_specs = (("dx", "X", Dimension.LENGTH),
                   ("dy", "Y", Dimension.LENGTH),
                   ("dz", "Z", Dimension.LENGTH))

    def build(self) -> None:
        bodies = self.selection.bodies
        groups = self.selection.groups
        subject = ", ".join(groups) if groups else ", ".join(bodies)
        if groups:
            subject += f" ({len(bodies)} objects, transforming together)"
        verb = "move" if self.mode == "move" else "rotate"
        self.set_subtitle(
            f"{subject} — drag a handle, or type exact values."
            if bodies else f"Select a body to {verb}."
        )
        if self.mode == "move" and bodies:
            self.set_subtitle(
                f"{subject} — ←/→ changes X, ↓/↑ changes Y, exactly 0.25 mm per press."
            )
        for key, label, dimension in self.field_specs:
            self.add_field(key, label, 0.0, dimension)
        self._origin_ghost = False
        self._translation_locks = {}
        self._translation_geometry = None
        self._snap_presentation = None
        self._attach_gizmo()

    def handle_arrow_key(self, key: int) -> bool:
        if self.mode != "move" or key not in (
            Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down,
        ):
            return False
        field_key, sign = {
            Qt.Key_Left: ("dx", -1.0), Qt.Key_Right: ("dx", 1.0),
            Qt.Key_Down: ("dy", -1.0), Qt.Key_Up: ("dy", 1.0),
        }[key]
        field = self.fields[field_key]
        field.set_value(field.value() + sign * 0.25)
        self._preview_keyboard_move()
        return True

    def _preview_keyboard_move(self) -> None:
        from ...kernel.occ import compound, make_transform

        bodies = self.selection.bodies
        shapes = [
            body.shape for body in (self.window_.document.body(name) for name in bodies)
            if body is not None and body.shape is not None
        ]
        if not shapes:
            return
        viewport = self.window_.stage.viewport
        if not self._origin_ghost:
            viewport.show_ghost(
                compound(shapes), self.window_.palette_.accent, transparency=0.14
            )
            self._origin_ghost = True
        transform = make_transform(translate=(
            self.value("dx"), self.value("dy"), self.value("dz")
        ))
        viewport.transform_ghost(transform)
        for name in bodies:
            viewport.set_transparency(self.window_._presentations.get(name), 0.78)
        self.window_.set_hint(
            f"Keyboard move  ΔX {self.value('dx'):+.2f}  "
            f"ΔY {self.value('dy'):+.2f} mm — arrows are exact; snapping is bypassed"
        )

    def _attach_gizmo(self) -> None:
        bodies = self.selection.bodies
        if not bodies:
            return
        presentations = [
            self.window_._presentations.get(name) for name in bodies
            if self.window_._presentations.get(name) is not None
        ]
        if not presentations:
            return
        pivot = self._shared_pivot(bodies)
        gizmo = self.window_.attach_gizmo(
            presentations,
            allow_translation=self.mode == "move",
            allow_rotation=self.mode == "rotate",
            allow_planes=self.mode == "move",
            allow_scale=False,
            pivot=pivot,
        )
        if gizmo is not None:
            if self.mode == "move":
                gizmo.translation_filter = self._snap_translation
            gizmo.changed.connect(self._on_gizmo_drag)
            gizmo.committed.connect(self._on_gizmo_commit)

    def _snap_translation(self, values, bypass: bool = False):
        """Magnetically align moved bounds to nearby centres, edges and corners."""
        from ...kernel.occ import bounding_box

        if bypass:
            self._translation_locks.clear()
            self._show_snap_target(None)
            return tuple(values)
        selected = set(self.selection.bodies)
        if self._translation_geometry is None:
            selected_shapes = [
                body.shape for body in (
                    self.window_.document.body(name) for name in selected
                ) if body is not None and body.shape is not None
            ]
            if not selected_shapes:
                return tuple(values)
            boxes = [bounding_box(shape) for shape in selected_shapes]
            source_low = tuple(min(box[0][i] for box in boxes) for i in range(3))
            source_high = tuple(max(box[1][i] for box in boxes) for i in range(3))
            source = tuple(
                (source_low[i], (source_low[i] + source_high[i]) / 2.0, source_high[i])
                for i in range(3)
            )
            targets = []
            for name, body in self.window_.document.bodies.items():
                if name in selected or body.shape is None or not body.visible:
                    continue
                low, high = bounding_box(body.shape)
                targets.append((name, tuple(
                    (low[i], (low[i] + high[i]) / 2.0, high[i])
                    for i in range(3)
                )))
            self._translation_geometry = (
                source, targets, self._shared_pivot(selected)
            )
        source, targets, pivot = self._translation_geometry
        capture = max(
            0.05,
            min(1.0, self.window_.stage.viewport.mm_per_pixel(pivot) * 10.0),
        )
        release = capture * 1.6
        output = list(values)
        labels = ("near edge", "centre", "far edge")
        active = None
        for axis in range(3):
            raw = float(values[axis])
            lock = self._translation_locks.get(axis)
            if lock is not None and abs(raw - lock[0]) <= release:
                output[axis] = lock[0]
                active = lock[1]
                continue
            self._translation_locks.pop(axis, None)
            if abs(raw) < 1.0e-8:
                continue
            best = None
            for name, target in targets:
                for source_index, source_value in enumerate(source[axis]):
                    for target_index, target_value in enumerate(target[axis]):
                        delta = target_value - source_value
                        if abs(delta) < 1.0e-7:
                            continue
                        distance = abs(raw - delta)
                        if best is None or distance < best[0]:
                            best = (
                                distance, delta,
                                f"{name}: {labels[source_index]} to {labels[target_index]}",
                            )
            if best is not None and best[0] <= capture:
                output[axis] = best[1]
                self._translation_locks[axis] = (best[1], best[2])
                active = best[2]
        self._show_snap_target(active)
        gizmo = self.window_.stage.viewport.gizmo
        if gizmo is not None:
            gizmo.translation_snap = active or ""
        return tuple(output)

    def _show_snap_target(self, label) -> None:
        viewport = self.window_.stage.viewport
        name = str(label).split(":", 1)[0] if label else ""
        wanted = self.window_._presentations.get(name) if name else None
        if wanted is self._snap_presentation:
            return
        if self._snap_presentation is not None:
            viewport.set_highlight(self._snap_presentation, False)
            self._snap_presentation = None
        if wanted is None:
            return
        self._snap_presentation = wanted
        viewport.set_highlight(self._snap_presentation, True)

    def _on_gizmo_drag(self, dx, dy, dz, rx, ry, rz) -> None:
        from PySide6.QtGui import QCursor

        if not self._origin_ghost:
            from ...kernel.occ import compound

            shapes = [
                body.shape for body in (
                    self.window_.document.body(name) for name in self.selection.bodies
                ) if body is not None and body.shape is not None
            ]
            self.window_.stage.viewport.show_ghost(
                compound(shapes), self.window_.palette_.text_faint, transparency=0.72
            )
            self._origin_ghost = True
        moved = math.sqrt(dx * dx + dy * dy + dz * dz)
        axis, angle = max(
            (("X", rx), ("Y", ry), ("Z", rz)), key=lambda item: abs(item[1])
        )
        if self.mode == "rotate":
            snapped = bool(
                self.window_.stage.viewport.gizmo
                and self.window_.stage.viewport.gizmo.rotation_snapped
            )
            suffix = " · snapped" if snapped else ""
            self.window_.set_hint(
                f"Rotating {axis} {angle:+.1f}°{suffix}. Shift bypasses snapping."
            )
            headline = f"{axis}  {angle:+.0f}°{suffix}" if snapped else f"{axis}  {angle:+.2f}°"
            sub = f"X {rx:.2f}°  Y {ry:.2f}°  Z {rz:.2f}°"
        else:
            snap = (
                self.window_.stage.viewport.gizmo.translation_snap
                if self.window_.stage.viewport.gizmo is not None else ""
            )
            suffix = f" · snapped to {snap}" if snap else " · Shift bypasses snapping"
            self.window_.set_hint(f"Moving {moved:.2f} mm{suffix}")
            active = "/".join(
                axis for axis, value in (("X", dx), ("Y", dy), ("Z", dz))
                if abs(value) > 1e-4
            ) or "Move"
            headline = f"{active}  {moved:.2f} mm"
            sub = f"ΔX {dx:.2f}  ΔY {dy:.2f}  ΔZ {dz:.2f}"
        for key, value in (
            ("dx", dx), ("dy", dy), ("dz", dz),
            ("rx", rx), ("ry", ry), ("rz", rz),
        ):
            if key in self.fields:
                self.fields[key].set_value(round(value, 3))
        self.window_.stage.drag_readout.show_text(
            self.window_.stage.mapFromGlobal(QCursor.pos()), headline, sub,
            self.window_.palette_.accent,
        )

    def _on_gizmo_commit(self, dx, dy, dz, rx, ry, rz) -> None:
        """A released drag fills the fields, then applies them."""
        self._clear_gizmo_feedback()
        for key, value in (
            ("dx", dx), ("dy", dy), ("dz", dz), ("rx", rx), ("ry", ry), ("rz", rz)
        ):
            field = self.fields.get(key)
            if field is not None:
                field.set_value(round(value, 3))
        if any(abs(v) > 1e-4 for v in (dx, dy, dz, rx, ry, rz)):
            self.commit()

    def _clear_gizmo_feedback(self) -> None:
        if getattr(self, "_origin_ghost", False):
            self.window_.stage.viewport.clear_ghost()
        self._origin_ghost = False
        for name in self.selection.bodies:
            self.window_.stage.viewport.set_transparency(
                self.window_._presentations.get(name), 0.0
            )
        self.window_.stage.drag_readout.finish()
        self._translation_locks.clear()
        self._show_snap_target(None)

    def teardown(self) -> None:
        self._clear_gizmo_feedback()
        self.window_.detach_gizmo()

    def _shared_pivot(self, names):
        """The centre of everything selected, for a rotation about the lot."""
        from ...kernel.occ import bounding_box

        boxes = [
            bounding_box(body.shape)
            for body in (self.window_.document.body(n) for n in names)
            if body is not None and body.shape is not None
        ]
        if not boxes:
            return None
        return tuple(
            (min(b[0][i] for b in boxes) + max(b[1][i] for b in boxes)) / 2.0
            for i in range(3)
        )

    def commit(self) -> None:
        bodies = self.selection.bodies
        verb = "move" if self.mode == "move" else "rotate"
        if not _need(self.window_, bool(bodies), f"Select a body to {verb}."):
            return
        keys = tuple(key for key, _label, _dimension in self.field_specs)
        if all(abs(self.value(k, 0.0)) < 1e-9 for k in keys):
            self.window_.set_hint(
                f"Nothing to {verb} — drag a handle or enter a value."
            )
            return
        self.window_.detach_gizmo()
        self.window_.history.record(self.title)
        # Several bodies turn about their *shared* centre. Letting each spin
        # about its own would leave a rotated group in the same places it
        # started, every part facing a new way -- which is not what turning an
        # assembly means.
        pivot = self._shared_pivot(bodies) if self.mode == "rotate" else None
        for name in bodies:
            inputs = {
                "body": BodyRef(name),
                **{k: self.expression(k, "0")
                   for k in keys},
            }
            if pivot is not None:
                inputs["pivot"] = list(pivot)
            self.window_.document.add_feature(
                MoveFeature(inputs=inputs, outputs=[name])
            )
        self.window_.mark_dirty()
        self.window_.rebuild()
        self.window_.cancel_tool()


@register_tool("move")
class MovePanel(_TransformPanel):
    """Translate bodies with arrows and plane handles only."""

    title = "Move"
    confirm_label = "Move"


@register_tool("rotate")
class RotatePanel(_TransformPanel):
    """Rotate bodies with rings only, about their visible centre."""

    mode = "rotate"
    title = "Rotate"
    confirm_label = "Rotate"
    field_specs = (("rx", "Around X", Dimension.ANGLE),
                   ("ry", "Around Y", Dimension.ANGLE),
                   ("rz", "Around Z", Dimension.ANGLE))


# ----------------------------------------------------------------------
@register_tool("align")
@register_tool("stack")
@register_tool("concentric")
@register_tool("center")
@register_tool("place_on_face")
class AlignPanel(_SelectionTool):
    """A role-led, anchored placement workflow with a live face frame."""

    title = "Align"
    confirm_label = "Done"

    OPERATIONS = (
        ("stack", "Stack"),
        ("center", "Center"),
        ("concentric", "Concentric"),
    )

    def build(self) -> None:
        from ...kernel.align import suggest

        self._preview_body = None
        self._placement_ghost = None
        self._axis_overlays = []
        self._role_overlays = []
        self._role_signature = None
        self._moving_role_overlay = None
        self._frame_overlay = None
        self._snap_lock = None
        self._placement_snap_candidates = []
        self._placement_snap_presentation = None
        self._cached_frames = None
        self._moving = None
        self._target = None
        self._moving_name = None
        self._capture_role = None
        pair = self._selection_pair()
        if pair is not None:
            self._moving, self._target = pair
            self._moving_name = self._moving.body
        self.operation = "stack"
        if pair is not None:
            moving, target = pair
            self.operation = suggest(moving.shape, target.shape)
            self.set_subtitle(
                "Choose the two anchors, then drag or type an exact offset."
            )
        else:
            self.set_subtitle(
                "1. Pick the object to move.  2. Pick a flat target face."
            )

        self.add_section("What moves where")
        roles = QWidget()
        role_layout = QGridLayout(roles)
        role_layout.setContentsMargins(0, 0, 0, 0)
        role_layout.setSpacing(METRICS.space(1))
        self.moving_status = QLabel()
        self.target_status = QLabel()
        self.pick_moving = GhostButton("Pick moving object")
        self.pick_target = GhostButton("Pick target face")
        self.pick_moving.clicked.connect(lambda: self._start_pick("moving"))
        self.pick_target.clicked.connect(lambda: self._start_pick("target"))
        role_layout.addWidget(self.moving_status, 0, 0)
        role_layout.addWidget(self.pick_moving, 0, 1)
        role_layout.addWidget(self.target_status, 1, 0)
        role_layout.addWidget(self.pick_target, 1, 1)
        self.add_widget(roles)

        self._buttons = QButtonGroup(self)
        self._buttons.setExclusive(True)
        if not getattr(self, "fixed_operation", False):
            self.add_section("Operation")
            chooser = QWidget()
            grid = QGridLayout(chooser)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(METRICS.space(1))
            for index, (key, label) in enumerate(self.OPERATIONS):
                button = GhostButton(label)
                button.setCheckable(True)
                button.setChecked(key == self.operation)
                button.clicked.connect(lambda _=False, k=key: self._choose(k))
                self._buttons.addButton(button)
                grid.addWidget(button, 0, index)
            self.add_widget(chooser)

        self.add_section("Anchor to anchor")
        anchor_row = QWidget()
        anchor_layout = QHBoxLayout(anchor_row)
        anchor_layout.setContentsMargins(0, 0, 0, 0)
        anchor_layout.setSpacing(METRICS.space(3))
        moving_column = QVBoxLayout()
        moving_column.addWidget(QLabel("Moving object"))
        self.moving_anchor = AnchorGrid(self._palette)
        moving_column.addWidget(self.moving_anchor)
        target_column = QVBoxLayout()
        target_column.addWidget(QLabel("Target face"))
        self.target_anchor = AnchorGrid(self._palette)
        target_column.addWidget(self.target_anchor)
        anchor_layout.addLayout(moving_column)
        anchor_layout.addLayout(target_column)
        self.add_widget(anchor_row)
        self.moving_anchor.changed.connect(lambda _value: self.preview())
        self.target_anchor.changed.connect(lambda _value: self.preview())

        self.add_section("Signed face offsets")
        self.direction_hint = QLabel("←  −U / Left     Right / +U  →\n↓  −V / Down       Up / +V  ↑")
        self.direction_hint.setAlignment(Qt.AlignCenter)
        self.direction_hint.setStyleSheet(
            f"color:{self._palette.text_muted}; padding:5px;"
        )
        self.add_widget(self.direction_hint)
        self.add_field("u", "U  (+ right)", 0.0)
        self.add_field("v", "V  (+ up)", 0.0)
        self.add_field("offset", "Normal gap", 0.0)
        self.flip = GhostButton("Flip")
        self.flip.setCheckable(True)
        self.flip.clicked.connect(lambda: self.preview())
        self.add_widget(self.flip)
        self.swap = GhostButton("Swap moving / target")
        self.swap.setEnabled(pair is not None)
        self.swap.clicked.connect(self._swap_pair)
        self.add_widget(self.swap)
        for key in ("u", "v", "offset"):
            self.fields[key].edited_live.connect(lambda _value: self.preview())
        self._refresh_roles()
        self._update_position_controls()
        from ..widgets.placement import PlacementOverlay

        self._frame_overlay = PlacementOverlay(
            self.window_.stage.viewport, self._palette, self.window_.stage
        )
        self.window_.stage.add_overlay(self._frame_overlay, "full")
        self._frame_overlay.show()
        self.window_.stage.viewport.plane_dragged.connect(self._on_plane_drag)
        self.preview()

    def _selection_pair(self):
        """The explicit face pair, or an inferred support face for a body."""
        faces = self.selection.faces()
        if len(faces) == 2:
            return faces[0], faces[1]
        planar = self.selection.planar_faces()
        bodies = self.selection.bodies
        if len(planar) != 1 or len(bodies) != 2:
            return None
        target = planar[0]
        moving_name = next((name for name in bodies if name != target.body), None)
        body = self.window_.document.body(moving_name) if moving_name else None
        if body is None or body.shape is None:
            return None
        from ...kernel.align import support_face
        from ...kernel.detect import analyse_plane
        from ..selection import Picked

        shape = support_face(body.shape, target.shape)
        info = analyse_plane(shape) if shape is not None else None
        if info is None:
            return None
        return (
            Picked(
                body=moving_name, kind="face", shape=shape,
                presentation=self.window_._presentations.get(moving_name), info=info,
            ),
            target,
        )

    def _pair(self):
        return (self._moving, self._target) if self._moving and self._target else None

    def _start_pick(self, role: str) -> None:
        self._capture_role = role
        self.warn(
            "Select the object or its mounting face."
            if role == "moving" else "Select a flat face on the receiving object."
        )

    def _capture_current(self) -> None:
        if self._capture_role is None or not self.selection.picks:
            return
        pick = self.selection.picks[-1]
        if self._capture_role == "moving":
            self._moving_name = pick.body
            self._moving = pick if pick.is_planar_face else None
            self._target = None if self._target and self._target.body == pick.body else self._target
            self._capture_role = "target"
            self.warn("Moving object selected. Now select a flat target face.")
        else:
            if not pick.is_planar_face:
                self.warn("The target must be a flat face.")
                return
            if pick.body == self._moving_name:
                self.warn("Pick a target face on a different object.")
                return
            self._target = pick
            if self._moving is None:
                body = self.window_.document.body(self._moving_name)
                if body is not None and body.shape is not None:
                    from ...kernel.align import support_face
                    from ...kernel.detect import analyse_plane
                    from ..selection import Picked

                    shape = support_face(body.shape, pick.shape)
                    info = analyse_plane(shape) if shape is not None else None
                    if info is not None:
                        self._moving = Picked(
                            body=self._moving_name, kind="face", shape=shape,
                            presentation=self.window_._presentations.get(self._moving_name),
                            info=info,
                        )
            self._capture_role = None
            self.warn("")

    def _refresh_roles(self) -> None:
        self.moving_status.setText(
            f"●  {self._moving_name or 'Moving object'}"
        )
        self.target_status.setText(
            f"◆  {self._target.body if self._target else 'Target face'}"
        )
        self.swap.setEnabled(self._pair() is not None)

    def _swap_pair(self) -> None:
        if self._pair() is None:
            return
        self._moving, self._target = self._target, self._moving
        self._moving_name = self._moving.body
        self._cached_frames = None
        self._refresh_roles()
        self.preview()

    def _update_position_controls(self) -> None:
        enabled = self.operation in ("stack", "center")
        self.moving_anchor.setEnabled(enabled)
        self.target_anchor.setEnabled(enabled)
        for key in ("u", "v"):
            self.fields[key].setEnabled(enabled)

    def _choose(self, key: str) -> None:
        self.operation = key
        self._update_position_controls()
        self.preview()

    def on_selection_changed(self) -> None:
        self._capture_current()
        self._refresh_roles()
        self.preview()

    def _solve(self, moving, target):
        from ...kernel.align import solve

        moving_frame = target_frame = None
        if self.operation in ("stack", "center"):
            moving_frame, target_frame = self._reference_frames(moving, target)

        return solve(
            moving.shape,
            target.shape,
            operation=self.operation,
            offset=self.value("offset", 0.0),
            flip=self.flip.isChecked(),
            moving_anchor=self.moving_anchor.value(),
            target_anchor=self.target_anchor.value(),
            u=self.value("u", 0.0),
            v=self.value("v", 0.0),
            moving_frame=moving_frame,
            target_frame=target_frame,
        )

    def _reference_frames(self, moving, target):
        from ...kernel.align import reference_frame

        if self._cached_frames is None or not (
            moving.shape.IsSame(self._cached_frames[0])
            and target.shape.IsSame(self._cached_frames[1])
        ):
            self._cached_frames = (
                moving.shape, target.shape,
                reference_frame(moving.shape), reference_frame(target.shape),
            )
        return self._cached_frames[2], self._cached_frames[3]

    def begin_freeform_drag(self, pos) -> bool:
        """Claim a drag that starts on the selected reference face."""
        pair = self._pair()
        if pair is None or self.operation not in ("stack", "center"):
            return False
        _moving, target = pair
        viewport = self.window_.stage.viewport
        under_cursor = viewport.detected_shape(pos)
        if under_cursor is None or not under_cursor.IsSame(target.shape):
            return False
        from ...kernel.align import ANCHOR_PRESETS, anchor_coordinates

        frame = self._reference_frames(pair[0], target)[1]
        self._snap_lock = None
        origin_u, origin_v = anchor_coordinates(frame, self.target_anchor.value())
        self._placement_snap_candidates = [
            (key.replace("_", " ").title(), u - origin_u, v - origin_v, None)
            for key in ANCHOR_PRESETS
            for u, v in (anchor_coordinates(frame, key),)
        ]
        # Nearby-object bbox extrema cover centres, edge midpoints and corners
        # without enumerating imported topology on the UI thread.
        from ...kernel.occ import bounding_box

        for name, body in self.window_.document.bodies.items():
            if name in (target.body, self._moving_name) or body.shape is None or not body.visible:
                continue
            low, high = bounding_box(body.shape)
            coordinates = tuple(
                (low[i], (low[i] + high[i]) / 2.0, high[i]) for i in range(3)
            )
            seen = set()
            for x in coordinates[0]:
                for y in coordinates[1]:
                    for z in coordinates[2]:
                        relative = (x - frame.center[0], y - frame.center[1], z - frame.center[2])
                        candidate_u = sum(relative[i] * frame.x_axis[i] for i in range(3)) - origin_u
                        candidate_v = sum(relative[i] * frame.y_axis[i] for i in range(3)) - origin_v
                        key = (round(candidate_u, 6), round(candidate_v, 6))
                        if key in seen:
                            continue
                        seen.add(key)
                        self._placement_snap_candidates.append(
                            (f"Aligned with {name}", candidate_u, candidate_v, name)
                        )
        return viewport.begin_plane_drag(
            frame.center, frame.normal, frame.x_axis, frame.y_axis,
            self.value("u"), self.value("v"),
        )

    def _on_plane_drag(self, value: dict) -> None:
        pair = self._pair()
        if pair is None:
            return
        from ...kernel.align import anchor_coordinates

        frame = self._reference_frames(*pair)[1]
        raw_u, raw_v = float(value["u"]), float(value["v"])
        modifiers = value.get("modifiers", Qt.NoModifier)
        snapped = ""
        if not (modifiers & Qt.ShiftModifier):
            origin_u, origin_v = anchor_coordinates(frame, self.target_anchor.value())
            candidates = self._placement_snap_candidates
            by_key = {item[0]: item for item in candidates}
            allowed = candidates
            if self._snap_lock in by_key:
                allowed = [by_key[self._snap_lock]]
            best = None
            cursor = value.get("pos")
            target_name = None
            for key, u, v, body_name in allowed:
                projected = self.window_.stage.viewport.project(frame.point(origin_u + u, origin_v + v))
                if projected is None or cursor is None:
                    continue
                distance = math.hypot(projected[0] - cursor.x(), projected[1] - cursor.y())
                if best is None or distance < best[0]:
                    best = (distance, key, u, v, body_name)
            limit = 16.0 if self._snap_lock else 10.0
            if best is not None and best[0] <= limit:
                _distance, self._snap_lock, raw_u, raw_v, target_name = best
                snapped = self._snap_lock
                self._show_placement_snap(target_name)
            else:
                self._snap_lock = None
                self._show_placement_snap(None)
        else:
            self._snap_lock = None
            self._show_placement_snap(None)
        self.fields["u"].set_value(raw_u)
        self.fields["v"].set_value(raw_v)
        self.preview()
        if snapped:
            self.warn(f"Snapped to {snapped}. Hold Shift for free placement.")

    def _show_placement_snap(self, body_name) -> None:
        viewport = self.window_.stage.viewport
        wanted = self.window_._presentations.get(body_name) if body_name else None
        if wanted is self._placement_snap_presentation:
            return
        if self._placement_snap_presentation is not None:
            viewport.set_highlight(self._placement_snap_presentation, False)
            self._placement_snap_presentation = None
        if wanted is not None:
            self._placement_snap_presentation = wanted
            viewport.set_highlight(self._placement_snap_presentation, True)

    def preview(self) -> None:
        pair = self._pair()
        if pair is None:
            self._clear_preview()
            self.warn("Select a moving body or face and a flat target face.")
            return
        moving, target = pair
        body = self.window_.document.body(moving.body)
        if body is None or body.shape is None:
            return
        try:
            result = self._solve(moving, target)
        except Exception as exc:  # noqa: BLE001 - invalid combinations stay editable
            from ...core.errors import translate

            self.warn(str(translate(exc, "align")))
            return
        viewport = self.window_.stage.viewport
        # Keep one AIS preview and move its presentation. Re-tessellating the
        # entire body for every typed character was the main source of stutter.
        if (
            self._preview_body != moving.body
            or getattr(viewport, "_ghost", None) is not self._placement_ghost
        ):
            self._placement_ghost = viewport.show_ghost(
                body.shape, self.window_.palette_.accent, transparency=0.14,
            )
        viewport.transform_ghost(result.transform)
        self._preview_body = moving.body
        viewport.set_transparency(
            self.window_._presentations.get(moving.body), 0.78
        )
        signature = (moving.body, target.body)
        if signature != self._role_signature:
            for presentation in self._role_overlays:
                viewport.erase(presentation)
            self._role_overlays = []
            target_overlay = viewport.show_overlay_shape(
                target.shape, self.window_.palette_.grid_axis_y, transparency=0.72
            )
            self._moving_role_overlay = viewport.show_overlay_shape(
                moving.shape, self.window_.palette_.accent, transparency=0.45
            )
            self._role_overlays = [
                presentation for presentation in (
                    target_overlay, self._moving_role_overlay
                ) if presentation is not None
            ]
            self._role_signature = signature
        if self._moving_role_overlay is not None:
            self._moving_role_overlay.SetLocalTransformation(result.transform)
            if viewport.context is not None:
                viewport.context.Redisplay(self._moving_role_overlay, False)
        self._show_face_frame(target.shape)
        self.warn("")

    def _show_face_frame(self, face) -> None:
        if self.operation not in ("stack", "center"):
            return
        from ...kernel.align import anchor_point, reference_frame

        pair = self._pair()
        frame = (
            self._reference_frames(*pair)[1]
            if pair is not None and pair[1].shape.IsSame(face)
            else reference_frame(face)
        )
        anchor = anchor_point(
            frame, self.target_anchor.value(), normal=self.value("offset", 0.0)
        )
        origin = tuple(
            anchor[i]
            + frame.x_axis[i] * self.value("u", 0.0)
            + frame.y_axis[i] * self.value("v", 0.0)
            for i in range(3)
        )
        if self._frame_overlay is not None:
            self._frame_overlay.set_placement(
                frame, origin, self.value("u"), self.value("v"),
                self._snap_lock or "",
            )

    def _clear_preview(self) -> None:
        viewport = self.window_.stage.viewport
        viewport.clear_ghost()
        if self._preview_body is not None:
            viewport.set_transparency(
                self.window_._presentations.get(self._preview_body), 0.0
            )
        self._preview_body = None
        self._placement_ghost = None
        for presentation in self._axis_overlays:
            viewport.erase(presentation)
        self._axis_overlays = []
        for presentation in self._role_overlays:
            viewport.erase(presentation)
        self._role_overlays = []
        self._role_signature = None
        self._moving_role_overlay = None
        self._show_placement_snap(None)

    def teardown(self) -> None:
        self._clear_preview()
        try:
            self.window_.stage.viewport.plane_dragged.disconnect(self._on_plane_drag)
        except (RuntimeError, TypeError):
            pass
        if self._frame_overlay is not None:
            self.window_.stage.remove_overlay(self._frame_overlay)
            self._frame_overlay.deleteLater()
            self._frame_overlay = None

    def commit(self) -> None:
        pair = self._pair()
        if not _need(
            self.window_, pair is not None,
            "Select a moving body or face and a flat target face.",
        ):
            return
        moving, target = pair
        if not _need(
            self.window_, moving.body != target.body,
            "Those faces are on the same body. Pick a face on each part.",
        ):
            return
        document = self.window_.document
        self.window_.add_feature(
            AlignFeature(
                inputs={
                    "body": BodyRef(moving.body),
                    "moving_face": moving.reference(document),
                    "target_face": target.reference(document),
                    "operation": self.operation,
                    "offset": self.expression("offset", "0"),
                    "flip": self.flip.isChecked(),
                    "u": self.expression("u", "0"),
                    "v": self.expression("v", "0"),
                    "moving_anchor": self.moving_anchor.value(),
                    "target_anchor": self.target_anchor.value(),
                },
                outputs=[moving.body],
            )
        )
        self.window_.cancel_tool()


@register_tool("section_replace")
class SectionReplacePanel(AlignPanel):
    """Align an insert, clear its silhouette, and merge it into the target."""

    title = "Section Replace"
    confirm_label = "Replace section"
    width = 332
    fixed_operation = True

    def build(self) -> None:
        self._section_ready = False
        self._section_overlays = []
        self._preview_controller = None
        self._valid_preview_signature = None
        self._requested_preview_signature = None
        super().build()
        self.operation = "stack"
        self.flip.hide()
        if self.fields["offset"].parentWidget() is not None:
            self.fields["offset"].parentWidget().hide()
        self.add_section("Integration")
        self.add_field("boundary", "Join boundary", 0.5)
        self.add_field("setback", "Setback depth", 0.0)
        for key in ("boundary", "setback"):
            self.fields[key].edited_live.connect(lambda _value: self.preview())
        self.set_subtitle(
            "The outer silhouette clears the target through; vent openings stay open. "
            "Drag on the target face, then refine U/V numerically. Positive setback "
            "sinks the insert; negative values let it protrude."
        )
        self._preview_controller = FeaturePreviewController(
            self, self._section_previewed, delay_ms=70
        )
        self._section_ready = True
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, self.preview)
        self.relayout()

    def _feature(self):
        pair = self._pair()
        if pair is None:
            return None
        replacement, target = pair
        document = self.window_.document
        return SectionReplaceFeature(
            inputs={
                "target": BodyRef(target.body),
                "target_face": target.reference(document),
                "replacement": BodyRef(replacement.body),
                "replacement_face": replacement.reference(document),
                "moving_anchor": self.moving_anchor.value(),
                "target_anchor": self.target_anchor.value(),
                "u": self.expression("u", "0"),
                "v": self.expression("v", "0"),
                "boundary": self.expression("boundary", "0.5"),
                "setback": self.expression("setback", "0"),
            },
            outputs=[target.body],
        )

    def preview(self) -> None:
        feature = self._feature() if getattr(self, "_section_ready", False) else None
        state = feature.to_dict() if feature else None
        pair = self._pair()
        signature = None
        if pair is not None:
            replacement, target = pair
            signature = (
                replacement.body, target.body,
                id(replacement.shape), id(target.shape),
                self.moving_anchor.value(), self.target_anchor.value(),
                self.value("u"), self.value("v"),
                self.value("boundary", 0.5), self.value("setback"),
            )
        # A ValueField commits once more when clicking Confirm takes focus.
        # Re-requesting an identical Boolean used to disable the button between
        # mouse-down and mouse-up, so an entirely normal click was swallowed.
        # Keep the already validated preview live when its inputs are unchanged.
        if signature is not None and signature == self._valid_preview_signature:
            self.confirm.setEnabled(True)
            return
        # Always provide the instant alignment preview while the process works
        # on the boolean; the result replaces it when ready.
        AlignPanel.preview(self)
        if not getattr(self, "_section_ready", False):
            return
        self.confirm.setEnabled(False)
        self._requested_preview_signature = signature
        self._preview_controller.request(state)

    def _section_previewed(self, message: dict) -> None:
        from ...core.geometry_service import deserialise_shape

        viewport = self.window_.stage.viewport
        for presentation in self._section_overlays:
            viewport.erase(presentation)
        self._section_overlays = []
        error = message.get("error")
        blob = message.get("shape")
        if error or not blob:
            self._valid_preview_signature = None
            self.confirm.setEnabled(False)
            if error:
                self.warn(error)
            return
        result = deserialise_shape(blob)
        viewport.show_ghost(result, self.window_.palette_.accent, transparency=0.24)
        pair = self._pair()
        if pair is not None:
            viewport.set_transparency(
                self.window_._presentations.get(pair[1].body), 0.76
            )
        colors = {
            "removed": self.window_.palette_.danger,
            "replacement": self.window_.palette_.success,
        }
        for role, color in colors.items():
            part_blob = (message.get("parts") or {}).get(role)
            if not part_blob:
                continue
            shown = viewport.show_overlay_shape(
                deserialise_shape(part_blob), color,
                transparency=0.30 if role == "removed" else 0.56,
            )
            if shown is not None:
                self._section_overlays.append(shown)
        self._valid_preview_signature = self._requested_preview_signature
        self.confirm.setEnabled(True)
        self.warn("Red is removed; green is the integrated replacement.")

    def commit(self) -> None:
        feature = self._feature()
        if not _need(
            self.window_, feature is not None,
            "Pick the replacement object and a flat target face.",
        ):
            return
        self.window_.add_feature(feature)
        self.window_.cancel_tool()

    def teardown(self) -> None:
        if self._preview_controller is not None:
            self._preview_controller.close()
        viewport = self.window_.stage.viewport
        for presentation in self._section_overlays:
            viewport.erase(presentation)
        pair = self._pair()
        if pair is not None:
            viewport.set_transparency(
                self.window_._presentations.get(pair[1].body), 0.0
            )
        self._section_overlays = []
        super().teardown()


# ----------------------------------------------------------------------
#: The tooth shapes offered, best first. Printed leads because SimpleCAD makes
#: parts for a printer, and a 60-degree ISO tooth printed axis-up overhangs by
#: 63 degrees -- it needs supports, and supports inside a thread are what stops
#: the two halves ever turning.
THREAD_FORMS = (
    ("printed", "Printed — 45° teeth, no supports"),
    ("iso", "Standard ISO — 60° V, needs supports"),
)


class _ThreadTool(_SelectionTool):
    """Shared furniture for the tools that create threads."""

    def add_form_chooser(self, into=None) -> None:
        self.form = QComboBox()
        for key, label in THREAD_FORMS:
            self.form.addItem(label, key)
        self.form.currentIndexChanged.connect(lambda _i: self.check_printability())
        self.add_section("Tooth shape", into=into)
        self.add_widget(self.form, into=into)

    def chosen_form(self) -> str:
        return self.form.currentData() if hasattr(self, "form") else "printed"

    def add_end_chooser(self, into=None) -> None:
        """Which end of the face a thread shorter than the face starts at.

        The kernel's own answer is the end OCCT happened to parameterise first,
        which is invisible from outside and lands the thread on whichever end it
        likes -- so a hole came back threaded at the bottom when the point was to
        start a screw at the top. Resolved against world Z, so the labels mean
        what they say.
        """
        self.from_end = QComboBox()
        self.from_end.addItem("From the top", "top")
        self.from_end.addItem("From the bottom", "bottom")
        self.add_section("Start the thread", into=into)
        self.add_widget(self.from_end, into=into)

    def chosen_end(self) -> str:
        return (
            self.from_end.currentData() if hasattr(self, "from_end") else "top"
        )

    def fill_sizes(self, combo: QComboBox, diameter: float, internal: bool) -> None:
        """(Re)stock *combo* for *diameter*, closest first, keeping the choice.

        The sizes that already suit the feature lead, and then **every** other
        standard size follows, each labelled with what choosing it would do to
        the face. Stopping at the ones within a couple of millimetres is what
        made "thread this hole" unanswerable for a ⌀5 or a ⌀45 bore: the list
        came back empty and Create stayed grey with nothing to click and no way
        forward. A hole that is the wrong size for the thread you want is an
        ordinary thing to have -- it is what a tap drill exists for -- so the
        answer is to offer the size and say what it costs, not to withhold it.
        """
        from ...kernel.thread_specs import load_sizes

        keep = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        suited = recommend(diameter, internal=internal, limit=8)
        for option in suited:
            combo.addItem(option.describe(), option.size.designation)
        offered = {option.size.designation for option in suited}
        for size in sorted(
            (s for s in load_sizes() if s.designation not in offered),
            key=lambda s: abs(s.diameter - diameter),
        ):
            combo.addItem(_resize_text(size, diameter, internal), size.designation)
        index = combo.findData(keep)
        if index >= 0:
            combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def check_printability(self, designation: str | None = None) -> bool:
        """Say up front what will be wrong with printing this thread.

        The same sentence the kernel would put on the finished feature, said at
        the point of choosing instead -- discovering that a thread was too fine
        for the nozzle after a four-hour print is not feedback, it is a bill.
        """
        from ...kernel.thread_specs import by_designation
        from ...kernel.threads import thread_printability

        designation = designation or (
            self.sizes.currentData() if hasattr(self, "sizes") else None
        )
        size = by_designation(str(designation)) if designation else None
        if size is None:
            if hasattr(self, "confirm"):
                self.confirm.setEnabled(False)
            return False
        try:
            result = thread_printability(size, self.chosen_form())
        except Exception:  # noqa: BLE001 - a warning is never worth failing over
            return False
        if result.printable:
            self.warn("")
            if hasattr(self, "confirm"):
                self.confirm.setEnabled(True)
            return True
        suffix = f" Use {result.replacement}." if result.replacement else ""
        self.warn(result.reason + suffix)
        if hasattr(self, "confirm"):
            self.confirm.setEnabled(False)
        return False


# ----------------------------------------------------------------------
@register_tool("hole")
class HolePanel(_ThreadTool):
    """Drill a hole -- and, when it is a threaded one, say what thread.

    The threaded style used to ask for nothing at all, so the kernel picked the
    size on its own and answered ⌀5 with M5: a 0.25 mm tooth that is real
    geometry, invisible on screen and finer than any nozzle resolves. "It made
    the hole but there is no thread" was that. The size, the tooth shape and the
    clearance are now the user's to choose, and the printability warning is said
    here, before Create, rather than in a hint line that the next click wipes.
    """

    title = "Hole"
    confirm_label = "Create"

    STYLES = (
        ("simple", "Simple"),
        ("counterbore", "Counterbore"),
        ("countersink", "Countersink"),
        ("threaded", "Threaded"),
    )

    def build(self) -> None:
        self.style = "simple"
        self.on_selection_changed()

        self.add_section("Type")
        chooser = QWidget()
        grid = QGridLayout(chooser)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(METRICS.space(1))
        for index, (key, label) in enumerate(self.STYLES):
            button = GhostButton(label)
            button.setCheckable(True)
            button.setChecked(key == "simple")
            button.clicked.connect(lambda _=False, k=key: self._choose(k))
            grid.addWidget(button, index // 2, index % 2)
            setattr(self, f"_button_{key}", button)
        self.add_widget(chooser)

        self.add_field("diameter", "Diameter", 6.0)
        self.add_section("Depth")
        self.depth_mode = QComboBox()
        self.depth_mode.addItem("Through all", "through")
        self.depth_mode.addItem("To a depth", "blind")
        self.depth_mode.addItem("To another body", "to_object")
        self.depth_mode.currentIndexChanged.connect(self._depth_changed)
        self.add_widget(self.depth_mode)

        self.until = QComboBox()
        for name in self.window_.document.bodies:
            self.until.addItem(name, name)
        self.until.setVisible(False)
        self.add_widget(self.until)

        self.add_field("depth", "Depth", 10.0)
        self.fields["depth"].setEnabled(False)

        self._build_thread_group()
        self._refresh_sizes()

    def _build_thread_group(self) -> None:
        """The controls that only a threaded hole needs, in one hideable block."""
        group = QWidget()
        layout = QVBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(METRICS.space(1.5))

        self.sizes = QComboBox()
        self.sizes.currentIndexChanged.connect(lambda _i: self.check_printability())
        self.add_section("Thread size", into=layout)
        self.add_widget(self.sizes, into=layout)

        self.clearance = QComboBox()
        for key, entry in clearance_presets().items():
            self.clearance.addItem(
                _clearance_text(key, entry, hint=True),
                key,
            )
        self.clearance.setCurrentIndex(1)   # Normal
        self.add_section("Printable clearance", into=layout)
        self.add_widget(self.clearance, into=layout)

        self.add_form_chooser(into=layout)
        self.add_field("thread_length", "Threaded length", 0.0, into=layout)
        self.fields["thread_length"].setPlaceholderText("full depth")
        self.add_end_chooser(into=layout)

        group.setVisible(False)
        self.add_widget(group)
        self._thread_group = group

    def _refresh_sizes(self) -> None:
        """Restock the size list for the diameter now in the field."""
        diameter = self.value("diameter", 6.0)
        self.fill_sizes(self.sizes, diameter, internal=True)
        if self.style != "threaded":
            self.on_selection_changed()
            return
        if self.sizes.count() == 0:
            self.warn(
                f"No standard thread is close to ⌀{diameter:.2f} mm. "
                "Change the diameter, or drill it plain."
            )
        else:
            self.check_printability()
        self.on_selection_changed()

    def _drill_diameter(self) -> float | None:
        """The bore the chosen thread wants, when it is not the one typed.

        The size list offers every standard size now, not only the handful that
        happened to suit the number in the Diameter field, so the two can
        disagree -- and a hole drilled at ⌀5 with a P12 thread asked of it is
        not a thread, it is a failed rebuild. Drilling what the thread needs is
        both what the user meant and the only answer that builds.
        """
        from ...kernel.thread_specs import by_designation
        from ...kernel.threads import required_bore

        if self.style != "threaded" or not hasattr(self, "sizes"):
            return None
        size = by_designation(str(self.sizes.currentData() or ""))
        if size is None:
            return None
        wanted = required_bore(size, self.clearance.currentData())
        typed = self.value("diameter", 6.0)
        return None if abs(wanted - typed) <= 0.05 else wanted

    def preview(self) -> None:
        # Reached when a value field is committed, which is where the diameter
        # changes -- and the diameter is what decides which threads can fit.
        self._refresh_sizes()

    def on_selection_changed(self) -> None:
        faces = self.selection.planar_faces()
        if faces:
            text = (
                f"Drilling into {faces[0].body}. The hole is centred on the face "
                "unless you click a position first."
            )
        else:
            text = "Select the flat face to drill into."
        wanted = self._drill_diameter()
        if wanted is not None:
            text += (
                f" {self.sizes.currentData()} will be drilled at "
                f"⌀{wanted:.2f} mm."
            )
        self.set_subtitle(text)
        self.relayout()

    def _choose(self, key: str) -> None:
        self.style = key
        for candidate, _label in self.STYLES:
            getattr(self, f"_button_{candidate}").setChecked(candidate == key)
        self._thread_group.setVisible(key == "threaded")
        if key == "threaded":
            self._refresh_sizes()
        else:
            self.warn("")
            self.confirm.setEnabled(True)
        self.relayout()

    def _depth_changed(self) -> None:
        mode = self.depth_mode.currentData()
        self.fields["depth"].setEnabled(mode == "blind")
        self.until.setVisible(mode == "to_object")
        self.relayout()

    def commit(self) -> None:
        faces = self.selection.planar_faces()
        if not _need(self.window_, bool(faces), "Select a flat face to drill into."):
            return
        pick = faces[0]
        document = self.window_.document
        inputs = {
            "body": BodyRef(pick.body),
            "face": pick.reference(document),
            "diameter": self.expression("diameter", "6"),
            "style": self.style,
            "depth_mode": self.depth_mode.currentData(),
            "depth": self.expression("depth", "10"),
        }
        if self.depth_mode.currentData() == "to_object" and self.until.currentData():
            inputs["until"] = BodyRef(self.until.currentData())
        if pick.info is not None:
            inputs["position"] = tuple(pick.info.center)
        if self.style == "threaded":
            inputs.update({
                "clearance": self.clearance.currentData(),
                "form": self.chosen_form(),
                "thread_length": self.expression("thread_length", "0"),
                "from_end": self.chosen_end(),
            })
            # Never send a null designation: the key existing is what stops the
            # kernel recording the size it chose for itself, and then nothing
            # downstream can find the thread again.
            if self.sizes.currentData():
                inputs["designation"] = self.sizes.currentData()
            wanted = self._drill_diameter()
            if wanted is not None:
                inputs["diameter"] = f"{wanted:g}"
        self.window_.add_feature(HoleFeature(inputs=inputs, outputs=[pick.body]))
        self.window_.cancel_tool()


@register_tool("thread")
class ThreadPanel(_ThreadTool):
    """Select a round face; the size is already worked out."""

    title = "Thread"
    confirm_label = "Create"

    def build(self) -> None:
        self._preview_valid = False
        self._preview_body: str | None = None
        self._preview = FeaturePreviewController(
            self, self._preview_answered, delay_ms=120
        )
        self.sizes = QComboBox()
        self.clearance = QComboBox()

        self.add_section("Size")
        self.add_widget(self.sizes)
        self.add_section("Printable clearance")
        for key, entry in clearance_presets().items():
            self.clearance.addItem(
                _clearance_text(key, entry, hint=True), key
            )
        self.clearance.setCurrentIndex(1)   # Normal
        self.add_widget(self.clearance)
        self.add_form_chooser()
        self.add_field("length", "Length", 0.0)
        self.fields["length"].setPlaceholderText("full face")
        self.add_end_chooser()
        # Shown only when the face is not already the size the chosen thread
        # wants. Off by default: opening somebody's bore out by four millimetres
        # is not a thing to do behind their back.
        self.resize = QCheckBox("Resize to suit the thread")
        self.resize.toggled.connect(lambda _on: self.preview())
        self.resize.setVisible(False)
        self.add_widget(self.resize)
        self.sizes.currentIndexChanged.connect(self._thread_choice_changed)
        self.clearance.currentIndexChanged.connect(lambda _i: self.preview())
        self.form.currentIndexChanged.connect(lambda _i: self.preview())
        self.from_end.currentIndexChanged.connect(lambda _i: self.preview())
        self.on_selection_changed()

    def _thread_choice_changed(self, _index: int) -> None:
        self.check_printability()
        self._refresh_resize()
        self.preview()

    def _refresh_resize(self) -> None:
        """Offer to bring the face to the chosen size, when it is not there."""
        from ...kernel.thread_specs import by_designation
        from ...kernel.threads import required_bore

        faces = self.selection.round_faces()
        size = by_designation(str(self.sizes.currentData() or ""))
        info = faces[0].info if faces else None
        # The same 0.05 mm the kernel draws the line at, so what the panel
        # offers and what the rebuild does cannot disagree.
        if info is None or size is None or info.diameter >= size.diameter - 0.05:
            self.resize.setVisible(False)
            return
        wanted = (
            required_bore(size, self.clearance.currentData())
            if info.internal else size.diameter
        )
        verb = "Open the hole" if info.internal else "Build the shaft up"
        self.resize.setText(f"{verb} ⌀{info.diameter:.2f} → ⌀{wanted:.2f} mm")
        self.resize.setVisible(True)
        self.relayout()

    def on_selection_changed(self) -> None:
        """Read the face the panel is about -- now, or whenever it is picked.

        Opening Thread and *then* clicking the face is a perfectly ordinary
        order to work in, and it used to leave the size list empty for good.
        """
        faces = self.selection.round_faces()
        if not faces:
            self.set_subtitle("Select the round face of a shaft or a hole.")
            self.sizes.clear()
            self.warn("")
            self._clear_preview()
            self.confirm.setEnabled(False)
            self.resize.setVisible(False)
            self.relayout()
            return

        info = faces[0].info
        self.set_subtitle(
            f"Detected a {info.kind}, ⌀{info.diameter:.2f} mm. "
            f"{'Internal' if info.internal else 'External'} thread."
        )
        self.fill_sizes(self.sizes, info.diameter, internal=info.internal)
        if self.sizes.count() == 0:
            self.warn(
                f"No standard thread is close to ⌀{info.diameter:.2f} mm. "
                "Resize the feature, or pick a size below."
            )
        else:
            self.check_printability()
        self._refresh_resize()
        self.preview()
        self.relayout()

    def preview(self) -> None:
        self._preview_valid = False
        self.confirm.setEnabled(False)
        self.confirm.setText("Building preview…")
        self._preview.request(self._feature_state())

    def _feature_state(self) -> dict | None:
        faces = self.selection.round_faces()
        designation = self.sizes.currentData()
        if not faces or not designation:
            return None
        pick = faces[0]
        return ThreadFeature(
            inputs={
                "body": BodyRef(pick.body),
                "face": pick.reference(self.window_.document),
                "designation": designation,
                "clearance": self.clearance.currentData(),
                "form": self.chosen_form(),
                "length": self.expression("length", "0"),
                "from_end": self.chosen_end(),
                "resize": self.resize.isChecked(),
            },
            outputs=[pick.body],
        ).to_dict()

    def _preview_answered(self, message: dict) -> None:
        from ...core.geometry_service import deserialise_shape

        blob = message.get("shape")
        shape = deserialise_shape(blob) if blob else None
        if shape is None:
            self._clear_preview()
            self.confirm.setEnabled(False)
            self.confirm.setText(self.confirm_label)
            self.warn(message.get("error") or "A physical thread could not be built here.")
            return
        faces = self.selection.round_faces()
        if not faces:
            self._clear_preview()
            return
        viewport = self.window_.stage.viewport
        self._clear_preview()
        self._preview_body = faces[0].body
        viewport.show_ghost(shape, self.window_.palette_.accent, transparency=0.12)
        presentation = self.window_._presentations.get(self._preview_body)
        viewport.set_transparency(presentation, 0.88)
        self._preview_valid = True
        self.confirm.setEnabled(True)
        self.confirm.setText(self.confirm_label)
        warnings = message.get("warnings") or []
        self.warn(" · ".join(warnings))

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
        if not self._preview_valid:
            self.warn("Wait for a valid physical thread preview before creating it.")
            return
        faces = self.selection.round_faces()
        if not _need(
            self.window_, bool(faces), "Select the round face of a shaft or hole."
        ):
            return
        pick = faces[0]
        inputs = {
            "body": BodyRef(pick.body),
            "face": pick.reference(self.window_.document),
            "clearance": self.clearance.currentData(),
            "form": self.chosen_form(),
            "length": self.expression("length", "0"),
            "from_end": self.chosen_end(),
            "resize": self.resize.isChecked(),
        }
        # Omitted rather than None when there is nothing chosen: the key merely
        # existing defeats the kernel's own ``setdefault``, and the feature then
        # records no size at all -- which is what left Create Matching Part
        # saying "no thread found yet" about a thread that was plainly there.
        if self.sizes.currentData():
            inputs["designation"] = self.sizes.currentData()
        self.window_.add_feature(
            ThreadFeature(inputs=inputs, outputs=[pick.body])
        )
        self.window_.cancel_tool()


@register_tool("align_threaded")
class AlignThreadedPanel(_ThreadTool):
    """Align two parts by their round faces *and* thread the pair, in one step.

    The spec asks for this as one command rather than two: selecting a post and
    its mating hole is already an unambiguous statement of intent, so making the
    user run Concentric and then Create Threaded Connection separately is asking
    them to say it twice.
    """

    title = "Align & thread"
    confirm_label = "Align and thread"
    width = 316

    def build(self) -> None:
        faces = self.selection.round_faces()
        self.sizes = QComboBox()
        self.clearance = QComboBox()

        if len(faces) == 2 and faces[0].info.internal != faces[1].info.internal:
            male = next(f for f in faces if not f.info.internal)
            female = next(f for f in faces if f.info.internal)
            self.set_subtitle(
                f"{male.body} will be seated into {female.body}, and both "
                "threads created to match."
            )
            for option in recommend(male.info.diameter, internal=False, limit=8):
                self.sizes.addItem(option.describe(), option.size.designation)
        else:
            self.set_subtitle(
                "Select the shaft on one part and its mating hole on the other."
            )

        self.add_section("Thread")
        self.add_widget(self.sizes)
        self.add_section("Printable clearance")
        for key, entry in clearance_presets().items():
            self.clearance.addItem(_clearance_text(key, entry), key)
        self.clearance.setCurrentIndex(1)
        self.add_widget(self.clearance)
        self.add_form_chooser()
        self.add_field("offset", "Seat offset", 0.0)
        self.sizes.currentIndexChanged.connect(lambda _i: self.check_printability())
        self.check_printability()

    def commit(self) -> None:
        faces = self.selection.round_faces()
        if not _need(
            self.window_, len(faces) == 2,
            "Select one round face on each part — a shaft and its mating hole.",
        ):
            return
        if not _need(
            self.window_, faces[0].info.internal != faces[1].info.internal,
            "Both selections are the same kind. Pick one shaft and one hole.",
        ):
            return

        document = self.window_.document
        male = next(f for f in faces if not f.info.internal)
        female = next(f for f in faces if f.info.internal)

        self.window_.history.record("Align & thread")
        # Move the male part onto the female's axis first, so the preview shows
        # the assembly rather than two parts that merely have matching threads.
        document.add_feature(
            AlignFeature(
                inputs={
                    "body": BodyRef(male.body),
                    "moving_face": male.reference(document),
                    "target_face": female.reference(document),
                    "operation": "concentric",
                    "offset": self.expression("offset", "0"),
                },
                outputs=[male.body],
            )
        )
        document.add_feature(
            ThreadedConnectionFeature(
                inputs={
                    "body_a": BodyRef(male.body),
                    "body_b": BodyRef(female.body),
                    "face_a": male.reference(document),
                    "face_b": female.reference(document),
                    "designation": self.sizes.currentData(),
                    "clearance": self.clearance.currentData(),
                    "form": self.chosen_form(),
                },
                outputs=[male.body, female.body],
            )
        )
        self.window_.mark_dirty()
        self.window_.rebuild()
        self.window_.cancel_tool()


@register_tool("align_stack")
class AlignAndStackPanel(AlignPanel):
    """Stack, with the offset field to hand -- the spec's *Align & Stack*."""

    title = "Align & stack"

    def build(self) -> None:
        super().build()
        self._choose("stack")
        self.set_subtitle(
            self.subtitle.text() + "  Set an offset to leave a gap."
        )


@register_tool("threaded_connection")
class ThreadedConnectionPanel(_ThreadTool):
    """The headline feature: two parts in, a matched printable pair out."""

    title = "Threaded connection"
    confirm_label = "Create pair"
    width = 316

    def build(self) -> None:
        faces = self.selection.round_faces()
        self.sizes = QComboBox()
        self.clearance = QComboBox()

        if len(faces) == 2 and faces[0].info.internal != faces[1].info.internal:
            male = next(f for f in faces if not f.info.internal)
            female = next(f for f in faces if f.info.internal)
            self.set_subtitle(
                f"{male.body} ⌀{male.info.diameter:.2f} shaft into "
                f"{female.body} ⌀{female.info.diameter:.2f} hole. "
                "Both threads are created together, with clearance on the hole."
            )
            for option in recommend(male.info.diameter, internal=False, limit=8):
                self.sizes.addItem(option.describe(), option.size.designation)
        else:
            self.set_subtitle(
                "Select the shaft on one part and its mating hole on the other."
            )

        self.add_section("Thread")
        self.add_widget(self.sizes)
        self.add_section("Printable clearance")
        for key, entry in clearance_presets().items():
            self.clearance.addItem(
                _clearance_text(key, entry), key
            )
        self.clearance.setCurrentIndex(1)
        self.add_widget(self.clearance)
        self.add_form_chooser()
        self.add_field("length", "Length", 0.0)
        self.fields["length"].setPlaceholderText("full engagement")
        self.sizes.currentIndexChanged.connect(lambda _i: self.check_printability())
        self.check_printability()

    def commit(self) -> None:
        faces = self.selection.round_faces()
        if not _need(
            self.window_, len(faces) == 2,
            "Select one round face on each part — a shaft and its mating hole.",
        ):
            return
        if not _need(
            self.window_, faces[0].info.internal != faces[1].info.internal,
            "Both selections are the same kind. Pick one shaft and one hole.",
        ):
            return
        document = self.window_.document
        first, second = faces[0], faces[1]
        self.window_.add_feature(
            ThreadedConnectionFeature(
                inputs={
                    "body_a": BodyRef(first.body),
                    "body_b": BodyRef(second.body),
                    "face_a": first.reference(document),
                    "face_b": second.reference(document),
                    "designation": self.sizes.currentData(),
                    "clearance": self.clearance.currentData(),
                    "form": self.chosen_form(),
                    "length": self.expression("length", "0"),
                },
                outputs=[first.body, second.body],
            )
        )
        self.window_.cancel_tool()


# ----------------------------------------------------------------------
class _EdgeTool(_SelectionTool):
    """Fillet and Chamfer: pull the size out on the model, or type it.

    The panel opens with a handle sitting on the selected edge, offset from it
    by the current radius so the handle's position *is* the size. Dragging it
    outward makes the corner rounder and shows the real filleted body while you
    move; the number in the panel follows the drag, and typing in the panel
    moves the handle.

    **Letting go commits**, exactly as letting go of a dragged face does. A
    drag is a complete gesture with an obvious end, and finishing one only to
    find the model unchanged until you hunt for a button reads as the drag not
    working at all. Typing a value still goes through Apply, because typing has
    no natural end.

    The preview is the genuine kernel result rather than an approximation,
    because the interesting radii are exactly the ones near where the operation
    stops being buildable. It is rebuilt on a short timer rather than on every
    mouse event, and a radius OCCT refuses leaves the last good preview standing
    and turns the readout red instead of throwing the drag away.
    """

    feature_class = FilletFeature
    field_key = "radius"
    field_label = "Radius"
    default_value = 2.0
    #: How long to coalesce drag events before rebuilding the preview, in ms.
    PREVIEW_MS = 70

    # -- construction ----------------------------------------------------
    def build(self) -> None:
        from PySide6.QtCore import QTimer

        self.picks = self._edge_picks()
        self._body_shape = self._resolve_body()
        self._max_value = self._resolve_max()
        self._handle = None
        self._anchor = None
        self._direction = None
        self._dragging = False
        self._drag_base = self.default_value
        self._dimmed = None
        self._last_good = None
        self._preview_failed = False
        # Previews are answered by the geometry process, so a reply can arrive
        # after the value it was for has been overtaken. The token says which
        # question each answer belongs to.
        self._preview_token = 0
        self._preview_value = self.default_value
        self._said_no_preview = False
        self.window_.geometry.previewed.connect(self._on_previewed)

        self.set_subtitle(self._describe())
        self.add_field(self.field_key, self.field_label, self.default_value)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(self.PREVIEW_MS)
        self._preview_timer.timeout.connect(self._refresh_preview)

        viewport = self.window_.stage.viewport
        viewport.handle_pressed.connect(self._on_handle_pressed)
        viewport.handle_dragged.connect(self._on_handle_dragged)
        self._install_handle()

    def _describe(self) -> str:
        if not self.picks:
            return "Select one or more edges, or a corner."
        return (
            f"{len(self.picks)} edge(s) selected — drag the handle on the model, "
            "or type an exact value."
        )

    def _edge_picks(self) -> list:
        """The edges to work on, expanding a picked corner into its edges.

        "Fillet this corner" is a perfectly ordinary request and a vertex is a
        perfectly ordinary thing to click, so a vertex selection is read as the
        three edges that meet there rather than rejected.
        """
        from ..selection import Picked

        edges = self.selection.edges()
        if edges:
            return edges
        from ...kernel.edge_frame import edges_at_vertex
        from OCP.TopoDS import TopoDS

        expanded: list = []
        for pick in self.selection.picks:
            if pick.kind != "vertex":
                continue
            body = self.window_.document.body(pick.body)
            if body is None or body.shape is None:
                continue
            try:
                found = edges_at_vertex(body.shape, TopoDS.Vertex_s(pick.shape))
            except Exception:  # noqa: BLE001 - a stray vertex simply expands to nothing
                continue
            expanded.extend(
                Picked(
                    body=pick.body, kind="edge", shape=TopoDS.Edge_s(edge),
                    presentation=pick.presentation,
                )
                for edge in found
            )
        return expanded

    def _resolve_body(self):
        if not self.picks:
            return None
        body = self.window_.document.body(self.picks[0].body)
        return body.shape if body is not None else None

    def _resolve_max(self) -> float:
        from ...kernel.edge_frame import max_radius

        if self._body_shape is None:
            return 1e6
        try:
            return max_radius(self._body_shape)
        except Exception:  # noqa: BLE001
            return 1e6

    # -- the handle ------------------------------------------------------
    def _handle_pick(self):
        """Which selected edge gets the handle: the one nearest the cursor.

        With several edges selected -- and a filleted corner is three of them
        -- putting the handle on whichever happened to be picked first can land
        it on the far side of the part, out of sight behind the model.
        """
        from PySide6.QtGui import QCursor

        viewport = self.window_.stage.viewport
        if len(self.picks) == 1:
            return self.picks[0]
        try:
            cursor = viewport.mapFromGlobal(QCursor.pos())
        except Exception:  # noqa: BLE001 - no cursor to speak of, headless
            return self.picks[0]

        from ...kernel.edge_frame import edge_midpoint

        best, best_distance = self.picks[0], None
        for pick in self.picks:
            try:
                screen = viewport.project(edge_midpoint(pick.shape))
            except Exception:  # noqa: BLE001 - an edge we cannot place
                continue
            if screen is None:
                continue
            distance = (screen[0] - cursor.x()) ** 2 + (screen[1] - cursor.y()) ** 2
            if best_distance is None or distance < best_distance:
                best, best_distance = pick, distance
        return best

    def _install_handle(self) -> None:
        from ...kernel.edge_frame import edge_drag_frame
        from ..viewport.handles import DragHandle

        if self._body_shape is None or not self.picks:
            return
        try:
            anchor, direction, _limit = edge_drag_frame(
                self._body_shape, self._handle_pick().shape
            )
        except Exception:  # noqa: BLE001 - no handle is better than a wrong one
            return
        self._anchor, self._direction = anchor, direction
        viewport = self.window_.stage.viewport
        self._handle = viewport.handles.add(
            DragHandle(self._offset_anchor(self.value(self.field_key,
                                                      self.default_value)),
                       direction, key=self.field_key),
            viewport, self.window_.palette_.accent,
        )
        self.window_.set_hint(
            "Drag the handle to size the "
            f"{self.title.lower()}, or type a value — then Apply."
        )

    def _offset_anchor(self, value: float):
        """Where the handle sits for a given value.

        Offset from the edge by the value itself, so the handle is not merely a
        control but a reading: how far it stands off the corner is how big the
        result will be.
        """
        return tuple(
            self._anchor[i] + self._direction[i] * max(0.0, value)
            for i in range(3)
        )

    def _move_handle(self, value: float) -> None:
        """Put the handle where *value* says, and redraw so it is seen there.

        The refresh is the point. ``move_to`` only updates a transformation;
        without asking the viewport to repaint, the handle stays where it was
        drawn and the drag looks like it is doing nothing at all -- which is
        precisely how this read. Split does the same thing and refreshes.
        """
        if self._handle is not None and self._anchor is not None:
            self._handle.move_to(self._offset_anchor(value))
            self.window_.stage.viewport.refresh()

    # -- dragging --------------------------------------------------------
    def _on_handle_pressed(self, key: str) -> None:
        if key != self.field_key:
            return
        self._dragging = True
        self._drag_base = self.value(self.field_key, self.default_value)

    def _on_handle_dragged(self, key: str, distance: float, finished: bool) -> None:
        from ...kernel.edge_frame import MIN_RADIUS

        if key != self.field_key or not self._dragging:
            return
        value = self._drag_base + distance
        value = max(MIN_RADIUS, min(self._max_value, value))
        field = self.fields.get(self.field_key)
        if field is not None:
            field.set_value(round(value, 3))
        self._move_handle(value)
        self._show_readout(value)
        if finished:
            self._dragging = False
            self._preview_timer.stop()
            # A press and release that went nowhere is a click on the handle,
            # not a gesture -- committing the default radius for it would be a
            # change nobody asked for. Push/Pull draws the same line.
            if abs(distance) < 0.05:
                self._refresh_preview()
                return
            self.commit()
            return
        # Started only if it is not already running. Restarting it on every
        # drag event -- which is what a debounce does -- means a 70 ms timer
        # never elapses while the mouse is moving, because the events arrive
        # every few milliseconds. The preview then appears only once the user
        # stops, which is not a preview.
        if not self._preview_timer.isActive():
            self._preview_timer.start()

    def _show_readout(self, value: float) -> None:
        from PySide6.QtGui import QCursor

        palette = self.window_.palette_
        # Unbuildable means "tried and failed", not "not tried yet". Reading
        # this straight off ``_last_good`` made the readout red for the first
        # few frames of every drag, before the preview timer had run once.
        buildable = self._last_good is not None or not self._preview_failed
        self.window_.stage.drag_readout.show_text(
            self.window_.stage.mapFromGlobal(QCursor.pos()),
            f"{self.field_label[0]} {value:.2f} mm",
            "" if buildable else "too large here",
            palette.accent if buildable else palette.danger,
        )

    # -- preview ---------------------------------------------------------
    def preview(self) -> None:
        """A typed value moves the handle and refreshes the preview."""
        value = self.value(self.field_key, self.default_value)
        self._move_handle(value)
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        """Ask the geometry process what this value builds. Answer arrives later.

        This used to run ``BRepFilletAPI_MakeFillet::Build`` right here, on the
        GUI thread, from inside a Qt signal handler. OCCT does not reliably
        raise on a blend it cannot compute -- twice on this machine it walked
        off a null curve adaptor in ``ChFi3d_Builder`` and the process was gone
        mid-instruction, taking the user's unsaved model with it. The
        ``except BaseException`` that wrapped the call could not have helped:
        there is no catching a SIGSEGV.

        So the preview goes where every other piece of fragile kernel work
        already goes. ``docs/architecture.md`` promises that "one bad fillet
        radius never empties the viewport or crashes the app"; this is the path
        that was not keeping it.
        """
        value = self.value(self.field_key, self.default_value)
        state = self._preview_feature(value)
        if state is None:
            self._preview_answered(value, None)
            return
        self._preview_token = next(_preview_tokens)
        self._preview_value = value
        if not self.window_.geometry.preview(state, self._preview_token):
            # No geometry process to ask. The tool still works -- the handle
            # moves, the number tracks it and Apply commits -- but there is
            # deliberately no in-process fallback here: running the solver that
            # killed the child in the parent instead is how a crash gets
            # promoted from an inconvenience to a lost model.
            self._preview_unavailable(value)

    def _preview_feature(self, value: float) -> dict | None:
        """The feature whose result the preview shows, serialised for the pipe.

        Built from the same inputs as :meth:`commit`, deliberately: a preview
        that is computed some other way is a preview of something the Apply
        button will not produce. The value goes over as a number rather than the
        expression, because it is the *current* one the handle is sitting at.
        """
        if not self.picks or value <= 0:
            return None
        body = self.picks[0].body
        document = self.window_.document
        return self.feature_class(
            inputs={
                "body": BodyRef(body),
                "edges": [
                    p.reference(document) for p in self.picks if p.body == body
                ],
                self.field_key: float(value),
            },
            outputs=[body],
        ).to_dict()

    def _on_previewed(self, message) -> None:
        from ...core.geometry_service import deserialise_shape

        if message.get("token") != self._preview_token:
            return          # an answer from earlier in the drag; overtaken
        blob = message.get("shape")
        shape = deserialise_shape(blob) if blob else None
        self._preview_answered(self._preview_value, shape)

    def _preview_answered(self, value: float, shape) -> None:
        """Show the answer. *value* is the one it was asked about, which during
        a drag is already behind the cursor -- so it names the failure, but the
        readout keeps showing where the hand actually is."""
        viewport = self.window_.stage.viewport
        live = self.value(self.field_key, self.default_value)
        if shape is None:
            # Keep whatever last worked on screen. Blanking the preview at the
            # exact moment the radius becomes invalid is the least useful thing
            # to do: it hides the shape you were steering toward just as you
            # need to see how far you overshot.
            self._preview_failed = True
            self.warn(
                f"{self.field_label} {value:.2f} mm is more than this shape can "
                "take here."
            )
            self._show_readout(live)
            return
        self._last_good = shape
        self._preview_failed = False
        self.warn("")
        viewport.show_ghost(shape, self.window_.palette_.accent, transparency=0.12)
        self._dim_body(0.88)
        self._show_readout(live)

    def _preview_unavailable(self, value: float) -> None:
        """No child to ask. Say so once and keep the tool usable."""
        if not self._said_no_preview:
            self._said_no_preview = True
            self.warn(
                "The geometry engine is not running, so there is no live "
                "preview. The value still applies."
            )
        self._show_readout(value)

    def _dim_body(self, amount: float) -> None:
        presentation = self.window_._presentations.get(self.picks[0].body)
        if presentation is None:
            return
        self.window_.stage.viewport.set_transparency(presentation, amount)
        self._dimmed = self.picks[0].body

    # -- lifecycle -------------------------------------------------------
    def teardown(self) -> None:
        viewport = self.window_.stage.viewport
        self._preview_timer.stop()
        try:
            self.window_.geometry.previewed.disconnect(self._on_previewed)
        except (RuntimeError, TypeError):
            pass
        viewport.clear_ghost()
        viewport.handles.clear(viewport)
        self.window_.stage.drag_readout.finish()
        if self._dimmed is not None:
            presentation = self.window_._presentations.get(self._dimmed)
            if presentation is not None:
                viewport.set_transparency(presentation, 0.0)
            self._dimmed = None
        for signal, slot in (
            (viewport.handle_pressed, self._on_handle_pressed),
            (viewport.handle_dragged, self._on_handle_dragged),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    def commit(self) -> None:
        if not _need(self.window_, bool(self.picks), "Select one or more edges."):
            return
        body = self.picks[0].body
        document = self.window_.document
        self.window_.add_feature(
            self.feature_class(
                inputs={
                    "body": BodyRef(body),
                    "edges": [
                        p.reference(document) for p in self.picks if p.body == body
                    ],
                    self.field_key: self.expression(
                        self.field_key, str(self.default_value)
                    ),
                },
                outputs=[body],
            )
        )
        self.window_.cancel_tool()


@register_tool("fillet")
class FilletPanel(_EdgeTool):
    title = "Fillet"
    confirm_label = "Apply"
    feature_class = FilletFeature
    field_key = "radius"
    field_label = "Radius"


@register_tool("chamfer")
class ChamferPanel(_EdgeTool):
    title = "Chamfer"
    confirm_label = "Apply"
    feature_class = ChamferFeature
    field_key = "distance"
    field_label = "Distance"


@register_tool("scale")
class ScalePanel(_SelectionTool):
    """Resize bodies numerically or with centred, uniform corner handles."""

    title = "Scale"
    confirm_label = "Scale"

    def build(self) -> None:
        bodies = self.selection.bodies
        self.set_subtitle(
            f"{', '.join(bodies)} — 2 doubles it, 0.5 halves it."
            if bodies else "Select a body to scale."
        )
        self.add_field("factor", "Scale", 1.0, Dimension.SCALAR)
        self.add_section("Per axis")
        for key, label in (("sx", "X"), ("sy", "Y"), ("sz", "Z")):
            field = self.add_field(key, label, 1.0, Dimension.SCALAR)
            field.setPlaceholderText("follows Scale")
        self._scale_frame = None
        self._scale_handles = []
        self._scale_previewing = False
        self._pivot = None
        self._base_corners = []
        self._drag_radius = 0.0
        self._attach_scale_handles()

    def _bounds(self):
        from ...kernel.occ import bounding_box

        boxes = [
            bounding_box(body.shape)
            for body in (
                self.window_.document.body(name) for name in self.selection.bodies
            )
            if body is not None and body.shape is not None
        ]
        if not boxes:
            return None
        return (
            tuple(min(box[0][i] for box in boxes) for i in range(3)),
            tuple(max(box[1][i] for box in boxes) for i in range(3)),
        )

    def _attach_scale_handles(self) -> None:
        bounds = self._bounds()
        if bounds is None:
            return
        low, high = bounds
        self._pivot = tuple((low[i] + high[i]) / 2.0 for i in range(3))
        self._base_corners = [
            (x, y, z)
            for x in (low[0], high[0])
            for y in (low[1], high[1])
            for z in (low[2], high[2])
        ]

        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
        from OCP.gp import gp_Pnt

        from ...kernel.occ import compound
        from ..viewport.handles import DragHandle

        edges = []
        for index, corner in enumerate(self._base_corners):
            for bit in (1, 2, 4):
                other = index ^ bit
                if other <= index:
                    continue
                edges.append(
                    BRepBuilderAPI_MakeEdge(
                        gp_Pnt(*corner), gp_Pnt(*self._base_corners[other])
                    ).Edge()
                )
        self._scale_frame = self.window_.stage.viewport.show_overlay_shape(
            compound(edges), self.window_.palette_.accent, transparency=0.0
        )

        viewport = self.window_.stage.viewport
        for index, corner in enumerate(self._base_corners):
            direction = tuple(corner[i] - self._pivot[i] for i in range(3))
            handle = viewport.handles.add(
                DragHandle(corner, direction, f"uniform_scale:{index}"),
                viewport,
                self.window_.palette_.accent,
            )
            self._scale_handles.append(handle)
        viewport.handle_pressed.connect(self._on_scale_pressed)
        viewport.handle_dragged.connect(self._on_scale_dragged)
        viewport.refresh()

    def _on_scale_pressed(self, key: str) -> None:
        if not key.startswith("uniform_scale:") or self._pivot is None:
            return
        try:
            corner = self._base_corners[int(key.partition(":")[2])]
        except (ValueError, IndexError):
            return
        self._drag_radius = math.dist(self._pivot, corner)
        self.fields["factor"].set_value(1.0)
        self._ensure_scale_preview()

    def _ensure_scale_preview(self) -> None:
        if self._scale_previewing:
            return
        from ...kernel.occ import compound

        shapes = [
            body.shape
            for body in (
                self.window_.document.body(name) for name in self.selection.bodies
            )
            if body is not None and body.shape is not None
        ]
        if not shapes:
            return
        viewport = self.window_.stage.viewport
        viewport.show_ghost(
            compound(shapes), self.window_.palette_.accent, transparency=0.12
        )
        for name in self.selection.bodies:
            viewport.set_transparency(
                self.window_._presentations.get(name), 0.76
            )
        self._scale_previewing = True

    def _on_scale_dragged(self, key: str, distance: float, finished: bool) -> None:
        if (
            not key.startswith("uniform_scale:")
            or self._pivot is None
            or self._drag_radius <= 1.0e-9
        ):
            return
        from PySide6.QtGui import QCursor
        from OCP.gp import gp_Pnt, gp_Trsf

        factor = max(0.01, 1.0 + distance / self._drag_radius)
        self.fields["factor"].set_value(round(factor, 4))
        transform = gp_Trsf()
        transform.SetScale(gp_Pnt(*self._pivot), factor)
        viewport = self.window_.stage.viewport
        viewport.transform_ghost(transform)
        if self._scale_frame is not None:
            self._scale_frame.SetLocalTransformation(transform)
            viewport.context.Redisplay(self._scale_frame, False)
        for handle, corner in zip(self._scale_handles, self._base_corners):
            handle.move_to(tuple(
                self._pivot[i] + (corner[i] - self._pivot[i]) * factor
                for i in range(3)
            ))
        self.window_.set_hint(f"Uniform scale {factor:.3f}×")
        self.window_.stage.drag_readout.show_text(
            self.window_.stage.mapFromGlobal(QCursor.pos()),
            f"{factor * 100.0:.1f}%",
            f"Uniform  {factor:.4f}×",
            self.window_.palette_.accent,
        )
        viewport.refresh()
        if finished:
            self.window_.stage.drag_readout.finish()
            if abs(factor - 1.0) > 1.0e-6:
                self.commit()
            else:
                self._clear_scale_preview()

    def _clear_scale_preview(self) -> None:
        viewport = self.window_.stage.viewport
        viewport.clear_ghost()
        if self._scale_previewing:
            for name in self.selection.bodies:
                viewport.set_transparency(
                    self.window_._presentations.get(name), 0.0
                )
        self._scale_previewing = False

    def teardown(self) -> None:
        viewport = self.window_.stage.viewport
        self._clear_scale_preview()
        self.window_.stage.drag_readout.finish()
        try:
            viewport.handle_pressed.disconnect(self._on_scale_pressed)
            viewport.handle_dragged.disconnect(self._on_scale_dragged)
        except (RuntimeError, TypeError):
            pass
        viewport.handles.clear(viewport)
        if self._scale_frame is not None:
            viewport.erase(self._scale_frame)
        self._scale_frame = None

    def commit(self) -> None:
        bodies = self.selection.bodies
        if not _need(self.window_, bool(bodies), "Select a body to scale."):
            return
        factor = self.value("factor", 1.0)
        per_axis = {k: self.value(k, 1.0) for k in ("sx", "sy", "sz")}
        # A per-axis field left at 1 while Scale says 2 means "follow Scale",
        # not "keep this axis the same" -- otherwise the obvious way to double
        # a part silently only doubles nothing.
        if all(abs(v - 1.0) < 1e-12 for v in per_axis.values()):
            inputs = {"factor": self.expression("factor", "1")}
        else:
            inputs = {k: self.expression(k, "1") for k in per_axis}
        if all(abs(self.value(k, 1.0) - 1.0) < 1e-12
               for k in ("factor", "sx", "sy", "sz")):
            self.window_.set_hint("Nothing to scale — enter a factor.")
            return

        self.window_.history.record("Scale")
        for name in bodies:
            feature_inputs = {"body": BodyRef(name), **inputs}
            if self._pivot is not None:
                feature_inputs["pivot"] = list(self._pivot)
            self.window_.document.add_feature(
                ScaleFeature(
                    inputs=feature_inputs, outputs=[name]
                )
            )
        self.window_.mark_dirty()
        self.window_.rebuild()
        self.window_.cancel_tool()


@register_tool("shell")
class ShellPanel(_SelectionTool):
    """Hollow a solid out, leaving walls of a chosen thickness.

    Named Hollow rather than Shell in the UI: "shell" is the CAD term, but the
    thing people arrive wanting is a bucket, and they look for the word they
    already have.
    """

    title = "Hollow"
    confirm_label = "Apply"

    def build(self) -> None:
        faces = self.selection.planar_faces()
        self.set_subtitle(
            f"{len(faces)} face(s) will be opened. The remaining walls keep "
            "this thickness."
            if faces else
            "Select the flat face to open. The remaining walls keep this "
            "thickness."
        )
        self.add_field("thickness", "Wall thickness", 2.0)

    def commit(self) -> None:
        faces = self.selection.planar_faces()
        if not _need(
            self.window_, bool(faces),
            "Select the flat face to open before hollowing.",
        ):
            return
        body = faces[0].body
        document = self.window_.document
        self.window_.add_feature(
            ShellFeature(
                inputs={
                    "body": BodyRef(body),
                    "faces": [f.reference(document) for f in faces if f.body == body],
                    "thickness": self.expression("thickness", "2"),
                },
                outputs=[body],
            )
        )
        self.window_.cancel_tool()


class VentPanel(_SelectionTool):
    """Perforate a flat wall with a hex grid -- a fan grille, in place.

    Dispatches on the selection: with a flat face picked it cuts through that
    wall, with nothing picked it hands over to the Shape panel to make a
    standalone vent plate. Same command, whichever way round the user got here.
    """

    title = "Vent"
    confirm_label = "Cut"

    def build(self) -> None:
        faces = self.selection.planar_faces()
        self.set_subtitle(
            f"{faces[0].describe()} — a hex grid will be cut through it."
            if faces else "Select the flat wall the fan blows through."
        )
        self.add_field("across_flats", "Hole size", 5.0)
        self.add_field("wall", "Wall between", 1.2)
        self.add_field("margin", "Border", 2.0)

    def commit(self) -> None:
        faces = self.selection.planar_faces()
        if not _need(
            self.window_, bool(faces), "Select the flat face to vent."
        ):
            return
        pick = faces[0]
        self.window_.add_feature(
            VentCutFeature(
                inputs={
                    "body": BodyRef(pick.body),
                    "face": pick.reference(self.window_.document),
                    "across_flats": self.expression("across_flats", "5"),
                    "wall": self.expression("wall", "1.2"),
                    "margin": self.expression("margin", "2"),
                },
                outputs=[pick.body],
            )
        )
        self.window_.cancel_tool()


@register_tool("vent")
def open_vent_tool(window):
    """Vent means two different things depending on what is selected.

    With a flat face picked, the user is pointing at the wall they want
    perforated, so cut it there. With nothing picked they want a grille as its
    own part, which is the Shape panel's job. One command either way -- asking
    "plate or cutter?" would be asking about something the selection already
    says.
    """
    from .shapes import ShapePanel

    if window.selection.planar_faces():
        return VentPanel(window, window.palette_)
    panel = ShapePanel(window, window.palette_)
    panel.choose("vent_plate")
    return panel
