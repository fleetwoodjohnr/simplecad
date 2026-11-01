"""A camera and manipulator stand-in for driving ``TransformGizmo`` headless.

The gizmo solves every drag from the cursor ray, so a test only needs a view
that turns pixels into rays (and points back into pixels) for a known camera,
plus a manipulator that says which handle was grabbed. OCCT's drawing and
detection are not involved.
"""

from __future__ import annotations

import math


def _unit(v):
    length = math.sqrt(sum(x * x for x in v))
    return tuple(x / length for x in v)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


class _Direction:
    def __init__(self, v):
        self.v = v

    def X(self):  # noqa: N802 - mirrors OCCT
        return self.v[0]

    def Y(self):  # noqa: N802
        return self.v[1]

    def Z(self):  # noqa: N802
        return self.v[2]


class StubCamera:
    def __init__(self, view):
        self._view = view

    def Direction(self):  # noqa: N802
        return _Direction(self._view.direction)

    def IsOrthographic(self):  # noqa: N802
        return True


class StubView:
    """An orthographic camera looking along *direction*, 1 mm per pixel.

    Pixel (cx, cy) looks at *target*; screen right and up follow the usual
    turntable convention (up stays as close to world +Z as it can).
    """

    def __init__(self, direction, target=(0.0, 0.0, 0.0), centre=(400, 300), scale=1.0):
        self.direction = _unit(direction)
        world_up = (0.0, 0.0, 1.0)
        if abs(sum(a * b for a, b in zip(world_up, self.direction))) > 0.999:
            world_up = (0.0, 1.0, 0.0)
        self.right = _unit(_cross(self.direction, world_up))
        self.up = _cross(self.right, self.direction)
        self.target = target
        self.centre = centre
        self.scale = scale

    def ConvertWithProj(self, x, y):  # noqa: N802
        sx = (x - self.centre[0]) * self.scale
        sy = -(y - self.centre[1]) * self.scale
        point = tuple(
            self.target[i] + sx * self.right[i] + sy * self.up[i] - 1000.0 * self.direction[i]
            for i in range(3)
        )
        return (*point, *self.direction)

    def Convert(self, x, y, z):  # noqa: N802
        offset = (x - self.target[0], y - self.target[1], z - self.target[2])
        sx = sum(offset[i] * self.right[i] for i in range(3)) / self.scale
        sy = sum(offset[i] * self.up[i] for i in range(3)) / self.scale
        return (self.centre[0] + sx, self.centre[1] - sy)

    def Camera(self):  # noqa: N802
        return StubCamera(self)

    def screen_of(self, point):
        return self.Convert(*point)


class StubManipulator:
    """Grabbed on one handle; records what the gizmo asks it to show."""

    def __init__(self, mode: str = "translation", axis: int = 0, pivot=(0.0, 0.0, 0.0)):
        from OCP.AIS import AIS_MM_Rotation, AIS_MM_Translation, AIS_MM_TranslationPlane

        self.mode = {"translation": AIS_MM_Translation, "plane": AIS_MM_TranslationPlane,
                     "rotation": AIS_MM_Rotation}[mode]
        self.axis = axis
        self.pivot = pivot
        self.stopped_with = None
        self.deactivated = False
        self.shown = None

    def HasActiveMode(self):  # noqa: N802
        return True

    def ActiveMode(self):  # noqa: N802
        return self.mode

    def ActiveAxisIndex(self):  # noqa: N802
        return self.axis

    def Position(self):  # noqa: N802
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        return gp_Ax2(gp_Pnt(*self.pivot), gp_Dir(0.0, 0.0, 1.0))

    def StartTransform(self, x, y, _view):  # noqa: N802
        pass

    def Transform(self, transform):  # noqa: N802
        self.shown = transform

    def SetPosition(self, _position):  # noqa: N802
        pass

    def StopTransform(self, apply):  # noqa: N802
        self.stopped_with = apply

    def DeactivateCurrentMode(self):  # noqa: N802
        self.deactivated = True


class StubContext:
    """Detection is OCCT's job; here it only says whether a handle is hit."""

    def __init__(self, detected=None):
        self.detected = detected

    def MoveTo(self, _x, _y, _view, _redraw):  # noqa: N802 - mirrors OCCT
        pass

    def HasDetected(self):  # noqa: N802
        return self.detected is not None

    def DetectedInteractive(self):  # noqa: N802
        return self.detected


class StubViewport:
    def __init__(self, view):
        self.view = view
        self.context = StubContext()

    def refresh(self):
        pass

    def devicePixelRatioF(self):  # noqa: N802
        return 1.0

    def mm_per_pixel(self, _point):
        return self.view.scale


def gizmo_on(direction=(0.0, 0.0, -1.0), mode="translation", axis=0, pivot=(0.0, 0.0, 0.0),
             frame=None, on_part=False, part=None):
    """A gizmo whose press lands on the given handle (or, *on_part*, the body)."""
    from simplecad.ui.viewport.gizmo import WORLD_FRAME, TransformGizmo

    view = StubView(direction, target=pivot)
    viewport = StubViewport(view)
    gizmo = TransformGizmo(viewport)
    manipulator = StubManipulator(mode, axis, pivot)
    gizmo._manipulator = manipulator
    gizmo._attached = [part or object()]
    gizmo._pivot = tuple(pivot)
    gizmo.frame = frame or WORLD_FRAME
    gizmo._allow_translation = mode in ("translation", "plane")
    gizmo._allow_rotation = mode == "rotation"
    if on_part:
        viewport.context.detected = gizmo._attached[0]
        gizmo._nearby_handle = lambda *_args, **_kwargs: None
    else:
        viewport.context.detected = manipulator
    return gizmo
