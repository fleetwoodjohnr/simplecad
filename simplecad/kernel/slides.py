"""Sliding joints: dovetail, T-rail and tongue-and-groove rails.

A part carrying rails slides into channels on its mate and stops at the far
end -- a removable cover, a drawer, or an accessory that drops into a bracket
screwed to a wall. These are ``clip_joint`` styles, so they are one feature
with the parts as its two outputs, edited from History like any joint.

Canonical frame, as for every joint (see ``clips.py``):
    Z   out of the part carrying the rails (the male part), into its mate
    X   the insertion direction: the male part moves +X to go in, -X to come out
    Y   across the rail

The female side is either **cut** -- a channel sunk into a part thick enough
to take it, the faces touching -- or **raised**: lips standing on the mate's
face that wrap the rail, for a part too thin to cut into (a wall bracket).
The parts then sit the rail's height apart, which is what "bring together"
seats them at.

Every profile is a 2-D polygon in (Y, Z), offset with shapely's mitred buffer
for the clearance, so a dovetail's flanks get exactly half the clearance each
and a T-rail's flange is cleared under its overhang too.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..core.errors import CadError, guard
from .occ import boolean_builder, built_shape, is_valid, transformed, unify

#: Every number a rail needs, with its default, in millimetres / degrees.
RAIL_DEFAULTS = {
    "rail_width": 8.0,       # across the rail at its widest
    "rail_height": 3.0,      # how far the rail stands off the male face
    "rail_angle": 15.0,      # dovetail flank, from upright
    "rails": 2,              # how many, side by side
    "lip": 2.0,              # raised channel: wall beside and over the rail
    "detent": None,          # bump near the stop that clicks home; 0 for none,
                             # unset for 0.3 mm proud of the clearance
    "rail_length": 0.0,      # 0: the whole shared length
}
#: What each style is called in messages, singular.
RAIL_NAMES = {"dovetail": "dovetail rail", "t_rail": "T-rail",
              "tongue_groove": "tongue-and-groove rail"}
#: How far the rail's root sinks into its own part, so the fuse is solid.
ROOT = 0.3


@dataclass(frozen=True)
class RailSpec:
    style: str
    width: float
    height: float
    angle: float
    clearance: float          # total; half on each side of every surface
    depth_clearance: float
    lead_in: float
    min_wall: float
    lip: float
    stop: bool
    detent: float

    @property
    def gap(self) -> float:
        """Per-side clearance."""
        return self.clearance / 2.0

    @property
    def stem(self) -> float:
        """A T-rail's stem width: what passes between the lips."""
        return self.width * 0.5

    @property
    def neck(self) -> float:
        """A dovetail's width at the face it stands on."""
        return self.width - 2.0 * self.height * math.tan(math.radians(self.angle))


def rail_profile(spec: RailSpec, root: float = ROOT) -> list[tuple[float, float]]:
    """The male cross-section, ``(y, z)``, counter-clockwise, from below the face."""
    w, h = spec.width / 2.0, spec.height
    if spec.style == "tongue_groove":
        return [(-w, -root), (w, -root), (w, h), (-w, h)]
    if spec.style == "dovetail":
        n = spec.neck / 2.0
        return [(-n, -root), (n, -root), (n, 0.0), (w, h), (-w, h), (-n, 0.0)]
    # T-rail: a stem up to half height, then a flange as wide as the rail.
    s, middle = spec.stem / 2.0, h * 0.5
    return [(-s, -root), (s, -root), (s, middle), (w, middle), (w, h), (-w, h),
            (-w, middle), (-s, middle)]


def channel_profile(spec: RailSpec):
    """The cavity the rail runs in: the profile grown by half the clearance."""
    from shapely.geometry import Polygon

    return Polygon(rail_profile(spec, root=spec.gap + 0.5)).buffer(spec.gap, join_style=2)


def resolve_rail_spec(feature, ctx) -> RailSpec:
    from .clips import DEFAULTS, _fit_clearance

    style = str(feature.inputs.get("style"))
    clearance = _fit_clearance(feature.inputs.get("clearance", "sliding"), ctx, feature)
    number = {key: ctx.value(feature, key, RAIL_DEFAULTS[key])
              for key in ("rail_width", "rail_height", "rail_angle", "lip")}
    # Unset, the detent is sized once the channel is known (see execute_slide).
    number["detent"] = ctx.value(feature, "detent", -1.0) if "detent" in feature.inputs else -1.0
    extras = {key: ctx.value(feature, key, DEFAULTS[key])
              for key in ("depth_clearance", "lead_in", "min_wall")}
    spec = RailSpec(
        style=style, width=number["rail_width"], height=number["rail_height"],
        angle=number["rail_angle"], clearance=clearance, lip=number["lip"],
        stop=bool(feature.inputs.get("stop", True)), detent=number["detent"], **extras,
    )
    if spec.clearance <= 0.0:
        raise CadError(
            f"A sliding joint needs clearance to slide; {spec.clearance:.2f} mm will bind.",
            suggestion="Use the Sliding or Loose fit, or a clearance above zero.",
        )
    if spec.width <= 0.0 or spec.height <= 0.0:
        raise CadError("The rail needs a width and a height greater than zero.")
    if spec.style == "dovetail":
        if not 2.0 <= spec.angle <= 35.0:
            raise CadError(f"A dovetail flank of {spec.angle:g}° will not hold.",
                           suggestion="Use between 5° and 30°; 15° is usual.")
        if spec.neck < max(1.0, 2.0 * spec.min_wall):
            raise CadError(
                f"At {spec.angle:g}° a {spec.height:g} mm tall dovetail leaves a "
                f"{spec.neck:.2f} mm neck.",
                suggestion="Make the rail wider or lower, or the angle smaller.",
            )
    if spec.style == "t_rail" and spec.stem < 1.0:
        raise CadError("This T-rail's stem would be under 1 mm wide.",
                       suggestion="Make the rail wider.")
    if spec.lip < spec.min_wall:
        raise CadError(f"The channel walls ({spec.lip:g} mm) are thinner than the minimum "
                       f"wall ({spec.min_wall:g} mm).")
    if (spec.detent < 0.0 and "detent" in feature.inputs) or spec.lead_in < 0.0:
        raise CadError("Detent and lead-in cannot be negative.")
    return spec


# -- solids in the canonical frame -----------------------------------------
def prism(points, x0: float, x1: float):
    """A ``(y, z)`` polygon extruded along X from *x0* to *x1*."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Pnt, gp_Vec

    polygon = BRepBuilderAPI_MakePolygon()
    for y, z in points:
        polygon.Add(gp_Pnt(x0, float(y), float(z)))
    polygon.Close()
    face = BRepBuilderAPI_MakeFace(polygon.Wire(), True).Face()
    return BRepPrimAPI_MakePrism(face, gp_Vec(x1 - x0, 0.0, 0.0)).Shape()


def _coords(polygon):
    return list(polygon.exterior.coords)[:-1]


def _box(x0, y0, z0, x1, y1, z1):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    return BRepPrimAPI_MakeBox(gp_Pnt(x0, y0, z0), gp_Pnt(x1, y1, z1)).Shape()


def _cut(a, b, what):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

    return built_shape(boolean_builder(BRepAlgoAPI_Cut, a, b), what)


def _fuse(a, b, what):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

    return built_shape(boolean_builder(BRepAlgoAPI_Fuse, a, b), what)


def male_rail(spec: RailSpec, x0: float, x1: float):
    return prism(rail_profile(spec), x0, x1)


def groove(spec: RailSpec, x_open: float, x_end: float):
    """The channel cut into a thick mate: open at *x_open*, closed at *x_end*."""
    return prism(deepened(channel_profile(spec), spec.depth_clearance), x_open, x_end)


def deepened(polygon, extra: float):
    """The polygon's points with only its deepest edge pushed *extra* further.

    Only the top: a T-rail's channel also has edges under the flange, and
    moving those would cut into the rail it is meant to clear.
    """
    points = _coords(polygon)
    top = max(z for _y, z in points)
    return [(y, z + extra if z >= top - 1e-9 else z) for y, z in points]


def lips(spec: RailSpec, x_open: float, x_end: float, *, cap: bool):
    """The raised channel standing on a thin mate's face, whose surface is at
    ``z = height + gap``. A cap closes the *x_end* end when there is a stop."""
    from shapely.geometry import box as shapely_box

    face_z = spec.height + spec.gap
    channel = channel_profile(spec)
    half = max(abs(channel.bounds[0]), abs(channel.bounds[2]))
    outer = shapely_box(-(half + spec.lip), spec.gap, half + spec.lip, face_z + 0.3)
    walls = outer.difference(channel)
    pieces = [g for g in getattr(walls, "geoms", [walls]) if g.area > 1e-6]
    if not pieces:
        raise CadError("The raised channel has no walls left once the rail's room is cut out.")
    solid = None
    for piece in pieces:
        part = prism(_coords(piece), x_open, x_end)
        solid = part if solid is None else _fuse(solid, part, "channel walls")
    if cap:
        end = _box(x_end, -(half + spec.lip), spec.gap, x_end + spec.lip,
                   half + spec.lip, face_z + 0.3)
        solid = _fuse(solid, end, "channel stop")
    return solid


def lead_in(spec: RailSpec, x_open: float, *, raised: bool):
    """A flared mouth at the open end, so the rail finds its way in."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
    from OCP.gp import gp_Pnt

    if spec.lead_in <= 0.0:
        return None
    channel = channel_profile(spec)
    wide = channel.buffer(spec.lead_in, join_style=2)
    narrow, wide = _coords(channel), _coords(wide)
    if len(narrow) != len(wide):
        return None

    def ring(points, x):
        polygon = BRepBuilderAPI_MakePolygon()
        for y, z in points:
            polygon.Add(gp_Pnt(x, y, z))
        polygon.Close()
        return polygon.Wire()

    loft = BRepOffsetAPI_ThruSections(True, True)
    loft.AddWire(ring(wide, x_open - 0.02))
    loft.AddWire(ring(narrow, x_open + spec.lead_in))
    loft.Build()
    return loft.Shape() if loft.IsDone() else None


def detent_bump(spec: RailSpec, x: float, *, grow: float = 0.0):
    """A half-round ridge across the rail top at *x* (or the dimple for it)."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    radius = max(spec.detent * 2.0, 0.8)
    span = spec.width * 0.6 + 2.0 * grow
    centre_z = spec.height - radius + spec.detent
    axis = gp_Ax2(gp_Pnt(x, -span / 2.0, centre_z), gp_Dir(0, 1, 0))
    return BRepPrimAPI_MakeCylinder(axis, radius + grow, span).Shape()


# -- placement --------------------------------------------------------------
INSERTIONS = ("+u", "-u", "+v", "-v")


def insertion_axis(seam, insertion: str):
    """The world direction the male part moves to go in."""
    sign = -1.0 if insertion.startswith("-") else 1.0
    axis = seam.x_axis if insertion.endswith("u") else seam.y_axis
    return tuple(sign * axis[i] for i in range(3))


def default_insertion(seam) -> str:
    """Downward on a wall (things drop into brackets); else along the longer side."""
    best = min(INSERTIONS, key=lambda key: insertion_axis(seam, key)[2])
    if insertion_axis(seam, best)[2] < -0.5:
        return best
    low_u, low_v, high_u, high_v = seam.region.bounds
    return "+u" if high_u - low_u >= high_v - low_v else "+v"


@dataclass(frozen=True)
class RailLayout:
    rails: tuple[float, ...]      # Y of each rail's centre line
    x_open: float                 # where the channel opens (the female part's edge)
    x_start: float                # the male rail's first end
    x_end: float                  # the male rail's last end, at the stop
    region_x: tuple[float, float]


def plan_rails(region, spec: RailSpec, *, count: int, length: float, edge_offset: float,
               raised: bool) -> RailLayout:
    """Where the rails run, in the joint's (X, Y) plane.

    *region* is the shared face area expressed in that plane. Rails are spread
    evenly across it, each with its channel walls clear of the edge, and run
    the whole shared length unless *length* says otherwise.
    """
    from shapely.geometry import box as shapely_box

    count = max(1, min(int(count), 8))
    channel = channel_profile(spec)
    half = max(abs(channel.bounds[0]), abs(channel.bounds[2]))
    keep = half + (spec.lip if raised else spec.min_wall)
    low_x, low_y, high_x, high_y = region.bounds
    span = (high_y - low_y) - 2.0 * (edge_offset + keep)
    if span < -1e-9 or (count > 1 and span < (count - 1) * 2.0 * keep):
        need = 2.0 * (edge_offset + keep) + (count - 1) * 2.0 * keep
        raise CadError(
            f"{count} rail{'s' if count != 1 else ''} need {need:.1f} mm across the shared "
            f"face; it is {high_y - low_y:.1f} mm.",
            suggestion="Use fewer or narrower rails, or a smaller edge offset.",
        )
    first = low_y + edge_offset + keep
    ys = (tuple(first + span * k / (count - 1) for k in range(count)) if count > 1
          else ((low_y + high_y) / 2.0,))
    x_lo, x_hi = low_x, high_x
    for y in ys:
        strip = region.intersection(shapely_box(low_x - 1.0, y - keep, high_x + 1.0, y + keep))
        if strip.is_empty:
            raise CadError("A rail would run off the shared face.",
                           suggestion="Use fewer rails or a smaller edge offset.")
        x_lo, x_hi = max(x_lo, strip.bounds[0]), min(x_hi, strip.bounds[2])
    for y in ys:
        if not region.buffer(1e-6).covers(shapely_box(x_lo + 0.01, y - keep, x_hi - 0.01, y + keep)):
            raise CadError(
                "A hole or an edge interrupts the path of a rail.",
                suggestion="Move the rails (edge offset or count) to run clear of it.",
            )
    end_room = spec.gap + (spec.lip if raised else spec.min_wall) if spec.stop else 0.0
    x_end = x_hi - edge_offset - end_room if spec.stop else x_hi
    x_start = x_lo if length <= 0.0 else x_end - length
    if x_start < x_lo - 1e-9:
        raise CadError(f"The rails can be at most {x_end - x_lo:.1f} mm long here.",
                       suggestion="Shorten them, or leave the length on Full.")
    if x_end - x_start < max(2.0 * spec.width, 6.0):
        raise CadError(
            f"Only {x_end - x_start:.1f} mm of rail fits; the part would rock on it.",
            suggestion="Turn the joint to slide along the longer side, or use a smaller "
                       "edge offset.",
        )
    return RailLayout(ys, x_lo, x_start, x_end, (x_lo, x_hi))


# -- the feature ------------------------------------------------------------
def _frame_transform(origin, x_dir, z_dir):
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf

    result = gp_Trsf()
    result.SetDisplacement(
        gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1), gp_Dir(1, 0, 0)),
        gp_Ax3(gp_Pnt(*origin), gp_Dir(*z_dir), gp_Dir(*x_dir)),
    )
    return result


def _translated(shape, vector):
    from .occ import make_transform

    return transformed(shape, make_transform(translate=tuple(vector)))


def execute_slide(feature, ctx) -> dict[str, object]:
    """Rails on one part, channels on the other, one feature writing both."""
    from shapely import affinity

    from .align import interference_volume, solve
    from .clips import DEFAULTS, _contained, _dot, mate
    from .detect import analyse_plane

    first_name = str(feature.inputs.get("body_a", ""))
    second_name = str(feature.inputs.get("body_b", ""))
    if not first_name or not second_name or first_name == second_name:
        raise CadError("A sliding joint needs faces on two different parts.")
    body_a, body_b = ctx.shape(feature, "body_a"), ctx.shape(feature, "body_b")
    face_a, face_b = ctx.resolve(feature, "face_a"), ctx.resolve(feature, "face_b")
    spec = resolve_rail_spec(feature, ctx)
    seated = bool(feature.inputs.get("seat"))
    if seated:
        placement = solve(face_a, face_b, operation="stack",
                          x=ctx.value(feature, "seat_x", 0.0), y=ctx.value(feature, "seat_y", 0.0))
        body_a = transformed(body_a, placement.transform)
        face_a = transformed(face_a, placement.transform)
    seam = mate(face_a, face_b, gap_tolerance=math.inf)
    male_on_a = str(feature.inputs.get("male", "a")) != "b"
    z_dir = seam.normal_a if male_on_a else tuple(-c for c in seam.normal_a)
    insertion = str(feature.inputs.get("insertion") or default_insertion(seam))
    if insertion not in INSERTIONS:
        raise CadError(f"Unknown slide direction: {insertion}")
    x_dir = insertion_axis(seam, insertion)
    y_dir = tuple(z_dir[(i + 1) % 3] * x_dir[(i + 2) % 3] - z_dir[(i + 2) % 3] * x_dir[(i + 1) % 3]
                  for i in range(3))
    # The shared face, in the joint's own (X, Y) plane.
    region = affinity.affine_transform(seam.region, [
        _dot(seam.x_axis, x_dir), _dot(seam.y_axis, x_dir),
        _dot(seam.x_axis, y_dir), _dot(seam.y_axis, y_dir), 0.0, 0.0,
    ])
    edge_offset = ctx.value(feature, "edge_offset", DEFAULTS["edge_offset"])
    count = int(feature.inputs.get("rails", RAIL_DEFAULTS["rails"]) or RAIL_DEFAULTS["rails"])
    length = ctx.value(feature, "rail_length", 0.0)
    mode = str(feature.inputs.get("female_mode", "auto"))
    if mode not in ("auto", "cut", "raised"):
        raise CadError(f"Unknown channel mode: {mode}")

    def frame(origin):
        placed = _frame_transform(origin, x_dir, z_dir)
        return lambda shape: transformed(shape, placed)

    male, female = (body_a, body_b) if male_on_a else (body_b, body_a)
    raised = mode == "raised"
    if not raised:
        # Cut: the faces touch, so the joint's origin is on the shared plane.
        layout = plan_rails(region, spec, count=count, length=length,
                            edge_offset=edge_offset, raised=False)
        world = frame(seam.origin)
        grown = channel_profile(spec).buffer(spec.min_wall, join_style=2)
        wall = [(y, max(z, 0.05)) for y, z in deepened(grown, spec.depth_clearance)]
        thick = all(
            _contained(female, world(_translated(prism(wall, layout.x_open, layout.x_end + spec.gap),
                                                 (0.0, y, 0.0))))
            for y in layout.rails
        )
        if not thick:
            if mode == "cut":
                raise CadError(
                    f"The {'second' if male_on_a else 'first'} part is too thin for a "
                    f"{spec.height + spec.gap + spec.depth_clearance:.1f} mm deep channel "
                    f"with {spec.min_wall:g} mm of wall behind it.",
                    suggestion="Choose a raised channel, or a lower rail.",
                )
            raised = True
    gap = spec.height + spec.gap if raised else 0.0
    if raised:
        layout = plan_rails(region, spec, count=count, length=length,
                            edge_offset=edge_offset, raised=True)

    # A raised channel holds the parts the rail's height apart; a cut one holds
    # them together. "Bring the parts together" puts them there; otherwise they
    # must already be there.
    if seated:
        body_a = _translated(body_a, tuple(-gap * seam.normal_a[i] for i in range(3)))
        male, female = (body_a, body_b) if male_on_a else (body_b, body_a)
    else:
        apart = _dot(tuple(analyse_plane(face_b).center[i] - analyse_plane(face_a).center[i]
                           for i in range(3)), seam.normal_a)
        if abs(apart - gap) > 0.05:
            raise CadError(
                f"The parts are {apart:.2f} mm apart; this joint holds them {gap:.2f} mm apart.",
                suggestion="Tick Bring the parts together.",
            )
    # Everything is built from the male face. Face B carries the seam's origin;
    # when the rails are on A, A's face is the gap behind it.
    male_origin = tuple(seam.origin[i] - (gap * z_dir[i] if male_on_a else 0.0)
                        for i in range(3))
    world = frame(male_origin)

    def at(shape, y):
        return world(_translated(shape, (0.0, y, 0.0)))

    with guard("sliding joint"):
        for y in layout.rails:
            if raised:
                female = _fuse(female, at(lips(spec, layout.x_open, layout.x_end + spec.gap,
                                               cap=spec.stop), y), "raised channel")
            else:
                end = layout.x_end + spec.gap if spec.stop else layout.region_x[1] + 1.0
                female = _cut(female, at(groove(spec, layout.x_open - 1.0, end), y), "channel")
            mouth = lead_in(spec, layout.x_open, raised=raised)
            if mouth is not None:
                female = _cut(female, at(mouth, y), "channel mouth")
            male = _fuse(male, at(male_rail(spec, layout.x_start, layout.x_end), y), "rail")
        male, female = unify(male), unify(female)

    # -- checks, before anything is committed ------------------------------
    overlap = interference_volume(male, female)
    if overlap > 1e-3:
        raise CadError(
            f"Assembled, the parts overlap by {overlap:.2f} mm³.",
            suggestion="Something else on the parts is in the way of the rails; move them "
                       "with the edge offset or change the slide direction.",
        )
    travel = layout.x_end - layout.x_open + 1.0
    out = tuple(-c for c in x_dir)
    for step in (0.2, 0.4, 0.6, 0.8, 1.0):
        hit = interference_volume(
            _translated(male, tuple(out[i] * travel * step for i in range(3))), female)
        if hit > 1e-3:
            raise CadError(
                f"The parts cannot be slid apart: they collide {travel * step:.1f} mm out "
                f"({hit:.2f} mm³).",
                suggestion="Something blocks the open end of the channel. Reverse the slide "
                           "direction, or move what is in the way.",
            )
    if spec.stop:
        pushed = _translated(male, tuple(x_dir[i] * (spec.gap + 0.5) for i in range(3)))
        if interference_volume(pushed, female) <= 1e-3:
            ctx.warn("The stop does not catch the rail; check the slide direction.")

    ceiling = spec.gap + (0.0 if raised else spec.depth_clearance)
    if spec.detent < 0.0:
        from dataclasses import replace

        spec = replace(spec, detent=round(ceiling + 0.3, 3))
    if spec.detent > 0.0:
        where = layout.x_end - max(3.0, spec.width * 0.5)
        with guard("detent"):
            for y in layout.rails:
                male = _fuse(male, at(detent_bump(spec, where), y), "detent")
                female = _cut(female, at(detent_bump(spec, where, grow=spec.gap * 0.5), y),
                              "detent")
        if spec.detent <= ceiling:
            ctx.warn(f"A {spec.detent:g} mm detent is inside the clearance and will not click.")
    if not is_valid(male) or not is_valid(female):
        raise CadError("The joint did not leave valid printable parts.",
                       suggestion="Try a smaller rail or move it away from other features.")

    engagement = layout.x_end - layout.x_start
    if engagement < 3.0 * spec.width:
        ctx.warn(f"{engagement:.0f} mm of rail is short for its {spec.width:g} mm width; "
                 "the part may rock.")
    result_a, result_b = (male, female) if male_on_a else (female, male)
    feature.annotations = {
        "style": spec.style,
        "slide": True,
        "raised": raised,
        "insertion": insertion,
        # How part A moves to come apart, and how far.
        "exit_a": list(out if male_on_a else x_dir),
        "travel": travel,
        "engagement": engagement,
        "gap": gap,
    }
    count = len(layout.rails)
    feature.outputs = [first_name, second_name]
    feature.message = (
        f"{count} {RAIL_NAMES[spec.style]}{'s' if count != 1 else ''}, {engagement:.0f} mm "
        f"long, in a {'raised' if raised else 'cut'} channel with {spec.clearance:.2f} mm "
        "clearance."
    )
    return feature.keep({first_name: result_a, second_name: result_b})
