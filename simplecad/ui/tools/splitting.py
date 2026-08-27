"""The Split tool: cut a part in two by dragging a plane through it.

Splitting is what you do to a bracket taller than the print plate, and the thing
you need to see while you do it is not the plane -- it is **how big the two
pieces end up**. So the plane is draggable, and the readout that follows it is
the resulting size of each half, live, updating as the plane slides. Those two
numbers are read off the bounding box rather than from a trial cut, because a
boolean per frame would stall the drag and the numbers would stop tracking the
mouse, which is the one thing this interaction cannot afford.

The plane carries five handles -- one at its centre and one at the middle of
each edge -- so that whichever way the model is turned there is always one
facing the camera to grab.
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QWidget

from ...core.document import BodyRef
from ...kernel.occ import bounding_box
from ...kernel.split import SplitFeature, side_extents
from ..theme import METRICS
from ..widgets.controls import GhostButton
from .modeling import _SelectionTool, _need
from .registry import register_tool

#: How far past the part the drawn plane extends, as a fraction of its size.
PLANE_OVERHANG = 0.18
#: Keep the cut this far from either end, so a "split" always makes two pieces.
EDGE_KEEPOUT = 0.05

AXES = {
    "x": (1.0, 0.0, 0.0),
    "y": (0.0, 1.0, 0.0),
    "z": (0.0, 0.0, 1.0),
}


def _unit(vector):
    length = math.sqrt(sum(v * v for v in vector))
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return tuple(v / length for v in vector)


def _perpendicular(normal):
    """Two unit vectors spanning the plane perpendicular to *normal*."""
    reference = (0.0, 0.0, 1.0)
    if abs(normal[2]) > 0.9:
        reference = (1.0, 0.0, 0.0)
    u = _unit((
        normal[1] * reference[2] - normal[2] * reference[1],
        normal[2] * reference[0] - normal[0] * reference[2],
        normal[0] * reference[1] - normal[1] * reference[0],
    ))
    v = _unit((
        normal[1] * u[2] - normal[2] * u[1],
        normal[2] * u[0] - normal[0] * u[2],
        normal[0] * u[1] - normal[1] * u[0],
    ))
    return u, v


@register_tool("split")
class SplitPanel(_SelectionTool):
    """Slide a cutting plane through a body, then commit it as two bodies."""

    title = "Split"
    confirm_label = "Split"
    width = 300

    SOURCES = (
        ("x", "X"),
        ("y", "Y"),
        ("z", "Z"),
        ("face", "Selected face"),
    )

    # -- construction ----------------------------------------------------
    def build(self) -> None:
        self.body_name = self._target_body()
        self.body_shape = self._body_shape()
        self._plane = None
        self._handles: list = []
        self._dragging = False
        self._drag_base = 0.0
        self._dimmed = False
        self._face_plane = self._selected_face_plane()

        self.set_subtitle(
            f"Cutting {self.body_name} into two parts — drag the plane, or "
            "type an exact position."
            if self.body_name else "Select a body to split."
        )

        self.add_section("Cut along")
        chooser = QWidget()
        grid = QGridLayout(chooser)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(METRICS.space(1))
        self._buttons: dict[str, GhostButton] = {}
        for index, (key, label) in enumerate(self.SOURCES):
            button = GhostButton(label)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            if key == "face" and self._face_plane is None:
                button.setEnabled(False)
                button.setToolTip("Select a flat face first to cut along it.")
            button.clicked.connect(lambda _=False, k=key: self.choose(k))
            if key == "face":
                # Spans the row: it is one long option under three short ones,
                # and a quarter-width button with that label reads as a stray.
                grid.addWidget(button, 1, 0, 1, 3)
            else:
                grid.addWidget(button, 0, index)
            self._buttons[key] = button
        self.add_widget(chooser)

        self.add_field("position", "Position", 0.0)
        self.sizes = self._make_sizes_label()
        self.add_widget(self.sizes)

        viewport = self.window_.stage.viewport
        viewport.handle_pressed.connect(self._on_handle_pressed)
        viewport.handle_dragged.connect(self._on_handle_dragged)

        self.choose("face" if self._face_plane is not None else self._longest_axis())

    def _make_sizes_label(self):
        from PySide6.QtWidgets import QLabel

        label = QLabel("")
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet(
            f"color:{self._palette.text}; font-family:{METRICS.font_mono};"
            f"font-size:13px; font-weight:600; padding:{METRICS.space(1)}px 0;"
        )
        return label

    def _target_body(self) -> str:
        bodies = self.selection.bodies
        return bodies[0] if bodies else ""

    def _body_shape(self):
        body = self.window_.document.body(self.body_name) if self.body_name else None
        return body.shape if body is not None else None

    def _selected_face_plane(self):
        """``(origin, normal)`` of a selected flat face, if there is one."""
        faces = self.selection.planar_faces()
        if not faces:
            return None
        info = faces[0].info
        return (tuple(info.center), _unit(tuple(info.normal)))

    def _longest_axis(self) -> str:
        """Default to cutting across the part's longest dimension.

        Which is nearly always the reason for splitting it in the first place --
        it does not fit on the plate the long way.
        """
        if self.body_shape is None:
            return "z"
        low, high = bounding_box(self.body_shape)
        spans = {key: high[i] - low[i] for i, key in enumerate("xyz")}
        return max(spans, key=spans.get)

    # -- the plane -------------------------------------------------------
    def choose(self, key: str) -> None:
        """Pick the cut direction, and re-place the plane in the middle of it."""
        if key == "face" and self._face_plane is None:
            return
        self.source = key
        for name, button in self._buttons.items():
            button.setChecked(name == key)

        if key == "face":
            _origin, self.normal = self._face_plane
        else:
            self.normal = AXES[key]

        # Position is measured from the far side of the part along the normal,
        # so 0 is one end, the full extent is the other, and the number in the
        # field reads as "this much of the part is on the near side".
        self.origin = self._low_point()
        self.span = sum(side_extents(self.body_shape, self.origin, self.normal)) \
            if self.body_shape is not None else 0.0
        field = self.fields.get("position")
        if field is not None:
            field.set_value(round(self.span / 2.0, 3))
        self._rebuild_plane()
        self.preview()

    def _low_point(self):
        """A point on the plane that just touches the low side of the body."""
        if self.body_shape is None:
            return (0.0, 0.0, 0.0)
        low, high = bounding_box(self.body_shape)
        centre = tuple((low[i] + high[i]) / 2.0 for i in range(3))
        behind, _ahead = side_extents(self.body_shape, centre, self.normal)
        return tuple(centre[i] - self.normal[i] * behind for i in range(3))

    def _plane_frame(self):
        """``(u, v, half_u, half_v)`` sizing the drawn rectangle."""
        u, v = _perpendicular(self.normal)
        if self.body_shape is None:
            return u, v, 20.0, 20.0
        low, high = bounding_box(self.body_shape)
        corners = [
            (x, y, z)
            for x in (low[0], high[0])
            for y in (low[1], high[1])
            for z in (low[2], high[2])
        ]
        centre = tuple((low[i] + high[i]) / 2.0 for i in range(3))

        def half(axis):
            return max(
                abs(sum((c[i] - centre[i]) * axis[i] for i in range(3)))
                for c in corners
            ) * (1.0 + PLANE_OVERHANG) + 1.0

        return u, v, half(u), half(v)

    def _rebuild_plane(self) -> None:
        """Build the plane presentation and its handles, at position zero.

        Built once per direction and then *moved* by a local transformation
        while it is dragged -- rebuilding a face and five spheres on every mouse
        event is exactly the kind of work that makes a drag feel sticky.
        """
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        from OCP.gp import gp_Dir, gp_Pln, gp_Pnt

        from ..viewport.handles import DragHandle

        viewport = self.window_.stage.viewport
        self._clear_scene()
        if self.body_shape is None:
            return

        u, v, half_u, half_v = self._plane_frame()
        try:
            face = BRepBuilderAPI_MakeFace(
                gp_Pln(gp_Pnt(*self.origin), gp_Dir(*self.normal)),
                -half_u, half_u, -half_v, half_v,
            ).Face()
        except Exception:  # noqa: BLE001 - a degenerate body has no plane to draw
            return
        self._plane = viewport.show_overlay_shape(
            face, self.window_.palette_.accent, transparency=0.55
        )
        # The plane and most of its handles are *inside* the part, so against an
        # opaque body the cut is invisible exactly where it matters and three of
        # the five handles cannot be grabbed at all. Making the body see-through
        # for the duration is the same move Push/Pull already makes when its
        # preview would be buried in the solid it is cutting.
        self._dim_body(0.62)

        # Centre, then the middle of each of the four edges: whichever way the
        # part is turned, at least one is facing the camera.
        offsets = [
            (0.0, 0.0),
            (half_u, 0.0), (-half_u, 0.0),
            (0.0, half_v), (0.0, -half_v),
        ]
        self._handles = [
            viewport.handles.add(
                DragHandle(
                    tuple(
                        self.origin[i] + u[i] * du + v[i] * dv for i in range(3)
                    ),
                    self.normal,
                    key="split",
                ),
                viewport, self.window_.palette_.accent,
            )
            for du, dv in offsets
        ]
        self._base_anchors = [handle.anchor for handle in self._handles]

    def _move_plane(self, position: float) -> None:
        from OCP.gp import gp_Trsf, gp_Vec

        shift = tuple(self.normal[i] * position for i in range(3))
        if self._plane is not None:
            transform = gp_Trsf()
            transform.SetTranslation(gp_Vec(*shift))
            self._plane.SetLocalTransformation(transform)
        for handle, base in zip(self._handles, getattr(self, "_base_anchors", [])):
            handle.move_to(tuple(base[i] + shift[i] for i in range(3)))
        self.window_.stage.viewport.refresh()

    # -- live feedback ---------------------------------------------------
    def preview(self) -> None:
        """Re-place the plane for the current position and update the sizes."""
        position = self._clamped(self.value("position", 0.0))
        self._move_plane(position)
        self._update_sizes(position)

    def _clamped(self, position: float) -> float:
        if self.span <= 0:
            return position
        return max(EDGE_KEEPOUT, min(self.span - EDGE_KEEPOUT, position))

    def _sides(self, position: float) -> tuple[float, float]:
        return (max(0.0, position), max(0.0, self.span - position))

    def _update_sizes(self, position: float) -> None:
        near, far = self._sides(position)
        self.sizes.setText(f"{near:.2f} mm   │   {far:.2f} mm")

    def _show_readout(self, position: float) -> None:
        from PySide6.QtGui import QCursor

        near, far = self._sides(position)
        self.window_.stage.drag_readout.show_text(
            self.window_.stage.mapFromGlobal(QCursor.pos()),
            f"{near:.2f}  │  {far:.2f} mm",
            f"{self.source.upper()} at {position:.2f} mm",
            self.window_.palette_.accent,
        )

    # -- dragging --------------------------------------------------------
    def _on_handle_pressed(self, key: str) -> None:
        if key == "split":
            self._dragging = True
            self._drag_base = self.value("position", 0.0)

    def _on_handle_dragged(self, key: str, distance: float, finished: bool) -> None:
        if key != "split" or not self._dragging:
            return
        position = self._clamped(self._drag_base + distance)
        field = self.fields.get("position")
        if field is not None:
            field.set_value(round(position, 3))
        self._move_plane(position)
        self._update_sizes(position)
        self._show_readout(position)
        if finished:
            self._dragging = False
            self.window_.stage.drag_readout.finish()

    # -- lifecycle -------------------------------------------------------
    def _dim_body(self, amount: float) -> None:
        presentation = self.window_._presentations.get(self.body_name)
        if presentation is None:
            return
        self.window_.stage.viewport.set_transparency(presentation, amount)
        self._dimmed = amount > 0.0

    def _clear_scene(self) -> None:
        viewport = self.window_.stage.viewport
        if self._plane is not None:
            viewport.erase(self._plane)
            self._plane = None
        viewport.handles.clear(viewport)
        self._handles = []

    def teardown(self) -> None:
        viewport = self.window_.stage.viewport
        self._clear_scene()
        if self._dimmed:
            self._dim_body(0.0)
        self.window_.stage.drag_readout.finish()
        for signal, slot in (
            (viewport.handle_pressed, self._on_handle_pressed),
            (viewport.handle_dragged, self._on_handle_dragged),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    def commit(self) -> None:
        if not _need(
            self.window_, bool(self.body_name) and self.body_shape is not None,
            "Select a body to split.",
        ):
            return
        position = self._clamped(self.value("position", 0.0))
        document = self.window_.document
        names = [
            document.unique_name(f"{self.body_name} A"),
            document.unique_name(f"{self.body_name} B"),
        ]
        self.window_.add_feature(
            SplitFeature(
                inputs={
                    "body": BodyRef(self.body_name),
                    "normal": list(self.normal),
                    "origin": list(self.origin),
                    "position": round(position, 4),
                    "names": names,
                },
                outputs=names,
            )
        )
        self.window_.set_hint(f"Split into {names[0]} and {names[1]}.")
        self.window_.cancel_tool()
