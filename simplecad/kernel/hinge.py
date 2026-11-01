"""Hinges between two parts, built from one picked edge.

Pick the straight edge the hinge runs along -- the top back edge of a box --
and the part that swings, the lid. The knuckles go just outside the face that
edge closes off (the box's back), centred on the plane between the parts
(the box's top), so the lid swings up and back, clear of the box, to about
180 degrees.

Types:
    pin       knuckles on both parts, bored; a separate pin body goes through
    knuckle   print-in-place: the pin is part of the fixed part's knuckles and
              the swinging part's knuckles turn on it with clearance
    snap      as knuckle, but the swinging knuckles are open C-clips that press
              onto the pin and come off again
    living    a thin flexible strip joining the two into one print -- for PP,
              PETG or TPU; PLA fatigues and cracks

Cross-section frame (u, w), with X along the hinge line:
    u   out of the back face        w   out of the seam face, toward the lid
The axis is at (r + gap, 0): outside the back face by a knuckle radius plus the
running gap, on the seam plane.

Everything that turns is checked by turning it: the swinging part is rotated
through its whole range against the fixed one, and the first angle where they
meet is reported.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..core.document import BuildContext, Feature, register
from ..core.errors import CadError, guard
from ..core.units import Dimension
from .occ import boolean_builder, built_shape, is_valid, transformed, unify

TYPES = {"pin": "Pin hinge", "knuckle": "Print-in-place", "snap": "Snap-on", "living": "Living hinge"}
DEFAULTS = {
    "knuckles": 5,
    "knuckle_od": 8.0,
    "pin": 3.0,
    "axial_clearance": 0.4,
    "length": 0.0,           # 0: the whole edge
    "open_max": 120.0,
    "flex": 0.5,             # living hinge strip thickness
    "flex_length": 3.0,      # living hinge: the part that bends
    "min_wall": 0.8,
}
ROOT = 0.3
#: The swing is checked every this many degrees, then refined near a contact.
SWEEP_STEP = 10.0


@dataclass(frozen=True)
class HingeFrame:
    origin: tuple            # a point on the hinge line (the edge start)
    x: tuple                 # along the hinge line
    u: tuple                 # out of the back face
    w: tuple                 # out of the seam face, toward the swinging part
    length: float            # edge length

    def point(self, x, u, w):
        return tuple(self.origin[i] + x * self.x[i] + u * self.u[i] + w * self.w[i]
                     for i in range(3))


def _dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


def hinge_frame(body_a, edge, body_b, flip: bool = False) -> HingeFrame:
    """Read the hinge line and its two faces off the picked edge of part A."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Line
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
    from OCP.TopoDS import TopoDS

    from .detect import analyse_plane
    from .occ import bounding_box

    edge = TopoDS.Edge_s(edge)
    curve = BRepAdaptor_Curve(edge)
    if curve.GetType() != GeomAbs_Line:
        raise CadError("A hinge runs along a straight edge; this one is curved.",
                       suggestion="Pick a straight edge where the two parts meet.")
    start = curve.Value(curve.FirstParameter())
    end = curve.Value(curve.LastParameter())
    p0 = (start.X(), start.Y(), start.Z())
    p1 = (end.X(), end.Y(), end.Z())
    length = math.dist(p0, p1)
    x = tuple((p1[i] - p0[i]) / length for i in range(3))

    ancestors = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(body_a, TopAbs_EDGE, TopAbs_FACE, ancestors)
    index = ancestors.FindIndex(edge)
    if index == 0:
        raise CadError("That edge is not on the part it was picked from.")
    planes = []
    for face in ancestors.FindFromIndex(index):
        info = analyse_plane(TopoDS.Face_s(face))
        if info is not None:
            planes.append(tuple(info.normal))
    unique = []
    for normal in planes:
        if all(_dot(normal, other) < 0.999 for other in unique):
            unique.append(normal)
    if len(unique) != 2:
        raise CadError("A hinge edge needs a flat face on each side of it.",
                       suggestion="Pick an edge between two flat faces, like a box's top back edge.")
    low, high = bounding_box(body_b)
    toward_b = tuple((low[i] + high[i]) / 2.0 - p0[i] for i in range(3))
    # The seam face is the one facing the swinging part; the knuckles go
    # outside the other one.
    seam, back = sorted(unique, key=lambda n: -_dot(n, toward_b))
    if flip:
        seam, back = back, seam
    # Run along the edge in whichever direction makes (x, u, w) right-handed,
    # so the local frame below maps u to *out of* the back face, not into it.
    cross = (seam[1] * x[2] - seam[2] * x[1], seam[2] * x[0] - seam[0] * x[2],
             seam[0] * x[1] - seam[1] * x[0])
    if _dot(cross, back) < 0.0:
        p0, x = p1, tuple(-c for c in x)
    return HingeFrame(origin=p0, x=x, u=back, w=seam, length=length)


def _transform(frame: HingeFrame):
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf

    result = gp_Trsf()
    result.SetDisplacement(
        gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1), gp_Dir(1, 0, 0)),
        # Local (x, u, w) -> world: local X is the hinge line, local Y is u.
        gp_Ax3(gp_Pnt(*frame.origin), gp_Dir(*frame.w), gp_Dir(*frame.x)),
    )
    return result


def _prism(points, x0, x1):
    from .slides import prism

    return prism(points, x0, x1)


def _cylinder(u, w, radius, x0, x1):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x0, u, w), gp_Dir(1, 0, 0)), radius,
                                    x1 - x0).Shape()


def _box(x0, u0, w0, x1, u1, w1):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    return BRepPrimAPI_MakeBox(gp_Pnt(x0, u0, w0), gp_Pnt(x1, u1, w1)).Shape()


def _fuse(a, b, what="hinge"):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

    return built_shape(boolean_builder(BRepAlgoAPI_Fuse, a, b), what)


def _cut(a, b, what="hinge"):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

    return built_shape(boolean_builder(BRepAlgoAPI_Cut, a, b), what)


def _rotated_box(axis_u, cx, cw, half_t, w0, w1, angle_degrees, x0, x1):
    """A box (tangential half-width *half_t*, radial *w0*..*w1* above the axis)
    turned clockwise by *angle* about the hinge axis -- the way the lid opens."""
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

    shape = _box(x0, axis_u - half_t + cx, w0 + cw, x1, axis_u + half_t + cx, w1 + cw)
    turn = gp_Trsf()
    turn.SetRotation(gp_Ax1(gp_Pnt(0, axis_u, 0), gp_Dir(1, 0, 0)), -math.radians(angle_degrees))
    return transformed(shape, turn)


def segments(length, count, gap):
    """``(start, end, owner)`` for each knuckle, alternating fixed/swinging."""
    count = max(2, int(count))
    each = (length - (count - 1) * gap) / count
    if each < 2.0:
        raise CadError(
            f"{count} knuckles leave each one {each:.1f} mm long.",
            suggestion="Use fewer knuckles or a longer hinge.",
        )
    result, x = [], 0.0
    for index in range(count):
        result.append((x, x + each, "a" if index % 2 == 0 else "b"))
        x += each + gap
    return result


@register("hinge")
class HingeFeature(Feature):
    """Knuckles, pin and stop between a fixed part and the part that swings."""

    label = "Hinge"
    dimensions = {"open_max": Dimension.ANGLE, "knuckles": Dimension.SCALAR}

    def consumed_bodies(self) -> list[str]:
        # A living hinge makes the two parts one print.
        if str(self.inputs.get("type")) == "living":
            return [str(self.inputs.get("body_b", ""))]
        return []

    def preview_parts(self, ctx: BuildContext):
        outputs = self.execute(ctx)
        from .occ import compound

        first, second = str(self.inputs.get("body_a")), str(self.inputs.get("body_b"))
        parts = {"result": compound(list(outputs.values())), "a": outputs[first]}
        if second in outputs:
            parts["b"] = outputs[second]
        for name in self.inputs.get("names") or []:
            if str(name) in outputs:
                parts["pin"] = outputs[str(name)]
        return parts

    def execute(self, ctx: BuildContext) -> dict[str, object]:
        from .calibration import effective_fits
        from .thread_specs import printer_profile

        first = str(self.inputs.get("body_a", ""))
        second = str(self.inputs.get("body_b", ""))
        if not first or not second or first == second:
            raise CadError("A hinge joins two different parts: pick the edge on the fixed "
                           "part and the part that swings.")
        body_a, body_b = ctx.shape(self, "body_a"), ctx.shape(self, "body_b")
        edge = ctx.resolve(self, "edge")
        kind = str(self.inputs.get("type", "knuckle"))
        if kind not in TYPES:
            raise CadError(f"Unknown hinge type: {kind}")
        frame = hinge_frame(body_a, edge, body_b, bool(self.inputs.get("flip")))
        value = {key: ctx.value(self, key, DEFAULTS[key]) for key in DEFAULTS}
        fit = self.inputs.get("clearance", "sliding")
        if isinstance(fit, str) and fit in ("snug", "sliding", "loose", "press"):
            clearance = float(effective_fits(printer_profile()["id"])[fit])
        else:
            clearance = ctx.value(self, "clearance", 0.3)
        length = value["length"] or frame.length
        if length > frame.length + 1e-6:
            raise CadError(f"The hinge is longer than its {frame.length:.1f} mm edge.")
        start = (frame.length - length) / 2.0
        place = _transform(frame)

        def world(shape):
            return transformed(shape, place)

        if kind == "living":
            return self._living(ctx, frame, body_a, body_b, first, second, value, start, length,
                                world)

        r = value["knuckle_od"] / 2.0
        pin = value["pin"]
        gap = max(clearance / 2.0, 0.25)
        bore = pin + clearance
        wall = r - bore / 2.0
        if clearance <= 0.0:
            raise CadError("A hinge that turns needs clearance; it would fuse solid.",
                           suggestion="Use the Sliding or Loose fit.")
        if wall < value["min_wall"]:
            raise CadError(
                f"A {pin:g} mm pin in a {value['knuckle_od']:g} mm knuckle leaves "
                f"{wall:.2f} mm of wall; the minimum is {value['min_wall']:g} mm.",
                suggestion="Use a thinner pin or larger knuckles.",
            )
        axis_u = r + gap
        knuckles = segments(length, value["knuckles"], value["axial_clearance"])
        open_max = value["open_max"]
        if not 10.0 <= open_max <= 180.0:
            raise CadError("The opening limit must be between 10° and 180°.")

        with guard("hinge"):
            parts = {"a": [], "b": []}
            for x0, x1, owner in knuckles:
                x0, x1 = x0 + start, x1 + start
                parts[owner].append(_cylinder(axis_u, 0.0, r, x0, x1))
                w0, w1 = (-r, 0.0) if owner == "a" else (0.0, r)
                parts[owner].append(_box(x0, -ROOT, w0, x1, axis_u, w1))
            stop = bool(self.inputs.get("stop", False))
            if stop:
                # A bar along the swinging part, above its knuckles, meets a
                # shoulder on the fixed knuckles at exactly the opening limit.
                bar_r0, thick, half = r + gap + 0.4, max(1.5, r * 0.5), max(1.0, r * 0.25)
                parts["b"].append(_box(start, axis_u - half, bar_r0, start + length,
                                       axis_u + half, bar_r0 + thick))
                for x0, x1, owner in knuckles:
                    if owner == "b":
                        parts["b"].append(_box(x0 + start, axis_u - half, r - 0.5,
                                               x1 + start, axis_u + half, bar_r0 + 0.01))
                    else:
                        parts["a"].append(_rotated_box(axis_u, half + 1.0 + 0.0, 0.0, 1.0,
                                                       r - 0.5, bar_r0 + thick, open_max,
                                                       x0 + start, x1 + start))
            a, b = body_a, body_b
            for shape in parts["a"]:
                a = _fuse(a, world(shape), "fixed knuckle")
            for shape in parts["b"]:
                b = _fuse(b, world(shape), "swinging knuckle")
            b = _cut(b, world(_cylinder(axis_u, 0.0, bore / 2.0, start - 1.0,
                                        start + length + 1.0)), "knuckle bore")
            outputs = {}
            if kind == "pin":
                a = _cut(a, world(_cylinder(axis_u, 0.0, bore / 2.0, start - 1.0,
                                            start + length + 1.0)), "knuckle bore")
                names = [str(n) for n in self.inputs.get("names") or []]
                if not names:
                    raise CadError("The hinge is missing a name for its pin.",
                                   suggestion="Open the hinge and apply it again.")
                if names[0] not in self.dropped:
                    outputs[names[0]] = world(_cylinder(axis_u, 0.0, pin / 2.0, start + 0.2,
                                                        start + length - 0.2))
            else:
                a = _fuse(a, world(_cylinder(axis_u, 0.0, pin / 2.0, start, start + length)),
                          "hinge pin")
            if kind == "snap":
                mouth = pin * 0.85
                for x0, x1, owner in knuckles:
                    if owner == "b":
                        b = _cut(b, world(_box(x0 + start - 0.01, axis_u, -mouth / 2.0,
                                               x1 + start + 0.01, axis_u + r + 1.0, mouth / 2.0)),
                                 "snap mouth")
                # Each side of the C bends like a quarter-ring cantilever, the
                # same estimate the snap tabs use: 1.5 * t * deflection / L².
                arm = math.pi * (r + bore / 2.0) / 4.0
                strain = 1.5 * wall * ((pin - mouth) / 2.0) / arm ** 2
                if strain > 0.02:
                    ctx.warn(f"The clip bends {strain * 100:.1f}% to snap on; PLA cracks at "
                             "about 2%, PETG about 4%."
                             + (" Use larger knuckles or a thinner pin." if strain > 0.04 else
                                " Print it in PETG."))
            a, b = unify(a), unify(b)

        if not is_valid(a) or not is_valid(b):
            raise CadError("The hinge did not leave valid printable parts.",
                           suggestion="Try smaller knuckles or move the hinge.")
        axis_point = frame.point(0.0, axis_u, 0.0)
        hit = first_contact(a, b, axis_point, frame.x, open_max)
        if hit is not None:
            raise CadError(
                f"{second} hits {first} at {hit:.0f}° of its {open_max:.0f}° swing.",
                suggestion="Lower the opening limit, use larger knuckles to move the hinge "
                           "out, or flip it to the other face.",
            )
        if stop and first_contact(a, b, axis_point, frame.x, open_max + 4.0,
                                  start_at=open_max + 2.0) is None:
            ctx.warn("The stop does not catch; the part swings past the limit.")
        self.annotations = {
            "type": kind, "axis": list(axis_point), "direction": list(frame.x),
            "open_max": open_max, "knuckles": len(knuckles),
        }
        self.outputs = [first, second, *outputs]
        self.message = (f"{TYPES[kind]}, {len(knuckles)} knuckles, opens to {open_max:.0f}°"
                        f"{' against a stop' if stop else ''}; {clearance:.2f} mm clearance.")
        return self.keep({first: a, second: b, **outputs})

    def _living(self, ctx, frame, body_a, body_b, first, second, value, start, length, world):
        from OCP.BRepExtrema import BRepExtrema_DistShapeShape

        flex, span = value["flex"], value["flex_length"]
        if flex < 0.3 or flex > 1.2:
            raise CadError(f"A {flex:g} mm living hinge will either tear or not bend.",
                           suggestion="0.4–0.8 mm suits most printers.")
        distance = BRepExtrema_DistShapeShape(body_a, body_b)
        if distance.IsDone() and distance.Value() < 0.2:
            raise CadError(
                "For a living hinge the two parts must stand apart; touching, they would "
                "print as one solid.",
                suggestion="Leave a gap of at least 0.4 mm between them.",
            )
        pad = max(2.0, flex * 4.0)
        height = max(span + 2.0 * pad, 8.0)
        with guard("living hinge"):
            x0, x1 = start, start + length
            strip = _box(x0, -ROOT, -span / 2.0 - 0.5, x1, flex, span / 2.0 + 0.5)
            pad_a = _box(x0, -ROOT, -height / 2.0, x1, pad, -span / 2.0)
            pad_b = _box(x0, -ROOT, span / 2.0, x1, pad, height / 2.0)
            one = body_a
            for shape in (pad_a, strip, pad_b):
                one = _fuse(one, world(shape), "living hinge")
            one = unify(_fuse(one, body_b, "living hinge"))
        if not is_valid(one):
            raise CadError("The living hinge did not leave a valid printable part.")
        ctx.warn("Living hinges suit PP, PETG or TPU. In PLA they crack after a few bends.")
        self.annotations = {"type": "living", "axis": list(frame.point(0.0, flex / 2.0, 0.0)),
                            "direction": list(frame.x), "open_max": value["open_max"],
                            "knuckles": 0}
        self.outputs = [first]
        self.message = f"Living hinge, {flex:g} mm thick, {length:.0f} mm long."
        return self.keep({first: one})


def first_contact(fixed, swinging, axis_point, axis_direction, open_max, *,
                  start_at: float = 0.0, step: float = SWEEP_STEP):
    """The first angle (degrees) in ``(start_at, open_max]`` where the swinging
    part, turned about the axis, meets the fixed one; None if it never does.

    Coarse steps, then a 1° search back from the first contact found.
    """
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

    from .align import interference_volume

    def hits(angle):
        turn = gp_Trsf()
        turn.SetRotation(gp_Ax1(gp_Pnt(*axis_point), gp_Dir(*axis_direction)),
                         -math.radians(angle))
        return interference_volume(fixed, transformed(swinging, turn)) > 1e-3

    angles = []
    angle = start_at + step
    while angle < open_max:
        angles.append(angle)
        angle += step
    angles.append(open_max)
    previous = start_at
    for angle in angles:
        if hits(angle):
            fine = previous + 1.0
            while fine < angle:
                if hits(fine):
                    return fine
                fine += 1.0
            return angle
        previous = angle
    return None


def hinge_for(document, body: str):
    """The newest hinge written into *body*, if any (for Edit hinge)."""
    graph = document.prepare_graph()
    head = graph.latest.get(str(body))
    if head is None:
        return None
    for fid in reversed(graph.writers.get(head.object_id, ())):
        feature = graph.features[fid]
        if feature.type_name == "hinge":
            return feature
    return None
