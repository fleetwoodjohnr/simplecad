"""Modelled, printable thread geometry.

A thread is a profile swept along a helix. The helix is built as a straight line
in the UV space of a cylindrical surface, which is the only construction OCCT
handles well; the profile is a trapezoid spanning root to crest; the sweep is a
pipe shell in binormal mode so the tooth stays radial as it climbs.

Two ideas keep this usable rather than merely correct:

* **One solid describes both halves of a pair.** :func:`thread_solid` builds the
  bolt. An external thread cuts away everything inside the shaft that the bolt
  does not occupy; an internal thread adds everything inside the hole that the
  bolt (grown by the clearance) does not occupy. So a matched pair is guaranteed
  to fit by construction, and clearance is applied once, to the female side.
* **It degrades instead of failing.** ``MakePipeShell`` on a helix genuinely does
  fail at some pitch/diameter combinations. When it does, the caller gets a
  :class:`ThreadResult` marked ``cosmetic`` with a usable body, not an exception.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from ..core.errors import CadError, guard
from .occ import built_shape, make_transform, transformed
from .thread_specs import ThreadSize, clearance_for

#: Fraction of the pitch left flat at the crest and root (ISO 68-1 truncation).
CREST_FLAT = 1.0 / 8.0
ROOT_FLAT = 1.0 / 4.0
#: How far the tooth is sunk into the core, as a fraction of tooth depth.
#: Without this the tooth's inner face lies exactly on the core cylinder, and a
#: boolean between tangential faces is where OCCT most reliably falls over --
#: M20 produced an invalid zero-volume solid and took 5x longer.
ROOT_OVERLAP = 0.05
#: Matching slack on the envelope used to carve the groove, same reasoning.
ENVELOPE_MARGIN = 0.02


@dataclass
class ThreadResult:
    """The outcome of applying a thread."""

    shape: object
    modelled: bool
    message: str = ""

    @property
    def cosmetic(self) -> bool:
        return not self.modelled


def helix_edge(
    radius: float,
    pitch: float,
    length: float,
    left_hand: bool = False,
    base_z: float = 0.0,
):
    """A true helical edge on a cylinder of *radius*, climbing +Z from *base_z*."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.BRepLib import BRepLib
    from OCP.Geom import Geom_CylindricalSurface
    from OCP.Geom2d import Geom2d_Line, Geom2d_TrimmedCurve
    from OCP.gp import gp_Ax3, gp_Dir, gp_Dir2d, gp_Lin2d, gp_Pnt, gp_Pnt2d

    if radius <= 0 or pitch <= 0 or length <= 0:
        raise CadError("A thread needs a positive diameter, pitch and length.")

    surface = Geom_CylindricalSurface(
        gp_Ax3(gp_Pnt(0, 0, base_z), gp_Dir(0, 0, 1)), radius
    )
    # In the surface's UV space, u is the angle and v is the height, so a helix
    # is simply a straight line of slope pitch / 2pi.
    slope = pitch / (2.0 * math.pi)
    direction = gp_Dir2d(-1.0 if left_hand else 1.0, slope)
    line = Geom2d_Line(gp_Lin2d(gp_Pnt2d(0.0, 0.0), direction))

    turns = length / pitch
    # The 2D direction is unit length, so advancing u by 2*pi*turns needs a
    # parameter span scaled by the hypotenuse of (1, slope).
    span = 2.0 * math.pi * turns * math.hypot(1.0, slope)
    trimmed = Geom2d_TrimmedCurve(line, 0.0, span)

    edge = BRepBuilderAPI_MakeEdge(trimmed, surface).Edge()
    BRepLib.BuildCurves3d_s(edge)
    return edge


def _tooth_profile(
    major_radius: float,
    minor_radius: float,
    pitch: float,
    base_z: float = 0.0,
    overlap: float = 0.0,
):
    """A closed trapezoid from root to crest, in the XZ plane at angle zero.

    Built at its final position rather than transformed into place: OCCT's
    transform returns a ``TopoDS_Shape``, and ``MakePipeShell`` needs a
    ``TopoDS_Wire``, so moving it afterwards would silently break the sweep.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
    from OCP.gp import gp_Pnt

    crest = pitch * CREST_FLAT / 2.0
    root = pitch * ROOT_FLAT / 2.0
    inner = minor_radius - overlap
    polygon = BRepBuilderAPI_MakePolygon()
    for x, z in (
        (inner, -root),
        (major_radius, -crest),
        (major_radius, crest),
        (inner, root),
    ):
        polygon.Add(gp_Pnt(x, 0.0, base_z + z))
    polygon.Close()
    return polygon.Wire()


def thread_solid(
    major_diameter: float,
    pitch: float,
    length: float,
    *,
    angle: float = 60.0,
    left_hand: bool = False,
) -> object:
    """Cached wrapper around :func:`_build_thread_solid`."""
    return _cached_thread_solid(
        round(major_diameter, 4), round(pitch, 4), round(length, 4),
        round(angle, 3), bool(left_hand),
    )


@lru_cache(maxsize=64)
def _cached_thread_solid(
    major_diameter: float, pitch: float, length: float, angle: float, left_hand: bool
):
    return _build_thread_solid(
        major_diameter, pitch, length, angle=angle, left_hand=left_hand
    )


def _build_thread_solid(
    major_diameter: float,
    pitch: float,
    length: float,
    *,
    angle: float = 60.0,
    left_hand: bool = False,
) -> object:
    """The solid a bolt of this size occupies: core cylinder plus helical tooth.

    Raises :class:`CadError` if the sweep fails; callers that must not fail
    should use :func:`apply_thread`, which falls back to a plain cylinder.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Dir

    height = pitch / (2.0 * math.tan(math.radians(angle / 2.0)))
    major_radius = major_diameter / 2.0
    minor_radius = major_radius - (17.0 / 24.0) * height
    if minor_radius <= 0:
        raise CadError(
            "This pitch is too coarse for that diameter.",
            suggestion="Choose a finer pitch or a larger diameter.",
        )

    # The helix runs exactly the requested length. An earlier version swept a
    # pitch beyond each end and trimmed it back, which cost an extra boolean on
    # the most expensive solid in the program for no visible benefit.
    with guard("thread"):
        spine = BRepBuilderAPI_MakeWire(
            helix_edge(minor_radius, pitch, length, left_hand)
        ).Wire()
        profile = _tooth_profile(
            major_radius,
            minor_radius,
            pitch,
            overlap=ROOT_OVERLAP * (major_radius - minor_radius),
        )

        shell = BRepOffsetAPI_MakePipeShell(spine)
        # Binormal mode with a fixed +Z: keeps the tooth radial and upright as it
        # climbs, which Frenet mode does not.
        shell.SetMode(gp_Dir(0.0, 0.0, 1.0))
        shell.Add(profile, False, False)
        shell.Build()
        if not shell.IsDone():
            raise CadError(
                "The thread geometry could not be swept along its helix.",
                suggestion="Try a coarser pitch or a larger diameter.",
            )
        shell.MakeSolid()
        ridge = shell.Shape()

        core = BRepPrimAPI_MakeCylinder(minor_radius, length).Shape()
        return built_shape(BRepAlgoAPI_Fuse(core, ridge), "thread")


def _axis_transform(origin, direction):
    """Move geometry built on +Z at the origin onto an arbitrary axis."""
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf

    target = gp_Ax3(gp_Pnt(*origin), gp_Dir(*direction))
    source = gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
    transform = gp_Trsf()
    transform.SetDisplacement(source, target)
    return transform


def apply_thread(
    body,
    *,
    size: ThreadSize,
    origin: tuple[float, float, float],
    direction: tuple[float, float, float],
    length: float,
    internal: bool,
    clearance: str | float = "normal",
    left_hand: bool = False,
    feature_diameter: float | None = None,
) -> ThreadResult:
    """Cut or add thread geometry on the cylindrical feature at *origin*.

    *feature_diameter* is the measured diameter of the face being threaded. When
    it differs from the nominal size, the shaft is turned down (or the bore
    opened out) to nominal over the threaded length -- which is what a die or a
    tap physically does. Without it, threading a 12.4 mm shaft as M12 leaves an
    unthreaded sleeve of the original diameter wrapped around the thread.

    An undersize feature cannot carry a full-depth thread; that comes back as a
    message rather than a silent partial thread.

    Returns a :class:`ThreadResult`; on a sweep failure the body comes back
    unchanged and marked cosmetic rather than raising, so a fragile thread never
    blocks the user or empties the model.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

    gap = clearance_for(clearance) if internal else 0.0
    if internal:
        # The bolt this nut must accept: nominal plus the printable clearance.
        diameter = size.diameter + gap
    else:
        # Oversize very slightly so the crest pokes through the shaft surface
        # rather than resting exactly on it. Coincident faces are what makes the
        # subsequent cut produce an invalid solid; the surplus is outside the
        # shaft and is trimmed away by the cut itself, leaving the crest at
        # exactly the nominal diameter.
        diameter = size.diameter + 2.0 * ENVELOPE_MARGIN
    placement = _axis_transform(origin, direction)

    note = ""
    envelope_radius = diameter / 2.0 + ENVELOPE_MARGIN
    if feature_diameter is not None and feature_diameter > 0:
        nominal = size.diameter
        if internal:
            # A bore wider than the thread needs the envelope to reach the wall,
            # or a ring of the original bore survives between the thread crests.
            envelope_radius = max(envelope_radius, feature_diameter / 2.0 + ENVELOPE_MARGIN)
            if feature_diameter > nominal + 0.05:
                note = (
                    f"The hole is ⌀{feature_diameter:.2f} mm, wider than "
                    f"{size.designation}'s ⌀{nominal:.2f} mm. The thread was cut "
                    "to the standard size; the fit will be loose."
                )
            elif feature_diameter < nominal - 0.05:
                note = (
                    f"The hole is ⌀{feature_diameter:.2f} mm, narrower than "
                    f"{size.designation}'s ⌀{nominal:.2f} mm, so the thread is "
                    "shallower than standard."
                )
        else:
            envelope_radius = max(envelope_radius, feature_diameter / 2.0 + ENVELOPE_MARGIN)
            if feature_diameter > nominal + 0.05:
                note = (
                    f"The shaft was ⌀{feature_diameter:.2f} mm and has been turned "
                    f"down to {size.designation}'s ⌀{nominal:.2f} mm over the "
                    "threaded length."
                )
            elif feature_diameter < nominal - 0.05:
                note = (
                    f"The shaft is ⌀{feature_diameter:.2f} mm, thinner than "
                    f"{size.designation}'s ⌀{nominal:.2f} mm, so the thread does "
                    "not reach full depth. Consider a smaller size."
                )

    try:
        bolt = thread_solid(
            diameter, size.pitch, length, angle=size.angle, left_hand=left_hand
        )
    except CadError as exc:
        return ThreadResult(
            body,
            modelled=False,
            message=(
                f"{exc.message} {size.designation} is shown as a plain cylinder "
                "for now; the size and pitch are still recorded."
            ),
        )

    with guard("thread"):
        # A hair oversize so the crest faces are not tangential to the envelope.
        # The surplus lies outside the shaft (external) or inside solid material
        # (internal), so it changes nothing about the resulting geometry.
        envelope = BRepPrimAPI_MakeCylinder(envelope_radius, length).Shape()
        if internal:
            # Everything inside the bore the bolt does not occupy is nut metal.
            filler = built_shape(BRepAlgoAPI_Cut(envelope, bolt), "thread")
            filler = transformed(filler, placement)
            shape = built_shape(BRepAlgoAPI_Fuse(body, filler), "thread")
        else:
            # Everything inside the shaft the bolt does not occupy is waste.
            waste = built_shape(BRepAlgoAPI_Cut(envelope, bolt), "thread")
            waste = transformed(waste, placement)
            shape = built_shape(BRepAlgoAPI_Cut(body, waste), "thread")
    return ThreadResult(shape, modelled=True, message=note)


def matching_size(size: ThreadSize) -> ThreadSize:
    """The complementary thread for a pair -- same standard, same designation.

    Male and female threads of a pair share a designation; what differs is which
    side carries the clearance, which :func:`apply_thread` handles.
    """
    return size
