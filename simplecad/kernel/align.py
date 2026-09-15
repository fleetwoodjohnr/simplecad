"""Positioning one body against another.

This is the feature that should make SimpleCAD feel less like CAD assembly and
more like arranging objects. Pick a face on each of two parts and the software
works out the rotation and translation itself: surface normals, face centres,
axes, which way round things should sit.

Everything here returns a ``gp_Trsf`` for the *moving* body, so the caller can
preview it, apply it, or store it as a parametric relationship that re-solves
when the target moves.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..core.errors import CadError
from .detect import CylinderInfo, PlaneInfo, analyse_cylinder, analyse_plane

#: Directions closer than this to parallel are treated as parallel.
PARALLEL_TOLERANCE = 1e-9


# ----------------------------------------------------------------------
# Small vector helpers -- kept local so the module reads as geometry.
# ----------------------------------------------------------------------
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _length(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    magnitude = _length(a)
    if magnitude < 1e-12:
        raise CadError("That geometry has no usable direction.")
    return _scale(a, 1.0 / magnitude)


def _perpendicular(a):
    """Any unit vector perpendicular to *a*."""
    reference = (1.0, 0.0, 0.0) if abs(a[0]) < 0.9 else (0.0, 1.0, 0.0)
    return _unit(_cross(a, reference))


# ----------------------------------------------------------------------
# Transform construction
# ----------------------------------------------------------------------
def rotation_between(source, target, pivot):
    """A ``gp_Trsf`` rotating *source* onto *target* about *pivot*."""
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

    source = _unit(source)
    target = _unit(target)
    axis = _cross(source, target)
    sine = _length(axis)
    cosine = _dot(source, target)

    transform = gp_Trsf()
    if sine < PARALLEL_TOLERANCE:
        if cosine > 0:
            return transform                      # already aligned
        axis = _perpendicular(source)             # opposed: any perpendicular axis
        angle = math.pi
    else:
        axis = _unit(axis)
        angle = math.atan2(sine, cosine)
    transform.SetRotation(gp_Ax1(gp_Pnt(*pivot), gp_Dir(*axis)), angle)
    return transform


def translation(vector):
    from OCP.gp import gp_Trsf, gp_Vec

    transform = gp_Trsf()
    transform.SetTranslation(gp_Vec(*vector))
    return transform


@dataclass(frozen=True)
class AlignResult:
    """A solved placement, with a sentence explaining what it did."""

    transform: object
    operation: str
    description: str


@dataclass(frozen=True)
class PlanarFrame:
    """A stable local frame and trimmed bounds for one planar face.

    Coordinates are relative to the face's area centre.  ``x_axis`` follows
    the underlying plane's authored X direction; ``y_axis`` is re-derived from
    the outward normal so the visible frame remains right-handed on reversed
    faces as well.
    """

    center: tuple[float, float, float]
    normal: tuple[float, float, float]
    x_axis: tuple[float, float, float]
    y_axis: tuple[float, float, float]
    x_bounds: tuple[float, float]
    y_bounds: tuple[float, float]

    def point(self, x: float = 0.0, y: float = 0.0, normal: float = 0.0):
        return tuple(
            self.center[i]
            + self.x_axis[i] * x
            + self.y_axis[i] * y
            + self.normal[i] * normal
            for i in range(3)
        )


ANCHOR_PRESETS = (
    "top_left", "top", "top_right",
    "left", "center", "right",
    "bottom_left", "bottom", "bottom_right",
)


def _projected_bounds(shape, axis, center):
    """Exact bounds along *axis*, expressed relative to *center*.

    OCCT's UV bounds inherit arbitrary surface parameter directions.  Rotating
    a copy so the requested axis becomes world X lets its geometry-aware bounder
    provide stable face extents for lines, arcs, and splines alike.
    """
    from .occ import bounding_box, transformed

    rotate = rotation_between(axis, (1.0, 0.0, 0.0), center)
    low, high = bounding_box(transformed(shape, rotate))
    point = _transformed_point(rotate, center)
    return (low[0] - point[0], high[0] - point[0])


def _transformed_point(transform, point):
    from OCP.gp import gp_Pnt

    result = gp_Pnt(*point)
    result.Transform(transform)
    return (result.X(), result.Y(), result.Z())


def reference_frame(face) -> PlanarFrame:
    """Return the user-facing U/V frame for a planar reference face.

    The frame is intrinsic to the face, not the camera.  V follows the authored
    face direction that most nearly points toward world-up (world-Y is used for
    horizontal faces); U is then derived so U-right, V-up and the outward normal
    stay right-handed.  Orbiting the camera therefore only changes how the
    arrows are projected on screen, never what entered coordinates mean.
    """
    legacy = planar_frame(face)
    candidates = (legacy.x_axis, legacy.y_axis)
    guide = (0.0, 0.0, 1.0)
    if max(abs(_dot(axis, guide)) for axis in candidates) < 0.25:
        guide = (0.0, 1.0, 0.0)
    v_axis = max(candidates, key=lambda axis: abs(_dot(axis, guide)))
    if _dot(v_axis, guide) < 0.0:
        v_axis = _scale(v_axis, -1.0)
    u_axis = _unit(_cross(v_axis, legacy.normal))
    return PlanarFrame(
        center=legacy.center,
        normal=legacy.normal,
        x_axis=u_axis,
        y_axis=v_axis,
        x_bounds=_projected_bounds(face, u_axis, legacy.center),
        y_bounds=_projected_bounds(face, v_axis, legacy.center),
    )


def anchor_coordinates(frame: PlanarFrame, preset: str) -> tuple[float, float]:
    """Coordinates of a clickable centre/edge/corner anchor preset."""
    if preset not in ANCHOR_PRESETS:
        raise CadError(f"Unknown placement anchor: {preset!r}.")
    low_u, high_u = frame.x_bounds
    low_v, high_v = frame.y_bounds
    horizontal = {
        "left": low_u,
        "center": 0.0,
        "right": high_u,
    }
    vertical = {
        "bottom": low_v,
        "center": 0.0,
        "top": high_v,
    }
    words = preset.split("_")
    h_key = next((word for word in words if word in ("left", "right")), "center")
    v_key = next((word for word in words if word in ("bottom", "top")), "center")
    return horizontal[h_key], vertical[v_key]


def anchor_point(
    frame: PlanarFrame, preset: str = "center", *, normal: float = 0.0,
) -> tuple[float, float, float]:
    u, v = anchor_coordinates(frame, preset)
    return frame.point(u, v, normal)


def planar_frame(face) -> PlanarFrame:
    """Return the face-local X/Y frame used by placement and arrangement."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS

    info = analyse_plane(face)
    if info is None:
        raise CadError(
            "Placement needs a flat target face.",
            suggestion="Select a planar face on the receiving object.",
        )
    face = TopoDS.Face_s(face)
    plane = BRepAdaptor_Surface(face).Plane()
    position = plane.Position()
    x_dir = position.XDirection()
    x_axis = _unit((x_dir.X(), x_dir.Y(), x_dir.Z()))
    y_axis = _unit(_cross(info.normal, x_axis))

    location = position.Location()
    origin = (location.X(), location.Y(), location.Z())
    center_delta = _sub(info.center, origin)
    center_x = _dot(center_delta, x_axis)
    center_y = _dot(center_delta, y_axis)

    u_min, u_max, v_min, v_max = BRepTools.UVBounds_s(face)
    surface_y = position.YDirection()
    surface_y = (surface_y.X(), surface_y.Y(), surface_y.Z())
    if _dot(surface_y, y_axis) < 0.0:
        v_min, v_max = -v_max, -v_min
    return PlanarFrame(
        center=info.center,
        normal=info.normal,
        x_axis=x_axis,
        y_axis=y_axis,
        x_bounds=(u_min - center_x, u_max - center_x),
        y_bounds=(v_min - center_y, v_max - center_y),
    )


def _anchored(bounds, anchor: str, value: float) -> float:
    """Resolve an edge/centre offset; positive edge values point inward."""
    low, high = bounds
    if anchor in ("left", "bottom"):
        return low + value
    if anchor in ("right", "top"):
        return high - value
    return value


def support_face(body, target_face):
    """Infer a useful planar support face for placing a whole body.

    Area is the primary signal. Equal caps (the common cylinder case) are
    broken by proximity to the target plane, so the cap already facing the
    receiving surface is chosen.
    """
    from ..core.naming import sub_shapes

    target = analyse_plane(target_face)
    if target is None:
        return None
    candidates = []
    for index, face in enumerate(sub_shapes(body, "face")):
        info = analyse_plane(face)
        if info is None:
            continue
        distance = abs(_dot(_sub(info.center, target.center), target.normal))
        candidates.append((-info.area, distance, index, face))
    return min(candidates)[-1] if candidates else None


# ----------------------------------------------------------------------
# Operations
# ----------------------------------------------------------------------
def stack(
    moving: PlaneInfo,
    target: PlaneInfo,
    *,
    offset: float = 0.0,
    flip: bool = False,
    target_frame: PlanarFrame | None = None,
    x: float = 0.0,
    y: float = 0.0,
    x_anchor: str = "center",
    y_anchor: str = "center",
) -> AlignResult:
    """Seat the moving face flat against the target face.

    The moving face's outward normal is turned to oppose the target's, which is
    what puts the two surfaces in contact rather than back to back, then the
    centres are brought together. This is the operation the spec calls Stack:
    select a face on each part, and they meet face to face with no manual
    rotation.
    """
    wanted = target.normal if flip else _scale(target.normal, -1.0)
    rotate = rotation_between(moving.normal, wanted, moving.center)
    if target_frame is None:
        destination = _add(target.center, _scale(target.normal, offset))
    else:
        destination = target_frame.point(
            _anchored(target_frame.x_bounds, x_anchor, x),
            _anchored(target_frame.y_bounds, y_anchor, y),
            offset,
        )
    move = translation(_sub(destination, moving.center))
    combined = move.Multiplied(rotate)
    gap = f", offset {offset:g} mm" if offset else ""
    return AlignResult(
        combined,
        "stack",
        f"Stacked face to face{gap}." if not flip else f"Stacked, flipped{gap}.",
    )


def center(
    moving: PlaneInfo,
    target: PlaneInfo,
    *,
    offset: float = 0.0,
    target_frame: PlanarFrame | None = None,
    x: float = 0.0,
    y: float = 0.0,
    x_anchor: str = "center",
    y_anchor: str = "center",
) -> AlignResult:
    """Line the moving face's centre up with the target's, without rotating."""
    destination = (
        target_frame.point(
            _anchored(target_frame.x_bounds, x_anchor, x),
            _anchored(target_frame.y_bounds, y_anchor, y),
            offset,
        )
        if target_frame is not None
        else _add(target.center, _scale(target.normal, offset))
    )
    return AlignResult(
        translation(_sub(destination, moving.center)),
        "center",
        "Centred on the target face.",
    )


def concentric(
    moving: CylinderInfo,
    target: CylinderInfo,
    *,
    offset: float = 0.0,
    flip: bool = False,
    seat: bool = True,
) -> AlignResult:
    """Make two cylindrical features share an axis.

    Select a shaft and a hole and this drops one into the other. With *seat* the
    entry faces are brought level as well, which is almost always what is wanted
    when a post goes into its mating bore.
    """
    wanted = _scale(target.direction, -1.0) if flip else target.direction
    pivot = moving.origin
    rotate = rotation_between(moving.direction, wanted, pivot)

    # After rotating, the moving axis is parallel to the target's; shift it onto
    # the target axis, then optionally slide along that axis to seat the parts.
    axis = _unit(wanted)
    delta = _sub(target.origin, moving.origin)
    perpendicular = _sub(delta, _scale(axis, _dot(delta, axis)))
    along = _scale(axis, _dot(delta, axis)) if seat else (0.0, 0.0, 0.0)
    move = translation(_add(_add(perpendicular, along), _scale(axis, offset)))

    combined = move.Multiplied(rotate)
    detail = f"⌀{moving.diameter:.2f} into ⌀{target.diameter:.2f}"
    return AlignResult(combined, "concentric", f"Aligned axes ({detail}).")


def align_edges(moving_points, target_points, *, offset: float = 0.0) -> AlignResult:
    """Bring one straight edge onto another: same direction, same start."""
    moving_start, moving_end = moving_points
    target_start, target_end = target_points
    moving_direction = _sub(moving_end, moving_start)
    target_direction = _sub(target_end, target_start)

    rotate = rotation_between(moving_direction, target_direction, moving_start)
    destination = _add(target_start, _scale(_unit(target_direction), offset))
    move = translation(_sub(destination, moving_start))
    return AlignResult(move.Multiplied(rotate), "align", "Aligned edge to edge.")


# ----------------------------------------------------------------------
# Smart suggestion
# ----------------------------------------------------------------------
#: What each operation is called in the UI.
OPERATION_LABELS = {
    "stack": "Stack",
    "concentric": "Concentric",
    "align": "Align",
    "center": "Center",
}


def describe_face(face) -> PlaneInfo | CylinderInfo | None:
    """Whichever of the two descriptions fits this face."""
    plane = analyse_plane(face)
    if plane is not None:
        return plane
    return analyse_cylinder(face)


def suggest(moving_face, target_face) -> str:
    """Pick the operation these two selections most likely want.

    Planar + planar suggests Stack, cylinder + cylinder suggests Concentric, and
    anything else falls back to Align -- exactly the mapping the spec describes,
    so the contextual toolbar can lead with the right button.
    """
    moving = describe_face(moving_face)
    target = describe_face(target_face)
    if isinstance(moving, PlaneInfo) and isinstance(target, PlaneInfo):
        return "stack"
    if isinstance(moving, CylinderInfo) and isinstance(target, CylinderInfo):
        return "concentric"
    return "align"


def solve(
    moving_face,
    target_face,
    *,
    operation: str | None = None,
    offset: float = 0.0,
    flip: bool = False,
    x: float = 0.0,
    y: float = 0.0,
    x_anchor: str = "center",
    y_anchor: str = "center",
    moving_anchor: str | None = None,
    target_anchor: str | None = None,
    u: float | None = None,
    v: float | None = None,
    moving_frame: PlanarFrame | None = None,
    target_frame: PlanarFrame | None = None,
) -> AlignResult:
    """Solve a placement for two selected faces, choosing the operation if not given."""
    operation = operation or suggest(moving_face, target_face)
    moving = describe_face(moving_face)
    target = describe_face(target_face)
    if moving is None or target is None:
        raise CadError(
            "Those faces cannot be aligned.",
            suggestion="Pick a flat face or a cylindrical face on each part.",
        )

    if operation == "concentric":
        if not (isinstance(moving, CylinderInfo) and isinstance(target, CylinderInfo)):
            raise CadError(
                "Concentric needs a cylindrical face on both parts.",
                suggestion="Select a shaft and a hole.",
            )
        return concentric(moving, target, offset=offset, flip=flip)

    if not (isinstance(moving, PlaneInfo) and isinstance(target, PlaneInfo)):
        raise CadError(
            "Stack needs a flat face on both parts.",
            suggestion="Select a planar face on each, or use Concentric for round features.",
        )
    # New placement features use independent anchor presets and signed U/V.
    # Keep the legacy edge-distance path for documents saved before that UI was
    # introduced; their authored meaning must not change on rebuild.
    if moving_anchor is not None or target_anchor is not None or u is not None or v is not None:
        moving_frame = moving_frame or reference_frame(moving_face)
        target_frame = target_frame or reference_frame(target_face)
        source_anchor = anchor_point(moving_frame, moving_anchor or "center")
        destination = anchor_point(target_frame, target_anchor or "center", normal=offset)
        destination = _add(
            destination,
            _add(
                _scale(target_frame.x_axis, float(u or 0.0)),
                _scale(target_frame.y_axis, float(v or 0.0)),
            ),
        )
        if operation == "center":
            rotate = translation((0.0, 0.0, 0.0))
        else:
            wanted = target.normal if flip else _scale(target.normal, -1.0)
            rotate = rotation_between(moving.normal, wanted, moving.center)
        rotated_anchor = _transformed_point(rotate, source_anchor)
        move = translation(_sub(destination, rotated_anchor))
        gap = f", offset {offset:g} mm" if offset else ""
        return AlignResult(
            move.Multiplied(rotate),
            operation,
            f"Placed {moving_anchor or 'center'} anchor on "
            f"{target_anchor or 'center'}{gap}.",
        )

    frame = planar_frame(target_face)
    if operation == "center":
        return center(
            moving, target, offset=offset, target_frame=frame,
            x=x, y=y, x_anchor=x_anchor, y_anchor=y_anchor,
        )
    return stack(
        moving, target, offset=offset, flip=flip, target_frame=frame,
        x=x, y=y, x_anchor=x_anchor, y_anchor=y_anchor,
    )
