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
from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, Signal


ROTATION_SNAP_STEP = 90.0
ROTATION_SNAP_CAPTURE = 5.0
ROTATION_SNAP_RELEASE = 8.0


@dataclass
class RotationSnapState:
    """Unwrap a ring drag and magnetically lock it to quarter turns."""

    previous: float | None = None
    accumulated: float = 0.0
    locked: float | None = None

    def update(self, wrapped: float, bypass: bool = False) -> tuple[float, bool]:
        if self.previous is None:
            self.accumulated = wrapped
        else:
            delta = (wrapped - self.previous + 180.0) % 360.0 - 180.0
            self.accumulated += delta
        self.previous = wrapped

        if bypass:
            self.locked = None
            return self.accumulated, False
        if self.locked is not None:
            if abs(self.accumulated - self.locked) <= ROTATION_SNAP_RELEASE:
                return self.locked, True
            self.locked = None
        target = round(self.accumulated / ROTATION_SNAP_STEP) * ROTATION_SNAP_STEP
        if abs(self.accumulated - target) <= ROTATION_SNAP_CAPTURE:
            self.locked = target
            return target, True
        return self.accumulated, False


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
        self._allow_translation = True
        self._allow_rotation = True
        self._pivot = (0.0, 0.0, 0.0)
        self._rotation_state = RotationSnapState()
        self._rotation_snapped = False
        self.translation_filter = None
        self.translation_snap = ""

    # -- lifecycle -------------------------------------------------------
    def attach(
        self,
        presentation,
        allow_scale: bool = False,
        allow_translation: bool = True,
        allow_rotation: bool = True,
        allow_planes: bool = True,
        pivot=None,
    ) -> bool:
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
        manipulator.SetPart(AIS_MM_Translation, allow_translation)
        manipulator.SetPart(AIS_MM_Rotation, allow_rotation)
        manipulator.SetPart(
            AIS_MM_TranslationPlane, allow_translation and allow_planes
        )
        manipulator.SetPart(AIS_MM_Scaling, allow_scale)
        # Activating on hover is what makes the handles feel like handles:
        # the one under the cursor lights up and takes the drag.
        manipulator.SetModeActivationOnDetection(True)
        # Keep a generous, predictable on-screen target however far the camera
        # is from the part. Tiny world-sized arrows are the main reason a move
        # direction becomes difficult to acquire after zooming out.
        try:
            presentations = (
                list(presentation)
                if isinstance(presentation, (list, tuple))
                else [presentation]
            )
            presentations = [item for item in presentations if item is not None]
            if not presentations:
                return False
            if len(presentations) == 1:
                manipulator.Attach(presentations[0])
            else:
                from OCP.AIS import AIS_ManipulatorObjectSequence

                sequence = AIS_ManipulatorObjectSequence()
                for item in presentations:
                    sequence.Append(item)
                manipulator.Attach(sequence)
            if pivot is not None:
                from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

                manipulator.SetPosition(
                    gp_Ax2(gp_Pnt(*pivot), gp_Dir(0.0, 0.0, 1.0))
                )
            # Attach derives a world-space size from the object bounds, so the
            # screen-persistent size has to be asserted afterwards.
            manipulator.SetZoomPersistence(True)
            manipulator.SetSize(96.0)
            manipulator.SetGap(8.0)
            manipulator.SetWidth(2.5)
        except Exception:  # noqa: BLE001 - an empty body has no box to attach to
            return False
        context.Display(manipulator, False)
        self._manipulator = manipulator
        self._attached = presentations
        self._allow_translation = bool(allow_translation)
        self._allow_rotation = bool(allow_rotation)
        self._pivot = tuple(pivot) if pivot is not None else (0.0, 0.0, 0.0)
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
        self._rotation_state = RotationSnapState()
        self._rotation_snapped = False
        self.translation_filter = None
        self.translation_snap = ""
        self.viewport.refresh()

    @property
    def active(self) -> bool:
        return self._manipulator is not None

    @property
    def dragging(self) -> bool:
        return self._dragging

    @property
    def rotation_snapped(self) -> bool:
        return self._rotation_snapped

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
        self._rotation_state = RotationSnapState()
        self._rotation_snapped = False
        return True

    def drag(self, device_x: int, device_y: int, modifiers=None) -> None:
        if not self._dragging or self._manipulator is None:
            return
        if self._allow_rotation and not self._allow_translation:
            from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

            raw = gp_Trsf()
            if not self._manipulator.ObjectTransformation(
                device_x, device_y, self.viewport.view, raw
            ):
                return
            components = _decompose(
                raw, allow_translation=False, allow_rotation=True
            )
            axis_index = int(self._manipulator.ActiveAxisIndex())
            if axis_index not in (0, 1, 2):
                return
            wrapped = components[3 + axis_index]
            bypass = bool(modifiers is not None and modifiers & Qt.ShiftModifier)
            angle, self._rotation_snapped = self._rotation_state.update(
                wrapped, bypass=bypass
            )
            axes = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
            transform = gp_Trsf()
            transform.SetRotation(
                gp_Ax1(gp_Pnt(*self._pivot), gp_Dir(*axes[axis_index])),
                math.radians(angle),
            )
            self._manipulator.Transform(transform)
            values = [0.0, 0.0, 0.0]
            values[axis_index] = angle
            self._last = (0.0, 0.0, 0.0, *values)
        else:
            if self.translation_filter is not None and self._allow_translation:
                from OCP.AIS import AIS_MM_Translation, AIS_MM_TranslationPlane
                from OCP.gp import gp_Trsf, gp_Vec

                raw = gp_Trsf()
                if not self._manipulator.ObjectTransformation(
                    device_x, device_y, self.viewport.view, raw
                ):
                    return
                values = _decompose(
                    raw, allow_translation=True, allow_rotation=False
                )
                axis_index = int(self._manipulator.ActiveAxisIndex())
                mode = self._manipulator.ActiveMode()
                translation = list(values[:3])
                if int(mode) == int(AIS_MM_Translation) and axis_index in (0, 1, 2):
                    translation = [
                        value if index == axis_index else 0.0
                        for index, value in enumerate(translation)
                    ]
                elif int(mode) == int(AIS_MM_TranslationPlane) and axis_index in (0, 1, 2):
                    # The plane handle's axis is its normal, so that component
                    # is numerical drift rather than an intended move.
                    translation[axis_index] = 0.0
                # Some OCCT builds report an axis drag as a dominant component
                # plus tiny numerical leakage even when ActiveMode is exposed
                # inconsistently. Do not turn that leakage into authored Y/Z
                # expressions on release.
                dominant = max(range(3), key=lambda index: abs(translation[index]))
                magnitude = abs(translation[dominant])
                if magnitude > 1.0e-8:
                    for index in range(3):
                        if index != dominant and abs(translation[index]) < magnitude * .002:
                            translation[index] = 0.0
                bypass = bool(modifiers is not None and modifiers & Qt.ShiftModifier)
                dx, dy, dz = self.translation_filter(tuple(translation), bypass)
                transform = gp_Trsf()
                transform.SetTranslation(gp_Vec(dx, dy, dz))
                self._manipulator.Transform(transform)
                self._last = (dx, dy, dz, 0.0, 0.0, 0.0)
            else:
                transform = self._manipulator.Transform(
                    device_x, device_y, self.viewport.view
                )
                if transform is not None:
                    self._last = _decompose(
                        transform,
                        allow_translation=self._allow_translation,
                        allow_rotation=self._allow_rotation,
                    )
        self.viewport.refresh()
        if self._last is not None:
            # Remember it: on release the accumulated transform has to come from
            # the last drag position. Re-querying at the *start* position -- as
            # an earlier version did -- always yields zero, so every drag
            # committed nothing.
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
        self._rotation_state = RotationSnapState()
        self._rotation_snapped = False
        self.viewport.refresh()
        return components


def _decompose(
    transform, *, allow_translation: bool = True, allow_rotation: bool = True
) -> tuple[float, float, float, float, float, float]:
    """Split a ``gp_Trsf`` into translation and XYZ rotation, in mm and degrees.

    Euler angles in extrinsic XYZ order, because that is the order
    ``MoveFeature`` composes its rotations in -- so a dragged transform and a
    typed one mean the same thing.
    """
    translation = transform.TranslationPart()
    dx, dy, dz = (
        (translation.X(), translation.Y(), translation.Z())
        if allow_translation
        else (0.0, 0.0, 0.0)
    )
    rx = ry = rz = 0.0
    if allow_rotation:
        try:
            from OCP.gp import gp_Extrinsic_XYZ

            angles = transform.GetRotation().GetEulerAngles(gp_Extrinsic_XYZ)
            rx, ry, rz = (math.degrees(a) for a in angles)
        except Exception:  # noqa: BLE001 - a pure translation carries no rotation
            pass
    return (dx, dy, dz, rx, ry, rz)
