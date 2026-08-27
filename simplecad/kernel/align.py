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


# ----------------------------------------------------------------------
# Operations
# ----------------------------------------------------------------------
def stack(
    moving: PlaneInfo,
    target: PlaneInfo,
    *,
    offset: float = 0.0,
    flip: bool = False,
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
    destination = _add(target.center, _scale(target.normal, offset))
    move = translation(_sub(destination, moving.center))
    combined = move.Multiplied(rotate)
    gap = f", offset {offset:g} mm" if offset else ""
    return AlignResult(
        combined,
        "stack",
        f"Stacked face to face{gap}." if not flip else f"Stacked, flipped{gap}.",
    )


def center(moving: PlaneInfo, target: PlaneInfo, *, offset: float = 0.0) -> AlignResult:
    """Line the moving face's centre up with the target's, without rotating."""
    destination = _add(target.center, _scale(target.normal, offset))
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
    if operation == "center":
        return center(moving, target, offset=offset)
    return stack(moving, target, offset=offset, flip=flip)
