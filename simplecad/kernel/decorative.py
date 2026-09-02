"""Decorative silhouette primitives: simple profiles extruded into solids."""

from __future__ import annotations

import math

from ..core.document import BuildContext, register
from ..core.errors import CadError, guard
from ..core.units import Dimension
from .occ import built_shape, make_transform, transformed, unify
from .primitives import _Primitive


def _normalise(points, size: float):
    low_x = min(p[0] for p in points)
    high_x = max(p[0] for p in points)
    low_y = min(p[1] for p in points)
    high_y = max(p[1] for p in points)
    scale = size / max(high_x - low_x, high_y - low_y)
    centre_x = (low_x + high_x) / 2.0
    centre_y = (low_y + high_y) / 2.0
    return [((x - centre_x) * scale, (y - centre_y) * scale) for x, y in points]


def _prism(points, height: float, operation: str):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Pnt, gp_Vec

    polygon = BRepBuilderAPI_MakePolygon()
    for x, y in points:
        polygon.Add(gp_Pnt(x, y, 0.0))
    polygon.Close()
    face = BRepBuilderAPI_MakeFace(polygon.Wire()).Face()
    return built_shape(BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, height)), operation)


class _Decorative(_Primitive):
    dimensions = {**_Primitive.dimensions, "ratio": Dimension.SCALAR}

    def _size_height(self, ctx: BuildContext) -> tuple[float, float]:
        return (
            self._positive(ctx.value(self, "size", 30.0), "size"),
            self._positive(ctx.value(self, "height", 10.0), "height"),
        )


@register("star")
class StarFeature(_Decorative):
    label = "Star"
    dimensions = {**_Decorative.dimensions, "inner_ratio": Dimension.SCALAR}

    def execute(self, ctx: BuildContext) -> dict:
        size, height = self._size_height(ctx)
        ratio = ctx.value(self, "inner_ratio", 0.45)
        if not 0.05 < ratio < 0.95:
            raise CadError(
                "The star inset must be between 0.05 and 0.95.",
                suggestion="Try 0.45 for a classic five-point star.",
            )
        points = []
        for index in range(10):
            angle = math.pi / 2.0 + index * math.pi / 5.0
            radius = 1.0 if index % 2 == 0 else ratio
            points.append((radius * math.cos(angle), radius * math.sin(angle)))
        with guard("star"):
            return self._finish(ctx, _prism(_normalise(points, size), height, "star"))


@register("heart")
class HeartFeature(_Decorative):
    label = "Heart"

    def execute(self, ctx: BuildContext) -> dict:
        size, height = self._size_height(ctx)
        # A dense sample of the standard analytic heart curve gives a smooth
        # printable outline without relying on a display-only SVG path.
        points = []
        for index in range(128):
            angle = 2.0 * math.pi * index / 128.0
            x = 16.0 * math.sin(angle) ** 3
            y = (
                13.0 * math.cos(angle)
                - 5.0 * math.cos(2.0 * angle)
                - 2.0 * math.cos(3.0 * angle)
                - math.cos(4.0 * angle)
            )
            points.append((x, y))
        with guard("heart"):
            return self._finish(ctx, _prism(_normalise(points, size), height, "heart"))


@register("cross")
class CrossFeature(_Decorative):
    label = "Cross"

    def execute(self, ctx: BuildContext) -> dict:
        size, height = self._size_height(ctx)
        arm = ctx.value(self, "arm_width", size / 3.0)
        if arm <= 0 or arm >= size:
            raise CadError(
                "The cross arm width must be smaller than its size.",
                suggestion=f"Use an arm width between 0 and {size:.2f} mm.",
            )
        half, narrow = size / 2.0, arm / 2.0
        points = [
            (-narrow, half), (narrow, half), (narrow, narrow),
            (half, narrow), (half, -narrow), (narrow, -narrow),
            (narrow, -half), (-narrow, -half), (-narrow, -narrow),
            (-half, -narrow), (-half, narrow), (-narrow, narrow),
        ]
        with guard("cross"):
            return self._finish(ctx, _prism(points, height, "cross"))


@register("crescent")
class CrescentFeature(_Decorative):
    label = "Crescent"

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

        size, height = self._size_height(ctx)
        thickness = ctx.value(self, "thickness", size * 0.27)
        if thickness <= 0 or thickness >= size:
            raise CadError(
                "The crescent thickness must be smaller than its size.",
                suggestion=f"Use a thickness between 0 and {size:.2f} mm.",
            )
        radius = size / 2.0
        with guard("crescent"):
            outer = BRepPrimAPI_MakeCylinder(radius, height).Shape()
            cutter = BRepPrimAPI_MakeCylinder(radius, height * 3.0).Shape()
            cutter = transformed(
                cutter,
                make_transform(translate=(thickness, 0.0, -height)),
            )
            crescent = unify(built_shape(BRepAlgoAPI_Cut(outer, cutter), "crescent"))
            return self._finish(ctx, crescent)


@register("lightning")
class LightningFeature(_Decorative):
    label = "Lightning"

    def execute(self, ctx: BuildContext) -> dict:
        size, height = self._size_height(ctx)
        points = [
            (0.20, 1.0), (-0.55, 0.05), (-0.12, 0.05),
            (-0.42, -1.0), (0.62, -0.02), (0.18, -0.02),
        ]
        with guard("lightning"):
            return self._finish(
                ctx, _prism(_normalise(points, size), height, "lightning")
            )
