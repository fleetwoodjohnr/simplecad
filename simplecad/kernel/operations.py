"""Modelling operations, as features.

Each one stores durable :class:`SubShapeRef` references rather than sub-shape
indices, so the faces and edges a user picked are found again after an upstream
edit. When they cannot be, the feature reports ``ReferenceLost`` and the rebuild
engine keeps the last good geometry instead of producing something wrong.
"""

from __future__ import annotations

import math

from ..core.document import BodyRef, BuildContext, Feature, register
from ..core.errors import CadError, guard
from ..core.units import Dimension
from .occ import bounding_box, built_shape, make_transform, transformed, unify


def _cast_faces(shapes) -> list:
    from OCP.TopoDS import TopoDS

    return [TopoDS.Face_s(s) for s in shapes]


def _cast_edges(shapes) -> list:
    from OCP.TopoDS import TopoDS

    return [TopoDS.Edge_s(s) for s in shapes]


def _depth_along(shape, direction) -> float:
    """How far *shape* reaches along *direction*, from its bounding box."""
    low, high = bounding_box(shape)
    return sum(abs((high[i] - low[i]) * direction[i]) for i in range(3))


def _has_solid(shape) -> bool:
    """True if *shape* contains at least one solid."""
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    if shape is None or shape.IsNull():
        return False
    return TopExp_Explorer(shape, TopAbs_SOLID).More()


class _BodyOperation(Feature):
    """An operation that reads one body and writes it back, modified."""

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
# Finishing
# ----------------------------------------------------------------------
@register("fillet")
class FilletFeature(_BodyOperation):
    label = "Fillet"

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet

        body = ctx.shape(self, "body")
        radius = ctx.value(self, "radius", 2.0)
        if radius <= 0:
            raise CadError(
                "A fillet needs a radius greater than zero.",
                suggestion="Enter a positive radius.",
            )
        edges = _cast_edges(ctx.resolve(self, "edges"))
        if not edges:
            raise CadError(
                "No edges are selected for this fillet.",
                suggestion="Select one or more edges, then set the radius.",
            )
        with guard("fillet"):
            builder = BRepFilletAPI_MakeFillet(body)
            for edge in edges:
                builder.Add(radius, edge)
            return self._emit(built_shape(builder, "fillet"))


@register("chamfer")
class ChamferFeature(_BodyOperation):
    label = "Chamfer"

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer

        body = ctx.shape(self, "body")
        distance = ctx.value(self, "distance", 1.0)
        if distance <= 0:
            raise CadError("A chamfer needs a distance greater than zero.")
        edges = _cast_edges(ctx.resolve(self, "edges"))
        if not edges:
            raise CadError(
                "No edges are selected for this chamfer.",
                suggestion="Select one or more edges.",
            )
        with guard("chamfer"):
            builder = BRepFilletAPI_MakeChamfer(body)
            for edge in edges:
                builder.Add(distance, edge)
            return self._emit(built_shape(builder, "chamfer"))


@register("shell")
class ShellFeature(_BodyOperation):
    label = "Shell"

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeThickSolid
        from OCP.TopTools import TopTools_ListOfShape

        body = ctx.shape(self, "body")
        thickness = ctx.value(self, "thickness", 2.0)
        if thickness <= 0:
            raise CadError("A shell needs a wall thickness greater than zero.")
        faces = _cast_faces(ctx.resolve(self, "faces"))
        if not faces:
            raise CadError(
                "Select the face or faces to open before shelling.",
                suggestion="Pick the face that should become the opening.",
            )
        # Opposite walls must not meet in the middle. MakeThickSolidByJoin fails
        # opaquely when they do, so say what the limit is instead: half the
        # narrowest dimension is the point at which the cavity disappears.
        low, high = bounding_box(body)
        narrowest = min(high[i] - low[i] for i in range(3))
        limit = narrowest / 2.0
        if thickness >= limit:
            raise CadError(
                f"A {thickness:.2f} mm wall is too thick to hollow this body.",
                suggestion=f"Use a wall below {limit:.2f} mm.",
            )
        with guard("shell"):
            removed = TopTools_ListOfShape()
            for face in faces:
                removed.Append(face)
            builder = BRepOffsetAPI_MakeThickSolid()
            # Negative offset hollows inward, leaving the outer surface where it is.
            builder.MakeThickSolidByJoin(body, removed, -abs(thickness), 1.0e-3)
            return self._emit(built_shape(builder, "shell"))


# ----------------------------------------------------------------------
# Direct modelling
# ----------------------------------------------------------------------
@register("push_pull")
class PushPullFeature(_BodyOperation):
    """Move a planar face along its normal, adding or removing material."""

    label = "Pull"

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeDraft
        from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.gp import gp_Vec

        from .detect import analyse_plane

        body = ctx.shape(self, "body")
        distance = ctx.value(self, "distance", 5.0)
        face = ctx.resolve(self, "face")
        info = analyse_plane(face)
        if info is None:
            raise CadError(
                "Press/Pull works on flat faces.",
                suggestion="Select a planar face, or use Move for the whole body.",
            )
        if abs(distance) < 1e-9:
            return self._emit(body)
        if distance < 0:
            # Pushing in further than the body is deep eats the whole solid.
            # OCCT reports that as a successful cut returning nothing, so the
            # check has to happen here or the user gets an empty body and no
            # explanation.
            available = _depth_along(body, info.normal)
            if abs(distance) >= available:
                raise CadError(
                    f"Pushing in {abs(distance):.2f} mm would go straight "
                    f"through this body.",
                    suggestion=f"Push in less than {available:.2f} mm.",
                )

        with guard("pull"):
            from OCP.TopoDS import TopoDS

            direction = gp_Vec(*info.normal) * distance
            prism = built_shape(
                BRepPrimAPI_MakePrism(TopoDS.Face_s(face), direction), "pull"
            )
            if distance > 0:
                result = built_shape(BRepAlgoAPI_Fuse(body, prism), "pull")
            else:
                result = built_shape(BRepAlgoAPI_Cut(body, prism), "pull")
            # A boolean can report success and still hand back a shape with no
            # solid in it. Emitting that leaves a body that looks present in the
            # tree and is not there in the model.
            if not _has_solid(result):
                raise CadError(
                    "That push would leave nothing behind.",
                    suggestion="Push in less far, or delete the body instead.",
                )
            # Merge the seam the boolean leaves between coplanar faces.
            return self._emit(unify(result))


@register("move")
class MoveFeature(_BodyOperation):
    label = "Move"
    dimensions = {"rx": Dimension.ANGLE, "ry": Dimension.ANGLE, "rz": Dimension.ANGLE}

    def execute(self, ctx: BuildContext) -> dict:
        body = ctx.shape(self, "body")
        # Rotate about the body's own centre, not the world origin. Spinning a
        # part that happens to sit at x=150 around (0,0,0) flings it across the
        # scene, which is never what "rotate this 90 degrees" means.
        low, high = bounding_box(body)
        pivot = tuple((a + b) / 2.0 for a, b in zip(low, high))
        if self.inputs.get("pivot"):
            pivot = tuple(self.inputs["pivot"])

        transform = make_transform(
            translate=(
                ctx.value(self, "dx", 0.0),
                ctx.value(self, "dy", 0.0),
                ctx.value(self, "dz", 0.0),
            )
        )
        for axis, key in (((1, 0, 0), "rx"), ((0, 1, 0), "ry"), ((0, 0, 1), "rz")):
            angle = ctx.value(self, key, 0.0)
            if abs(angle) > 1e-12:
                transform = transform.Multiplied(
                    make_transform(
                        rotate_axis=axis, rotate_degrees=angle, origin=pivot
                    )
                )
        return self._emit(transformed(body, transform))


@register("scale")
class ScaleFeature(_BodyOperation):
    """Resize a body, uniformly or per axis.

    Scaled about the body's own centre rather than the world origin, for the
    same reason Move rotates about it: a part sitting at x=150 that shrinks
    toward (0,0,0) does not look scaled, it looks like it has been thrown
    across the scene.
    """

    label = "Scale"
    dimensions = {
        "factor": Dimension.SCALAR,
        "sx": Dimension.SCALAR,
        "sy": Dimension.SCALAR,
        "sz": Dimension.SCALAR,
    }

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepBuilderAPI import BRepBuilderAPI_GTransform
        from OCP.gp import gp_GTrsf, gp_Pnt, gp_Trsf, gp_XYZ

        body = ctx.shape(self, "body")
        low, high = bounding_box(body)
        centre = tuple((a + b) / 2.0 for a, b in zip(low, high))
        if self.inputs.get("pivot"):
            centre = tuple(self.inputs["pivot"])

        uniform = ctx.value(self, "factor", 1.0)
        factors = (
            ctx.value(self, "sx", uniform),
            ctx.value(self, "sy", uniform),
            ctx.value(self, "sz", uniform),
        )
        if any(f <= 0 for f in factors):
            raise CadError(
                "A scale factor has to be greater than zero.",
                suggestion="Use 0.5 to halve the part, or 2 to double it.",
            )
        if all(abs(f - 1.0) < 1e-12 for f in factors):
            return self._emit(body)

        if len({round(f, 12) for f in factors}) == 1:
            # Uniform: gp_Trsf keeps circles circular and the result exact,
            # which a general transformation cannot promise.
            transform = gp_Trsf()
            transform.SetScale(gp_Pnt(*centre), factors[0])
            return self._emit(transformed(body, transform))

        with guard("scale"):
            general = gp_GTrsf()
            general.SetVectorialPart(_diagonal(factors))
            general.SetTranslationPart(
                gp_XYZ(*(centre[i] * (1.0 - factors[i]) for i in range(3)))
            )
            builder = BRepBuilderAPI_GTransform(body, general, True)
            return self._emit(built_shape(builder, "scale"))


def _diagonal(factors):
    """A ``gp_Mat`` scaling each axis independently."""
    from OCP.gp import gp_Mat

    return gp_Mat(
        factors[0], 0.0, 0.0,
        0.0, factors[1], 0.0,
        0.0, 0.0, factors[2],
    )


@register("boolean")
class BooleanFeature(Feature):
    """Join, Cut or Intersect -- one target body against one or more tools."""

    label = "Combine"

    def tool_names(self) -> list[str]:
        """The bodies acting on the target, in order.

        Reads ``tools`` when it is there and falls back to the singular
        ``tool``, so a project saved before several tools were allowed still
        rebuilds.
        """
        tools = self.inputs.get("tools")
        if tools:
            return [str(name) for name in tools]
        single = self.inputs.get("tool")
        return [str(single)] if single else []

    def consumed_bodies(self) -> list[str]:
        """The tools, unless the user asked to keep them.

        Without this the cutter stays in the tree and in the viewport, sitting
        inside the part it just cut -- which is not what "subtract" means
        anywhere else in CAD. Keep tools is how you ask for the other
        behaviour, and then it is not consumed at all.
        """
        if bool(self.inputs.get("keep_tool", False)):
            return []
        target = str(self.inputs.get("body", ""))
        return [name for name in self.tool_names() if name != target]

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepAlgoAPI import (
            BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse,
        )

        operation = str(self.inputs.get("operation", "join"))
        builders = {
            "join": BRepAlgoAPI_Fuse,
            "cut": BRepAlgoAPI_Cut,
            "intersect": BRepAlgoAPI_Common,
        }
        if operation not in builders:
            raise CadError(f"'{operation}' is not a combine operation.")

        name = str(self.inputs["body"])
        tool_names = self.tool_names()
        if not tool_names:
            raise CadError("Combine needs a second body to work with.")

        target = ctx.shape(self, "body")
        tools = {tool: ctx.named_shape(tool) for tool in tool_names}
        with guard(operation):
            result = target
            for tool in tool_names:
                # One boolean per tool rather than one call with a list of
                # arguments: OCCT copes with either, and doing them in turn
                # means a failure names the tool that caused it.
                result = built_shape(
                    builders[operation](result, tools[tool]), operation
                )
            result = unify(result)

        keep_tool = bool(self.inputs.get("keep_tool", False))
        self.outputs = [name]
        outputs = {name: result}
        if keep_tool:
            for tool in tool_names:
                if tool == name:
                    continue
                self.outputs.append(tool)
                outputs[tool] = tools[tool]
        return outputs


# ----------------------------------------------------------------------
# Holes
# ----------------------------------------------------------------------
@register("hole")
class HoleFeature(_BodyOperation):
    """A hole placed on a face: simple, counterbored, countersunk or threaded."""

    label = "Hole"
    dimensions = {"countersink_angle": Dimension.ANGLE}

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        from .detect import analyse_plane

        body = ctx.shape(self, "body")
        face = ctx.resolve(self, "face")
        info = analyse_plane(face)
        if info is None:
            raise CadError(
                "Holes are placed on flat faces.",
                suggestion="Select a planar face to drill into.",
            )

        diameter = ctx.value(self, "diameter", 6.0)
        if diameter <= 0:
            raise CadError("A hole needs a diameter greater than zero.")
        position = self.inputs.get("position") or info.center
        style = str(self.inputs.get("style", "simple"))
        depth_mode = str(self.inputs.get("depth_mode", "through"))

        # Drill along the inward normal.
        inward = tuple(-v for v in info.normal)
        low, high = bounding_box(body)
        span = math.dist(low, high) + 10.0
        if depth_mode == "through":
            depth = span
        elif depth_mode == "to_object":
            depth = self._depth_to_object(ctx, position, inward, span)
        else:
            depth = ctx.value(self, "depth", 10.0)

        start = gp_Pnt(*position)
        axis = gp_Ax2(start, gp_Dir(*inward))
        with guard("hole"):
            tool = BRepPrimAPI_MakeCylinder(axis, diameter / 2.0, depth).Shape()

            if style == "counterbore":
                cb_diameter = ctx.value(self, "counterbore_diameter", diameter * 1.8)
                cb_depth = ctx.value(self, "counterbore_depth", diameter * 0.6)
                cap = BRepPrimAPI_MakeCylinder(axis, cb_diameter / 2.0, cb_depth).Shape()
                tool = built_shape(BRepAlgoAPI_Fuse(tool, cap), "hole")
            elif style == "countersink":
                angle = ctx.value(self, "countersink_angle", 90.0)
                cs_diameter = ctx.value(self, "countersink_diameter", diameter * 2.0)
                cone_height = (cs_diameter - diameter) / 2.0 / math.tan(
                    math.radians(angle / 2.0)
                )
                cone = BRepPrimAPI_MakeCone(
                    axis, cs_diameter / 2.0, diameter / 2.0, max(cone_height, 1e-3)
                ).Shape()
                tool = built_shape(BRepAlgoAPI_Fuse(tool, cone), "hole")

            result = built_shape(BRepAlgoAPI_Cut(body, tool), "hole")

        if style == "threaded":
            result = self._thread_the_bore(ctx, result, diameter, inward)
        return self._emit(result)

    def _thread_the_bore(self, ctx: BuildContext, shape, diameter: float, inward):
        """Thread the bore that was just cut, over the material it passes through.

        The thread must span the *bore*, not the drill. A through hole is cut
        with a tool long enough to clear the whole bounding box, and threading
        that length asks for tens of turns of helix through empty space -- which
        fails to sweep, so the user silently got a plain hole. Measuring the
        cylindrical face the cut actually produced gives the real thickness.
        """
        from .detect import analyse_cylinder
        from .thread_specs import best_match
        from .threads import apply_thread

        bore = self._find_bore(shape, diameter, inward)
        if bore is None:
            ctx.warn(
                "The hole was made, but its wall could not be measured, so no "
                "thread was added."
            )
            return shape

        size = best_match(bore.diameter, internal=True)
        if size is None:
            ctx.warn(
                f"No standard thread matches ⌀{bore.diameter:.2f} mm, so the "
                "hole was left plain."
            )
            return shape

        outcome = apply_thread(
            shape, size=size, origin=bore.origin, direction=bore.direction,
            length=bore.length, internal=True,
            clearance=str(self.inputs.get("clearance", "normal")),
            feature_diameter=bore.diameter,
        )
        if outcome.message:
            ctx.warn(outcome.message)
        self.inputs.setdefault("designation", size.designation)
        return outcome.shape

    def _depth_to_object(self, ctx: BuildContext, position, direction, span: float):
        """Drill until it reaches another body, and stop there.

        Measured by casting a ray from the hole's start along its axis and
        taking the first intersection with the target -- which is what "to
        object" means, rather than a depth the user has to work out.
        """
        from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter
        from OCP.gp import gp_Dir, gp_Lin, gp_Pnt

        name = self.inputs.get("until")
        target = ctx.bodies.get(str(name)) if name else None
        if target is None:
            ctx.warn(
                "No body was given to stop at, so the hole goes all the way "
                "through."
            )
            return span

        ray = gp_Lin(gp_Pnt(*position), gp_Dir(*direction))
        finder = BRepIntCurveSurface_Inter()
        finder.Init(target, ray, 1e-6)
        nearest = None
        while finder.More():
            distance = finder.W()
            if distance > 1e-6 and (nearest is None or distance < nearest):
                nearest = distance
            finder.Next()
        if nearest is None:
            ctx.warn(
                f"The hole never reaches '{name}', so it goes all the way "
                "through."
            )
            return span
        return nearest

    @staticmethod
    def _find_bore(shape, diameter: float, direction):
        """The cylindrical face this hole created: right size, right axis."""
        from .detect import analyse_cylinder
        from ..core.naming import sub_shapes

        best = None
        for face in sub_shapes(shape, "face"):
            info = analyse_cylinder(face)
            if info is None or not info.internal:
                continue
            if abs(info.diameter - diameter) > 1e-6:
                continue
            aligned = abs(sum(a * b for a, b in zip(info.direction, direction)))
            if aligned < 0.999:
                continue
            if best is None or info.length > best.length:
                best = info
        return best


# ----------------------------------------------------------------------
# Positioning
# ----------------------------------------------------------------------
@register("align")
class AlignFeature(_BodyOperation):
    """Place one body against another, re-solved on every rebuild.

    The transform is not baked in at commit time: it is recomputed from the live
    face references, so moving or resizing the target carries the aligned body
    with it. That is what makes Stack a relationship rather than a one-off nudge.
    """

    label = "Align"

    def execute(self, ctx: BuildContext) -> dict:
        from .align import solve

        body = ctx.shape(self, "body")
        moving_face = ctx.resolve(self, "moving_face")
        target_face = ctx.resolve(self, "target_face")
        result = solve(
            moving_face,
            target_face,
            operation=self.inputs.get("operation") or None,
            offset=ctx.value(self, "offset", 0.0),
            flip=bool(self.inputs.get("flip", False)),
        )
        self.message = result.description
        return self._emit(transformed(body, result.transform))


# ----------------------------------------------------------------------
# Threads
# ----------------------------------------------------------------------
@register("thread")
class ThreadFeature(_BodyOperation):
    """A thread on one cylindrical face, internal or external as detected."""

    label = "Thread"

    def execute(self, ctx: BuildContext) -> dict:
        from .detect import analyse_cylinder
        from .thread_specs import best_match, by_designation
        from .threads import apply_thread

        body = ctx.shape(self, "body")
        face = ctx.resolve(self, "face")
        info = analyse_cylinder(face)
        if info is None:
            raise CadError(
                "Threads go on round faces.",
                suggestion="Select the cylindrical face of a shaft or a hole.",
            )

        designation = self.inputs.get("designation")
        size = by_designation(str(designation)) if designation else None
        if size is None:
            size = best_match(info.diameter, internal=info.internal)
        if size is None:
            raise CadError(
                f"No standard thread matches ⌀{info.diameter:.2f} mm.",
                suggestion="Resize the feature, or pick a size manually.",
            )

        length = ctx.value(self, "length", info.length) or info.length
        outcome = apply_thread(
            body, size=size, origin=info.origin, direction=info.direction,
            length=min(length, info.length), internal=info.internal,
            clearance=str(self.inputs.get("clearance", "normal")),
            left_hand=bool(self.inputs.get("left_hand", False)),
            feature_diameter=info.diameter,
        )
        if outcome.message:
            ctx.warn(outcome.message)
            self.message = outcome.message
        self.inputs.setdefault("designation", size.designation)
        return self._emit(outcome.shape)


@register("threaded_connection")
class ThreadedConnectionFeature(Feature):
    """A matched thread pair across two parts.

    One node with **two** outputs. It owns the clearance both halves share, so
    changing the clearance updates the male and female threads together -- which
    is the whole reason the feature graph is not a per-body linear history.
    """

    label = "Threaded Connection"

    def execute(self, ctx: BuildContext) -> dict:
        from .detect import analyse_cylinder
        from .thread_specs import best_match, by_designation
        from .threads import apply_thread

        first_name = str(self.inputs["body_a"])
        second_name = str(self.inputs["body_b"])
        first = ctx.shape(self, "body_a")
        second = ctx.shape(self, "body_b")

        face_a = analyse_cylinder(ctx.resolve(self, "face_a"))
        face_b = analyse_cylinder(ctx.resolve(self, "face_b"))
        if face_a is None or face_b is None:
            raise CadError(
                "A threaded connection needs a round face on each part.",
                suggestion="Select the post on one part and its mating hole on the other.",
            )
        if face_a.internal == face_b.internal:
            kind = "holes" if face_a.internal else "shafts"
            raise CadError(
                f"Both selections are {kind}.",
                suggestion="Select one shaft and one hole so they can mate.",
            )

        # The male side sets the size; the female side carries the clearance.
        male, female = (face_b, face_a) if face_a.internal else (face_a, face_b)
        designation = self.inputs.get("designation")
        size = by_designation(str(designation)) if designation else None
        if size is None:
            size = best_match(male.diameter, internal=False)
        if size is None:
            raise CadError(
                f"No standard thread matches ⌀{male.diameter:.2f} mm.",
                suggestion="Adjust the diameter, or choose a size manually.",
            )

        clearance = str(self.inputs.get("clearance", "normal"))
        length = ctx.value(self, "length", 0.0) or min(male.length, female.length)

        outputs: dict[str, object] = {}
        for name, shape, info in (
            (first_name, first, face_a),
            (second_name, second, face_b),
        ):
            outcome = apply_thread(
                shape, size=size, origin=info.origin, direction=info.direction,
                length=min(length, info.length), internal=info.internal,
                clearance=clearance,
                left_hand=bool(self.inputs.get("left_hand", False)),
                feature_diameter=info.diameter,
            )
            if outcome.message:
                ctx.warn(outcome.message)
            outputs[name] = outcome.shape

        self.inputs.setdefault("designation", size.designation)
        self.message = (
            f"{size.designation} pair, {clearance} clearance "
            f"({male.kind} ⌀{male.diameter:.2f} into {female.kind} ⌀{female.diameter:.2f})"
        )
        self.outputs = [first_name, second_name]
        return outputs
