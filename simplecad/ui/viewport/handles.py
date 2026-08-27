"""Grabbable handles floating on the model.

A handle is a small sphere at a point in space that drags along a direction in
space. Fillet uses one on an edge; Split uses five on its cutting plane. They
are what turns "type a radius" into "pull the corner out until it looks right".

Two decisions worth stating, because both were the second attempt:

**Hit testing is screen-space, not OCCT selection.** A handle registered as a
selectable ``AIS_Shape`` joins the pick stack, which means it can be selected,
it fights the geometry underneath for the cursor, and it needs its own
activation mode juggled against the four the window already keeps live. Instead
the handle is displayed with no selection mode at all and
:meth:`DragHandle.hit` simply asks how far the cursor is from where the anchor
projects to. That works from any camera angle, needs no OCCT cooperation, and
cannot disturb what the user has selected.

**The sphere is built once and scaled by its local transformation.** A handle
has to keep the same size on screen however far you zoom, and rebuilding the
sphere for every wheel click would re-tessellate on every frame of a zoom.
Moving and resizing through ``SetLocalTransformation`` is a matrix update with
no geometry work behind it.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint

#: Handle radius, in widget pixels. Big enough to be an obvious target, small
#: enough not to hide the geometry it is sitting on.
HANDLE_PX = 7.0
#: How close the cursor has to be to grab one, in widget pixels. Deliberately
#: larger than the handle itself -- the drawn circle is the affordance, not the
#: hit box, and a target you have to hit exactly is a target you miss.
GRAB_PX = 15.0


def _unit(vector) -> tuple[float, float, float]:
    length = math.sqrt(sum(v * v for v in vector))
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return tuple(v / length for v in vector)


class DragHandle:
    """A sphere at *anchor* that drags along *direction*."""

    def __init__(
        self,
        anchor: tuple[float, float, float],
        direction: tuple[float, float, float],
        key: str = "",
    ) -> None:
        self.anchor = tuple(float(v) for v in anchor)
        self.direction = _unit(direction)
        self.key = key
        self.presentation = None
        self._viewport = None

    # -- display ---------------------------------------------------------
    def show(self, viewport, color: str) -> None:
        """Display the handle, replacing any presentation it already had."""
        from OCP.AIS import AIS_Shape
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere
        from OCP.Quantity import Quantity_Color, Quantity_TOC_sRGB

        from ..theme import rgb

        self.hide(viewport)
        if viewport.context is None:
            return
        # A unit sphere at the origin. Everything positional lives in the local
        # transformation, so this shape is never rebuilt.
        shape = BRepPrimAPI_MakeSphere(1.0).Shape()
        presentation = AIS_Shape(shape)
        presentation.SetColor(Quantity_Color(*rgb(color), Quantity_TOC_sRGB))
        drawer = presentation.Attributes()
        drawer.SetFaceBoundaryDraw(False)
        # Displayed with selection mode -1: visible, never pickable. See the
        # module docstring.
        viewport.context.Display(presentation, 1, -1, False)
        self.presentation = presentation
        self._viewport = viewport
        self.rescale(viewport)

    def move_to(self, anchor, direction=None) -> None:
        """Put the handle somewhere else. Cheap: no geometry is rebuilt."""
        self.anchor = tuple(float(v) for v in anchor)
        if direction is not None:
            self.direction = _unit(direction)
        if self._viewport is not None:
            self.rescale(self._viewport)

    def rescale(self, viewport) -> None:
        """Re-place and re-size for the current camera, keeping screen size."""
        from OCP.gp import gp_Trsf, gp_Vec

        if self.presentation is None or viewport.context is None:
            return
        radius = viewport.mm_per_pixel(self.anchor) * HANDLE_PX
        if radius <= 0.0:
            return
        scale = gp_Trsf()
        scale.SetScale(_origin(), radius)
        move = gp_Trsf()
        move.SetTranslation(gp_Vec(*self.anchor))
        self.presentation.SetLocalTransformation(move.Multiplied(scale))

    def hide(self, viewport) -> None:
        if self.presentation is not None and viewport.context is not None:
            viewport.context.Remove(self.presentation, False)
        self.presentation = None

    # -- interaction -----------------------------------------------------
    def screen_position(self, viewport):
        """Where the handle is on screen, in widget pixels, or None."""
        return viewport.project(self.anchor)

    def distance_to(self, viewport, pos: QPoint) -> float:
        """How far the cursor is from the handle, in widget pixels."""
        screen = self.screen_position(viewport)
        if screen is None:
            return float("inf")
        return math.hypot(screen[0] - pos.x(), screen[1] - pos.y())

    def hit(self, viewport, pos: QPoint, radius: float = GRAB_PX) -> bool:
        return self.distance_to(viewport, pos) <= radius


def _origin():
    from OCP.gp import gp_Pnt

    return gp_Pnt(0.0, 0.0, 0.0)


class HandleSet:
    """The handles currently on screen. The viewport asks this on press."""

    def __init__(self) -> None:
        self.handles: list[DragHandle] = []

    def __bool__(self) -> bool:
        return bool(self.handles)

    def __len__(self) -> int:
        return len(self.handles)

    def __iter__(self):
        return iter(self.handles)

    def add(self, handle: DragHandle, viewport, color: str) -> DragHandle:
        handle.show(viewport, color)
        self.handles.append(handle)
        return handle

    def hit(self, viewport, pos: QPoint, radius: float = GRAB_PX):
        """The nearest handle under the cursor, or None.

        Nearest rather than first: Split puts five handles on one plane and
        two of them can overlap when the plane is seen close to edge-on.
        """
        best, best_distance = None, radius
        for handle in self.handles:
            distance = handle.distance_to(viewport, pos)
            if distance <= best_distance:
                best, best_distance = handle, distance
        return best

    def rescale(self, viewport) -> None:
        for handle in self.handles:
            handle.rescale(viewport)

    def clear(self, viewport) -> None:
        for handle in self.handles:
            handle.hide(viewport)
        self.handles = []
