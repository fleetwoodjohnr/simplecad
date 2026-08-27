"""Primitive solids, as features.

These are the fastest route to geometry in SimpleCAD: no sketch required, the
user drops a box and drags its faces. Each one is a feature with expression
inputs, so a box created by dragging is still fully parametric afterwards.
"""

from __future__ import annotations

import math

from ..core.document import BuildContext, Feature, register
from ..core.errors import guard
from ..core.units import Dimension
from .occ import built_shape, make_transform, transformed


class _Primitive(Feature):
    """Shared placement handling: every primitive can be positioned and rotated."""

    dimensions = {"rx": Dimension.ANGLE, "ry": Dimension.ANGLE, "rz": Dimension.ANGLE}

    def _placement(self, ctx: BuildContext):
        position = (
            ctx.value(self, "x", 0.0),
            ctx.value(self, "y", 0.0),
            ctx.value(self, "z", 0.0),
        )
        transform = make_transform(translate=position)
        for axis, key in (((1, 0, 0), "rx"), ((0, 1, 0), "ry"), ((0, 0, 1), "rz")):
            angle = ctx.value(self, key, 0.0)
            if abs(angle) > 1e-12:
                transform = transform.Multiplied(
                    make_transform(rotate_axis=axis, rotate_degrees=angle)
                )
        return transform

    def _finish(self, ctx: BuildContext, shape):
        placed = transformed(shape, self._placement(ctx))
        name = self.outputs[0] if self.outputs else self.name
        self.outputs = [name]
        return {name: placed}

    @staticmethod
    def _positive(value: float, what: str) -> float:
        from ..core.errors import CadError

        if value <= 0:
            raise CadError(
                f"The {what} must be greater than zero.",
                suggestion=f"Enter a positive {what}.",
            )
        return value


@register("box")
class BoxFeature(_Primitive):
    label = "Box"

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

        width = self._positive(ctx.value(self, "width", 40.0), "width")
        depth = self._positive(ctx.value(self, "depth", 30.0), "depth")
        height = self._positive(ctx.value(self, "height", 20.0), "height")
        with guard("box"):
            builder = BRepPrimAPI_MakeBox(width, depth, height)
            return self._finish(ctx, built_shape(builder, "box"))


@register("cylinder")
class CylinderFeature(_Primitive):
    label = "Cylinder"

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

        radius = self._positive(ctx.value(self, "radius", 12.0), "radius")
        height = self._positive(ctx.value(self, "height", 30.0), "height")
        with guard("cylinder"):
            builder = BRepPrimAPI_MakeCylinder(radius, height)
            return self._finish(ctx, built_shape(builder, "cylinder"))


@register("sphere")
class SphereFeature(_Primitive):
    label = "Sphere"

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere

        radius = self._positive(ctx.value(self, "radius", 15.0), "radius")
        with guard("sphere"):
            builder = BRepPrimAPI_MakeSphere(radius)
            return self._finish(ctx, built_shape(builder, "sphere"))


@register("cone")
class ConeFeature(_Primitive):
    label = "Cone"

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone
        from ..core.errors import CadError

        bottom = ctx.value(self, "bottom_radius", 15.0)
        top = ctx.value(self, "top_radius", 0.0)
        height = self._positive(ctx.value(self, "height", 30.0), "height")
        if bottom < 0 or top < 0 or (bottom == 0 and top == 0):
            raise CadError(
                "A cone needs at least one radius greater than zero.",
                suggestion="Set the bottom or top radius above zero.",
            )
        with guard("cone"):
            builder = BRepPrimAPI_MakeCone(bottom, top, height)
            return self._finish(ctx, built_shape(builder, "cone"))


@register("torus")
class TorusFeature(_Primitive):
    label = "Torus"

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeTorus
        from ..core.errors import CadError

        major = self._positive(ctx.value(self, "major_radius", 25.0), "ring radius")
        minor = self._positive(ctx.value(self, "minor_radius", 6.0), "tube radius")
        if minor >= major:
            raise CadError(
                "The tube is too thick for this ring radius.",
                suggestion=f"Use a tube radius below {major:.2f} mm.",
            )
        with guard("torus"):
            builder = BRepPrimAPI_MakeTorus(major, minor)
            return self._finish(ctx, built_shape(builder, "torus"))


@register("tube")
class TubeFeature(_Primitive):
    """A pipe: a cylinder with a concentric bore."""

    label = "Tube"

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from ..core.errors import CadError

        outer = self._positive(ctx.value(self, "outer_radius", 15.0), "outer radius")
        inner = self._positive(ctx.value(self, "inner_radius", 10.0), "inner radius")
        height = self._positive(ctx.value(self, "height", 30.0), "height")
        if inner >= outer:
            raise CadError(
                "The bore is as wide as the tube.",
                suggestion=f"Use an inner radius below {outer:.2f} mm.",
            )
        with guard("tube"):
            solid = BRepPrimAPI_MakeCylinder(outer, height).Shape()
            # Overshoot the bore so the cut leaves clean end faces.
            bore = BRepPrimAPI_MakeCylinder(inner, height * 3).Shape()
            bore = transformed(bore, make_transform(translate=(0, 0, -height)))
            cut = BRepAlgoAPI_Cut(solid, bore)
            return self._finish(ctx, built_shape(cut, "tube"))


@register("wedge")
class WedgeFeature(_Primitive):
    label = "Wedge"

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeWedge

        width = self._positive(ctx.value(self, "width", 40.0), "width")
        depth = self._positive(ctx.value(self, "depth", 30.0), "depth")
        height = self._positive(ctx.value(self, "height", 20.0), "height")
        top = ctx.value(self, "top_width", 0.0)
        with guard("wedge"):
            builder = BRepPrimAPI_MakeWedge(width, height, depth, max(0.0, top))
            return self._finish(ctx, built_shape(builder, "wedge"))


@register("polygon_prism")
class PolygonPrismFeature(_Primitive):
    """An N-sided prism. Hexagonal by default -- the common case for nuts."""

    label = "Polygon"

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepBuilderAPI import (
            BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace,
            BRepBuilderAPI_MakePolygon,
        )
        from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
        from OCP.gp import gp_Pnt, gp_Vec
        from ..core.errors import CadError

        sides = int(ctx.value(self, "sides", 6))
        height = self._positive(ctx.value(self, "height", 20.0), "height")
        across_flats = ctx.value(self, "across_flats", 0.0)
        radius = ctx.value(self, "radius", 15.0)
        if sides < 3:
            raise CadError(
                "A polygon needs at least three sides.",
                suggestion="Try 6 for a hexagon.",
            )
        across_corners = ctx.value(self, "across_corners", 0.0)
        if across_flats > 0:
            # Across-flats is how fasteners are actually specified.
            radius = across_flats / (2.0 * math.cos(math.pi / sides))
        elif across_corners > 0:
            # The circle the corners sit on. Odd-sided polygons have neither
            # opposite flats nor opposite corners, so this is the only size that
            # means one thing for every side count.
            radius = across_corners / 2.0
        radius = self._positive(radius, "size")

        with guard("polygon"):
            polygon = BRepBuilderAPI_MakePolygon()
            for index in range(sides):
                angle = 2.0 * math.pi * index / sides
                polygon.Add(
                    gp_Pnt(radius * math.cos(angle), radius * math.sin(angle), 0.0)
                )
            polygon.Close()
            face = BRepBuilderAPI_MakeFace(polygon.Wire()).Face()
            builder = BRepPrimAPI_MakePrism(face, gp_Vec(0.0, 0.0, height))
            return self._finish(ctx, built_shape(builder, "polygon"))


#: Convenience map for the UI's primitive palette.
PRIMITIVES = {
    "box": BoxFeature,
    "cylinder": CylinderFeature,
    "sphere": SphereFeature,
    "cone": ConeFeature,
    "torus": TorusFeature,
    "tube": TubeFeature,
    "wedge": WedgeFeature,
    "polygon_prism": PolygonPrismFeature,
}
