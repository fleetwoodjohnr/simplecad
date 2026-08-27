"""A 2D sketch: geometry, constraints, and the parameter vector between them."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .constraints import Constraint, constraint_from_dict, make
from .entities import (
    Arc, Circle, Ellipse, Line, Point, Spline, entity_from_dict, reserve_id,
)


@dataclass
class SketchPlane:
    """Where the sketch lives in 3D: an origin and two in-plane axes."""

    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    x_axis: tuple[float, float, float] = (1.0, 0.0, 0.0)
    normal: tuple[float, float, float] = (0.0, 0.0, 1.0)

    @staticmethod
    def named(name: str) -> "SketchPlane":
        """One of the three base planes."""
        planes = {
            "XY": ((0, 0, 0), (1, 0, 0), (0, 0, 1)),
            "XZ": ((0, 0, 0), (1, 0, 0), (0, -1, 0)),
            "YZ": ((0, 0, 0), (0, 1, 0), (1, 0, 0)),
        }
        origin, x_axis, normal = planes[name.upper()]
        return SketchPlane(origin, x_axis, normal)

    def y_axis(self) -> tuple[float, float, float]:
        n, x = self.normal, self.x_axis
        return (
            n[1] * x[2] - n[2] * x[1],
            n[2] * x[0] - n[0] * x[2],
            n[0] * x[1] - n[1] * x[0],
        )

    def to_3d(self, x: float, y: float) -> tuple[float, float, float]:
        ax, ay = self.x_axis, self.y_axis()
        return tuple(
            self.origin[i] + ax[i] * x + ay[i] * y for i in range(3)
        )

    def to_dict(self) -> dict:
        return {
            "origin": list(self.origin), "x_axis": list(self.x_axis),
            "normal": list(self.normal),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SketchPlane":
        return cls(
            tuple(data.get("origin", (0, 0, 0))),
            tuple(data.get("x_axis", (1, 0, 0))),
            tuple(data.get("normal", (0, 0, 1))),
        )


class Sketch:
    """Points, entities and constraints, plus the flat parameter vector.

    The parameter vector is rebuilt whenever geometry is added, and every
    entity's numbers are a slice of it. The solver only ever sees that vector.
    """

    def __init__(self, name: str = "Sketch", plane: SketchPlane | None = None) -> None:
        self.name = name
        self.plane = plane or SketchPlane.named("XY")
        self.points: dict[str, Point] = {}
        self.entities: dict[str, object] = {}
        self.constraints: list[Constraint] = []
        #: point id -> the coordinates a Fix constraint pins it to.
        self.fixed_targets: dict[str, tuple[float, float]] = {}

    # -- building --------------------------------------------------------
    def add_point(self, x: float, y: float, fixed: bool = False) -> Point:
        point = Point(x=x, y=y, fixed=fixed)
        self.points[point.id] = point
        if fixed:
            self.fixed_targets[point.id] = (x, y)
        return point

    def add_line(self, start, end, construction: bool = False) -> Line:
        line = Line(start.id, end.id, construction=construction)
        self.entities[line.id] = line
        return line

    def add_circle(self, center, radius: float, construction: bool = False) -> Circle:
        circle = Circle(center.id, radius, construction=construction)
        self.entities[circle.id] = circle
        return circle

    def add_arc(self, center, radius: float, start_angle: float, end_angle: float) -> Arc:
        arc = Arc(center.id, radius, start_angle, end_angle)
        self.entities[arc.id] = arc
        return arc

    def add_ellipse(self, center, major: float, minor: float,
                    rotation: float = 0.0, construction: bool = False) -> Ellipse:
        if minor > major:
            major, minor = minor, major
            rotation += math.pi / 2
        ellipse = Ellipse(center.id, major, minor, rotation,
                          construction=construction)
        self.entities[ellipse.id] = ellipse
        return ellipse

    def add_spline(self, points, periodic: bool = False,
                   construction: bool = False) -> Spline:
        spline = Spline([p.id for p in points], periodic,
                        construction=construction)
        self.entities[spline.id] = spline
        return spline

    def add_rectangle(self, x1: float, y1: float, x2: float, y2: float) -> list[Line]:
        """A closed rectangle, already constrained horizontal and vertical.

        Convenience worth having: a hand-built rectangle needs four coincidences
        and four alignments before the solver treats it as a rectangle at all.
        """
        corners = [
            self.add_point(x1, y1), self.add_point(x2, y1),
            self.add_point(x2, y2), self.add_point(x1, y2),
        ]
        lines = []
        for index in range(4):
            lines.append(self.add_line(corners[index], corners[(index + 1) % 4]))
        for index, line in enumerate(lines):
            self.constrain("horizontal" if index % 2 == 0 else "vertical", [line.id])
        # No coincidence constraints: consecutive lines already share the same
        # corner point, so connectivity is structural. Constraining a point to
        # itself would add four rows of zeros and make the sketch report as
        # redundant when it is simply closed.
        return lines

    def add_polygon(self, center_x: float, center_y: float, radius: float,
                    sides: int) -> list[Line]:
        points = []
        for index in range(sides):
            angle = 2 * math.pi * index / sides
            points.append(
                self.add_point(
                    center_x + radius * math.cos(angle),
                    center_y + radius * math.sin(angle),
                )
            )
        # As with add_rectangle, consecutive edges share their corner points, so
        # the polygon is closed by construction rather than by constraints.
        lines = [
            self.add_line(points[i], points[(i + 1) % sides]) for i in range(sides)
        ]
        # Give it a dimension and an anchor so the profile is editable
        # afterwards, rather than solving as a shape with no constraints at all.
        centre = self.add_point(center_x, center_y)
        self.constrain("fix", [centre.id])
        for index in range(sides):
            self.constrain("distance", [centre.id, points[index].id], radius)
        for index in range(sides):
            self.constrain(
                "equal", [lines[index].id, lines[(index + 1) % sides].id]
            )
        return lines

    def add_slot(self, x1: float, y1: float, x2: float, y2: float,
                 radius: float) -> dict:
        """A slot: two parallel sides capped by arcs."""
        first = self.add_point(x1, y1)
        second = self.add_point(x2, y2)
        centre_line = self.add_line(first, second, construction=True)
        left = self.add_circle(first, radius)
        right = self.add_circle(second, radius)
        self.constrain("equal", [left.id, right.id])
        return {"axis": centre_line, "ends": (left, right)}

    def constrain(self, kind: str, refs, value: float = 0.0) -> Constraint:
        constraint = make(kind, refs, value)
        constraint.id = f"k{len(self.constraints) + 1}"
        self.constraints.append(constraint)
        if kind == "fix":
            point = self.points[refs[0]]
            self.fixed_targets[point.id] = (point.x, point.y)
            point.fixed = True
        return constraint

    def remove_constraint(self, constraint_id: str) -> None:
        self.constraints = [c for c in self.constraints if c.id != constraint_id]

    # -- parameter vector -------------------------------------------------
    def parameter_layout(self) -> list[tuple[str, str]]:
        """``(owner_id, field)`` for every free parameter, in vector order."""
        layout: list[tuple[str, str]] = []
        for point in self.points.values():
            layout.append((point.id, "x"))
            layout.append((point.id, "y"))
        for entity in self.entities.values():
            if entity.kind in ("circle", "arc"):
                layout.append((entity.id, "radius"))
            if entity.kind == "arc":
                layout.append((entity.id, "start_angle"))
                layout.append((entity.id, "end_angle"))
            if entity.kind == "ellipse":
                layout.append((entity.id, "major"))
                layout.append((entity.id, "minor"))
                layout.append((entity.id, "rotation"))
            # A spline carries no parameters of its own: its shape is entirely
            # in its control points, which are already in the vector.
        return layout

    def to_vector(self) -> list[float]:
        values = []
        for owner, field_name in self.parameter_layout():
            source = self.points.get(owner) or self.entities[owner]
            values.append(getattr(source, field_name))
        return values

    def from_vector(self, values) -> None:
        for (owner, field_name), value in zip(self.parameter_layout(), values):
            target = self.points.get(owner) or self.entities[owner]
            setattr(target, field_name, float(value))

    def point_coords(self, point_id: str, values) -> tuple[float, float]:
        index = self._index_of(point_id, "x")
        return (values[index], values[index + 1])

    def radius_of(self, entity_id: str, values) -> float:
        return values[self._index_of(entity_id, "radius")]

    def _index_of(self, owner: str, field_name: str) -> int:
        if not hasattr(self, "_layout_cache") or self._layout_dirty():
            self._layout_cache = {
                key: index for index, key in enumerate(self.parameter_layout())
            }
            self._layout_signature = (len(self.points), len(self.entities))
        return self._layout_cache[(owner, field_name)]

    def _layout_dirty(self) -> bool:
        return getattr(self, "_layout_signature", None) != (
            len(self.points), len(self.entities)
        )

    # -- output ------------------------------------------------------------
    def profile_entities(self) -> list:
        """Entities that contribute to the shape, i.e. not construction."""
        return [e for e in self.entities.values() if not e.construction]

    # -- serialisation -----------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "plane": self.plane.to_dict(),
            "points": [p.to_dict() for p in self.points.values()],
            "entities": [e.to_dict() for e in self.entities.values()],
            "constraints": [c.to_dict() for c in self.constraints],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Sketch":
        sketch = cls(data.get("name", "Sketch"),
                     SketchPlane.from_dict(data.get("plane", {})))
        for entry in data.get("points", []):
            point = Point.from_dict(entry)
            reserve_id(point.id)
            sketch.points[point.id] = point
            if point.fixed:
                sketch.fixed_targets[point.id] = (point.x, point.y)
        for entry in data.get("entities", []):
            entity = entity_from_dict(entry)
            reserve_id(entity.id)
            sketch.entities[entity.id] = entity
        for entry in data.get("constraints", []):
            sketch.constraints.append(constraint_from_dict(entry))
        return sketch
