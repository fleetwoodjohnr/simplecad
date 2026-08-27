"""Camera navigation: orbit, pan, zoom, and eased transitions between views.

OCCT ships ``V3d_View::StartRotation``/``Rotation``, and SimpleCAD used them.
They are not good enough for a CAD viewport, for three reasons that all show up
within a minute of use:

* they turn about the view's own ``At`` point, which is wherever the last
  ``FitAll`` left it -- not around the thing you are looking at;
* they accumulate **roll**, so the horizon tilts and the model ends up askew;
* they flip when the view direction crosses a pole, which reads as the model
  suddenly jumping to a different orientation.

So orbit is implemented here instead, as a **turntable**: yaw about world Z,
pitch about the camera's right axis, pitch clamped short of the poles, and the
up vector re-derived every frame as the projection of world +Z perpendicular to
the view direction. That last line is the whole trick -- it means roll is not
merely small but exactly zero, always, so the horizon can never tilt and no
sequence of drags can leave the model crooked.

Everything here operates on ``Graphic3d_Camera`` and ``V3d_View`` and touches no
GL state, so it is testable without a window -- see ``tests/test_camera.py``.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QObject, QTimer

#: Radians of orbit per pixel of drag. Tuned so a drag across a 1440 px window
#: is a little under two full turns -- enough to sweep right round a model
#: without lifting the mouse, slow enough to aim.
ORBIT_RADIANS_PER_PX = 0.0085

#: How close to straight up or straight down the view is allowed to get, in
#: radians. Without a limit the up vector becomes undefined at the pole and the
#: view snaps through it; with one, tilting simply stops, which is what every
#: CAD turntable does.
PITCH_LIMIT = math.radians(89.0)

#: Zoom sensitivity. A 120-unit wheel notch is one "unit"; the factor applied is
#: ``exp(units * this)``, so zoom is geometric -- the same gesture covers the
#: same proportion of the scene however far in you already are.
ZOOM_PER_UNIT = 0.18
#: Never let one event do more than this, so a coarse wheel or a flung trackpad
#: gesture cannot cross the whole scene in a single step.
ZOOM_UNIT_LIMIT = 3.0

#: Fraction of the outstanding zoom applied per tick, and the tick period. Zoom
#: is eased because it is the one gesture that arrives as a burst of discrete
#: events -- a trackpad can deliver thirty in the time a mouse delivers one --
#: and applying each one instantly is what makes trackpad zoom feel like it is
#: falling down a well.
ZOOM_EASE = 0.34
TICK_MS = 16

#: Duration of a scripted camera move (standard views, ViewCube, framing).
TRANSITION_MS = 260


def _length(vector) -> float:
    return math.sqrt(sum(v * v for v in vector))


def _normalise(vector) -> tuple[float, float, float]:
    length = _length(vector)
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return tuple(v / length for v in vector)


def _cross(a, b) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def _sub(a, b) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b) -> tuple[float, float, float]:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a, k: float) -> tuple[float, float, float]:
    return (a[0] * k, a[1] * k, a[2] * k)


WORLD_UP = (0.0, 0.0, 1.0)


def constrained_up(direction) -> tuple[float, float, float]:
    """The up vector for a view looking along *direction*, with zero roll.

    World +Z with the component along the view direction removed. This is what
    guarantees the horizon stays level: there is no state to accumulate error
    into, because up is recomputed from the view direction every single time.
    """
    view = _normalise(direction)
    up = _sub(WORLD_UP, _scale(view, _dot(WORLD_UP, view)))
    if _length(up) < 1e-9:
        # Looking exactly along Z. The pitch clamp is meant to make this
        # unreachable, but a programmatic Top view lands here legitimately.
        return (0.0, 1.0, 0.0)
    return _normalise(up)


def pitch_of(direction) -> float:
    """How far above the horizontal the camera sits, in radians.

    Positive means looking down at the model from above.
    """
    view = _normalise(direction)
    return math.asin(max(-1.0, min(1.0, -view[2])))


def orbit_state(eye, center, pivot, d_azimuth: float, d_pitch: float):
    """Turntable-orbit a camera about *pivot*. Returns ``(eye, center, up)``.

    Pure arithmetic on tuples, deliberately: this is the part worth testing, and
    a test that has to stand up a ``Graphic3d_Camera`` to check that the horizon
    stays level is a test nobody runs.

    Both the eye *and* the centre are rotated, rather than the centre being
    snapped onto the pivot. Snapping is the obvious implementation and it is
    wrong: when you orbit around a selected face the camera is generally not
    already looking straight at it, so snapping makes the view lurch on the
    first pixel of the very first drag.
    """
    view = _sub(center, eye)
    if _length(view) < 1e-12:
        return (eye, center, constrained_up((0.0, 1.0, 0.0)))

    # Clamp the pitch against the *view* direction, which is what the user
    # perceives, not against the eye-to-pivot vector.
    current = pitch_of(view)
    d_pitch = max(-PITCH_LIMIT - current, min(PITCH_LIMIT - current, d_pitch))

    right = _cross(WORLD_UP, view)
    if _length(right) < 1e-9:
        # Looking straight up or down: any horizontal axis will do, and the
        # clamp above means we only get here from a programmatic Top/Bottom.
        right = (1.0, 0.0, 0.0)
    right = _normalise(right)

    def turn(point):
        moved = _sub(point, pivot)
        moved = _rotate(moved, right, d_pitch)
        moved = _rotate(moved, WORLD_UP, d_azimuth)
        return _add(pivot, moved)

    new_eye = turn(eye)
    new_center = turn(center)
    return (new_eye, new_center, constrained_up(_sub(new_center, new_eye)))


def _rotate(vector, axis, angle: float):
    """Rodrigues' rotation of *vector* about the unit *axis*."""
    if abs(angle) < 1e-15:
        return vector
    axis = _normalise(axis)
    cos, sin = math.cos(angle), math.sin(angle)
    return _add(
        _add(_scale(vector, cos), _scale(_cross(axis, vector), sin)),
        _scale(axis, _dot(axis, vector) * (1.0 - cos)),
    )


# ----------------------------------------------------------------------
# Camera state, read from and written to a live V3d_View
# ----------------------------------------------------------------------
def read_state(view):
    """``(eye, center, up, scale)`` as plain tuples, or None."""
    if view is None:
        return None
    camera = view.Camera()
    eye, center, up = camera.Eye(), camera.Center(), camera.Up()
    return (
        (eye.X(), eye.Y(), eye.Z()),
        (center.X(), center.Y(), center.Z()),
        (up.X(), up.Y(), up.Z()),
        camera.Scale(),
    )


def write_state(view, state) -> None:
    from OCP.gp import gp_Dir, gp_Pnt

    if view is None or state is None:
        return
    eye, center, up, scale = state
    camera = view.Camera()
    camera.SetEye(gp_Pnt(*eye))
    camera.SetCenter(gp_Pnt(*center))
    camera.SetUp(gp_Dir(*up))
    if scale and scale > 0:
        camera.SetScale(scale)


def view_direction(state):
    """Which way a camera state is looking, normalised."""
    return _normalise(_sub(state[1], state[0]))


def state_for_direction(state, direction, distance=None):
    """The same camera, re-aimed to look along *direction* at its centre.

    Keeps the centre and the eye distance, so a standard view re-orients without
    also re-framing -- which is the behaviour that makes pressing 1/5/7 feel
    like turning the part over rather than starting again.
    """
    eye, center, _up, scale = state
    view = _normalise(direction)
    if distance is None:
        distance = _length(_sub(eye, center)) or 1.0
    new_eye = _sub(center, _scale(view, distance))
    return (new_eye, center, constrained_up(view), scale)


class CameraController(QObject):
    """Orbit, pan and zoom for one viewport."""

    def __init__(self, viewport) -> None:
        super().__init__(viewport)
        self.viewport = viewport
        #: What the current orbit turns about. Resolved on press, held for the
        #: whole drag so the model cannot drift under the cursor mid-gesture.
        self._pivot: tuple[float, float, float] | None = None
        #: Outstanding zoom, in log space, plus the pixel it is anchored to.
        self._zoom_pending = 0.0
        self._zoom_anchor: tuple[int, int] | None = None
        self._zoom_timer = QTimer(self)
        self._zoom_timer.setInterval(TICK_MS)
        self._zoom_timer.timeout.connect(self._zoom_tick)

    # -- orbit -----------------------------------------------------------
    def begin_orbit(self, pos) -> None:
        """Resolve and latch the pivot for a drag starting at *pos*."""
        self._pivot = self.resolve_pivot(pos)

    def end_orbit(self) -> None:
        self._pivot = None

    @property
    def pivot(self):
        return self._pivot

    def orbit(self, dx: float, dy: float) -> None:
        """Turn by a mouse delta in widget pixels."""
        view = self.viewport.view
        state = read_state(view)
        if state is None:
            return
        pivot = self._pivot or state[1]
        eye, center, up = orbit_state(
            state[0], state[1], pivot,
            -dx * ORBIT_RADIANS_PER_PX,
            dy * ORBIT_RADIANS_PER_PX,
        )
        write_state(view, (eye, center, up, state[3]))

    def resolve_pivot(self, pos=None):
        """What the camera should turn about.

        In order: what is selected, then what is under the cursor, then the
        whole scene. "Orbit around the thing you are looking at" is the entire
        difference between a camera that feels aimed and one that feels like it
        is swinging the model around some arbitrary point behind it.
        """
        viewport = self.viewport
        selected = bbox_center(
            entry["shape"] for entry in viewport.selected_entries()
        )
        if selected is not None:
            return selected
        if pos is not None:
            under = viewport.detected_shape(pos)
            if under is not None and not under.IsNull():
                cursor = bbox_center([under])
                if cursor is not None:
                    return cursor
        scene = viewport.scene_center()
        if scene is not None:
            return scene
        state = read_state(viewport.view)
        return state[1] if state else (0.0, 0.0, 0.0)

    # -- pan -------------------------------------------------------------
    def pan(self, dx: float, dy: float) -> None:
        """Slide the view by a delta in widget pixels."""
        view = self.viewport.view
        if view is None:
            return
        ratio = self.viewport.devicePixelRatioF()
        view.Pan(int(round(dx * ratio)), int(round(-dy * ratio)))

    # -- zoom ------------------------------------------------------------
    def zoom(self, units: float, at) -> None:
        """Zoom by *units* (one wheel notch is 1.0), anchored at a pixel.

        Accumulated rather than applied: see :data:`ZOOM_EASE`.
        """
        view = self.viewport.view
        if view is None or not units:
            return
        units = max(-ZOOM_UNIT_LIMIT, min(ZOOM_UNIT_LIMIT, units))
        self._zoom_anchor = at
        self._zoom_pending += units * ZOOM_PER_UNIT
        if not self._zoom_timer.isActive():
            self._zoom_timer.start()
        self._zoom_tick()

    def _zoom_tick(self) -> None:
        view = self.viewport.view
        if view is None or self._zoom_anchor is None:
            self._zoom_timer.stop()
            self._zoom_pending = 0.0
            return
        step = self._zoom_pending * ZOOM_EASE
        if abs(self._zoom_pending) < 1e-4:
            self._zoom_pending = 0.0
            self._zoom_timer.stop()
            return
        # The last step is taken whole, so zoom always settles exactly on the
        # value asked for instead of creeping toward it forever.
        if abs(self._zoom_pending) < 5e-3:
            step, self._zoom_pending = self._zoom_pending, 0.0
        else:
            self._zoom_pending -= step
        x, y = self._zoom_anchor
        try:
            view.StartZoomAtPoint(int(x), int(y))
            view.SetZoom(math.exp(step), True)
        except Exception:  # noqa: BLE001 - a degenerate camera must not throw
            self._zoom_pending = 0.0
            self._zoom_timer.stop()
            return
        self.viewport.camera_moved()

    def stop(self) -> None:
        self._zoom_timer.stop()
        self._zoom_pending = 0.0


def bbox_center(shapes):
    """Centre of the combined bounding box of *shapes*, or None."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    found = False
    for shape in shapes:
        if shape is None or shape.IsNull():
            continue
        try:
            BRepBndLib.Add_s(shape, box, True)
            found = True
        except Exception:  # noqa: BLE001
            continue
    if not found or box.IsVoid():
        return None
    low, high = box.CornerMin(), box.CornerMax()
    return (
        (low.X() + high.X()) / 2.0,
        (low.Y() + high.Y()) / 2.0,
        (low.Z() + high.Z()) / 2.0,
    )


class CameraAnimator(QObject):
    """Eases the camera from one state to another.

    Standard views and the ViewCube used to cut straight to the new orientation,
    which is disorienting for exactly the reason a hard cut in film is: nothing
    tells you how the new view relates to the old one. A quarter-second sweep
    does, and costs nothing.
    """

    def __init__(self, viewport) -> None:
        super().__init__(viewport)
        self.viewport = viewport
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._from = None
        self._to = None
        self._elapsed = 0
        self._duration = TRANSITION_MS

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def to(self, target, duration: int = TRANSITION_MS) -> None:
        """Sweep to *target*, a ``(eye, center, up, scale)`` tuple."""
        view = self.viewport.view
        start = read_state(view)
        if start is None or target is None:
            return
        self._from, self._to = start, target
        self._elapsed = 0
        self._duration = max(1, duration)
        self._timer.start()

    def jump(self, target) -> None:
        """Go straight there, cancelling any sweep in progress."""
        self.cancel()
        write_state(self.viewport.view, target)
        self.viewport.camera_moved()

    def cancel(self) -> None:
        self._timer.stop()
        self._from = self._to = None

    def _tick(self) -> None:
        self._elapsed += TICK_MS
        fraction = min(1.0, self._elapsed / self._duration)
        # Smoothstep: no abrupt start, no abrupt stop.
        eased = fraction * fraction * (3.0 - 2.0 * fraction)
        write_state(self.viewport.view, _blend(self._from, self._to, eased))
        self.viewport.camera_moved()
        if fraction >= 1.0:
            self.cancel()


def _blend(start, end, t: float):
    """Interpolate two camera states.

    The eye is swung around the centre on the arc between the two directions
    rather than moved along the straight line between the two positions -- a
    straight line takes the camera *through* the model on any turn approaching
    180 degrees, which looks like the view briefly falling inside the part.
    """
    eye_a, center_a, _up_a, scale_a = start
    eye_b, center_b, _up_b, scale_b = end

    center = _lerp(center_a, center_b, t)
    dir_a = _normalise(_sub(eye_a, center_a))
    dir_b = _normalise(_sub(eye_b, center_b))
    direction = _slerp(dir_a, dir_b, t)
    distance = (
        _length(_sub(eye_a, center_a)) * (1.0 - t)
        + _length(_sub(eye_b, center_b)) * t
    )
    eye = _add(center, _scale(direction, distance))
    scale = scale_a * (1.0 - t) + scale_b * t
    return (eye, center, constrained_up(_sub(center, eye)), scale)


def _lerp(a, b, t: float):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def _slerp(a, b, t: float):
    cos = max(-1.0, min(1.0, _dot(a, b)))
    angle = math.acos(cos)
    if angle < 1e-6:
        return _normalise(_lerp(a, b, t))
    if abs(math.pi - angle) < 1e-4:
        # Exactly opposite: any arc will do, so nudge off the antipode rather
        # than dividing by a vanishing sine.
        nudge = _cross(a, WORLD_UP)
        if _length(nudge) < 1e-9:
            nudge = _cross(a, (1.0, 0.0, 0.0))
        b = _normalise(_add(b, _scale(_normalise(nudge), 1e-3)))
        cos = max(-1.0, min(1.0, _dot(a, b)))
        angle = math.acos(cos)
    sin = math.sin(angle)
    return _normalise(
        _add(
            _scale(a, math.sin((1.0 - t) * angle) / sin),
            _scale(b, math.sin(t * angle) / sin),
        )
    )
