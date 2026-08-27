"""Sketch constraints, as residual equations.

Each constraint contributes one or more residuals — numbers the solver drives to
zero. Writing them this way, rather than as special-cased geometric rules, is
what makes the constraint set open-ended: adding Tangent meant adding four lines,
not teaching a solver about circles.

Residuals are scaled to be dimensionally comparable. A distance residual is in
millimetres; an angle residual is multiplied by a nominal length so that being a
degree out and being a millimetre out carry similar weight. Without that the
solver happily satisfies the distances and leaves the angles askew.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import ClassVar

#: Length used to scale dimensionless residuals into millimetre-like units.
SCALE = 10.0


@dataclass
class Constraint:
    """Base class. Subclasses implement :meth:`residuals`."""

    #: The constraint's name. A ClassVar, not a field: as a dataclass field the
    #: base's default would be baked into the generated ``__init__`` and every
    #: subclass would report "constraint" regardless of what it set.
    kind: ClassVar[str] = "constraint"

    #: Entity or point ids this constraint refers to.
    refs: tuple[str, ...] = ()
    #: Target value, for dimensional constraints.
    value: float = 0.0
    id: str = ""
    driving: bool = True

    def residuals(self, view) -> list[float]:
        raise NotImplementedError

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "refs": list(self.refs),
            "value": self.value, "id": self.id, "driving": self.driving,
        }


class _View:
    """Read-only access to the current parameter values, by id."""

    def __init__(self, sketch, values) -> None:
        self.sketch = sketch
        self.values = values

    def point(self, point_id: str) -> tuple[float, float]:
        return self.sketch.point_coords(point_id, self.values)

    def radius(self, entity_id: str) -> float:
        return self.sketch.radius_of(entity_id, self.values)

    def line(self, line_id: str):
        line = self.sketch.entities[line_id]
        return self.point(line.start), self.point(line.end)

    def direction(self, line_id: str) -> tuple[float, float]:
        (x1, y1), (x2, y2) = self.line(line_id)
        return (x2 - x1, y2 - y1)


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _cross(a, b) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _norm(a) -> float:
    return math.hypot(a[0], a[1])


# ----------------------------------------------------------------------
# Geometric constraints
# ----------------------------------------------------------------------
class Coincident(Constraint):
    kind = "coincident"

    def residuals(self, view):
        (x1, y1), (x2, y2) = view.point(self.refs[0]), view.point(self.refs[1])
        return [x1 - x2, y1 - y2]


class Horizontal(Constraint):
    kind = "horizontal"

    def residuals(self, view):
        (_x1, y1), (_x2, y2) = view.line(self.refs[0])
        return [y1 - y2]


class Vertical(Constraint):
    kind = "vertical"

    def residuals(self, view):
        (x1, _y1), (x2, _y2) = view.line(self.refs[0])
        return [x1 - x2]


class Parallel(Constraint):
    kind = "parallel"

    def residuals(self, view):
        a, b = view.direction(self.refs[0]), view.direction(self.refs[1])
        scale = max(_norm(a) * _norm(b), 1e-9)
        return [_cross(a, b) / scale * SCALE]


class Perpendicular(Constraint):
    kind = "perpendicular"

    def residuals(self, view):
        a, b = view.direction(self.refs[0]), view.direction(self.refs[1])
        scale = max(_norm(a) * _norm(b), 1e-9)
        return [_dot(a, b) / scale * SCALE]


class Equal(Constraint):
    """Equal length for two lines, or equal radius for two circles/arcs."""

    kind = "equal"

    def residuals(self, view):
        first, second = self.refs[0], self.refs[1]
        if view.sketch.entities[first].kind == "line":
            return [_norm(view.direction(first)) - _norm(view.direction(second))]
        return [view.radius(first) - view.radius(second)]


class Concentric(Constraint):
    kind = "concentric"

    def residuals(self, view):
        centres = [view.sketch.entities[r].center for r in self.refs[:2]]
        (x1, y1), (x2, y2) = view.point(centres[0]), view.point(centres[1])
        return [x1 - x2, y1 - y2]


class PointOnLine(Constraint):
    kind = "point_on_line"

    def residuals(self, view):
        point = view.point(self.refs[0])
        (x1, y1), (x2, y2) = view.line(self.refs[1])
        direction = (x2 - x1, y2 - y1)
        offset = (point[0] - x1, point[1] - y1)
        return [_cross(direction, offset) / max(_norm(direction), 1e-9)]


class Midpoint(Constraint):
    kind = "midpoint"

    def residuals(self, view):
        px, py = view.point(self.refs[0])
        (x1, y1), (x2, y2) = view.line(self.refs[1])
        return [px - (x1 + x2) / 2.0, py - (y1 + y2) / 2.0]


class Tangent(Constraint):
    """A line tangent to a circle or arc."""

    kind = "tangent"

    def residuals(self, view):
        line_id, circle_id = self.refs[0], self.refs[1]
        (x1, y1), (x2, y2) = view.line(line_id)
        centre = view.point(view.sketch.entities[circle_id].center)
        direction = (x2 - x1, y2 - y1)
        offset = (centre[0] - x1, centre[1] - y1)
        distance = abs(_cross(direction, offset)) / max(_norm(direction), 1e-9)
        return [distance - view.radius(circle_id)]


class Symmetric(Constraint):
    """Two points mirrored about a line."""

    kind = "symmetric"

    def residuals(self, view):
        first, second = view.point(self.refs[0]), view.point(self.refs[1])
        (x1, y1), (x2, y2) = view.line(self.refs[2])
        axis = (x2 - x1, y2 - y1)
        length = max(_norm(axis), 1e-9)
        unit = (axis[0] / length, axis[1] / length)
        midpoint = ((first[0] + second[0]) / 2, (first[1] + second[1]) / 2)
        # The midpoint sits on the axis, and the chord crosses it at right angles.
        to_mid = (midpoint[0] - x1, midpoint[1] - y1)
        chord = (second[0] - first[0], second[1] - first[1])
        return [_cross(unit, to_mid), _dot(unit, chord)]


class Fix(Constraint):
    """Pin a point where it is."""

    kind = "fix"

    def residuals(self, view):
        x, y = view.point(self.refs[0])
        target = view.sketch.fixed_targets.get(self.refs[0])
        if target is None:
            return [0.0, 0.0]
        return [x - target[0], y - target[1]]


# ----------------------------------------------------------------------
# Dimensional constraints
# ----------------------------------------------------------------------
class Distance(Constraint):
    kind = "distance"

    def residuals(self, view):
        (x1, y1), (x2, y2) = view.point(self.refs[0]), view.point(self.refs[1])
        return [math.hypot(x2 - x1, y2 - y1) - self.value]


class Radius(Constraint):
    kind = "radius"

    def residuals(self, view):
        return [view.radius(self.refs[0]) - self.value]


class Diameter(Constraint):
    kind = "diameter"

    def residuals(self, view):
        return [2.0 * view.radius(self.refs[0]) - self.value]


class Angle(Constraint):
    """Angle between two lines, in degrees."""

    kind = "angle"

    def residuals(self, view):
        a, b = view.direction(self.refs[0]), view.direction(self.refs[1])
        current = math.atan2(_cross(a, b), _dot(a, b))
        target = math.radians(self.value)
        # Wrap into (-pi, pi] so 359 degrees does not read as a huge error.
        error = (current - target + math.pi) % (2 * math.pi) - math.pi
        return [error * SCALE]


#: kind -> class, for deserialising.
REGISTRY = {
    cls.kind: cls
    for cls in (
        Coincident, Horizontal, Vertical, Parallel, Perpendicular, Equal,
        Concentric, PointOnLine, Midpoint, Tangent, Symmetric, Fix,
        Distance, Radius, Diameter, Angle,
    )
}


def constraint_from_dict(data: dict) -> Constraint:
    cls = REGISTRY.get(data["kind"])
    if cls is None:
        raise ValueError(f"unknown constraint: {data['kind']!r}")
    instance = cls()
    instance.refs = tuple(data.get("refs", ()))
    instance.value = float(data.get("value", 0.0))
    instance.id = data.get("id", "")
    instance.driving = data.get("driving", True)
    return instance


def make(kind: str, refs, value: float = 0.0) -> Constraint:
    """Build a constraint by name."""
    cls = REGISTRY.get(kind)
    if cls is None:
        raise ValueError(f"unknown constraint: {kind!r}")
    instance = cls()
    instance.refs = tuple(refs)
    instance.value = float(value)
    return instance
