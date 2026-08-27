"""Measurement.

One entry point, :func:`measure`, which looks at what is selected and reports
everything that can sensibly be said about it. That is the shape the tool wants:
a user selects two holes and expects to be told the centre distance without
first choosing "centre distance" from a menu.

Every reading carries a label, a value and a formatted string, so the panel
displays them without knowing what any of them mean.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .detect import analyse_cylinder, analyse_plane
from .occ import area as surface_area
from .occ import bounding_box, center_of_mass, volume as solid_volume


@dataclass(frozen=True)
class Reading:
    """One measured quantity."""

    label: str
    value: float
    text: str
    primary: bool = False


@dataclass
class Measurement:
    subject: str = ""
    readings: list[Reading] = field(default_factory=list)

    @property
    def headline(self) -> Reading | None:
        return next(
            (r for r in self.readings if r.primary),
            self.readings[0] if self.readings else None,
        )

    def is_empty(self) -> bool:
        return not self.readings


def _mm(value: float) -> str:
    return f"{value:.3f} mm".rstrip("0").rstrip(".").replace(" mm", " mm")


def _fmt(value: float, unit: str = "mm", decimals: int = 3) -> str:
    text = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    return f"{text} {unit}" if unit else text


# ----------------------------------------------------------------------
# Single things
# ----------------------------------------------------------------------
def _edge_readings(edge) -> list[Reading]:
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepGProp import BRepGProp
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Ellipse, GeomAbs_Line
    from OCP.GProp import GProp_GProps
    from OCP.TopoDS import TopoDS

    props = GProp_GProps()
    BRepGProp.LinearProperties_s(edge, props)
    readings = [Reading("Length", props.Mass(), _fmt(props.Mass()), primary=True)]

    adaptor = BRepAdaptor_Curve(TopoDS.Edge_s(edge))
    kind = adaptor.GetType()
    if kind == GeomAbs_Circle:
        circle = adaptor.Circle()
        readings.append(Reading("Radius", circle.Radius(), _fmt(circle.Radius())))
        readings.append(
            Reading("Diameter", circle.Radius() * 2, _fmt(circle.Radius() * 2))
        )
        centre = circle.Location()
        readings.append(
            Reading("Centre", 0.0, _point(centre.X(), centre.Y(), centre.Z()))
        )
    elif kind == GeomAbs_Ellipse:
        ellipse = adaptor.Ellipse()
        readings.append(
            Reading("Major radius", ellipse.MajorRadius(), _fmt(ellipse.MajorRadius()))
        )
        readings.append(
            Reading("Minor radius", ellipse.MinorRadius(), _fmt(ellipse.MinorRadius()))
        )
    elif kind == GeomAbs_Line:
        first = adaptor.Value(adaptor.FirstParameter())
        last = adaptor.Value(adaptor.LastParameter())
        readings.append(
            Reading("From", 0.0, _point(first.X(), first.Y(), first.Z()))
        )
        readings.append(Reading("To", 0.0, _point(last.X(), last.Y(), last.Z())))
    return readings


def _face_readings(face) -> list[Reading]:
    readings = [
        Reading("Area", surface_area(face), _fmt(surface_area(face), "mm²"), primary=True)
    ]
    cylinder = analyse_cylinder(face)
    if cylinder is not None:
        readings.append(
            Reading("Diameter", cylinder.diameter, _fmt(cylinder.diameter))
        )
        readings.append(Reading("Radius", cylinder.radius, _fmt(cylinder.radius)))
        readings.append(Reading("Length", cylinder.length, _fmt(cylinder.length)))
        readings.append(
            Reading("Axis", 0.0, _direction(cylinder.direction))
        )
        readings[0] = Reading(
            "Diameter", cylinder.diameter, _fmt(cylinder.diameter), primary=True
        )
        readings = readings[:1] + [
            r for r in readings[1:] if r.label != "Diameter"
        ] + [Reading("Area", surface_area(face), _fmt(surface_area(face), "mm²"))]
        return readings

    plane = analyse_plane(face)
    if plane is not None:
        readings.append(Reading("Centre", 0.0, _point(*plane.center)))
        readings.append(Reading("Normal", 0.0, _direction(plane.normal)))
        low, high = bounding_box(face)
        extent = tuple(high[i] - low[i] for i in range(3))
        span = sorted(extent, reverse=True)[:2]
        readings.append(
            Reading("Extent", span[0], f"{_fmt(span[0])} x {_fmt(span[1])}")
        )
    return readings


def _body_readings(shape) -> list[Reading]:
    low, high = bounding_box(shape)
    size = tuple(high[i] - low[i] for i in range(3))
    volume = solid_volume(shape)
    return [
        Reading("Volume", volume, _fmt(volume, "mm³", 2), primary=True),
        Reading("Surface area", surface_area(shape), _fmt(surface_area(shape), "mm²", 2)),
        Reading(
            "Bounding box", max(size),
            f"{_fmt(size[0])} x {_fmt(size[1])} x {_fmt(size[2])}",
        ),
        Reading("Centre of mass", 0.0, _point(*center_of_mass(shape))),
    ]


def _vertex_readings(vertex) -> list[Reading]:
    from OCP.BRep import BRep_Tool
    from OCP.TopoDS import TopoDS

    point = BRep_Tool.Pnt_s(TopoDS.Vertex_s(vertex))
    return [
        Reading("Position", 0.0, _point(point.X(), point.Y(), point.Z()), primary=True)
    ]


def _point(x: float, y: float, z: float) -> str:
    return f"{x:.2f}, {y:.2f}, {z:.2f}"


def _direction(vector) -> str:
    return f"{vector[0]:.3f}, {vector[1]:.3f}, {vector[2]:.3f}"


# ----------------------------------------------------------------------
# Pairs
# ----------------------------------------------------------------------
def minimum_distance(first, second) -> float | None:
    """Closest approach between two shapes."""
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    try:
        measure = BRepExtrema_DistShapeShape(first, second)
        measure.Perform()
        return measure.Value() if measure.IsDone() else None
    except Exception:  # noqa: BLE001
        return None


def _angle_between(first, second) -> float | None:
    """Angle in degrees between two planar faces or two straight edges."""
    def direction(shape):
        plane = analyse_plane(shape)
        if plane is not None:
            return plane.normal
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.GeomAbs import GeomAbs_Line
        from OCP.TopoDS import TopoDS

        try:
            adaptor = BRepAdaptor_Curve(TopoDS.Edge_s(shape))
            if adaptor.GetType() == GeomAbs_Line:
                line = adaptor.Line().Direction()
                return (line.X(), line.Y(), line.Z())
        except Exception:  # noqa: BLE001
            return None
        return None

    a, b = direction(first), direction(second)
    if a is None or b is None:
        return None
    dot = max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b))))
    return math.degrees(math.acos(abs(dot)))


def _pair_readings(first, second) -> list[Reading]:
    readings: list[Reading] = []

    gap = minimum_distance(first, second)
    if gap is not None:
        label = "Minimum distance" if gap > 1e-9 else "Touching"
        readings.append(Reading(label, gap, _fmt(gap), primary=True))

    # Centre distance, when both have a centre worth speaking of.
    centres = [_centre_of(first), _centre_of(second)]
    if all(c is not None for c in centres):
        span = math.dist(centres[0], centres[1])
        readings.append(Reading("Centre distance", span, _fmt(span)))
        for axis, index in (("X", 0), ("Y", 1), ("Z", 2)):
            delta = abs(centres[1][index] - centres[0][index])
            if delta > 1e-9:
                readings.append(Reading(f"Δ{axis}", delta, _fmt(delta)))

    angle = _angle_between(first, second)
    if angle is not None:
        readings.append(Reading("Angle", angle, _fmt(angle, "°", 2)))

    return readings


def _centre_of(shape):
    cylinder = analyse_cylinder(shape)
    if cylinder is not None:
        origin, direction, length = (
            cylinder.origin, cylinder.direction, cylinder.length
        )
        return tuple(origin[i] + direction[i] * length / 2.0 for i in range(3))
    plane = analyse_plane(shape)
    if plane is not None:
        return plane.center
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_VERTEX

    try:
        props = GProp_GProps()
        if shape.ShapeType() == TopAbs_EDGE:
            BRepGProp.LinearProperties_s(shape, props)
        elif shape.ShapeType() == TopAbs_VERTEX:
            from OCP.BRep import BRep_Tool
            from OCP.TopoDS import TopoDS

            point = BRep_Tool.Pnt_s(TopoDS.Vertex_s(shape))
            return (point.X(), point.Y(), point.Z())
        else:
            BRepGProp.VolumeProperties_s(shape, props)
        centre = props.CentreOfMass()
        return (centre.X(), centre.Y(), centre.Z())
    except Exception:  # noqa: BLE001
        return None


# ----------------------------------------------------------------------
def measure(picks) -> Measurement:
    """Measure whatever is selected.

    *picks* is a list of ``(kind, shape)`` pairs, where kind is one of
    ``vertex``, ``edge``, ``face`` or ``body``.
    """
    picks = list(picks)
    if not picks:
        return Measurement("Nothing selected")

    if len(picks) == 1:
        kind, shape = picks[0]
        readings = {
            "vertex": _vertex_readings,
            "edge": _edge_readings,
            "face": _face_readings,
        }.get(kind, _body_readings)(shape)
        return Measurement(kind.title(), readings)

    if len(picks) == 2:
        (first_kind, first), (second_kind, second) = picks[:2]
        subject = f"{first_kind.title()} to {second_kind}"
        return Measurement(subject, _pair_readings(first, second))

    # Three or more: report the overall extent, which is the useful thing.
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    for _kind, shape in picks:
        builder.Add(compound, shape)
    low, high = bounding_box(compound)
    size = tuple(high[i] - low[i] for i in range(3))
    return Measurement(
        f"{len(picks)} items",
        [
            Reading(
                "Overall extent", max(size),
                f"{_fmt(size[0])} x {_fmt(size[1])} x {_fmt(size[2])}",
                primary=True,
            )
        ],
    )
