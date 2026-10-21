"""Parametric placement of several independent bodies.

Two families share one feature. A **row** (the original Arrange, and what any
feature without a ``pattern`` input still means) lines bodies up along an axis
with equal or fixed clear gaps. A **pattern** -- grid, linear, radial,
staggered, packed or custom -- lays them out *on a target face*: the face
becomes a 2-D region (margins, holes, other objects taken out), each body a
footprint seated on its contact face, and ``arrange_layout`` decides where the
footprints go. Bodies are only ever moved and turned; they stay separate,
keep their names and are never scaled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from ..core.document import BuildContext, Feature, register
from ..core.errors import CadError
from ..core.units import Dimension
from .align import (
    _cross, _dot, _scale, _sub, _unit, planar_frame, reference_frame,
    rotation_between, support_face, translation,
)
from .arrange_layout import PATTERNS, Settings, alternatives, check, placed, plan
from .detect import analyse_cylinder, analyse_plane
from .occ import bounding_box, compound, transformed


def _add(*vectors):
    return tuple(sum(vector[i] for vector in vectors) for i in range(3))


def projected_bounds(shape, axis) -> tuple[float, float]:
    """The exact axis-aligned bounds of *shape* along an arbitrary direction."""
    rotate = rotation_between(axis, (1.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    low, high = bounding_box(transformed(shape, rotate))
    return (low[0], high[0])


def projected_center(shape, axis) -> float:
    low, high = projected_bounds(shape, axis)
    return (low + high) / 2.0


@dataclass(frozen=True)
class Arrangement:
    shapes: dict[str, object]
    gap: float
    order: tuple[str, ...]
    warnings: tuple[str, ...] = ()


def _oriented_and_seated(name, shape, target_face, orient: bool, *, stable_frame=False):
    frame = reference_frame(target_face) if stable_frame else planar_frame(target_face)
    warnings = []
    placed = shape
    if orient:
        support = support_face(shape, target_face)
        info = analyse_plane(support) if support is not None else None
        if info is None:
            warnings.append(
                f"{name} has no flat support face; its rotation was preserved."
            )
        else:
            rotate = rotation_between(
                info.normal, _scale(frame.normal, -1.0), info.center
            )
            placed = transformed(placed, rotate)
            support = transformed(support, rotate)
            support_info = analyse_plane(support)
            distance = _dot(_sub(frame.center, support_info.center), frame.normal)
            placed = transformed(placed, translation(_scale(frame.normal, distance)))
            return placed, warnings

    # Preserved orientations (and non-planar fallbacks) are seated by their
    # geometric support extent rather than by a guessed face.
    low, _high = projected_bounds(placed, frame.normal)
    plane = _dot(frame.center, frame.normal)
    placed = transformed(placed, translation(_scale(frame.normal, plane - low)))
    return placed, warnings


def arrange_shapes(
    items,
    *,
    mode: str = "equal_gaps",
    axis: str = "x",
    gap: float = 10.0,
    target_face=None,
    orient: bool = True,
    normal_offset: float = 0.0,
    group_offset=(0.0, 0.0, 0.0),
    stable_frame: bool = False,
) -> Arrangement:
    """Return separately named bodies distributed and aligned in one row."""
    items = [(str(name), shape) for name, shape in items if shape is not None]
    minimum = 3 if mode == "equal_gaps" else 2
    if len(items) < minimum:
        raise CadError(
            "Equal spacing needs at least three objects."
            if mode == "equal_gaps" else "A fixed gap needs at least two objects."
        )
    if mode not in ("equal_gaps", "fixed_gap"):
        raise CadError("That arrangement mode is not available.")
    if mode == "fixed_gap" and gap < 0.0:
        raise CadError("The clear gap cannot be negative.")

    warnings = []
    if target_face is not None:
        frame = reference_frame(target_face) if stable_frame else planar_frame(target_face)
        row_axis = frame.x_axis if axis == "x" else frame.y_axis
        cross_axes = (frame.y_axis,) if axis == "x" else (frame.x_axis,)
        prepared = []
        for name, shape in items:
            placed, notes = _oriented_and_seated(
                name, shape, target_face, orient, stable_frame=stable_frame
            )
            if normal_offset:
                placed = transformed(
                    placed, translation(_scale(frame.normal, normal_offset))
                )
            prepared.append((name, placed))
            warnings.extend(notes)
    else:
        axes = {
            "x": ((1.0, 0.0, 0.0), ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0))),
            "y": ((0.0, 1.0, 0.0), ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0))),
            "z": ((0.0, 0.0, 1.0), ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))),
        }
        row_axis, cross_axes = axes.get(axis, axes["x"])
        prepared = items

    ordered = sorted(prepared, key=lambda item: projected_center(item[1], row_axis))
    bounds = {name: projected_bounds(shape, row_axis) for name, shape in ordered}
    widths = {name: bounds[name][1] - bounds[name][0] for name, _shape in ordered}

    first_name = ordered[0][0]
    cursor = bounds[first_name][0]
    if mode == "equal_gaps":
        available = bounds[ordered[-1][0]][1] - cursor
        clear = (available - sum(widths.values())) / (len(ordered) - 1)
        if clear < -1e-6:
            raise CadError(
                "The outer objects are too close for clear equal gaps.",
                suggestion="Move them farther apart or use Fixed gap.",
            )
        gap = max(0.0, clear)

    desired_centers = {}
    for index, (name, _shape) in enumerate(ordered):
        desired_centers[name] = cursor + widths[name] / 2.0
        cursor += widths[name]
        if index + 1 < len(ordered):
            cursor += gap

    # A shared average centreline keeps the row in place while removing the
    # accidental stagger in every perpendicular direction.
    cross_targets = {
        cross: sum(projected_center(shape, cross) for _name, shape in ordered)
        / len(ordered)
        for cross in cross_axes
    }
    outputs = {}
    for name, shape in ordered:
        shift = _scale(
            row_axis, desired_centers[name] - projected_center(shape, row_axis)
        )
        for cross in cross_axes:
            shift = _add(
                shift,
                _scale(cross, cross_targets[cross] - projected_center(shape, cross)),
            )
        shift = _add(shift, tuple(float(v) for v in group_offset))
        outputs[name] = transformed(shape, translation(shift))
    return Arrangement(outputs, float(gap), tuple(name for name, _ in ordered), tuple(warnings))


# ----------------------------------------------------------------------
# Patterns on a face
# ----------------------------------------------------------------------
class _FlatTarget:
    """A planar face in the U/V frame users already see in Place on Face."""

    period = None
    internal = False

    def __init__(self, face):
        from OCP.TopoDS import TopoDS
        from shapely import affinity

        from .operations import _face_profile

        self.face = face = TopoDS.Face_s(face)
        self.frame = reference_frame(face)
        profile, (origin, px, py) = _face_profile(face, 0.035)
        u, v = self.frame.x_axis, self.frame.y_axis
        offset = _sub(origin, self.frame.center)
        self.domain = affinity.affine_transform(profile, [
            _dot(px, u), _dot(py, u), _dot(px, v), _dot(py, v), _dot(offset, u), _dot(offset, v),
        ])

    def point(self, u, v, lift=0.0):
        return self.frame.point(u, v, lift)

    def axes(self, _u=0.0, _v=0.0):
        return self.frame.x_axis, self.frame.y_axis, self.frame.normal

    def to_uv(self, point):
        delta = _sub(point, self.frame.center)
        return _dot(delta, self.frame.x_axis), _dot(delta, self.frame.y_axis)


class _RoundTarget:
    """A cylindrical face unrolled: U runs round it, V along its axis."""

    def __init__(self, face):
        from OCP.TopoDS import TopoDS
        from shapely import affinity

        from .face_texture import CylindricalMapping

        self.mapping = m = CylindricalMapping(TopoDS.Face_s(face))
        self.period = m.period
        self.radius = m.radius
        self.internal = bool(m.info.internal)
        self._x = (m.x.X(), m.x.Y(), m.x.Z())
        self._y = (m.y.X(), m.y.Y(), m.y.Z())
        self._axis = (m.axis.X(), m.axis.Y(), m.axis.Z())
        self._origin = (m.origin.X(), m.origin.Y(), m.origin.Z())
        tangent, _along, normal = self._raw_axes(0.0)
        # Keep U x V = outward normal, so a footprint is never seen mirrored.
        self.sign = 1.0 if _dot(_cross(tangent, self._axis), normal) > 0 else -1.0
        self.domain = m.domain if self.sign > 0 else affinity.scale(m.domain, 1.0, -1.0, origin=(0, 0))

    def _raw_axes(self, u):
        theta = self.mapping.u_min + u / self.radius
        c, s = math.cos(theta), math.sin(theta)
        radial = tuple(self._x[i] * c + self._y[i] * s for i in range(3))
        tangent = tuple(-self._x[i] * s + self._y[i] * c for i in range(3))
        return tangent, self._axis, _scale(radial, self.mapping.outward_sign)

    def point(self, u, v, lift=0.0):
        return self.mapping.point(u, self.sign * v, lift)

    def axes(self, u=0.0, _v=0.0):
        tangent, along, normal = self._raw_axes(u)
        return tangent, _scale(along, self.sign), normal

    def to_uv(self, point):
        delta = _sub(point, self._origin)
        theta = math.atan2(_dot(delta, self._y), _dot(delta, self._x))
        u = ((theta - self.mapping.u_min) % (2.0 * math.pi)) * self.radius
        return u, self.sign * (_dot(delta, self._axis) - self.mapping.v_min)


def _target(face):
    if analyse_plane(face) is not None:
        return _FlatTarget(face)
    if analyse_cylinder(face) is not None:
        return _RoundTarget(face)
    raise CadError(
        "Arrange needs a flat or cylindrical target face.",
        suggestion="Pick a flat face, or the round side of a cylinder or tube.",
    )


def face_axes(face):
    """U, V and the normal a pattern on *face* is laid out along, at its middle."""
    target = _target(face)
    middle = target.domain.centroid
    return target.axes(middle.x, middle.y)


def _polygons(geometry):
    return [g for g in getattr(geometry, "geoms", [geometry])
            if g.geom_type == "Polygon" and not g.is_empty]


def _outline_2d(shape, origin, x_axis, y_axis, deflection):
    """*shape* seen along ``x_axis × y_axis``, as one polygon in (x, y)."""
    import numpy
    import shapely
    from shapely.geometry import Polygon

    from .tessellate import triangulate

    # Mesh a copy: the document's shape may be shared with what is on screen.
    mesh = triangulate(transformed(shape, translation((0.0, 0.0, 0.0))), deflection)
    if mesh.is_empty:
        raise CadError("One of the objects has no surface to arrange by.")
    points = numpy.asarray(mesh.vertices, dtype=float) - numpy.asarray(origin, dtype=float)
    flat = numpy.column_stack([points @ numpy.asarray(x_axis), points @ numpy.asarray(y_axis)])
    triangles = flat[numpy.asarray(mesh.triangles)]
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    area = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    # For a closed surface the triangles facing the viewer already cover the
    # whole silhouette; the rest only slow the union down.
    facing = triangles[area > 1e-10]
    if not len(facing):
        facing = triangles[numpy.abs(area) > 1e-12]
    union = shapely.union_all(shapely.polygons(facing)).buffer(0)
    union = union.simplify(deflection / 4.0)
    parts = _polygons(union)
    if not parts:
        raise CadError("One of the objects has no outline to arrange by.")
    if len(parts) == 1:
        return parts[0]
    # Separate shells (an imported mesh, say): their hull is a safe outline.
    return Polygon(union.convex_hull.exterior)


@dataclass(frozen=True)
class _Seat:
    """A body moved to stand on z = 0 with its outline centred on the origin."""

    transform: object
    footprint: object
    height: float
    warning: str = ""


#: Seats are cached between previews; meshing is the expensive part.
_SEATS: dict = {}


def _seat(name, shape, target, contact, orient: bool) -> _Seat:
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf

    # Which face a body stands on, and how it is turned when it keeps its
    # orientation, both depend on the target.
    where = tuple(round(value, 6) for value in target.point(0.0, 0.0) + target.axes()[2])
    key = (hash(shape), hash(contact) if contact is not None else None, orient, where)
    hit = _SEATS.get(key)
    if hit is not None and hit[0].IsSame(shape):
        return hit[1]

    warning = ""
    support = contact
    if support is None and orient:
        if isinstance(target, _FlatTarget):
            support = support_face(shape, target.face)
        if support is None:
            from ..core.naming import sub_shapes

            flats = [(-(info.area), index, face) for index, face in enumerate(sub_shapes(shape, "face"))
                     if (info := analyse_plane(face)) is not None]
            support = min(flats)[-1] if flats else None
    info = analyse_plane(support) if orient and support is not None else None
    if info is not None:
        seat = rotation_between(info.normal, (0.0, 0.0, -1.0), info.center)
        plane = info.center[2]
    else:
        if orient:
            warning = f"{name} has no flat face to stand on; its turn was kept."
        x_axis, _y_axis, n = target.axes()
        seat = gp_Trsf()
        seat.SetTransformation(gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(*n), gp_Dir(*x_axis)))
        plane = None
    seated = transformed(shape, seat)
    low, high = bounding_box(seated)
    if plane is None:
        plane = low[2]
    size = math.dist(low, high)
    deflection = min(max(size / 200.0, 0.02), 0.5)
    outline = _outline_2d(seated, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), deflection)
    bx0, by0, bx1, by1 = outline.bounds
    cx, cy = (bx0 + bx1) / 2.0, (by0 + by1) / 2.0
    from shapely import affinity

    result = _Seat(
        translation((-cx, -cy, -plane)).Multiplied(seat),
        affinity.translate(outline, -cx, -cy),
        high[2] - plane,
        warning,
    )
    if len(_SEATS) > 256:
        _SEATS.clear()   # ponytail: whole-cache reset, an LRU if sessions churn it
    _SEATS[key] = (shape, result)
    return result


def _edge_line(edge, target):
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection
    from OCP.TopoDS import TopoDS
    from shapely.geometry import LineString

    curve = BRepAdaptor_Curve(TopoDS.Edge_s(edge))
    sampler = GCPnts_QuasiUniformDeflection(curve, 0.02)
    if sampler.IsDone() and sampler.NbPoints() >= 2:
        points = [sampler.Value(i) for i in range(1, sampler.NbPoints() + 1)]
    else:
        points = [curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter())]
    return LineString([target.to_uv((p.X(), p.Y(), p.Z())) for p in points])


def _region(target, options, edges=(), obstacles=()):
    """The usable part of the face, and the extent a pattern spreads across."""
    from shapely import affinity
    from shapely.geometry import Polygon, box
    from shapely.ops import unary_union

    domain = target.domain
    parts = _polygons(domain)
    outer = unary_union([Polygon(part.exterior) for part in parts])
    holes = [Polygon(ring) for part in parts for ring in part.interiors]
    lo_x, lo_y, hi_x, hi_y = domain.bounds
    if target.period:
        # The seam is a parameter boundary, not an edge anything should keep off.
        outer = unary_union([outer] + [affinity.translate(outer, xoff=k * target.period)
                                       for k in (-1, 1)])
        holes += [affinity.translate(h, xoff=k * target.period) for h in holes for k in (-1, 1)]
    margin = max(float(options.get("margin", 0.0)), 0.0)
    # Eight segments per quarter, scaled by 1/cos(pi/32) so the buffer
    # circumscribes the true offset: a clearance is never less than asked.
    clear = margin / math.cos(math.pi / 32)
    manual = options.get("boundary") == "manual"
    if manual:
        area = outer.intersection(box(
            lo_x + options.get("inset_left", 0.0), lo_y + options.get("inset_bottom", 0.0),
            hi_x - options.get("inset_right", 0.0), hi_y - options.get("inset_top", 0.0),
        ))
        if edges and margin > 0:
            area = area.difference(unary_union([line.buffer(clear, quad_segs=8) for line in edges]))
        keep_off = [h for h in holes] if options.get("avoid_holes", True) else []
    else:
        area = outer.buffer(-margin, join_style="mitre") if margin > 0 else outer
        if target.period:
            area = area.intersection(box(lo_x - target.period, lo_y + margin,
                                         hi_x + target.period, hi_y - margin))
        keep_off = ([h.buffer(clear, quad_segs=8) if margin > 0 else h for h in holes]
                    if options.get("avoid_holes", True) else [])
    if keep_off:
        area = area.difference(unary_union(keep_off))
    if obstacles:
        area = area.difference(unary_union(list(obstacles)))
    # Round a full cylinder the pattern spans exactly one turn; along the axis
    # it spans what the margins left.
    bounds = None
    if target.period and not area.is_empty:
        bounds = (lo_x, area.bounds[1], lo_x + target.period, area.bounds[3])
    return area, bounds


def _obstacles(target, bodies, reach, gap):
    """Outlines of other bodies standing on or over a flat face."""
    from shapely.geometry import box

    if not isinstance(target, _FlatTarget):
        return []   # ponytail: other objects are only kept clear of on flat faces
    frame = target.frame
    lo_x, lo_y, hi_x, hi_y = target.domain.bounds
    found = []
    for name, shape in bodies:
        low, high = bounding_box(shape)
        corners = [(x, y, z) for x in (low[0], high[0]) for y in (low[1], high[1])
                   for z in (low[2], high[2])]
        heights = [_dot(_sub(c, frame.center), frame.normal) for c in corners]
        if max(heights) < 1e-3 or min(heights) > reach:
            continue
        flat = [target.to_uv(c) for c in corners]
        us, vs = [p[0] for p in flat], [p[1] for p in flat]
        if not box(min(us), min(vs), max(us), max(vs)).intersects(
                box(lo_x - gap, lo_y - gap, hi_x + gap, hi_y + gap)):
            continue
        key = ("obstacle", hash(shape), tuple(round(c, 9) for c in frame.normal + frame.center))
        hit = _SEATS.get(key)
        if hit is not None and hit[0].IsSame(shape):
            outline = hit[1]
        else:
            deflection = min(max(math.dist(low, high) / 200.0, 0.02), 0.5)
            outline = _outline_2d(shape, frame.center, frame.x_axis, frame.y_axis, deflection)
            _SEATS[key] = (shape, outline)
        found.append(outline.buffer(gap, quad_segs=8) if gap > 0 else outline)
    return found


_WORDS = {"outside": "is outside the boundary", "overlap": "overlaps", "close": "is closer than the gap to"}


@dataclass
class FaceArrangement:
    shapes: dict[str, object]
    order: tuple[str, ...]
    layout: object
    region: object
    target: object
    problems: dict[str, str] = field(default_factory=dict)
    summary: str = ""
    warnings: tuple[str, ...] = ()
    suggestions: tuple[tuple[str, dict], ...] = ()

    @property
    def fits(self) -> bool:
        return not self.problems


def _settings(options, period) -> Settings:
    pattern = options.get("pattern", "grid")
    return Settings(
        pattern=pattern,
        spacing="fixed" if options.get("spacing") == "fixed" else "auto",
        gap=max(float(options.get("gap", 5.0)), 0.0),
        gap_y=(max(float(options["gap_y"]), 0.0)
               if pattern == "custom" and options.get("gap_y") is not None else None),
        direction=float(options.get("direction", 0.0)),
        align=str(options.get("align", "center")),
        rows=max(int(options.get("rows", 0)), 0) if pattern in ("custom", "grid", "staggered") else 0,
        columns=max(int(options.get("columns", 0)), 0) if pattern in ("custom", "grid", "staggered") else 0,
        fill="columns" if options.get("fill") == "columns" else "rows",
        distribute="evenly" if options.get("distribute") == "evenly" else "between",
        rotation=float(options.get("rotation", 0.0)),
        auto_rotate=bool(options.get("auto_rotate", False)),
        radius=max(float(options.get("radius", 0.0)), 0.0),
        start_angle=float(options.get("start_angle", 0.0)),
        face_center=bool(options.get("face_center", False)),
        contain=bool(options.get("contain", True)),
        period=period,
    )


def _summary(arrangement_count, layout, settings) -> str:
    n = arrangement_count
    noun = "object" if n == 1 else "objects"
    if settings.pattern == "radial":
        detail = f"ring r {layout.radius:.1f} mm"
    elif settings.pattern == "packed":
        detail = "packed"
    elif settings.pattern == "linear":
        detail = "in a row"
    else:
        kind = "staggered" if settings.pattern == "staggered" else "grid"
        detail = f"{layout.rows} × {layout.columns} {kind}"
    return f"{n} {noun} · {detail} · {layout.gap:.2f} mm gaps"


def arrange_on_face(
    items,
    target_face,
    options: dict,
    *,
    contacts: dict | None = None,
    edges=(),
    others=(),
    offset=(0.0, 0.0, 0.0),
    suggest: bool = False,
) -> FaceArrangement:
    """Lay separately named bodies out on *target_face* in a pattern.

    *options* holds evaluated feature inputs (pattern, spacing, gap, margin,
    boundary, ...). *contacts* maps a body name to the face it should stand
    on; *edges* are picked edges of the target face that a manual boundary
    keeps its margin from; *others* are ``(name, shape)`` pairs of bodies to
    keep clear of. *offset* moves the whole arrangement along U, V and the
    normal after it is planned -- and is checked like everything else.
    """
    import numpy
    from OCP.gp import gp_Ax1, gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf
    from shapely import affinity

    items = [(str(name), shape) for name, shape in items if shape is not None]
    if not items:
        raise CadError("Select at least one object to arrange.")
    pattern = options.get("pattern", "grid")
    if pattern not in PATTERNS:
        raise CadError("That arrangement pattern is not available.")
    target = _target(target_face)
    if pattern == "radial" and not isinstance(target, _FlatTarget):
        raise CadError("A radial arrangement needs a flat face.",
                       suggestion="Use Linear to go round a cylinder.")
    contacts = contacts or {}
    orient = bool(options.get("orient", True))
    seats = [_seat(name, shape, target, contacts.get(name), orient) for name, shape in items]
    warnings = tuple(seat.warning for seat in seats if seat.warning)
    footprints = [seat.footprint for seat in seats]
    if target.internal:
        # Parts stood inside a bore lean together at the top; plan with the
        # outline they have at their tips.
        footprints = [affinity.scale(fp, target.radius / max(target.radius - seat.height, 1e-3), 1.0,
                                     origin=(0, 0))
                      for fp, seat in zip(footprints, seats)]

    # Keep the order the objects already have across the face, so a layout
    # changes as little as it can.
    settings = _settings(options, target.period)
    turn = math.radians(settings.direction)
    tallest = max(fp.bounds[3] - fp.bounds[1] for fp in footprints) or 1.0

    def where(shape):
        low, high = bounding_box(shape)
        u, v = target.to_uv(tuple((low[i] + high[i]) / 2.0 for i in range(3)))
        along = u * math.cos(turn) + v * math.sin(turn)
        across = -u * math.sin(turn) + v * math.cos(turn)
        return (0 if pattern == "linear" else round(-across / tallest), along)

    order = sorted(range(len(items)), key=lambda i: where(items[i][1]))
    items = [items[i] for i in order]
    seats = [seats[i] for i in order]
    footprints = [footprints[i] for i in order]

    gap = settings.gap if settings.gap_y is None else min(settings.gap, settings.gap_y)
    reach = max(seat.height for seat in seats) + float(options.get("standoff", 0.0)) + gap
    moving = {name for name, _ in items}
    blockers = (_obstacles(target, [(n, s) for n, s in others if n not in moving], reach, gap)
                if options.get("avoid_bodies", True) else [])
    lines = [_edge_line(edge, target) for edge in edges]
    region, bounds = _region(target, options, lines, blockers)
    layout = plan(footprints, region, settings, bounds)
    du, dv, lift = (float(value) for value in offset)
    if du or dv:
        layout.placements = [(u + du, v + dv, a) for u, v, a in layout.placements]
        wrap = None
        if settings.period:
            wrap = (settings.period, 0.0)
        layout.problems = check(footprints, layout.placements, region, gap=gap,
                                contain=settings.contain, wrap=wrap)
    lift += float(options.get("standoff", 0.0))

    shapes, names = {}, [name for name, _ in items]
    for (name, shape), seat, (u, v, angle) in zip(items, seats, layout.placements):
        rise = lift
        if target.internal:
            half = max(abs(x) for x, _y in numpy.asarray(
                affinity.rotate(seat.footprint, angle, origin=(0, 0)).exterior.coords))
            if half >= target.radius:
                layout.problems.setdefault(names.index(name), ("outside", None))
                half = target.radius * 0.999
            # Drop the flat base until its corners, not its middle, touch the bore.
            rise += target.radius - math.sqrt(target.radius ** 2 - half ** 2)
        x_axis, _y_axis, normal = target.axes(u, v)
        spin = gp_Trsf()
        spin.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), math.radians(angle))
        move = gp_Trsf()
        move.SetDisplacement(
            gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1), gp_Dir(1, 0, 0)),
            gp_Ax3(gp_Pnt(*target.point(u, v, rise)), gp_Dir(*normal), gp_Dir(*x_axis)),
        )
        shapes[name] = transformed(shape, move.Multiplied(spin.Multiplied(seat.transform)))

    problems = {}
    for index, (kind, other) in layout.problems.items():
        words = _WORDS[kind]
        problems[names[index]] = words if other is None else f"{words} {names[other]}"
    if problems:
        collide = sum(1 for kind, _ in layout.problems.values() if kind != "outside")
        outside = len(problems) - collide
        bits = ([f"{collide} {'object collides' if collide == 1 else 'objects collide'}"] if collide else []) \
            + ([f"{outside} outside the boundary"] if outside else [])
        summary = " · ".join(bits)
    else:
        summary = _summary(len(items), layout, settings)
    suggestions = ()
    if suggest and problems:
        offers = alternatives(footprints, region, settings, bounds)
        if len(offers) < 3 and options.get("boundary") != "manual" and options.get("margin", 0.0) > 0:
            bare, bare_bounds = _region(target, {**options, "margin": 0.0}, lines, blockers)
            if plan(footprints, bare, settings, bare_bounds).fits:
                offers.append(("Remove the edge margin", {"margin": 0.0}))
        suggestions = tuple(offers)
    return FaceArrangement(shapes, tuple(names), layout, region, target, problems,
                           summary, warnings, suggestions)


def boundary_wires(arrangement: FaceArrangement, lift: float = 0.05):
    """The usable region drawn on the face, for the preview."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
    from OCP.gp import gp_Pnt
    from shapely.geometry import box

    region = arrangement.region
    target = arrangement.target
    if target.period:
        region = region.intersection(box(*target.domain.bounds))
    wires = []
    for polygon in _polygons(region):
        for ring in [polygon.exterior, *polygon.interiors]:
            coords = list(ring.coords)
            maker = BRepBuilderAPI_MakePolygon()
            for (u0, v0), (u1, v1) in zip(coords, coords[1:]):
                # Short steps, so a ring on a cylinder follows the surface.
                steps = max(1, int(math.hypot(u1 - u0, v1 - v0) / 2.0)) if target.period or \
                    isinstance(target, _RoundTarget) else 1
                for k in range(steps):
                    t = k / steps
                    maker.Add(gp_Pnt(*target.point(u0 + (u1 - u0) * t, v0 + (v1 - v0) * t, lift)))
            maker.Close()
            if maker.IsDone():
                wires.append(maker.Wire())
    return compound(wires)


#: Pattern inputs, by how they are read: choices as text, switches as
#: booleans, lengths and angles as expressions.
_CHOICES = ("pattern", "spacing", "boundary", "align", "fill", "distribute")
_SWITCHES = {"contain": True, "avoid_holes": True, "avoid_bodies": True,
             "auto_rotate": False, "face_center": False, "orient": True}
_LENGTHS = {"gap": 5.0, "gap_y": None, "margin": 0.0, "inset_left": 0.0, "inset_right": 0.0,
            "inset_top": 0.0, "inset_bottom": 0.0, "radius": 0.0, "normal_offset": 0.0}
_ANGLES = ("direction", "rotation", "start_angle")


@register("arrange")
class ArrangeFeature(Feature):
    """Arrange several bodies without fusing or renaming any of them."""

    label = "Arrange"
    dimensions = {key: Dimension.ANGLE for key in _ANGLES}

    def is_pattern(self) -> bool:
        return self.inputs.get("pattern", "row") != "row"

    def execute(self, ctx: BuildContext) -> dict:
        if not self.is_pattern():
            return self._row(ctx)
        names, result = self._solve(ctx, suggest=False)
        for warning in result.warnings:
            ctx.warn(warning)
        if not result.fits:
            # Something upstream changed since this was applied. Placing the
            # bodies anyway keeps the model building; the warning says why it
            # needs attention rather than leaving an overlap unexplained.
            ctx.warn(f"The arrangement no longer fits: {result.summary}. "
                     "Edit the arrangement to choose a layout that does.")
        self.outputs = list(names)
        self.message = f"Arranged {result.summary}."
        return {name: result.shapes[name] for name in names}

    def preview_parts(self, ctx: BuildContext):
        if not self.is_pattern():
            return None
        names, result = self._solve(ctx, suggest=True)
        for warning in result.warnings:
            ctx.warn(warning)
        layout = result.layout
        self.message = result.summary
        self.annotations = {
            "fits": result.fits,
            "summary": result.summary,
            "problems": [{"name": name, "reason": reason} for name, reason in result.problems.items()],
            "suggestions": [{"label": label, "patch": patch} for label, patch in result.suggestions],
            "gap": layout.gap, "rows": layout.rows, "columns": layout.columns,
            "radius": layout.radius, "order": list(result.order),
        }
        good = [result.shapes[n] for n in result.order if n not in result.problems]
        bad = [result.shapes[n] for n in result.order if n in result.problems]
        return {
            "result": compound([result.shapes[n] for n in names]),
            "ok": compound(good),
            "problem": compound(bad),
            "boundary": boundary_wires(result),
        }

    def _options(self, ctx: BuildContext) -> dict:
        inputs = self.inputs
        options = {key: str(inputs[key]) for key in _CHOICES if key in inputs}
        options.update({key: bool(inputs.get(key, default)) for key, default in _SWITCHES.items()})
        for key, default in _LENGTHS.items():
            options[key] = ctx.value(self, key) if key in inputs else default
        for key in _ANGLES:
            options[key] = ctx.value(self, key, 0.0)
        options["rows"] = int(inputs.get("rows", 0) or 0)
        options["columns"] = int(inputs.get("columns", 0) or 0)
        options["standoff"] = options.pop("normal_offset")
        return options

    def _solve(self, ctx: BuildContext, *, suggest: bool):
        names = [str(value) for value in self.inputs.get("bodies", [])]
        items = [(name, ctx.named_shape(name)) for name in names]
        target_face = ctx.resolve(self, "target_face", required=False)
        if target_face is None:
            raise CadError("Pick the face to arrange the objects on.",
                           suggestion="Click a flat face, or the round side of a cylinder.")
        contacts = {}
        refs = self.inputs.get("contact_faces") or []
        if refs:
            contacts = {ref.body: face for ref, face in zip(refs, ctx.resolve(self, "contact_faces"))}
        edges = ctx.resolve(self, "boundary_edges") if self.inputs.get("boundary_edges") else []
        target_body = getattr(self.inputs.get("target_face"), "body", "")
        bodies = getattr(ctx.document, "bodies", {})
        others = [
            (name, shape) for name, shape in ctx.bodies.items()
            if not name.startswith("__") and name != target_body and name not in names
            and getattr(bodies.get(name), "visible", True)
        ]
        offset = (ctx.value(self, "move_x", 0.0), ctx.value(self, "move_y", 0.0),
                  ctx.value(self, "move_normal", 0.0))
        result = arrange_on_face(items, target_face, self._options(ctx), contacts=contacts,
                                 edges=edges, others=others, offset=offset, suggest=suggest)
        return names, result

    def _row(self, ctx: BuildContext) -> dict:
        names = [str(value) for value in self.inputs.get("bodies", [])]
        items = [(name, ctx.named_shape(name)) for name in names]
        target_face = ctx.resolve(self, "target_face", required=False)
        frame = None
        if target_face is not None:
            frame = (
                reference_frame(target_face)
                if self.inputs.get("frame_mode") == "reference"
                else planar_frame(target_face)
            )
            offset = _add(
                _scale(frame.x_axis, ctx.value(self, "move_x", 0.0)),
                _scale(frame.y_axis, ctx.value(self, "move_y", 0.0)),
                _scale(frame.normal, ctx.value(self, "move_normal", 0.0)),
            )
        else:
            offset = (
                ctx.value(self, "dx", 0.0),
                ctx.value(self, "dy", 0.0),
                ctx.value(self, "dz", 0.0),
            )
        result = arrange_shapes(
            items,
            mode=str(self.inputs.get("mode", "equal_gaps")),
            axis=str(self.inputs.get("axis", "x")),
            gap=ctx.value(self, "gap", 10.0),
            target_face=target_face,
            orient=bool(self.inputs.get("orient", True)),
            normal_offset=ctx.value(self, "normal_offset", 0.0),
            group_offset=offset,
            stable_frame=self.inputs.get("frame_mode") == "reference",
        )
        for warning in result.warnings:
            ctx.warn(warning)
        if frame is not None:
            past = _overhang(frame, result.shapes.values())
            if past > 1e-3:
                ctx.warn(f"The row runs {past:.1f} mm past the edge of the target face.")
        self.outputs = list(names)
        self.message = f"Arranged {len(names)} objects with {result.gap:g} mm gaps."
        return {name: result.shapes[name] for name in names}


def _overhang(frame, shapes) -> float:
    """How far any of *shapes* reaches past the face's extent along U or V."""
    worst = 0.0
    for shape in shapes:
        for axis, (low, high) in ((frame.x_axis, frame.x_bounds), (frame.y_axis, frame.y_bounds)):
            start, end = projected_bounds(shape, axis)
            middle = _dot(frame.center, axis)
            worst = max(worst, middle + low - start, end - middle - high)
    return worst


__all__ = ["ArrangeFeature", "Arrangement", "FaceArrangement", "arrange_on_face",
           "arrange_shapes", "boundary_wires", "face_axes", "projected_bounds"]
