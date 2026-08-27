"""Sketch geometry.

Everything reduces to **points plus scalars**, and every entity is a view onto a
slice of one flat parameter vector. That is what lets the solver treat a sketch
as a plain system of equations: it never needs to know what a slot or a polygon
is, only which numbers it may move.

Coordinates are 2D, in the sketch plane's own frame. Placing that plane in 3D is
the sketch's job, not the geometry's.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field


_COUNTER = itertools.count(1)


def _new_id(prefix: str) -> str:
    return f"{prefix}{next(_COUNTER)}"


def reserve_id(identifier: str) -> None:
    """Make sure future ids come after *identifier*.

    The counter is module-global and restarts at 1 in every process, while a
    reopened sketch brings its old ids with it. Without this, the first point
    added after opening a saved file is issued an id that already exists and
    silently overwrites the geometry that had it -- taking its constraints with
    it. Called for every id restored from a file.
    """
    global _COUNTER

    digits = "".join(c for c in identifier if c.isdigit())
    if not digits:
        return
    value = int(digits)
    # itertools.count has no introspection, so step it forward instead.
    peeked = next(_COUNTER)
    _COUNTER = itertools.count(max(peeked, value + 1))


@dataclass
class Point:
    """A 2D point. Two parameters, unless fixed."""

    id: str = field(default_factory=lambda: _new_id("p"))
    x: float = 0.0
    y: float = 0.0
    fixed: bool = False

    def to_dict(self) -> dict:
        return {"id": self.id, "x": self.x, "y": self.y, "fixed": self.fixed}

    @classmethod
    def from_dict(cls, data: dict) -> "Point":
        return cls(data["id"], data["x"], data["y"], data.get("fixed", False))


@dataclass
class Line:
    """A straight segment between two points."""

    start: str
    end: str
    id: str = field(default_factory=lambda: _new_id("l"))
    construction: bool = False

    kind = "line"

    def to_dict(self) -> dict:
        return {
            "kind": "line", "id": self.id, "start": self.start,
            "end": self.end, "construction": self.construction,
        }


@dataclass
class Circle:
    """A full circle: a centre point and a radius parameter."""

    center: str
    radius: float = 10.0
    id: str = field(default_factory=lambda: _new_id("c"))
    construction: bool = False

    kind = "circle"

    def to_dict(self) -> dict:
        return {
            "kind": "circle", "id": self.id, "center": self.center,
            "radius": self.radius, "construction": self.construction,
        }


@dataclass
class Arc:
    """A circular arc, counter-clockwise from *start_angle* to *end_angle*."""

    center: str
    radius: float = 10.0
    start_angle: float = 0.0
    end_angle: float = math.pi / 2
    id: str = field(default_factory=lambda: _new_id("a"))
    construction: bool = False

    kind = "arc"

    def to_dict(self) -> dict:
        return {
            "kind": "arc", "id": self.id, "center": self.center,
            "radius": self.radius, "start_angle": self.start_angle,
            "end_angle": self.end_angle, "construction": self.construction,
        }


@dataclass
class Ellipse:
    """An ellipse: a centre, two radii, and a rotation of the major axis."""

    center: str
    major: float = 15.0
    minor: float = 8.0
    rotation: float = 0.0            # radians, major axis from +X
    id: str = field(default_factory=lambda: _new_id("e"))
    construction: bool = False

    kind = "ellipse"

    def to_dict(self) -> dict:
        return {
            "kind": "ellipse", "id": self.id, "center": self.center,
            "major": self.major, "minor": self.minor,
            "rotation": self.rotation, "construction": self.construction,
        }


@dataclass
class Spline:
    """A B-spline through a run of sketch points.

    Stored as point references rather than raw coordinates, so the control
    points are ordinary sketch points: they can be constrained, snapped to and
    dragged like anything else.
    """

    points: list[str] = field(default_factory=list)
    periodic: bool = False
    id: str = field(default_factory=lambda: _new_id("s"))
    construction: bool = False

    kind = "spline"

    def to_dict(self) -> dict:
        return {
            "kind": "spline", "id": self.id, "points": list(self.points),
            "periodic": self.periodic, "construction": self.construction,
        }


def entity_from_dict(data: dict):
    kind = data["kind"]
    if kind == "line":
        return Line(data["start"], data["end"], data["id"],
                    data.get("construction", False))
    if kind == "circle":
        return Circle(data["center"], data["radius"], data["id"],
                      data.get("construction", False))
    if kind == "arc":
        return Arc(data["center"], data["radius"], data["start_angle"],
                   data["end_angle"], data["id"], data.get("construction", False))
    if kind == "ellipse":
        return Ellipse(data["center"], data["major"], data["minor"],
                       data.get("rotation", 0.0), data["id"],
                       data.get("construction", False))
    if kind == "spline":
        return Spline(list(data.get("points", [])), data.get("periodic", False),
                      data["id"], data.get("construction", False))
    raise ValueError(f"unknown sketch entity: {kind!r}")
