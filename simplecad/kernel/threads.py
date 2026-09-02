"""Modelled, printable thread geometry.

A thread is a profile swept along a helix. The helix is built as a straight line
in the UV space of a cylindrical surface, which is the only construction OCCT
handles well; the profile is a trapezoid spanning root to crest; the sweep is a
pipe shell in binormal mode so the tooth stays radial as it climbs.

Three ideas keep this usable rather than merely correct:

* **One solid describes both halves of a pair.** :func:`thread_solid` builds the
  bolt. An external thread cuts away everything inside the shaft that the bolt
  does not occupy; an internal thread adds everything inside the hole that the
  bolt (grown by the clearance) does not occupy. So a matched pair is guaranteed
  to fit by construction, and clearance is applied once, to the female side.
* **The tooth is shaped for the printer, not for a tap.** A 60-degree ISO tooth
  has flanks 27 degrees off the radial plane, so printed axis-up its underside
  overhangs by 63 degrees and needs support -- and support inside a thread is
  what makes the two halves refuse to turn. The default :class:`ThreadForm` puts
  the flanks at 45 degrees instead, with real flats at crest and root so there
  are no knife edges to curl or fuse shut. See :data:`PRINT_FLANK_ANGLE`.
* **It protects the upstream body on failure.** ``MakePipeShell`` on a helix can
  fail at some pitch/diameter combinations. The low-level operation returns an
  unchanged shape marked ``cosmetic`` so geometry is never lost; user-facing
  features then reject that result with :func:`require_modelled`, ensuring a
  plain cylinder is never reported as a successfully created thread.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from ..core.errors import CadError, guard
from .occ import axis_transform, built_shape, transformed
from .thread_specs import ThreadSize, effective_clearance_for

#: Fraction of the pitch left flat at the crest and at the root of an ISO form
#: (ISO 68-1 truncation). ``ROOT_FLAT`` is the width of the *groove* at the root,
#: which is why the tooth is ``pitch - ROOT_FLAT`` wide there and not
#: ``ROOT_FLAT`` wide -- reading it the other way round is what produced a
#: razor-thin fin with a 6-degree flank instead of a thread.
CREST_FLAT = 1.0 / 8.0
ROOT_FLAT = 1.0 / 4.0

#: The printable form: flank angle measured from the radial plane, in degrees.
#: 45 is the number that matters -- the downward flank of a thread printed
#: axis-up overhangs by ``90 - this``, and 45 degrees is what an FDM printer
#: bridges unsupported. Raising it makes a shallower, weaker tooth; lowering it
#: brings the supports back.
PRINT_FLANK_ANGLE = 45.0
#: Flats at crest and root of the printable form, as a fraction of the pitch.
#: Both non-zero on purpose: a sharp crest prints as a curl and a sharp root
#: fills with the tooth opposite it. The root flat is the wider of the two for
#: the same reason ISO makes it wider -- it is the gap between one turn of the
#: tooth and the next, and at ``1/8`` the two nearly touch, which is where
#: ``MakePipeShell`` starts handing back solids that will not validate.
PRINT_CREST_FLAT = 1.0 / 8.0
PRINT_ROOT_FLAT = 1.0 / 4.0

#: The smallest change in a body's volume that counts as a boolean having
#: happened, as a fraction of the body. See :func:`_apply_tool`.
MIN_VOLUME_CHANGE = 1e-6

#: How far to shift a tool along its axis when a boolean comes back having done
#: nothing, in millimetres. One micron: far below the clearance a printed fit is
#: built around and far below anything a nozzle resolves, but enough to move the
#: tool's end faces off the body's and let OCCT find the intersection it missed.
#: See :func:`_tool_attempts`.
NUDGE = 1e-3

#: How far the tooth is sunk into the core, as a fraction of tooth depth.
#:
#: Without this the tooth's inner face lies exactly on the core cylinder, and a
#: boolean between tangential faces is where OCCT most reliably falls over --
#: M20 produced an invalid zero-volume solid and took 5x longer. At 0.05 it was
#: still too thin: fusing the tooth onto the core returned the *tooth alone* at
#: some sizes, a bolt with no shaft, which then carved a nut with no thread.
#: None of it raised. See ``_ATTEMPT_OVERLAPS`` for what happens when even this
#: is not enough.
ROOT_OVERLAP = 0.15
#: Deeper penetrations to retry with when the fuse drops an operand. Sunk metal
#: is buried inside the core, so a deeper tooth changes nothing you can measure
#: on the finished thread -- it only gives the kernel more to work with.
_ATTEMPT_OVERLAPS = (ROOT_OVERLAP, 0.5, 1.0)
#: Matching slack on the envelope used to carve the groove, same reasoning.
ENVELOPE_MARGIN = 0.02
#: Thread forms, by code. The first is the default.
FORMS = ("printed", "iso")


@dataclass(frozen=True)
class ThreadForm:
    """The shape of one tooth in the axial cross-section.

    Everything the sweep needs, and everything a printability check wants to
    ask, derived in one place so the profile and the warnings can never
    disagree about what was actually built.
    """

    code: str
    pitch: float
    depth: float          # radial, root to crest
    crest_flat: float     # axial width of the flat at the crest
    root_flat: float      # axial width of the groove at the root

    @property
    def crest_half(self) -> float:
        return self.crest_flat / 2.0

    @property
    def root_half(self) -> float:
        """Half the tooth's axial width where it meets the core."""
        return (self.pitch - self.root_flat) / 2.0

    @property
    def flank_run(self) -> float:
        """How far the flank travels along the axis, root to crest."""
        return self.root_half - self.crest_half

    @property
    def flank_angle(self) -> float:
        """Degrees between the flank and the radial plane."""
        return math.degrees(math.atan2(self.flank_run, self.depth))

    @property
    def overhang(self) -> float:
        """Degrees from vertical the lower flank leans, printed axis-up.

        The number that decides whether the thread needs supports.
        """
        return 90.0 - self.flank_angle

    def describe(self) -> str:
        return (
            f"{self.depth:.2f} mm deep, {self.flank_angle:.0f}° flanks "
            f"({self.overhang:.0f}° overhang)"
        )


def thread_form(pitch: float, angle: float = 60.0, code: str = "printed") -> ThreadForm:
    """The tooth shape for a pitch, under the named form."""
    if pitch <= 0:
        raise CadError("A thread needs a pitch greater than zero.")
    if code == "iso":
        # H, the height of the sharp (untruncated) profile triangle.
        height = pitch / (2.0 * math.tan(math.radians(angle / 2.0)))
        return ThreadForm(
            code="iso",
            pitch=pitch,
            depth=(17.0 / 24.0) * height,
            crest_flat=pitch * CREST_FLAT,
            root_flat=pitch * ROOT_FLAT,
        )
    crest = pitch * PRINT_CREST_FLAT
    root = pitch * PRINT_ROOT_FLAT
    run = (pitch - crest - root) / 2.0
    if run <= 0:
        raise CadError("The flats leave no room for a tooth at this pitch.")
    return ThreadForm(
        code="printed",
        pitch=pitch,
        depth=run / math.tan(math.radians(PRINT_FLANK_ANGLE)),
        crest_flat=crest,
        root_flat=root,
    )


@dataclass
class ThreadResult:
    """The outcome of applying a thread."""

    shape: object
    modelled: bool
    message: str = ""

    @property
    def cosmetic(self) -> bool:
        return not self.modelled


@dataclass(frozen=True)
class ThreadPrintability:
    """Whether a physical thread is credible on the active printer."""

    printable: bool
    reason: str = ""
    replacement: str = ""


def thread_printability(
    size: ThreadSize, form: str = "printed", profile: dict | None = None
) -> ThreadPrintability:
    """Validate feature size and overhang, and name a usable alternative."""
    from .thread_specs import load_sizes, printer_profile

    profile = profile or printer_profile()

    def reason_for(candidate: ThreadSize, candidate_form: str) -> str:
        tooth = thread_form(candidate.pitch, candidate.angle, candidate_form)
        minimum = float(profile.get("min_feature_size", 0.0))
        limit = float(profile.get("max_overhang_angle", 90.0))
        if minimum and tooth.depth < minimum:
            return (
                f"{candidate.designation}'s {tooth.depth:.2f} mm tooth is below "
                f"this printer's {minimum:.2f} mm feature limit."
            )
        if tooth.overhang > limit + 0.5:
            return (
                f"Its {tooth.overhang:.0f}° overhang is beyond this printer's "
                f"{limit:.0f}° support-free limit."
            )
        return ""

    reason = reason_for(size, form)
    if not reason:
        return ThreadPrintability(True)

    if form != "printed" and not reason_for(size, "printed"):
        return ThreadPrintability(False, reason, f"{size.designation} printed form")

    candidates = [
        candidate for candidate in load_sizes()
        if not reason_for(candidate, "printed")
    ]
    candidates.sort(key=lambda candidate: (
        abs(candidate.diameter - size.diameter),
        0 if candidate.code == "printed" else 1,
        -candidate.pitch,
    ))
    replacement = candidates[0].designation if candidates else ""
    return ThreadPrintability(False, reason, replacement)


def require_printable(size: ThreadSize, form: str = "printed") -> None:
    """Refuse geometry the selected FDM profile cannot reproduce reliably."""
    result = thread_printability(size, form)
    if result.printable:
        return
    suggestion = (
        f"Use {result.replacement}." if result.replacement
        else "Choose a coarser pitch or a larger thread."
    )
    raise CadError(
        result.reason,
        suggestion=suggestion,
        operation="thread",
    )


def require_modelled(result: ThreadResult) -> ThreadResult:
    """Return *result* only when it contains physical helical geometry.

    ``apply_thread`` deliberately has a safe low-level fallback: if OCCT cannot
    build the sweep, it returns the unchanged body instead of destroying it.
    That is useful kernel behaviour, but a user-facing Thread feature must not
    call a plain cylinder a success. Feature implementations pass their result
    through here so the rebuild retains the upstream body and reports a clear
    failure instead.
    """
    if result.modelled:
        return result
    from ..core.errors import CadError

    raise CadError(
        result.message or "The physical thread could not be modelled.",
        suggestion="Choose a coarser size or a shorter threaded length.",
        operation="thread",
    )


def helix_edge(
    radius: float,
    pitch: float,
    length: float,
    left_hand: bool = False,
    base_z: float = 0.0,
    taper: float = 0.0,
):
    """A true helical edge on a cylinder or tapered cone.

    ``taper`` is the change in diameter per unit axial length. A positive value
    narrows toward +Z, matching NPT's 1:16 diametral taper.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.BRepLib import BRepLib
    from OCP.Geom import Geom_ConicalSurface, Geom_CylindricalSurface
    from OCP.Geom2d import Geom2d_Line, Geom2d_TrimmedCurve
    from OCP.gp import gp_Ax3, gp_Dir, gp_Dir2d, gp_Lin2d, gp_Pnt, gp_Pnt2d

    if radius <= 0 or pitch <= 0 or length <= 0:
        raise CadError("A thread needs a positive diameter, pitch and length.")

    radial_slope = taper / 2.0
    cone_angle = -math.atan(radial_slope) if taper else 0.0
    axis = gp_Ax3(gp_Pnt(0, 0, base_z), gp_Dir(0, 0, 1))
    surface = (
        Geom_ConicalSurface(axis, cone_angle, radius)
        if taper else Geom_CylindricalSurface(axis, radius)
    )
    # In the surface's UV space, u is the angle and v is the height, so a helix
    # is simply a straight line of slope pitch / 2pi.
    # A cone's v parameter follows its sloping generatrix, so its axial
    # component is v*cos(angle). Correcting for that keeps pitch axial.
    slope = pitch / (2.0 * math.pi * math.cos(cone_angle))
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


def thread_profile_points(
    major_radius: float,
    form: ThreadForm,
    base_z: float = 0.0,
    overlap: float = 0.0,
    swell: float = 0.0,
) -> list[tuple[float, float]]:
    """The tooth's four corners as ``(radius, z)``, root first, going clockwise.

    Pure geometry, deliberately separate from the OCCT wire, so the shape of the
    tooth -- the thing that decides whether the thread prints -- can be asserted
    directly in a test without building a solid.

    *swell* widens the tooth along the axis. Only the female side uses it, and
    it is what makes the printable clearance mean the same thing in every
    direction: growing the carving bolt's diameter alone opens the gap radially
    and leaves the flanks as close as they ever were, which on a coarse thread
    -- where the flanks, not the crests, are what actually touch -- is most of
    the clearance going to the one place it is not needed.
    """
    inner = major_radius - form.depth - overlap
    crest = form.crest_half + swell
    root = min(form.root_half + swell, form.pitch / 2.0 - 1e-4)
    return [
        (inner, base_z - root),
        (major_radius, base_z - crest),
        (major_radius, base_z + crest),
        (inner, base_z + root),
    ]


def _tooth_profile(
    major_radius: float,
    form: ThreadForm,
    base_z: float = 0.0,
    overlap: float = 0.0,
    swell: float = 0.0,
):
    """A closed trapezoid from root to crest, in the XZ plane at angle zero.

    Built at its final position rather than transformed into place: OCCT's
    transform returns a ``TopoDS_Shape``, and ``MakePipeShell`` needs a
    ``TopoDS_Wire``, so moving it afterwards would silently break the sweep.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
    from OCP.gp import gp_Pnt

    polygon = BRepBuilderAPI_MakePolygon()
    for x, z in thread_profile_points(major_radius, form, base_z, overlap, swell):
        polygon.Add(gp_Pnt(x, 0.0, z))
    polygon.Close()
    return polygon.Wire()


def thread_solid(
    major_diameter: float,
    pitch: float,
    length: float,
    *,
    angle: float = 60.0,
    left_hand: bool = False,
    form: str = "printed",
    swell: float = 0.0,
    taper: float = 0.0,
) -> object:
    """Cached wrapper around :func:`_build_thread_solid`."""
    return _cached_thread_solid(
        round(major_diameter, 4), round(pitch, 4), round(length, 4),
        round(angle, 3), bool(left_hand), str(form), round(swell, 4),
        round(taper, 6),
    )


@lru_cache(maxsize=64)
def _cached_thread_solid(
    major_diameter: float, pitch: float, length: float, angle: float,
    left_hand: bool, form: str, swell: float, taper: float,
):
    return _build_thread_solid(
        major_diameter, pitch, length, angle=angle, left_hand=left_hand,
        form=form, swell=swell, taper=taper,
    )


def _build_thread_solid(
    major_diameter: float,
    pitch: float,
    length: float,
    *,
    angle: float = 60.0,
    left_hand: bool = False,
    form: str = "printed",
    swell: float = 0.0,
    taper: float = 0.0,
) -> object:
    """The solid a bolt of this size occupies: core cylinder plus helical tooth.

    Raises :class:`CadError` if the sweep fails; callers that must not fail
    should use :func:`apply_thread`, which falls back to a plain cylinder.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Fuse
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Dir

    shape = thread_form(pitch, angle, form)
    major_radius = major_diameter / 2.0
    minor_radius = major_radius - shape.depth
    end_minor_radius = minor_radius - taper * length / 2.0
    if min(minor_radius, end_minor_radius) <= 0:
        raise CadError(
            "This pitch is too coarse for that diameter.",
            suggestion="Choose a finer pitch or a larger diameter.",
        )

    # The helix runs exactly the requested length. An earlier version swept a
    # pitch beyond each end and trimmed it back, which cost an extra boolean on
    # the most expensive solid in the program for no visible benefit.
    with guard("thread"):
        # Sweep on the parallel reference cylinder, then trim the complete bolt
        # with the standard's major-diameter cone. This is considerably more
        # robust than PipeShell on a conical spine (whose fixed-binormal frame
        # twists the profile differently at different radii), and most
        # importantly preserves the containment invariant between nominal and
        # clearance-grown mating solids.
        spine = BRepBuilderAPI_MakeWire(
            helix_edge(minor_radius, pitch, length, left_hand)
        ).Wire()
        core = BRepPrimAPI_MakeCylinder(minor_radius, length).Shape()
        core_volume = math.pi * minor_radius ** 2 * length

        for overlap in _ATTEMPT_OVERLAPS:
            profile = _tooth_profile(
                major_radius, shape, overlap=overlap * shape.depth, swell=swell
            )
            shell = BRepOffsetAPI_MakePipeShell(spine)
            # Binormal mode with a fixed +Z: keeps the tooth radial and upright
            # as it climbs, which Frenet mode does not.
            shell.SetMode(gp_Dir(0.0, 0.0, 1.0))
            shell.Add(profile, False, False)
            shell.Build()
            if not shell.IsDone():
                raise CadError(
                    "The thread geometry could not be swept along its helix.",
                    suggestion="Try a coarser pitch or a larger diameter.",
                )
            shell.MakeSolid()
            bolt = built_shape(BRepAlgoAPI_Fuse(core, shell.Shape()), "thread")
            # A fuse that dropped the core reports done and hands back the
            # tooth on its own, so the answer has to be checked rather than
            # trusted. Sinking the tooth deeper gives the kernel more overlap
            # to work with and is invisible in the finished thread.
            if _volume(bolt) >= core_volume * 0.99:
                if taper:
                    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

                    end_major_radius = major_radius - taper * length / 2.0
                    start_limit = (
                        major_radius + ENVELOPE_MARGIN + taper * NUDGE / 2.0
                    )
                    end_limit = (
                        end_major_radius + ENVELOPE_MARGIN - taper * NUDGE / 2.0
                    )
                    limiter = BRepPrimAPI_MakeCone(
                        gp_Ax2(gp_Pnt(0.0, 0.0, -NUDGE), gp_Dir(0.0, 0.0, 1.0)),
                        start_limit, end_limit, length + 2.0 * NUDGE,
                    ).Shape()
                    bolt = built_shape(
                        BRepAlgoAPI_Common(bolt, limiter), "tapered thread"
                    )
                    if _volume(bolt) <= 0.0:
                        continue
                return bolt

        raise CadError(
            "The thread could not be built at this diameter and pitch.",
            suggestion="Try a coarser pitch or a larger diameter.",
        )


def _volume(shape) -> float:
    from .occ import volume

    try:
        return volume(shape)
    except Exception:  # noqa: BLE001
        return 0.0


def _lead_length(envelope_radius: float, minor_radius: float, length: float) -> float:
    """How much of each end the thread fades over, at 45 degrees.

    Equal rise and run, so the taper is exactly the steepest angle an FDM
    printer bridges unsupported -- there is no point relieving a thread for
    printing with a surface that then needs support itself.
    """
    lead = envelope_radius - minor_radius
    if lead <= 1e-4 or 2.0 * lead >= length:
        return 0.0
    return lead


def _mouth_cones(envelope_radius: float, minor_radius: float, length: float) -> list:
    """Cones to subtract from the bore envelope, wide at each mouth.

    A countersink, taken out of the envelope *before* the nut's thread is carved
    from it, so the thread fades away over the last turn and the bolt has
    somewhere to start rather than a knife edge to fight.

    Cut from the envelope rather than fused onto the bolt, and that is not a
    stylistic choice. Fusing a cone onto a helical solid is a boolean between a
    cone and a swept spiral, and OCCT reports it done while handing back the
    cone alone -- which threw the bolt away and left the nut a solid plug that
    no bolt could ever enter. Every boolean here is instead between two analytic
    primitives, where the kernel is dependable.
    """
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    lead = _lead_length(envelope_radius, minor_radius, length)
    if not lead:
        return []

    def at(z: float):
        return gp_Ax2(gp_Pnt(0.0, 0.0, z), gp_Dir(0.0, 0.0, 1.0))

    return [
        BRepPrimAPI_MakeCone(at(0.0), envelope_radius, minor_radius, lead).Shape(),
        BRepPrimAPI_MakeCone(
            at(length - lead), minor_radius, envelope_radius, lead
        ).Shape(),
    ]


def _end_rings(envelope_radius: float, minor_radius: float, length: float) -> list:
    """Rings whose removal chamfers both ends of a threaded shaft.

    The counterpart of :func:`_mouth_cones` for the male side: what is left
    after cutting these away is a 45-degree chamfer from the crest down to the
    core, so the bolt starts by hand and its first tooth is not a fragile sliver
    the nozzle drags off.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    lead = _lead_length(envelope_radius, minor_radius, length)
    if not lead:
        return []

    def at(z: float):
        return gp_Ax2(gp_Pnt(0.0, 0.0, z), gp_Dir(0.0, 0.0, 1.0))

    rings = []
    for z, bottom, top in (
        (0.0, minor_radius, envelope_radius),
        (length - lead, envelope_radius, minor_radius),
    ):
        block = BRepPrimAPI_MakeCylinder(at(z), envelope_radius, lead).Shape()
        keep = BRepPrimAPI_MakeCone(at(z), bottom, top, lead).Shape()
        rings.append(built_shape(BRepAlgoAPI_Cut(block, keep), "thread"))
    return rings


def _tool_attempts(tool, axis):
    """The tool, and then the tool nudged a micron each way along *axis*.

    OCCT hands one operand straight back on geometry it ought to manage. Measured
    on an M12 thread applied to an 11.6 mm shaft: the waste genuinely overlaps
    the shaft by 231.5 mm3, and the cut removed exactly nothing -- at every fuzzy
    value from 1e-6 to 1e-3, and whether the shaft was flush with the waste,
    longer than it or shorter. Moving the tool one micron along the axis removed
    the full 231.5.

    So, the same reasoning as swapping the operands, one step further: try the
    obvious thing, and when the kernel declines, ask again slightly differently
    rather than quietly handing back a shaft with no thread on it.
    """
    yield tool
    if axis is None:
        return
    from OCP.gp import gp_Trsf, gp_Vec

    for step in (NUDGE, -NUDGE):
        move = gp_Trsf()
        move.SetTranslation(gp_Vec(*(component * step for component in axis)))
        try:
            shifted = transformed(tool, move)
        except BaseException:  # noqa: BLE001 - OCCT raises non-Exceptions
            continue
        yield shifted


def _apply_tool(body, tool, cut: bool, axis=None):
    """Add or remove *tool* from *body*, and check the kernel's answer.

    OCCT reports these booleans done and hands back one operand untouched often
    enough that the result cannot be trusted on sight. Fusing a thread's filler
    into a drilled plate returned **the filler**, so the part became a loose
    ring of thread floating where the lid had been -- valid geometry, a
    successful rebuild, and the model destroyed. Cutting has the mirror failure:
    the waste comes back instead of the threaded shaft.

    Swapping the operands is not superstition; it genuinely succeeds where the
    first order fails, so a fuse gets both orders before giving up. Returns None
    when neither produces something that could be the answer, and the caller
    then leaves the body alone and says so.

    **The body coming back untouched is a failure, not a success.** OCCT reports
    that done too, and the old test -- "most of the body is still there" -- was
    satisfied by exactly that, so a thread that never got applied was reported as
    a finished one and the user got a plain hole with no warning at all. The
    volume has to have actually moved, and moved by no more than the tool could
    account for.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse

    from .occ import volume

    before = volume(body)
    tool_volume = volume(tool)
    # Enough to tell a real thread from arithmetic noise. The shallowest tooth
    # the size tables offer still moves ~4e-5 of a plate's volume, and a boolean
    # that did nothing moves ~1e-15 of it, so there is no near miss to worry
    # about in between.
    threshold = abs(before) * MIN_VOLUME_CHANGE
    for candidate in _tool_attempts(tool, axis):
        # A cut has one sensible order; a fuse is symmetric, so it gets two goes.
        orders = (
            [(body, candidate)] if cut
            else [(body, candidate), (candidate, body)]
        )
        for first, second in orders:
            try:
                result = built_shape(
                    (BRepAlgoAPI_Cut if cut else BRepAlgoAPI_Fuse)(first, second),
                    "thread",
                )
            except BaseException:  # noqa: BLE001 - OCCT raises non-Exceptions
                continue
            after = volume(result)
            moved = (before - after) if cut else (after - before)
            if moved <= threshold:
                # The operand came back untouched, or it moved the wrong way.
                continue
            # Nothing can be added or removed that the tool did not occupy, so a
            # bigger change means one of the operands was substituted.
            if tool_volume > 0.0 and moved > tool_volume * 1.05:
                continue
            if cut:
                # Threading a shaft removes a little and must leave most of it.
                if before * 0.25 < after <= before * 1.001:
                    return result
            elif after >= before * 0.99:
                return result
    return None


def _cut_away(shape, tools, placement=None):
    """Subtract each of *tools* in turn. Never worth failing the thread over."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

    for tool in tools:
        try:
            if placement is not None:
                tool = transformed(tool, placement)
            shape = built_shape(BRepAlgoAPI_Cut(shape, tool), "thread")
        except BaseException:  # noqa: BLE001 - a lead-in is a courtesy, not a must
            continue
    return shape


def required_bore(size: ThreadSize, clearance: str | float = "normal") -> float:
    """The bore an internal thread of *size* is cut into.

    Nominal plus the printable clearance -- the tap drill, in the only sense
    that matters here. Shared with :func:`fasteners.make_threaded_hole_tool` so
    a hole drilled for a thread and a hole re-bored for one can never disagree
    about what size that is.
    """
    return size.diameter + effective_clearance_for(clearance)


def thread_needs_resizing(
    measured_diameter: float,
    size: ThreadSize,
    *,
    internal: bool,
    clearance: str | float = "normal",
    form: str = "printed",
) -> bool:
    """Would this thread fail outright on a feature of this diameter?

    Not "is it a bit off" -- :func:`apply_thread` already copes with a bore
    somewhat wider or narrower than nominal, filling one and shallowing the
    other, and says so. This is the case where there is no thread to be had at
    all: a bore at or inside the carving bolt's own minor diameter leaves the
    fuse nothing to add, so the kernel reports the body unchanged and the
    feature fails. Threading a 8 mm hole as P12 is exactly that.
    """
    if measured_diameter <= 0:
        return False
    tooth = thread_form(size.pitch, size.angle, form)
    if internal:
        gap = effective_clearance_for(clearance) + NUDGE
        minor = (size.diameter + gap) / 2.0 - tooth.depth
        return measured_diameter / 2.0 <= minor + ENVELOPE_MARGIN
    # A shaft has to carry the full tooth; thinner than the root and there is
    # nothing left to cut a thread into.
    return measured_diameter / 2.0 <= size.diameter / 2.0 - tooth.depth


def resize_for_thread(
    body,
    *,
    diameter: float,
    origin: tuple[float, float, float],
    direction: tuple[float, float, float],
    length: float,
    internal: bool,
):
    """Open a bore out, or build a shaft up, to *diameter* over *length*.

    What a tap drill does before the tap, and what makes "thread this hole"
    answerable for a hole that was never drilled with a thread in mind. Cut for
    a bore, fused for a shaft; both extended a whisker past each end so the
    boolean is never asked to resolve coincident end faces, which is the one
    thing OCCT reliably gets wrong on this geometry.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

    if diameter <= 0 or length <= 0:
        return body
    with guard("thread"):
        tool = BRepPrimAPI_MakeCylinder(
            diameter / 2.0, length + 2.0 * NUDGE
        ).Shape()
        tool = transformed(
            tool,
            axis_transform(
                tuple(o - d * NUDGE for o, d in zip(origin, direction)), direction
            ),
        )
        return built_shape(
            (BRepAlgoAPI_Cut if internal else BRepAlgoAPI_Fuse)(body, tool),
            "thread",
        )


def resize_target(
    body,
    info,
    size: ThreadSize,
    *,
    length: float,
    origin: tuple[float, float, float] | None = None,
    clearance: str | float = "normal",
    form: str = "printed",
    resize: bool = False,
) -> tuple[object, float, str]:
    """Bring a feature to the diameter *size* wants, when asked -- or explain.

    The one place that decides what "thread this hole" means when the hole was
    not drilled for this thread. Returns the body, the diameter the thread
    should be told it is cutting into, and a sentence describing what moved --
    empty when nothing did.

    Only ever *adds room*: a bore is opened, a shaft is thickened. A feature
    already larger than the thread needs is left alone, because
    :func:`apply_thread` handles that case itself by taking its envelope out to
    the wall.
    """
    wanted = required_bore(size, clearance) if info.internal else size.diameter
    if not resize:
        if thread_needs_resizing(
            info.diameter, size, internal=info.internal,
            clearance=clearance, form=form,
        ):
            raise CadError(
                f"This {info.kind} is ⌀{info.diameter:.2f} mm and "
                f"{size.designation} needs ⌀{wanted:.2f} mm, so there is nothing "
                "for the thread to bite into.",
                suggestion=(
                    "Turn on “Resize to suit the thread”, or choose a size "
                    "nearer this diameter."
                ),
                operation="thread",
            )
        return body, info.diameter, ""
    # Only when the feature is meaningfully off. A bore drilled at the thread's
    # nominal size is already right -- apply_thread's envelope takes it out to
    # the wall -- and "opening" it by the clearance would be a change the user
    # can measure for no gain they can. Same 0.05 mm the shallow-thread warning
    # uses, so the panel and the kernel draw the line in one place.
    if info.diameter >= size.diameter - 0.05:
        return body, info.diameter, ""
    body = resize_for_thread(
        body, diameter=wanted, origin=origin or info.origin,
        direction=info.direction, length=length, internal=info.internal,
    )
    verb = "opened" if info.internal else "built up"
    return body, wanted, (
        f"The {info.kind} was {verb} from ⌀{info.diameter:.2f} mm to "
        f"⌀{wanted:.2f} mm to take {size.designation}."
    )


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
    form: str = "printed",
) -> ThreadResult:
    """Cut or add thread geometry on the cylindrical feature at *origin*.

    *feature_diameter* is the measured diameter of the face being threaded. When
    it differs from the nominal size, the shaft is turned down (or the bore
    opened out) to nominal over the threaded length -- which is what a die or a
    tap physically does. Without it, threading a 12.4 mm shaft as M12 leaves an
    unthreaded sleeve of the original diameter wrapped around the thread.

    An undersize feature cannot carry a full-depth thread; that comes back as a
    message rather than a silent partial thread. So does a tooth too small for
    the printer to resolve.

    Returns a :class:`ThreadResult`; on a sweep failure the body comes back
    unchanged and marked cosmetic rather than raising, so a fragile thread never
    blocks the user or empties the model.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder

    tooth = thread_form(size.pitch, size.angle, form)
    try:
        gap = effective_clearance_for(clearance) if internal else 0.0
    except ValueError as exc:
        return ThreadResult(body, modelled=False, message=str(exc))
    if internal:
        # The bolt this nut must accept: nominal plus the printable clearance.
        # One micron of surplus keeps common decimal clearances (notably P6 at
        # 0.60 mm) away from an OCCT coincident-surface failure. It is far below
        # printer resolution and makes the physical fit no tighter than asked.
        geometry_gap = gap + NUDGE
        diameter = size.diameter + geometry_gap
    else:
        # Oversize very slightly so the crest pokes through the shaft surface
        # rather than resting exactly on it. Coincident faces are what makes the
        # subsequent cut produce an invalid solid; the surplus is outside the
        # shaft and is trimmed away by the cut itself, leaving the crest at
        # exactly the nominal diameter.
        diameter = size.diameter + 2.0 * ENVELOPE_MARGIN
        geometry_gap = 0.0
    placement = axis_transform(origin, direction)

    notes: list[str] = []
    envelope_radius = diameter / 2.0 + ENVELOPE_MARGIN
    if feature_diameter is not None and feature_diameter > 0:
        nominal = size.diameter
        if internal:
            # A bore wider than the thread needs the envelope to reach the wall,
            # or a ring of the original bore survives between the thread crests.
            envelope_radius = max(envelope_radius, feature_diameter / 2.0 + ENVELOPE_MARGIN)
            # Two bore sizes are both right: the thread's nominal diameter, and
            # nominal plus the clearance, which is the tap size and what a hole
            # opened for this thread becomes. Anything between them is the
            # ordinary case and says nothing; only outside the band is there
            # something to tell the user. Warning at nominal alone made the
            # kernel complain about a hole it had just bored itself.
            if feature_diameter > diameter + 0.05:
                notes.append(
                    f"The hole was ⌀{feature_diameter:.2f} mm, wider than "
                    f"{size.designation}'s ⌀{diameter:.2f} mm, and has been filled "
                    "back to the standard size over the threaded length."
                )
            elif feature_diameter < nominal - 0.05:
                notes.append(
                    f"The hole is ⌀{feature_diameter:.2f} mm, narrower than "
                    f"{size.designation}'s ⌀{nominal:.2f} mm, so the thread is "
                    "shallower than standard."
                )
        else:
            envelope_radius = max(envelope_radius, feature_diameter / 2.0 + ENVELOPE_MARGIN)
            if feature_diameter > nominal + 0.05:
                notes.append(
                    f"The shaft was ⌀{feature_diameter:.2f} mm and has been turned "
                    f"down to {size.designation}'s ⌀{nominal:.2f} mm over the "
                    "threaded length."
                )
            elif feature_diameter < nominal - 0.05:
                notes.append(
                    f"The shaft is ⌀{feature_diameter:.2f} mm, thinner than "
                    f"{size.designation}'s ⌀{nominal:.2f} mm, so the thread does "
                    "not reach full depth. Consider a smaller size."
                )

    printability = printable_note(tooth, size)
    if printability:
        notes.append(printability)

    try:
        bolt = thread_solid(
            diameter, size.pitch, length, angle=size.angle,
            left_hand=left_hand, form=form,
            # The female side alone: the male part is the nominal size, and the
            # gap between them is opened once, on the part that carves the nut.
            swell=geometry_gap / 2.0 if internal else 0.0,
            taper=size.taper,
        )
        carving_bolt = (
            thread_solid(
                diameter, size.pitch, length, angle=size.angle,
                left_hand=left_hand, form=form,
                swell=geometry_gap / 2.0 if internal else 0.0,
                taper=0.0,
            )
            if size.taper else bolt
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

    minor_radius = diameter / 2.0 - tooth.depth
    end_envelope_radius = envelope_radius - size.taper * length / 2.0
    end_minor_radius = minor_radius - size.taper * length / 2.0

    with guard("thread"):
        # A hair oversize so the crest faces are not tangential to the envelope.
        # The surplus lies outside the shaft (external) or inside solid material
        # (internal), so it changes nothing about the resulting geometry.
        envelope = (
            BRepPrimAPI_MakeCone(
                envelope_radius, end_envelope_radius, length
            ).Shape()
            if size.taper else
            BRepPrimAPI_MakeCylinder(envelope_radius, length).Shape()
        )
        # The bolt comes out of the *plain* cylinder, always, and any relief is
        # taken off the ribbon afterwards. Subtracting a helix from an envelope
        # that had already been countersunk reported success and returned the
        # envelope untouched -- a nut with a solid plug where its thread should
        # be. Simplest shape first is the order that survives.
        groove = None
        groove_envelope = (
            BRepPrimAPI_MakeCylinder(envelope_radius, length).Shape()
            if size.taper else envelope
        )
        # Coarse, generously cleared teeth can put an end face exactly on the
        # envelope's end. OCCT then reports success while returning an operand
        # with the wrong volume. The same micron nudge used for applying the
        # finished tool makes this subtraction dependable too.
        for candidate in _tool_attempts(carving_bolt, (0.0, 0.0, 1.0)):
            try:
                attempt = built_shape(
                    BRepAlgoAPI_Cut(groove_envelope, candidate), "thread"
                )
            except BaseException:  # noqa: BLE001 - try the next harmless nudge
                continue
            if _carved(
                attempt, envelope_radius, minor_radius, length,
                envelope_radius if size.taper else end_envelope_radius,
                minor_radius if size.taper else end_minor_radius,
            ):
                groove = attempt
                break
        if groove is not None and size.taper:
            from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

            # Intersecting the already carved ribbon is dependable; cutting a
            # swept thread directly from a cone is not. Extend the cone a micron
            # past both end faces to keep the boolean off coincident planes.
            start = envelope_radius + NUDGE + size.taper * NUDGE / 2.0
            end = end_envelope_radius + NUDGE - size.taper * NUDGE / 2.0
            cone = BRepPrimAPI_MakeCone(
                gp_Ax2(gp_Pnt(0.0, 0.0, -NUDGE), gp_Dir(0.0, 0.0, 1.0)),
                start, end, length + 2.0 * NUDGE,
            ).Shape()
            try:
                tapered = built_shape(
                    BRepAlgoAPI_Common(groove, cone), "tapered thread"
                )
                groove = tapered if _volume(tapered) > 0.0 else None
            except BaseException:  # noqa: BLE001 - handled by failure below
                groove = None
        if groove is None:
            return ThreadResult(
                body,
                modelled=False,
                message=(
                    f"The {size.designation} thread could not be cut cleanly at "
                    "this size. The body is unchanged; try a coarser size or a "
                    "shorter threaded length."
                ),
            )

        if internal:
            # Everything inside the bore the bolt does not occupy is nut metal,
            # less a countersink at each mouth so the bolt has somewhere to
            # start.
            filler = _cut_away(
                groove, _mouth_cones(envelope_radius, minor_radius, length)
            )
            filler = transformed(filler, placement)
            shape = _apply_tool(body, filler, cut=False, axis=direction)
        else:
            # Everything inside the shaft the bolt does not occupy is waste.
            waste = transformed(groove, placement)
            shape = _apply_tool(body, waste, cut=True, axis=direction)
            if shape is not None:
                # Then chamfer the ends, so the bolt starts by hand.
                shape = _cut_away(
                    shape,
                    _end_rings(envelope_radius, minor_radius, length),
                    placement,
                )
        if shape is None:
            return ThreadResult(
                body,
                modelled=False,
                message=(
                    f"The {size.designation} thread could not be applied to this "
                    "body. It is unchanged; try a coarser size or a shorter "
                    "threaded length."
                ),
            )
    return ThreadResult(shape, modelled=True, message=" ".join(notes))


def _carved(
    groove, envelope_radius: float, minor_radius: float, length: float,
    end_envelope_radius: float | None = None,
    end_minor_radius: float | None = None,
) -> bool:
    """Did subtracting the bolt from the envelope actually remove the bolt?

    OCCT can report a boolean done and hand back an operand untouched, and on
    this particular subtraction it does. Unchecked, that is the worst possible
    outcome: a nut whose bore is a solid plug, or a shaft with no thread on it,
    both looking perfectly fine in the viewport. The bolt's core alone accounts
    for most of the envelope, so a groove anywhere near the envelope's own size
    means the cut did not happen.
    """
    from .occ import volume

    try:
        end_envelope_radius = (
            envelope_radius if end_envelope_radius is None else end_envelope_radius
        )
        end_minor_radius = (
            minor_radius if end_minor_radius is None else end_minor_radius
        )
        envelope = math.pi * length * (
            envelope_radius ** 2
            + envelope_radius * end_envelope_radius
            + end_envelope_radius ** 2
        ) / 3.0
        core = math.pi * length * (
            minor_radius ** 2
            + minor_radius * end_minor_radius
            + end_minor_radius ** 2
        ) / 3.0
    except Exception:  # noqa: BLE001
        return True
    if envelope <= 0:
        return True
    # Generous: the true groove is the envelope less the whole bolt, and the
    # bolt is at least its core. Ten per cent of slack on top of that catches a
    # cut that did not run without ever second-guessing one that did.
    return volume(groove) < (envelope - core) * 1.1 + envelope * 0.02


def printable_note(form: ThreadForm, size: ThreadSize) -> str:
    """What is wrong with printing this thread, in one sentence, or nothing.

    Two things stop a printed thread working, and both are arithmetic rather
    than opinion: a tooth the nozzle cannot resolve, and a flank the printer
    cannot bridge. Saying so at the point of choosing beats discovering it after
    a four-hour print.
    """
    from .thread_specs import printer_profile

    try:
        profile = printer_profile()
    except Exception:  # noqa: BLE001 - a missing profile must not block threading
        return ""
    minimum = float(profile.get("min_feature_size", 0.0))
    limit = float(profile.get("max_overhang_angle", 90.0))

    if minimum and form.depth < minimum:
        return (
            f"{size.designation}'s tooth is only {form.depth:.2f} mm deep, under "
            f"the {minimum:.2f} mm this printer resolves. Use a coarser size."
        )
    if form.overhang > limit + 0.5:
        return (
            f"A {form.code} thread at this pitch overhangs by "
            f"{form.overhang:.0f}°, past the {limit:.0f}° this printer bridges. "
            "It will need supports; the printed form does not."
        )
    return ""


def matching_size(size: ThreadSize) -> ThreadSize:
    """The complementary thread for a pair -- same standard, same designation.

    Male and female threads of a pair share a designation; what differs is which
    side carries the clearance, which :func:`apply_thread` handles.
    """
    return size
