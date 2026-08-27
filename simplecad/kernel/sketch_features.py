"""Features that consume sketches: the sketch itself, Extrude and Revolve."""

from __future__ import annotations

import math

from ..core.document import BodyRef, BuildContext, Feature, register
from ..core.errors import CadError, guard
from ..core.units import Dimension
from ..sketch.sketch import Sketch
from ..sketch.solver import solve
from .occ import built_shape, unify


@register("sketch")
class SketchFeature(Feature):
    """Holds a 2D sketch. Produces no body of its own.

    Solved on every rebuild, so a dimension driven by a document parameter
    (``width / 2``) re-solves when that parameter changes.
    """

    label = "Sketch"

    def sketch(self) -> Sketch:
        data = self.inputs.get("sketch")
        if isinstance(data, Sketch):
            return data
        if not data:
            raise CadError("This sketch feature has no geometry.")
        restored = Sketch.from_dict(data)
        self.inputs["sketch"] = restored
        return restored

    def execute(self, ctx: BuildContext) -> dict:
        sketch = self.sketch()
        result = solve(sketch)
        self.message = result.message
        if not result.ok:
            raise CadError(result.message, suggestion="Remove or relax a constraint.")
        # A sketch is geometry other features consume, not a body in its own
        # right. The double-underscore marks it internal, so the rebuild engine
        # passes it to downstream features without listing it as a body.
        name = f"__sketch__{self.name}"
        ctx.bodies[name] = sketch
        self.outputs = [name]
        return {name: sketch}

    def to_dict(self) -> dict:
        data = super().to_dict()
        sketch = self.inputs.get("sketch")
        if isinstance(sketch, Sketch):
            data["inputs"]["sketch"] = sketch.to_dict()
        return data


class _FromSketch(Feature):
    """Shared plumbing for features built on a sketch."""

    def _sketch(self, ctx: BuildContext) -> Sketch:
        name = self.inputs.get("sketch")
        if not name:
            raise CadError(
                "This feature has no sketch to work from.",
                suggestion="Select a sketch first.",
            )
        found = ctx.bodies.get(f"__sketch__{name}")
        if found is None:
            raise CadError(
                f"The sketch '{name}' is not available.",
                suggestion="It may have been deleted or renamed.",
            )
        return found

    def _emit(self, shape) -> dict:
        name = self.outputs[0] if self.outputs else self.name
        self.outputs = [name]
        return {name: shape}


@register("extrude")
class ExtrudeFeature(_FromSketch):
    """Push a sketch profile along its plane normal."""

    label = "Extrude"
    dimensions = {"taper": Dimension.ANGLE}

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
        from OCP.gp import gp_Vec

        from ..sketch.to_occ import profile_face

        sketch = self._sketch(ctx)
        distance = ctx.value(self, "distance", 10.0)
        if abs(distance) < 1e-9:
            raise CadError("An extrude needs a distance greater than zero.")

        symmetric = bool(self.inputs.get("symmetric", False))
        normal = sketch.plane.normal
        with guard("extrude"):
            face = profile_face(sketch)
            if symmetric:
                from .occ import make_transform, transformed

                face = transformed(
                    face,
                    make_transform(
                        translate=tuple(-n * distance / 2.0 for n in normal)
                    ),
                )
                from OCP.TopoDS import TopoDS

                face = TopoDS.Face_s(face)
            direction = gp_Vec(*normal) * distance
            solid = built_shape(BRepPrimAPI_MakePrism(face, direction), "extrude")
            return self._emit(unify(solid))


@register("revolve")
class RevolveFeature(_FromSketch):
    """Spin a sketch profile about an axis in its plane."""

    label = "Revolve"
    dimensions = {"angle": Dimension.ANGLE}

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
        from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt

        from ..sketch.to_occ import profile_face

        sketch = self._sketch(ctx)
        angle = ctx.value(self, "angle", 360.0)
        if abs(angle) < 1e-9:
            raise CadError("A revolve needs an angle greater than zero.")

        origin = self.inputs.get("axis_origin") or sketch.plane.origin
        direction = self.inputs.get("axis_direction") or sketch.plane.x_axis
        with guard("revolve"):
            face = profile_face(sketch)
            axis = gp_Ax1(gp_Pnt(*origin), gp_Dir(*direction))
            solid = built_shape(
                BRepPrimAPI_MakeRevol(face, axis, math.radians(angle)), "revolve"
            )
            return self._emit(unify(solid))
