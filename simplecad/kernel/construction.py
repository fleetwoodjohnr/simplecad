"""Construction geometry: planes, axes and points that guide other features.

None of these produce material. They exist so features can be placed where
there is no face to place them on -- a plane 20 mm above the top of a part, an
axis through two holes, a plane halfway between two others.

Each is a feature, so a construction plane moves when the geometry it was
derived from moves, and a sketch drawn on it follows.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..core.document import BuildContext, Feature, register
from ..core.errors import CadError
from ..core.units import Dimension
from ..sketch.sketch import SketchPlane


def _unit(vector) -> tuple[float, float, float]:
    length = math.sqrt(sum(v * v for v in vector))
    if length < 1e-12:
        raise CadError("That geometry has no usable direction.")
    return tuple(v / length for v in vector)


def _cross(a, b) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _perpendicular_to(normal) -> tuple[float, float, float]:
    """Any unit vector perpendicular to *normal*, for a plane's X axis."""
    reference = (1.0, 0.0, 0.0) if abs(normal[0]) < 0.9 else (0.0, 1.0, 0.0)
    return _unit(_cross(normal, reference))


@dataclass(frozen=True)
class ConstructionPlane:
    """A plane other features can be built on."""

    name: str
    origin: tuple[float, float, float]
    normal: tuple[float, float, float]
    x_axis: tuple[float, float, float]

    def as_sketch_plane(self) -> SketchPlane:
        return SketchPlane(self.origin, self.x_axis, self.normal)

    def describe(self) -> str:
        return (
            f"plane at {self.origin[0]:.1f}, {self.origin[1]:.1f}, "
            f"{self.origin[2]:.1f}"
        )


@dataclass(frozen=True)
class ConstructionAxis:
    name: str
    origin: tuple[float, float, float]
    direction: tuple[float, float, float]


@dataclass(frozen=True)
class ConstructionPoint:
    name: str
    position: tuple[float, float, float]


class _Construction(Feature):
    """Publishes an internal result rather than a body."""

    def _emit(self, value) -> dict:
        name = f"__construction__{self.name}"
        self.outputs = [name]
        return {name: value}


@register("offset_plane")
class OffsetPlaneFeature(_Construction):
    """A plane parallel to a face, a given distance away."""

    label = "Offset Plane"

    def execute(self, ctx: BuildContext) -> dict:
        from .detect import analyse_plane

        face = ctx.resolve(self, "face")
        info = analyse_plane(face)
        if info is None:
            raise CadError(
                "An offset plane needs a flat face to offset from.",
                suggestion="Select a planar face.",
            )
        distance = ctx.value(self, "distance", 10.0)
        origin = tuple(
            info.center[i] + info.normal[i] * distance for i in range(3)
        )
        self.message = f"{distance:g} mm from the face"
        return self._emit(
            ConstructionPlane(
                self.name, origin, info.normal, _perpendicular_to(info.normal)
            )
        )


@register("midplane")
class MidplaneFeature(_Construction):
    """The plane halfway between two parallel faces."""

    label = "Midplane"

    def execute(self, ctx: BuildContext) -> dict:
        from .detect import analyse_plane

        faces = ctx.resolve(self, "faces")
        faces = faces if isinstance(faces, list) else [faces]
        if len(faces) != 2:
            raise CadError(
                "A midplane needs two faces.",
                suggestion="Select the two faces it should sit between.",
            )
        first, second = (analyse_plane(f) for f in faces)
        if first is None or second is None:
            raise CadError("A midplane needs two flat faces.")
        origin = tuple(
            (first.center[i] + second.center[i]) / 2.0 for i in range(3)
        )
        # The two normals oppose each other on facing surfaces, so pick one.
        normal = _unit(first.normal)
        gap = math.dist(first.center, second.center)
        self.message = f"halfway across {gap:.2f} mm"
        return self._emit(
            ConstructionPlane(self.name, origin, normal, _perpendicular_to(normal))
        )


@register("plane_at_angle")
class PlaneAtAngleFeature(_Construction):
    """A plane rotated about an edge, away from a face."""

    label = "Plane at Angle"
    dimensions = {"angle": Dimension.ANGLE}

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

        from .detect import analyse_plane

        face = ctx.resolve(self, "face")
        edge = ctx.resolve(self, "edge")
        info = analyse_plane(face)
        if info is None:
            raise CadError("A plane at an angle needs a flat face to start from.")

        axis_origin, axis_direction = _edge_axis(edge)
        angle = ctx.value(self, "angle", 45.0)

        rotation = gp_Trsf()
        rotation.SetRotation(
            gp_Ax1(gp_Pnt(*axis_origin), gp_Dir(*axis_direction)),
            math.radians(angle),
        )
        normal = _rotate(info.normal, rotation)
        self.message = f"{angle:g}° from the face"
        return self._emit(
            ConstructionPlane(
                self.name, axis_origin, normal, _perpendicular_to(normal)
            )
        )


@register("plane_through_points")
class PlaneThroughPointsFeature(_Construction):
    """The plane containing three points."""

    label = "Plane through Points"

    def execute(self, ctx: BuildContext) -> dict:
        positions = self.inputs.get("points")
        if not positions:
            vertices = ctx.resolve(self, "vertices")
            vertices = vertices if isinstance(vertices, list) else [vertices]
            positions = [_vertex_position(v) for v in vertices]
        if len(positions) < 3:
            raise CadError(
                "A plane needs three points.",
                suggestion="Select three vertices.",
            )
        a, b, c = (tuple(p) for p in positions[:3])
        normal = _cross(
            tuple(b[i] - a[i] for i in range(3)),
            tuple(c[i] - a[i] for i in range(3)),
        )
        if math.sqrt(sum(v * v for v in normal)) < 1e-9:
            raise CadError(
                "Those three points are in a line.",
                suggestion="Pick points that are not collinear.",
            )
        normal = _unit(normal)
        return self._emit(
            ConstructionPlane(self.name, a, normal, _unit(
                tuple(b[i] - a[i] for i in range(3))
            ))
        )


@register("tangent_plane")
class TangentPlaneFeature(_Construction):
    """A plane touching a cylinder, at a given angle around it."""

    label = "Tangent Plane"
    dimensions = {"around": Dimension.ANGLE}

    def execute(self, ctx: BuildContext) -> dict:
        from .detect import analyse_cylinder

        face = ctx.resolve(self, "face")
        info = analyse_cylinder(face)
        if info is None:
            raise CadError(
                "A tangent plane needs a round face.",
                suggestion="Select a cylindrical face.",
            )
        around = math.radians(ctx.value(self, "around", 0.0))
        axis = _unit(info.direction)
        reference = _unit(_cross(axis, _perpendicular_to(axis)))
        second = _unit(_cross(axis, reference))
        outward = tuple(
            reference[i] * math.cos(around) + second[i] * math.sin(around)
            for i in range(3)
        )
        centre = tuple(
            info.origin[i] + axis[i] * info.length / 2.0 for i in range(3)
        )
        origin = tuple(centre[i] + outward[i] * info.radius for i in range(3))
        self.message = f"tangent to ⌀{info.diameter:.2f}"
        return self._emit(
            ConstructionPlane(self.name, origin, outward, axis)
        )


@register("construction_axis")
class AxisFeature(_Construction):
    """An axis: down a cylinder, along an edge, or through two points."""

    label = "Axis"

    def execute(self, ctx: BuildContext) -> dict:
        from .detect import analyse_cylinder

        face = self.inputs.get("face")
        if face is not None:
            info = analyse_cylinder(ctx.resolve(self, "face"))
            if info is None:
                raise CadError("That face has no axis. Select a round face.")
            self.message = f"axis of ⌀{info.diameter:.2f}"
            return self._emit(
                ConstructionAxis(self.name, info.origin, _unit(info.direction))
            )

        edge = self.inputs.get("edge")
        if edge is not None:
            origin, direction = _edge_axis(ctx.resolve(self, "edge"))
            return self._emit(ConstructionAxis(self.name, origin, direction))

        raise CadError(
            "An axis needs a round face or a straight edge.",
            suggestion="Select one, then try again.",
        )


@register("construction_point")
class PointFeature(_Construction):
    """A point: at a coordinate, or at the centre of a round face."""

    label = "Point"

    def execute(self, ctx: BuildContext) -> dict:
        from .detect import analyse_cylinder, analyse_plane

        if self.inputs.get("face") is not None:
            face = ctx.resolve(self, "face")
            cylinder = analyse_cylinder(face)
            if cylinder is not None:
                axis = _unit(cylinder.direction)
                position = tuple(
                    cylinder.origin[i] + axis[i] * cylinder.length / 2.0
                    for i in range(3)
                )
            else:
                plane = analyse_plane(face)
                if plane is None:
                    raise CadError("That face has no centre to mark.")
                position = plane.center
        else:
            position = (
                ctx.value(self, "x", 0.0),
                ctx.value(self, "y", 0.0),
                ctx.value(self, "z", 0.0),
            )
        self.message = f"{position[0]:.2f}, {position[1]:.2f}, {position[2]:.2f}"
        return self._emit(ConstructionPoint(self.name, tuple(position)))


# ----------------------------------------------------------------------
def _edge_axis(edge):
    """Origin and unit direction of a straight edge."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Line
    from OCP.TopoDS import TopoDS

    adaptor = BRepAdaptor_Curve(TopoDS.Edge_s(edge))
    if adaptor.GetType() != GeomAbs_Line:
        raise CadError(
            "That edge is curved.",
            suggestion="Select a straight edge.",
        )
    line = adaptor.Line()
    origin, direction = line.Location(), line.Direction()
    return (
        (origin.X(), origin.Y(), origin.Z()),
        (direction.X(), direction.Y(), direction.Z()),
    )


def _vertex_position(vertex):
    from OCP.BRep import BRep_Tool
    from OCP.TopoDS import TopoDS

    point = BRep_Tool.Pnt_s(TopoDS.Vertex_s(vertex))
    return (point.X(), point.Y(), point.Z())


def _rotate(vector, transform):
    from OCP.gp import gp_Vec

    rotated = gp_Vec(*vector).Transformed(transform)
    return (rotated.X(), rotated.Y(), rotated.Z())


def plane_from_face(face, name: str = "Face") -> ConstructionPlane:
    """A sketch plane lying on an existing planar face.

    This is what makes "sketch on that face" work: the face's own frame becomes
    the sketch's, so drawing on it lands where the user expects.
    """
    from .detect import analyse_plane

    info = analyse_plane(face)
    if info is None:
        raise CadError(
            "Sketches go on flat faces.",
            suggestion="Select a planar face, or use a base plane.",
        )
    return ConstructionPlane(
        name, info.center, info.normal, _perpendicular_to(info.normal)
    )
