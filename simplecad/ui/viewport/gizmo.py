"""The transform gizmo.

X/Y/Z arrows, plane handles and rotation rings, wrapped around OCCT's
``AIS_Manipulator``. Dragging a handle moves the body live; releasing commits a
parametric Move feature, so a drag is as editable afterwards as a typed number.

The gizmo owns no geometry of its own -- it reports the transform the user
dragged out, and the window turns that into a feature. That keeps the undo
history and the feature tree the single source of truth about where things are.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QObject, Signal


class TransformGizmo(QObject):
    """Drag handles attached to a body."""

    #: Emitted while dragging: (dx, dy, dz, rx, ry, rz) so far.
    changed = Signal(float, float, float, float, float, float)
    #: Emitted on release with the final transform.
    committed = Signal(float, float, float, float, float, float)

    def __init__(self, viewport, parent=None) -> None:
        super().__init__(parent)
        self.viewport = viewport
        self._manipulator = None
        self._attached = None
        self._dragging = False
        self._start = None
        self._last = None

    # -- lifecycle -------------------------------------------------------
    def attach(self, presentation, allow_scale: bool = False) -> bool:
        """Put the gizmo on a presentation. Returns False if it cannot."""
        from OCP.AIS import (
            AIS_MM_Rotation, AIS_MM_Scaling, AIS_MM_Translation,
            AIS_MM_TranslationPlane, AIS_Manipulator,
        )

        context = self.viewport.context
        if context is None or presentation is None:
            return False
        self.detach()

        manipulator = AIS_Manipulator()
        manipulator.SetPart(AIS_MM_Translation, True)
        manipulator.SetPart(AIS_MM_Rotation, True)
        manipulator.SetPart(AIS_MM_TranslationPlane, True)
        manipulator.SetPart(AIS_MM_Scaling, allow_scale)
        # Activating on hover is what makes the handles feel like handles:
        # the one under the cursor lights up and takes the drag.
        manipulator.SetModeActivationOnDetection(True)
        try:
            manipulator.Attach(presentation)
        except Exception:  # noqa: BLE001 - an empty body has no box to attach to
            return False
        context.Display(manipulator, False)
        self._manipulator = manipulator
        self._attached = presentation
        self.viewport.refresh()
        return True

    def detach(self) -> None:
        context = self.viewport.context
        if self._manipulator is not None and context is not None:
            try:
                self._manipulator.Detach()
            except Exception:  # noqa: BLE001
                pass
            context.Remove(self._manipulator, False)
        self._manipulator = None
        self._attached = None
        self._dragging = False
        self._start = None
        self._last = None
        self.viewport.refresh()

    @property
    def active(self) -> bool:
        return self._manipulator is not None

    @property
    def dragging(self) -> bool:
        return self._dragging

    # -- interaction -----------------------------------------------------
    def press(self, device_x: int, device_y: int) -> bool:
        """Begin a drag if a handle is under the cursor."""
        if self._manipulator is None:
            return False
        context = self.viewport.context
        if context is None:
            return False
        context.MoveTo(device_x, device_y, self.viewport.view, False)
        if not self._manipulator.HasActiveMode():
            return False
        self._manipulator.StartTransform(device_x, device_y, self.viewport.view)
        self._dragging = True
        self._start = (device_x, device_y)
        self._last = None
        return True

    def drag(self, device_x: int, device_y: int) -> None:
        if not self._dragging or self._manipulator is None:
            return
        transform = self._manipulator.Transform(
            device_x, device_y, self.viewport.view
        )
        self.viewport.refresh()
        if transform is not None:
            # Remember it: on release the accumulated transform has to come from
            # the last drag position. Re-querying at the *start* position -- as
            # an earlier version did -- always yields zero, so every drag
            # committed nothing.
            self._last = _decompose(transform)
            self.changed.emit(*self._last)

    def release(self, apply: bool = True):
        """End the drag. Returns the transform components the user dragged out."""
        if not self._dragging or self._manipulator is None:
            return None
        manipulator = self._manipulator
        components = self._last
        # Roll the preview back: the real move is applied as a feature, so
        # letting the manipulator keep its own transform would apply it twice.
        manipulator.StopTransform(False)
        manipulator.DeactivateCurrentMode()
        self._dragging = False
        self._last = None
        self.viewport.refresh()
        return components


def _decompose(transform) -> tuple[float, float, float, float, float, float]:
    """Split a ``gp_Trsf`` into translation and XYZ rotation, in mm and degrees.

    Euler angles in extrinsic XYZ order, because that is the order
    ``MoveFeature`` composes its rotations in -- so a dragged transform and a
    typed one mean the same thing.
    """
    translation = transform.TranslationPart()
    rx = ry = rz = 0.0
    try:
        from OCP.gp import gp_Extrinsic_XYZ

        angles = transform.GetRotation().GetEulerAngles(gp_Extrinsic_XYZ)
        rx, ry, rz = (math.degrees(a) for a in angles)
    except Exception:  # noqa: BLE001 - a pure translation carries no rotation
        pass
    return (translation.X(), translation.Y(), translation.Z(), rx, ry, rz)
