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
#: How far a press may miss a handle and still take it, in widget pixels.
#: OCCT answers only for the exact pixel and an arrow or ring is a few pixels
#: wide, so a near miss used to select the face behind the gizmo instead.
GRAB_REACH_PX = (5, 10, 15, 20)


@dataclass
class RotationSnapState:
    """Unwrap a ring drag and magnetically lock it to multiples of *step*.

    The starting angle is deliberately *not* a detent. It used to be, so any
    drag under the release distance (8 degrees) snapped back to zero and
    committed nothing -- a small rotation simply failed.
    """

    previous: float | None = None
    accumulated: float = 0.0
    locked: float | None = None
    step: float = ROTATION_SNAP_STEP

    def update(self, wrapped: float, bypass: bool = False,
               fine: bool = False) -> tuple[float, bool]:
        if self.previous is None:
            self.accumulated = wrapped
        else:
            delta = (wrapped - self.previous + 180.0) % 360.0 - 180.0
            self.accumulated += delta
        self.previous = wrapped

        if bypass:
            self.locked = None
            return self.accumulated, False
        if fine:
            # Ctrl: exact whole degrees, for numbers that read cleanly.
            self.locked = None
            return float(round(self.accumulated)), True
        capture = min(ROTATION_SNAP_CAPTURE, self.step * 0.2)
        release = capture * ROTATION_SNAP_RELEASE / ROTATION_SNAP_CAPTURE
        if self.locked is not None:
            if abs(self.accumulated - self.locked) <= release:
                return self.locked, True
            self.locked = None
        target = round(self.accumulated / self.step) * self.step
        if target != 0.0 and abs(self.accumulated - target) <= capture:
            self.locked = target
            return target, True
        return self.accumulated, False


_AXES = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def ring_angle(transform, axis) -> float:
    """The signed angle, in degrees, a rotation turns about a known *axis*.

    Read straight from the quaternion: ``q = (axis * sin(t/2), cos(t/2))``.
    Euler angles cannot be used for this -- the middle angle of any Euler
    convention is confined to +/-90 degrees, which is why the Y ring used to
    fold back on itself past a quarter turn and could never reach 180.
    """
    q = transform.GetRotation()
    along = q.X() * axis[0] + q.Y() * axis[1] + q.Z() * axis[2]
    degrees = math.degrees(2.0 * math.atan2(along, q.W()))
    return (degrees + 180.0) % 360.0 - 180.0


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
        self._rotation_state = RotationSnapState(step=ROTATION_SNAP_STEP)
        self._rotation_snapped = False
        self._screen_fallback = None
        self._offset = (0, 0)
        self._free_axis = None
        self._free_start = None
        self.rotation_step = ROTATION_SNAP_STEP
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
            # In framebuffer pixels: unscaled, a 2x display drew it half size.
            ratio = self.viewport.devicePixelRatioF()
            manipulator.SetZoomPersistence(True)
            # Arrows are drawn far shorter than rings for the same size.
            manipulator.SetSize((144.0 if allow_translation else 96.0) * ratio)
            manipulator.SetGap(8.0 * ratio)
            manipulator.SetWidth(2.5 * ratio)
        except Exception:  # noqa: BLE001 - an empty body has no box to attach to
            return False
        context.Display(manipulator, False)
        self._manipulator = manipulator
        self._attached = presentations
        self._allow_translation = bool(allow_translation)
        self._allow_rotation = bool(allow_rotation)
        self._pivot = tuple(pivot) if pivot is not None else (0.0, 0.0, 0.0)
        from ...core.settings import number_preference
        self.rotation_step = number_preference("rotation_snap")
        self._rotation_state = RotationSnapState(step=self.rotation_step)
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
        self._rotation_state = RotationSnapState(step=self.rotation_step)
        self._rotation_snapped = False
        self._free_axis = self._free_start = None
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
        """Begin a drag on a handle, a handle just missed, or the part itself."""
        if self._manipulator is None:
            return False
        context = self.viewport.context
        if context is None:
            return False
        grab = (device_x, device_y) if self._handle_under(device_x, device_y) else None
        on_part = context.HasDetected() and any(
            context.DetectedInteractive() is item for item in self._attached or ()
        )
        if grab is None:
            grab = self._nearby_handle(device_x, device_y)
        self._free_axis = self._free_start = None
        self._screen_fallback = None
        if grab is None:
            if not on_part:
                return False
            # Every handle missed, but the press is on the part: the part is
            # the handle. Move slides it over the ground, Rotate turns it like
            # a turntable -- about whichever axis faces the camera.
            self._free_axis = self._facing_axis()
            grab = (device_x, device_y)
            if self._allow_translation:
                origin = self._manipulator.Position().Location()
                origin = (origin.X(), origin.Y(), origin.Z())
                hit = self._plane_hit(device_x, device_y, origin)
                if hit is None:
                    return False
                self._free_start = (hit, origin)
            else:
                self._screen_fallback = (device_x, device_y, 0.0)
        self._offset = (grab[0] - device_x, grab[1] - device_y)
        self._manipulator.StartTransform(grab[0], grab[1], self.viewport.view)
        self._dragging = True
        self._start = (device_x, device_y)
        self._last = None
        self._rotation_state = RotationSnapState(step=self.rotation_step)
        self._rotation_snapped = False
        return True

    def _handle_under(self, device_x: int, device_y: int) -> bool:
        """Whether OCCT finds a gizmo handle at exactly this device pixel.

        Asked of the detection, not of ``HasActiveMode`` alone: the manipulator
        keeps its last mode after the cursor leaves it, so a press on the part
        after hovering a ring used to turn that ring.
        """
        context = self.viewport.context
        context.MoveTo(device_x, device_y, self.viewport.view, False)
        return bool(
            context.HasDetected()
            and context.DetectedInteractive() is self._manipulator
            and self._manipulator.HasActiveMode()
        )

    def _nearby_handle(self, device_x: int, device_y: int):
        """The nearest point within ``GRAB_REACH_PX`` that is on a handle."""
        ratio = self.viewport.devicePixelRatioF()
        # ponytail: a pixel search through OCCT's own detection -- up to 48
        # MoveTo calls, and only on a press that missed. Switch to distances
        # from the projected handles if presses ever lag on huge models.
        for reach in GRAB_REACH_PX:
            for step in range(12):
                angle = step * math.pi / 6.0
                x = round(device_x + reach * ratio * math.cos(angle))
                y = round(device_y + reach * ratio * math.sin(angle))
                if self._handle_under(x, y):
                    return (x, y)
        return None

    def _facing_axis(self) -> int:
        """The world axis the camera looks along most, preferring Z (the ground)."""
        eye = self.viewport.view.Camera().Direction()
        if abs(eye.Z()) > 0.3:
            return 2
        return 0 if abs(eye.X()) > abs(eye.Y()) else 1

    def _plane_hit(self, device_x: int, device_y: int, origin):
        """Where the cursor ray meets the free-drag plane through *origin*."""
        try:
            px, py, pz, vx, vy, vz = self.viewport.view.ConvertWithProj(device_x, device_y)
        except Exception:  # noqa: BLE001 - no ray off the edge of the view
            return None
        return self.viewport._ray_plane(
            ((px, py, pz), (vx, vy, vz)), origin, _AXES[self._free_axis]
        )

    def drag(self, device_x: int, device_y: int, modifiers=None) -> None:
        if not self._dragging or self._manipulator is None:
            return
        # A press that took a handle it just missed drags as if it had hit it.
        device_x += self._offset[0]
        device_y += self._offset[1]
        if self._allow_rotation and not self._allow_translation:
            from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

            axis_index = (self._free_axis if self._free_axis is not None
                          else int(self._manipulator.ActiveAxisIndex()))
            if axis_index not in (0, 1, 2):
                return
            axis = _AXES[axis_index]
            raw = gp_Trsf()
            if self._screen_fallback is None and self._manipulator.ObjectTransformation(
                device_x, device_y, self.viewport.view, raw
            ):
                wrapped = ring_angle(raw, axis)
            else:
                # OCCT cannot intersect the cursor ray with a ring seen edge-on
                # -- two of the three rings in every standard view -- and used
                # to ignore the drag outright. Carry on from the screen.
                wrapped = self._screen_ring_angle(device_x, device_y, axis)
                if wrapped is None:
                    return
            bypass = bool(modifiers is not None and modifiers & Qt.ShiftModifier)
            fine = bool(modifiers is not None and modifiers & Qt.ControlModifier)
            angle, self._rotation_snapped = self._rotation_state.update(
                wrapped, bypass=bypass, fine=fine
            )
            transform = gp_Trsf()
            transform.SetRotation(
                gp_Ax1(gp_Pnt(*self._pivot), gp_Dir(*axis)),
                math.radians(angle),
            )
            self._manipulator.Transform(transform)
            values = [0.0, 0.0, 0.0]
            values[axis_index] = angle
            self._last = (0.0, 0.0, 0.0, *values)
        elif self._free_start is not None:
            from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

            start, origin = self._free_start
            hit = self._plane_hit(device_x, device_y, origin)
            if hit is None:
                return
            translation = [hit[i] - start[i] for i in range(3)]
            translation[self._free_axis] = 0.0
            if self.translation_filter is not None:
                bypass = bool(modifiers is not None and modifiers & Qt.ShiftModifier)
                fine = bool(modifiers is not None and modifiers & Qt.ControlModifier)
                translation = self.translation_filter(tuple(translation), bypass, fine)
            dx, dy, dz = translation
            transform = gp_Trsf()
            transform.SetTranslation(gp_Vec(dx, dy, dz))
            self._manipulator.Transform(transform)
            # The arrows travel with the part; StopTransform puts them back.
            self._manipulator.SetPosition(gp_Ax2(
                gp_Pnt(origin[0] + dx, origin[1] + dy, origin[2] + dz),
                gp_Dir(0.0, 0.0, 1.0),
            ))
            self._last = (dx, dy, dz, 0.0, 0.0, 0.0)
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
                fine = bool(modifiers is not None and modifiers & Qt.ControlModifier)
                dx, dy, dz = self.translation_filter(tuple(translation), bypass, fine)
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

    def _screen_ring_angle(self, device_x, device_y, axis):
        """The ring angle from screen motion alone, continuing the current drag.

        Face-on enough, it is the angle swept around the projected pivot.
        Edge-on, the ring is a line on screen, so motion *along* that line
        turns it -- the near side of a ring moves along ``view x axis`` for a
        positive turn -- at one radian per gizmo radius.
        """
        view = self.viewport.view
        try:
            px, py = view.Convert(*self._pivot)
            eye = view.Camera().Direction()
        except Exception:  # noqa: BLE001 - behind the camera
            return None
        if self._screen_fallback is None:
            base = self._rotation_state.previous or 0.0
            self._screen_fallback = (device_x, device_y, base)
        x0, y0, base = self._screen_fallback
        d = (eye.X(), eye.Y(), eye.Z())
        facing = sum(a * b for a, b in zip(axis, d))
        if abs(facing) > 0.35:
            swept = (math.atan2(-(device_y - py), device_x - px)
                     - math.atan2(-(y0 - py), x0 - px))
            turned = math.degrees(swept) * (1.0 if facing < 0 else -1.0)
        else:
            tangent = (d[1] * axis[2] - d[2] * axis[1],
                       d[2] * axis[0] - d[0] * axis[2],
                       d[0] * axis[1] - d[1] * axis[0])
            reach = self.viewport.mm_per_pixel(self._pivot) * 50.0
            try:
                tx, ty = view.Convert(*(self._pivot[i] + tangent[i] * reach for i in range(3)))
            except Exception:  # noqa: BLE001
                return None
            sx, sy = tx - px, -(ty - py)
            length = math.hypot(sx, sy)
            if length < 1e-6:
                return None
            radius = 96.0 * self.viewport.devicePixelRatioF()
            moved = ((device_x - x0) * sx - (device_y - y0) * sy) / length
            turned = math.degrees(moved / radius)
        return (base + turned + 180.0) % 360.0 - 180.0

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
        self._rotation_state = RotationSnapState(step=self.rotation_step)
        self._rotation_snapped = False
        self._screen_fallback = None
        self._offset = (0, 0)
        self._free_axis = self._free_start = None
        self.viewport.refresh()
        return components


def _decompose(
    transform, *, allow_translation: bool = True, allow_rotation: bool = True
) -> tuple[float, float, float, float, float, float]:
    """Split a ``gp_Trsf`` into translation and XYZ rotation, in mm and degrees.

    Extrinsic XYZ: about world X first, then Y, then Z (``R = Rz*Ry*Rx``),
    which is how a ``MoveFeature`` with ``order: "xyz"`` composes them. Not
    for reading a ring drag -- see :func:`ring_angle`.
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
