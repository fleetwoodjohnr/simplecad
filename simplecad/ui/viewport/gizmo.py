"""The transform gizmo.

X/Y/Z arrows, optional plane handles and rotation rings, drawn by OCCT's
``AIS_Manipulator``. Dragging a handle moves the body live; releasing commits a
parametric Move feature, so a drag is as editable afterwards as a typed number.

The gizmo owns no geometry of its own -- it reports the transform the user
dragged out, and the window turns that into a feature. That keeps the undo
history and the feature tree the single source of truth about where things are.

**Every drag is solved here, not by OCCT.** OCCT draws the handles and says
which one is under the cursor; the motion itself is the closest approach of the
cursor ray to the handle's axis, or its intersection with the handle's plane,
computed below. The constraint -- one axis, one plane, one ring -- is fixed at
press time and applied *last*, after any snapping filter, so nothing between
the mouse and the committed feature can add motion along an axis that is not
being dragged. See ``docs/specs/transforms.md``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, Signal


ROTATION_SNAP_STEP = 90.0
ROTATION_SNAP_CAPTURE = 5.0
ROTATION_SNAP_RELEASE = 8.0
#: How far a press may miss a handle and still take it, in device pixels.
#: OCCT answers only for the exact pixel and an arrow or ring is a few pixels
#: wide, so a near miss used to select the face behind the gizmo instead.
#: Searched nearest first, so the handle taken is the closest one.
GRAB_REACH_PX = (3, 6, 9, 12, 15, 18, 21)
#: On the part itself, only a handle this close beats dragging the part.
PART_GRAB_REACH_PX = 6
#: An axis this nearly along the line of sight is dragged by screen motion:
#: the closest approach of the cursor ray to it is ill-conditioned there and a
#: pixel of mouse travel became metres of motion.
AXIS_EDGE_ON = 0.97
#: A ring whose normal is this nearly across the line of sight is seen edge-on
#: and turned by screen motion along its projected line instead.
RING_EDGE_ON = 0.2
#: Around the pivot, in device pixels, where a turn is not measured: the angle
#: of a cursor right next to the centre swings wildly with every pixel.
ROTATE_DEAD_ZONE_PX = 24.0
#: The ground plane is preferred for dragging a part unless the camera is
#: this many times more face-on to a vertical one.
GROUND_PREFERENCE = 1.5

WORLD_FRAME = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
_AXES = WORLD_FRAME


# -- vector helpers ---------------------------------------------------------
def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(vector):
    length = math.sqrt(_dot(vector, vector))
    return tuple(value / length for value in vector) if length > 1e-12 else None


def axis_parameter(ray, point, axis) -> float | None:
    """How far along the line ``point + s * axis`` the cursor ray passes closest.

    None when the ray runs (nearly) along the axis and every point is as close
    as every other.
    """
    origin, direction = ray
    direction = _unit(direction)
    if direction is None:
        return None
    w0 = _sub(point, origin)
    b = _dot(axis, direction)
    denominator = 1.0 - b * b
    if denominator < 1.0e-9:
        return None
    return (b * _dot(direction, w0) - _dot(axis, w0)) / denominator


def plane_hit(ray, origin, normal, forward_only: bool = False):
    """Where the cursor ray meets the plane through *origin* with *normal*."""
    start, direction = ray
    denominator = _dot(direction, normal)
    if abs(denominator) < 1.0e-9:
        return None
    distance = _dot(_sub(origin, start), normal) / denominator
    if forward_only and distance < 0.0:
        return None                   # behind a perspective camera
    return tuple(start[i] + direction[i] * distance for i in range(3))


def constrain(components, constraint):
    """Zero every frame component the constraint does not allow.

    ``("axis", i)`` keeps component *i* only; ``("plane", i)`` drops *i*, the
    plane's normal. Exact zeros, not small numbers: an untouched axis must
    commit as ``0``.
    """
    if constraint is None:
        return tuple(components)
    kind, index = constraint
    if kind == "axis":
        return tuple(value if i == index else 0.0 for i, value in enumerate(components))
    return tuple(0.0 if i == index else value for i, value in enumerate(components))


def to_world(components, frame):
    """Frame components to a world vector. Exact for the world frame."""
    # ``+ 0.0`` folds -0.0 into 0.0, so an untouched axis never reads "-0".
    return tuple(
        sum(components[j] * frame[j][i] for j in range(3) if components[j] != 0.0) + 0.0
        for i in range(3)
    )


def to_frame(vector, frame):
    return tuple(_dot(vector, axis) for axis in frame)


def facing_axis(eye, frame=WORLD_FRAME) -> int:
    """The frame axis the camera looks along most, preferring the ground (Z).

    The plane a part slides in when it is dragged by its body is the one
    normal to this axis. The ground used to win whenever the camera's Z
    component exceeded 0.3 -- a plane seen at 17 degrees, where a pixel of
    mouse travel is three and a half pixels of motion.
    """
    weights = [abs(_dot(eye, axis)) for axis in frame]
    weights[2] *= GROUND_PREFERENCE
    return max(range(3), key=lambda i: weights[i])


def screen_frame_axes(right, up, forward, frame=WORLD_FRAME):
    """The frame axes that best match screen right, up and depth.

    Each is ``(index, sign)``. Right picks first, then up from what is left,
    and depth gets the remaining axis, so the three never collapse onto one --
    the arrow-key rule, for any set of axes.
    """
    remaining = [0, 1, 2]
    chosen = []
    for vector in (right, up, forward):
        index = max(remaining, key=lambda i: abs(_dot(vector, frame[i])))
        remaining.remove(index)
        chosen.append((index, 1.0 if _dot(vector, frame[index]) >= 0 else -1.0))
    return tuple(chosen)


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


def signed_angle(start, current, axis) -> float:
    """Degrees from *start* to *current* about *axis*, in (-180, 180]."""
    return math.degrees(math.atan2(_dot(_cross(start, current), axis), _dot(start, current)))


class TransformGizmo(QObject):
    """Drag handles attached to a body."""

    #: Emitted while dragging: (dx, dy, dz, rx, ry, rz) so far. Translation is
    #: world millimetres; rotation is degrees about the gizmo's frame axes.
    changed = Signal(float, float, float, float, float, float)
    #: Emitted on release with the final transform.
    committed = Signal(float, float, float, float, float, float)
    #: A press took a handle or the part: a new gesture begins.
    started = Signal()
    #: The gesture ended without being a drag (a click): nothing to apply.
    cancelled = Signal()
    #: The constraint changed mid-drag (X/Y/Z keys): a short description.
    constraint_changed = Signal(str)

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
        self.frame = WORLD_FRAME
        self._rotation_state = RotationSnapState(step=ROTATION_SNAP_STEP)
        self._rotation_snapped = False
        self._screen_fallback = None
        self._offset = (0, 0)
        self._reset_gesture()
        self.rotation_step = ROTATION_SNAP_STEP
        self.translation_filter = None
        self.translation_snap = ""

    def _reset_gesture(self) -> None:
        self._constraint = None
        self._initial_constraint = None
        self._origin = None
        self._measure = None
        self._base = (0.0, 0.0, 0.0)
        self._components = (0.0, 0.0, 0.0)
        self._ring = None
        self._screen_fallback = None
        self._free = False
        self._cursor = None

    # -- lifecycle -------------------------------------------------------
    def attach(
        self,
        presentation,
        allow_scale: bool = False,
        allow_translation: bool = True,
        allow_rotation: bool = True,
        allow_planes: bool = True,
        pivot=None,
        frame=None,
    ) -> bool:
        """Put the gizmo on a presentation. Returns False if it cannot.

        *frame* is three orthonormal axes the handles point along -- the world
        axes unless a Local or picked-axis frame is asked for.
        """
        from OCP.AIS import (
            AIS_MM_Rotation, AIS_MM_Scaling, AIS_MM_Translation,
            AIS_MM_TranslationPlane, AIS_Manipulator,
        )
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

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
        frame = tuple(tuple(float(v) for v in axis) for axis in (frame or WORLD_FRAME))
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
            if pivot is None:
                location = manipulator.Position().Location()
                pivot = (location.X(), location.Y(), location.Z())
            manipulator.SetPosition(
                gp_Ax2(gp_Pnt(*pivot), gp_Dir(*frame[2]), gp_Dir(*frame[0]))
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
        self._pivot = tuple(pivot)
        self.frame = frame
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
        self._reset_gesture()
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

    @property
    def constraint(self):
        return self._constraint

    def set_rotation_step(self, step: float) -> None:
        self.rotation_step = float(step)
        self._rotation_state.step = self.rotation_step

    # -- interaction -----------------------------------------------------
    def press(self, device_x: int, device_y: int) -> bool:
        """Begin a drag on a handle, a handle just missed, or the part itself."""
        if self._manipulator is None:
            return False
        context = self.viewport.context
        if context is None:
            return False
        exact = self._handle_under(device_x, device_y)
        on_part = context.HasDetected() and any(
            context.DetectedInteractive() is item for item in self._attached or ()
        ) if not exact else False
        grab = (device_x, device_y) if exact else self._nearby_handle(
            device_x, device_y, PART_GRAB_REACH_PX if on_part else None
        )
        self._reset_gesture()
        ray = self._ray(device_x, device_y)
        if ray is None:
            return False
        if grab is None:
            if not on_part:
                return False
            # Every handle missed, but the press is on the part: the part is
            # the handle. Move slides it in the plane most face-on to the
            # camera; Rotate turns it about the axis that faces the camera.
            self._free = True
            index = facing_axis(ray[1], self.frame)
            if self._allow_translation:
                constraint = ("plane", index)
            elif self._allow_rotation:
                constraint = ("ring", index)
            else:
                return False
            grab = (device_x, device_y)
        else:
            constraint = self._handle_constraint()
            if constraint is None:
                return False
        self._offset = (grab[0] - device_x, grab[1] - device_y)
        self._manipulator.StartTransform(grab[0], grab[1], self.viewport.view)
        location = self._manipulator.Position().Location()
        self._origin = (location.X(), location.Y(), location.Z())
        self._dragging = True
        self._start = (device_x, device_y)
        self._last = None
        self._rotation_state = RotationSnapState(step=self.rotation_step)
        self._rotation_snapped = False
        self._constraint = self._initial_constraint = constraint
        if not self._begin_measure(grab[0], grab[1]):
            self._manipulator.StopTransform(False)
            self._dragging = False
            return False
        self.started.emit()
        return True

    def _handle_constraint(self):
        """The constraint the handle OCCT has activated stands for."""
        from OCP.AIS import AIS_MM_Rotation, AIS_MM_Translation, AIS_MM_TranslationPlane

        manipulator = self._manipulator
        index = int(manipulator.ActiveAxisIndex())
        if index not in (0, 1, 2):
            return None
        mode = int(manipulator.ActiveMode())
        if mode == int(AIS_MM_Translation) and self._allow_translation:
            return ("axis", index)
        if mode == int(AIS_MM_TranslationPlane) and self._allow_translation:
            return ("plane", index)
        if mode == int(AIS_MM_Rotation) and self._allow_rotation:
            return ("ring", index)
        return None

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

    def _nearby_handle(self, device_x: int, device_y: int, limit=None):
        """The point on the nearest handle within reach, or None.

        Rings of points are searched nearest first. Within the first ring that
        touches any handle, the handle touched most often wins -- it is the one
        the press was closest to -- rather than whichever the scan happened to
        reach first, which near the hub was frequently the wrong arrow.
        """
        ratio = self.viewport.devicePixelRatioF()
        # ponytail: a pixel search through OCCT's own detection -- up to 84
        # MoveTo calls, and only on a press that missed. Switch to distances
        # from the projected handles if presses ever lag on huge models.
        for reach in GRAB_REACH_PX:
            if limit is not None and reach > limit:
                break
            hits: dict[tuple[int, int], list[tuple[int, int]]] = {}
            for step in range(12):
                angle = step * math.pi / 6.0
                x = round(device_x + reach * ratio * math.cos(angle))
                y = round(device_y + reach * ratio * math.sin(angle))
                if self._handle_under(x, y):
                    key = (int(self._manipulator.ActiveMode()),
                           int(self._manipulator.ActiveAxisIndex()))
                    hits.setdefault(key, []).append((x, y))
            if hits:
                points = max(hits.values(), key=len)
                point = points[len(points) // 2]
                self._handle_under(*point)          # re-activate the winner
                return point
        return None

    def _ray(self, device_x: int, device_y: int):
        try:
            px, py, pz, vx, vy, vz = self.viewport.view.ConvertWithProj(device_x, device_y)
        except Exception:  # noqa: BLE001 - no ray off the edge of the view
            return None
        direction = _unit((vx, vy, vz))
        if direction is None:
            return None
        return ((px, py, pz), direction)

    def _perspective(self) -> bool:
        try:
            return not self.viewport.view.Camera().IsOrthographic()
        except Exception:  # noqa: BLE001 - stub views in tests
            return False

    def _device_mm(self) -> float:
        """Millimetres per device pixel at the gizmo."""
        try:
            return self.viewport.mm_per_pixel(self._origin) / self.viewport.devicePixelRatioF()
        except Exception:  # noqa: BLE001
            return 0.0

    def _project(self, point):
        try:
            return self.viewport.view.Convert(*point)
        except Exception:  # noqa: BLE001 - behind the camera
            return None

    def _begin_measure(self, device_x: int, device_y: int) -> bool:
        """Record where the current constraint's measurement starts."""
        kind, index = self._constraint
        axis = self.frame[index]
        ray = self._ray(device_x, device_y)
        if ray is None:
            return False
        if kind == "axis":
            if abs(_dot(axis, ray[1])) > AXIS_EDGE_ON:
                self._measure = ("screen-axis", (device_x, device_y), self._screen_axis(axis))
            else:
                start = axis_parameter(ray, self._origin, axis)
                if start is None:
                    return False
                self._measure = ("axis", start)
            return True
        if kind == "plane":
            hit = plane_hit(ray, self._origin, axis, self._perspective())
            if hit is None:
                return False
            self._measure = ("plane", hit)
            return True
        # ring
        if abs(_dot(axis, ray[1])) < RING_EDGE_ON:
            self._measure = ("screen-ring", None)
            self._screen_fallback = (device_x, device_y, 0.0)
        else:
            self._measure = ("ring", None)
            self._ring = self._ring_vector(ray, axis)
        return True

    def _screen_axis(self, axis):
        """The unit screen direction an axis points in, in device pixels."""
        mm = self._device_mm() or 1.0
        start = self._project(self._origin)
        ahead = self._project(tuple(self._origin[i] + axis[i] * mm * 100.0 for i in range(3)))
        if start is None or ahead is None:
            return (0.0, -1.0)
        dx, dy = ahead[0] - start[0], ahead[1] - start[1]
        length = math.hypot(dx, dy)
        # Pointing straight at the camera it has no screen direction: then
        # dragging up the screen moves it along the axis, away from the viewer.
        return (dx / length, dy / length) if length > 2.0 else (0.0, -1.0)

    def _ring_vector(self, ray, axis):
        """The pivot-to-cursor vector in the ring's plane, or None near the pivot."""
        hit = plane_hit(ray, self._pivot, axis, self._perspective())
        if hit is None:
            return None
        vector = _sub(hit, self._pivot)
        vector = _sub(vector, tuple(axis[i] * _dot(vector, axis) for i in range(3)))
        dead = ROTATE_DEAD_ZONE_PX * self._device_mm()
        if math.sqrt(_dot(vector, vector)) <= dead:
            return None
        return vector

    def _measured(self, device_x: int, device_y: int):
        """Frame components measured since the constraint's start, or None."""
        kind, index = self._constraint
        axis = self.frame[index]
        mode = self._measure[0]
        output = [0.0, 0.0, 0.0]
        if mode == "screen-axis":
            (x0, y0), (sx, sy) = self._measure[1], self._measure[2]
            output[index] = ((device_x - x0) * sx + (device_y - y0) * sy) * self._device_mm()
            return tuple(output)
        ray = self._ray(device_x, device_y)
        if ray is None:
            return None
        if mode == "axis":
            now = axis_parameter(ray, self._origin, axis)
            if now is None:
                return None
            output[index] = now - self._measure[1]
            return tuple(output)
        hit = plane_hit(ray, self._origin, axis, self._perspective())
        if hit is None:
            return None
        return constrain(to_frame(_sub(hit, self._measure[1]), self.frame), self._constraint)

    def set_constraint(self, kind: str | None, index: int | None = None) -> bool:
        """Lock the current translation drag to one axis or one plane.

        ``kind`` is ``"axis"`` or ``"plane"`` (*index* being the plane's
        normal), or None to go back to what the press grabbed. The motion so far
        is projected onto the new constraint, so the part does not jump.
        """
        if not self._dragging or self._constraint is None or self._constraint[0] == "ring":
            return False
        wanted = self._initial_constraint if kind is None else (kind, int(index))
        if wanted == self._constraint:
            wanted = self._initial_constraint
        self._base = constrain(self._components, wanted)
        self._constraint = wanted
        x, y = self._cursor or self._start
        if not self._begin_measure(x, y):
            return False
        names = "XYZ"
        if wanted[0] == "axis":
            text = f"{names[wanted[1]]} only"
        else:
            text = f"{''.join(n for i, n in enumerate(names) if i != wanted[1])} plane"
        self.constraint_changed.emit(text)
        self._apply_translation(self._base, None)
        return True

    def drag(self, device_x: int, device_y: int, modifiers=None) -> None:
        if not self._dragging or self._manipulator is None:
            return
        # A press that took a handle it just missed drags as if it had hit it.
        device_x += self._offset[0]
        device_y += self._offset[1]
        self._cursor = (device_x, device_y)
        bypass = bool(modifiers is not None and modifiers & Qt.ShiftModifier)
        fine = bool(modifiers is not None and modifiers & Qt.ControlModifier)
        if self._constraint[0] == "ring":
            self._drag_ring(device_x, device_y, bypass, fine)
        else:
            measured = self._measured(device_x, device_y)
            if measured is None:
                return
            self._apply_translation(
                tuple(self._base[i] + measured[i] for i in range(3)), (bypass, fine)
            )
        self.viewport.refresh()
        if self._last is not None:
            # Remember it: on release the accumulated transform has to come from
            # the last drag position. Re-querying at the *start* position -- as
            # an earlier version did -- always yields zero, so every drag
            # committed nothing.
            self.changed.emit(*self._last)

    def _apply_translation(self, components, snapping) -> None:
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

        components = constrain(components, self._constraint)
        if self.translation_filter is not None and snapping is not None:
            components = self.translation_filter(tuple(components), *snapping)
            # The filter snaps; it never decides direction. Re-applying the
            # constraint here is what makes "dragging X changes X only" true
            # whatever the snapping did -- a stale magnet lock once added Y.
            components = constrain(components, self._constraint)
        self._components = tuple(components)
        dx, dy, dz = to_world(components, self.frame)
        transform = gp_Trsf()
        transform.SetTranslation(gp_Vec(dx, dy, dz))
        self._manipulator.Transform(transform)
        # The arrows travel with the part; StopTransform puts them back.
        origin = self._origin
        self._manipulator.SetPosition(gp_Ax2(
            gp_Pnt(origin[0] + dx, origin[1] + dy, origin[2] + dz),
            gp_Dir(*self.frame[2]), gp_Dir(*self.frame[0]),
        ))
        self._last = (dx, dy, dz, 0.0, 0.0, 0.0)

    def _drag_ring(self, device_x, device_y, bypass, fine) -> None:
        from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

        index = self._constraint[1]
        axis = self.frame[index]
        if self._measure[0] == "ring":
            ray = self._ray(device_x, device_y)
            if ray is None:
                return
            current = self._ring_vector(ray, axis)
            if current is None:
                return
            if self._ring is None:
                # The press was too near the pivot to have a direction; the
                # turn is measured from where the cursor first left that zone.
                self._ring = current
            wrapped = signed_angle(self._ring, current, axis)
        else:
            # A ring seen edge-on cannot be intersected; carry on from the screen.
            wrapped = self._screen_ring_angle(device_x, device_y, axis)
            if wrapped is None:
                return
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
        values[index] = angle
        self._last = (0.0, 0.0, 0.0, *values)

    def _screen_ring_angle(self, device_x, device_y, axis):
        """The ring angle from screen motion along an edge-on ring's line.

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
        tangent = _cross(d, axis)
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
        self._offset = (0, 0)
        self._reset_gesture()
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
