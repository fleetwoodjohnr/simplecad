"""Sweep, Loft, Draft, Mirror and the pattern features.

These all share a shape: take geometry that already exists, produce more of it.
Patterns in particular are worth doing properly -- a circular pattern of eight
bolt holes is a single parametric feature, so changing the count or the circle
diameter moves all eight, rather than leaving the user to place them by hand.
"""

from __future__ import annotations

import math

from ..core.document import BodyRef, BuildContext, Feature, register
from ..core.errors import CadError, guard
from ..core.units import Dimension
from .occ import built_shape, make_transform, transformed, unify


def _fuse_all(shapes, operation: str):
    """Union a list of shapes into one."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

    result = shapes[0]
    for other in shapes[1:]:
        result = built_shape(BRepAlgoAPI_Fuse(result, other), operation)
    return unify(result)


class _BodyOperation(Feature):
    """An operation that reads one body and writes it back."""

    def _target_name(self) -> str:
        name = self.inputs.get("body")
        if not name:
            raise CadError("This feature has no body to work on.")
        return str(name)

    def _emit(self, shape) -> dict:
        name = self._target_name()
        self.outputs = [name]
        return {name: shape}


# ----------------------------------------------------------------------
# Sweeping and lofting
# ----------------------------------------------------------------------
@register("sweep")
class SweepFeature(Feature):
    """Drag a profile along a path, both given as sketches."""

    label = "Sweep"

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell

        from ..sketch.to_occ import profile_face, wires

        profile_sketch = ctx.bodies.get(f"__sketch__{self.inputs.get('profile')}")
        path_sketch = ctx.bodies.get(f"__sketch__{self.inputs.get('path')}")
        if profile_sketch is None or path_sketch is None:
            raise CadError(
                "A sweep needs a profile sketch and a path sketch.",
                suggestion="Create both, then select them here.",
            )

        path_wires = wires(path_sketch) or _open_wires(path_sketch)
        if not path_wires:
            raise CadError(
                "The path sketch has no usable curve.",
                suggestion="Draw a line, arc or spline for the sweep to follow.",
            )

        with guard("sweep"):
            face = profile_face(profile_sketch)
            outer = _outer_wire(face)
            shell = BRepOffsetAPI_MakePipeShell(path_wires[0])
            if self.inputs.get("keep_orientation"):
                from OCP.gp import gp_Dir

                shell.SetMode(gp_Dir(*profile_sketch.plane.normal))
            shell.Add(outer, bool(self.inputs.get("contact", True)), True)
            shell.Build()
            if not shell.IsDone():
                raise CadError(
                    "The profile could not be swept along that path.",
                    suggestion="Try a gentler path, or a smaller profile.",
                )
            shell.MakeSolid()
            name = self.outputs[0] if self.outputs else self.name
            self.outputs = [name]
            return {name: unify(shell.Shape())}


def _open_wires(sketch) -> list:
    """Wires from a sketch whose geometry does not close."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire
    from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_HSequenceOfShape

    from ..sketch.to_occ import entity_edges

    edges = []
    for entity in sketch.profile_entities():
        edges.extend(entity_edges(sketch, entity))
    if not edges:
        return []
    sequence = TopTools_HSequenceOfShape()
    for edge in edges:
        sequence.Append(edge)
    closed = TopTools_HSequenceOfShape()
    open_chains = TopTools_HSequenceOfShape()
    ShapeAnalysis_FreeBounds.ConnectEdgesToWires_s(sequence, 1e-6, False, closed)
    if closed.Length():
        return [TopoDS.Wire_s(closed.Value(1))]
    builder = BRepBuilderAPI_MakeWire()
    for edge in edges:
        builder.Add(edge)
    return [builder.Wire()] if builder.IsDone() else []


def _outer_wire(face):
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS

    return TopoDS.Wire_s(BRepTools.OuterWire_s(TopoDS.Face_s(face)))


@register("loft")
class LoftFeature(Feature):
    """Blend between two or more sketch profiles."""

    label = "Loft"

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections

        from ..sketch.to_occ import profile_face

        names = self.inputs.get("sketches") or []
        if len(names) < 2:
            raise CadError(
                "A loft needs at least two profiles.",
                suggestion="Select two or more sketches.",
            )
        sketches = []
        for name in names:
            found = ctx.bodies.get(f"__sketch__{name}")
            if found is None:
                raise CadError(f"The sketch '{name}' is not available.")
            sketches.append(found)

        with guard("loft"):
            builder = BRepOffsetAPI_ThruSections(True, bool(self.inputs.get("ruled", False)))
            for sketch in sketches:
                builder.AddWire(_outer_wire(profile_face(sketch)))
            builder.Build()
            if not builder.IsDone():
                raise CadError(
                    "These profiles could not be blended.",
                    suggestion="Check they are all closed and in a sensible order.",
                )
            name = self.outputs[0] if self.outputs else self.name
            self.outputs = [name]
            return {name: unify(builder.Shape())}


@register("draft")
class DraftFeature(_BodyOperation):
    """Taper faces so a part releases from a mould -- or prints cleanly."""

    label = "Draft"
    dimensions = {"angle": Dimension.ANGLE}

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepOffsetAPI import BRepOffsetAPI_DraftAngle
        from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
        from OCP.TopoDS import TopoDS

        from .detect import analyse_plane

        body = ctx.shape(self, "body")
        angle = ctx.value(self, "angle", 3.0)
        if abs(angle) < 1e-9:
            raise CadError("A draft needs an angle greater than zero.")
        faces = ctx.resolve(self, "faces")
        faces = faces if isinstance(faces, list) else [faces]

        neutral = self.inputs.get("neutral_plane")
        pull = tuple(self.inputs.get("pull_direction") or (0.0, 0.0, 1.0))
        origin = tuple(neutral or (0.0, 0.0, 0.0))

        with guard("draft"):
            builder = BRepOffsetAPI_DraftAngle(body)
            plane = gp_Pln(gp_Pnt(*origin), gp_Dir(*pull))
            for face in faces:
                builder.Add(
                    TopoDS.Face_s(face), gp_Dir(*pull), math.radians(angle), plane
                )
            builder.Build()
            if not builder.IsDone():
                raise CadError(
                    "That draft angle does not fit this geometry.",
                    suggestion="Try a smaller angle, or fewer faces.",
                )
            return self._emit(unify(builder.Shape()))


# ----------------------------------------------------------------------
# Patterns
# ----------------------------------------------------------------------
class _Pattern(_BodyOperation):
    """Shared plumbing: copy a body several times and fuse the copies."""

    def _apply(self, ctx: BuildContext, transforms) -> dict:
        body = ctx.shape(self, "body")
        if not transforms:
            return self._emit(body)
        with guard(self.label.lower()):
            copies = [body] + [transformed(body, t) for t in transforms]
            return self._emit(_fuse_all(copies, self.label.lower()))


@register("mirror")
class MirrorFeature(_Pattern):
    """Reflect a body across a plane and keep both halves."""

    label = "Mirror"

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf

        normal = tuple(self.inputs.get("normal") or (1.0, 0.0, 0.0))
        origin = tuple(self.inputs.get("origin") or (0.0, 0.0, 0.0))
        transform = gp_Trsf()
        transform.SetMirror(gp_Ax2(gp_Pnt(*origin), gp_Dir(*normal)))
        return self._apply(ctx, [transform])


@register("rectangular_pattern")
class RectangularPatternFeature(_Pattern):
    label = "Rectangular Pattern"

    def execute(self, ctx: BuildContext) -> dict:
        count_x = max(1, int(ctx.value(self, "count_x", 2)))
        count_y = max(1, int(ctx.value(self, "count_y", 1)))
        spacing_x = ctx.value(self, "spacing_x", 20.0)
        spacing_y = ctx.value(self, "spacing_y", 20.0)
        direction_x = tuple(self.inputs.get("direction_x") or (1.0, 0.0, 0.0))
        direction_y = tuple(self.inputs.get("direction_y") or (0.0, 1.0, 0.0))
        if count_x * count_y > 400:
            raise CadError(
                "That pattern would make over 400 copies.",
                suggestion="Reduce the counts.",
            )

        transforms = []
        for i in range(count_x):
            for j in range(count_y):
                if i == 0 and j == 0:
                    continue
                offset = tuple(
                    direction_x[k] * spacing_x * i + direction_y[k] * spacing_y * j
                    for k in range(3)
                )
                transforms.append(make_transform(translate=offset))
        return self._apply(ctx, transforms)


@register("circular_pattern")
class CircularPatternFeature(_Pattern):
    label = "Circular Pattern"
    dimensions = {"total_angle": Dimension.ANGLE}

    def execute(self, ctx: BuildContext) -> dict:
        count = max(1, int(ctx.value(self, "count", 4)))
        total = ctx.value(self, "total_angle", 360.0)
        axis = tuple(self.inputs.get("axis") or (0.0, 0.0, 1.0))
        centre = tuple(self.inputs.get("center") or (0.0, 0.0, 0.0))
        if count > 400:
            raise CadError("That pattern would make over 400 copies.")

        # A full turn puts the last copy back on the first, so step by count;
        # a partial sweep should reach the stated angle, so step by count - 1.
        full = abs(abs(total) - 360.0) < 1e-9
        divisor = count if full else max(count - 1, 1)
        transforms = [
            make_transform(
                rotate_axis=axis, rotate_degrees=total * index / divisor, origin=centre
            )
            for index in range(1, count)
        ]
        return self._apply(ctx, transforms)


@register("path_pattern")
class PathPatternFeature(_Pattern):
    """Copies spaced evenly along a sketch path."""

    label = "Pattern Along Path"

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepAdaptor import BRepAdaptor_CompCurve
        from OCP.GCPnts import GCPnts_AbscissaPoint

        count = max(1, int(ctx.value(self, "count", 4)))
        path_sketch = ctx.bodies.get(f"__sketch__{self.inputs.get('path')}")
        if path_sketch is None:
            raise CadError(
                "This pattern needs a path sketch.",
                suggestion="Draw the path as a sketch, then select it.",
            )
        path_wires = _open_wires(path_sketch)
        if not path_wires:
            raise CadError("The path sketch has no usable curve.")

        with guard("pattern along path"):
            curve = BRepAdaptor_CompCurve(path_wires[0])
            total = GCPnts_AbscissaPoint.Length_s(curve)
            start = curve.Value(curve.FirstParameter())
            transforms = []
            for index in range(1, count):
                distance = total * index / max(count - 1, 1)
                locator = GCPnts_AbscissaPoint(curve, distance, curve.FirstParameter())
                point = curve.Value(locator.Parameter())
                transforms.append(
                    make_transform(
                        translate=(
                            point.X() - start.X(),
                            point.Y() - start.Y(),
                            point.Z() - start.Z(),
                        )
                    )
                )
            return self._apply(ctx, transforms)
