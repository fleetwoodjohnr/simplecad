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
from .occ import (
    axis_transform, bounding_box, built_shape, is_valid, make_transform,
    transformed, unify, volume,
)


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


def _usable_shell(original, result) -> bool:
    """A shell must be valid, retain its exterior bounds, and remove material."""
    if not _has_solid(result) or not is_valid(result):
        return False
    before = volume(original)
    after = volume(result)
    if before - after <= max(1.0e-7, before * 1.0e-8):
        return False
    old_box, new_box = bounding_box(original), bounding_box(result)
    return all(
        abs(old_box[side][axis] - new_box[side][axis]) <= 1.0e-5
        for side in (0, 1) for axis in range(3)
    )


def _sample_edge(edge, start_vertex, deflection: float):
    """Ordered points along one edge, starting at WireExplorer's vertex."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection

    curve = BRepAdaptor_Curve(edge)
    try:
        sampler = GCPnts_QuasiUniformDeflection(curve, deflection)
        points = [sampler.Value(i) for i in range(1, sampler.NbPoints() + 1)] \
            if sampler.IsDone() else []
    except BaseException:  # noqa: BLE001 - a degenerate edge still has endpoints
        points = []
    if len(points) < 2:
        points = [curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter())]
    start = BRep_Tool.Pnt_s(start_vertex)
    first = points[0]
    last = points[-1]
    first_d2 = sum((a - b) ** 2 for a, b in zip(
        (first.X(), first.Y(), first.Z()), (start.X(), start.Y(), start.Z())
    ))
    last_d2 = sum((a - b) ** 2 for a, b in zip(
        (last.X(), last.Y(), last.Z()), (start.X(), start.Y(), start.Z())
    ))
    return list(reversed(points)) if last_d2 < first_d2 else points


def _face_profile(face, deflection: float):
    """The planar face as a Shapely polygon plus its local 3-D frame."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepTools import BRepTools_WireExplorer
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from shapely.geometry import Polygon

    plane = BRepAdaptor_Surface(face).Plane()
    position = plane.Position()
    origin = position.Location()
    x_dir = position.XDirection()
    y_dir = position.YDirection()
    origin_xyz = (origin.X(), origin.Y(), origin.Z())
    x_axis = (x_dir.X(), x_dir.Y(), x_dir.Z())
    y_axis = (y_dir.X(), y_dir.Y(), y_dir.Z())

    rings = []
    explorer = TopExp_Explorer(face, TopAbs_WIRE)
    while explorer.More():
        wire = TopoDS.Wire_s(explorer.Current())
        ordered = BRepTools_WireExplorer(wire, face)
        points = []
        while ordered.More():
            samples = _sample_edge(
                ordered.Current(), ordered.CurrentVertex(), deflection
            )
            for point in samples:
                xyz = (point.X(), point.Y(), point.Z())
                relative = tuple(xyz[i] - origin_xyz[i] for i in range(3))
                uv = (
                    sum(relative[i] * x_axis[i] for i in range(3)),
                    sum(relative[i] * y_axis[i] for i in range(3)),
                )
                if not points or math.dist(points[-1], uv) > 1.0e-8:
                    points.append(uv)
            ordered.Next()
        if len(points) >= 3:
            if math.dist(points[0], points[-1]) > 1.0e-8:
                points.append(points[0])
            rings.append(points)
        explorer.Next()
    if not rings:
        raise CadError("The selected opening has no usable boundary.")

    def signed_area(ring):
        return sum(
            ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
            for i in range(len(ring) - 1)
        ) / 2.0

    rings.sort(key=lambda ring: abs(signed_area(ring)), reverse=True)
    profile = Polygon(rings[0], rings[1:])
    if not profile.is_valid:
        profile = profile.buffer(0)
    if profile.is_empty or profile.geom_type not in ("Polygon", "MultiPolygon"):
        raise CadError("The selected opening has a self-intersecting boundary.")
    return profile, (origin_xyz, x_axis, y_axis)


def _prism_depth(body, opening, inward) -> float:
    """Depth of a constant-profile extrusion, or fail when the body is not one."""
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from .detect import analyse_plane

    info = analyse_plane(opening)
    if info is None:
        raise CadError("The fallback hollow path needs one flat opening face.")
    levels = []
    explorer = TopExp_Explorer(body, TopAbs_VERTEX)
    while explorer.More():
        point = BRep_Tool.Pnt_s(TopoDS.Vertex_s(explorer.Current()))
        offset = (
            point.X() - info.center[0],
            point.Y() - info.center[1],
            point.Z() - info.center[2],
        )
        levels.append(sum(offset[i] * inward[i] for i in range(3)))
        explorer.Next()
    depth = max(levels, default=0.0)
    tolerance = max(1.0e-5, depth * 1.0e-6)
    if depth <= tolerance or any(
        tolerance < level < depth - tolerance for level in levels
    ):
        raise CadError(
            "This body is not a constant-depth extrusion.",
            suggestion="Try a thinner wall; this shape needs the general shell solver.",
        )
    return depth


def _wire_from_coords(coords, frame, outward, epsilon: float):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
    from OCP.gp import gp_Pnt

    origin, x_axis, y_axis = frame
    polygon = BRepBuilderAPI_MakePolygon()
    for u, v in list(coords)[:-1]:
        polygon.Add(gp_Pnt(*(
            origin[i] + x_axis[i] * u + y_axis[i] * v + outward[i] * epsilon
            for i in range(3)
        )))
    polygon.Close()
    return polygon.Wire()


def _profile_cavity(body, opening, thickness: float):
    """Hollow a constant-depth solid through a robust 2-D inward offset."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec
    from shapely import buffer as planar_buffer

    from .detect import analyse_plane

    info = analyse_plane(opening)
    if info is None:
        raise CadError("Select one flat face to open before hollowing.")
    outward = info.normal
    inward = tuple(-value for value in outward)
    depth = _prism_depth(body, opening, inward)
    if thickness >= depth:
        raise CadError(
            f"A {thickness:.2f} mm wall leaves no interior depth.",
            suggestion=f"Use a wall below {depth:.2f} mm.",
        )

    deflection = max(0.005, min(0.05, thickness / 20.0))
    profile, frame = _face_profile(opening, deflection)
    inner = planar_buffer(
        profile, -thickness, join_style="mitre", mitre_limit=2.0
    )
    if not inner.is_valid:
        inner = inner.buffer(0)
    if inner.is_empty:
        raise CadError(
            f"A {thickness:.2f} mm wall closes this profile completely.",
            suggestion="Use a thinner wall.",
        )
    polygons = [inner] if inner.geom_type == "Polygon" else list(inner.geoms)
    epsilon = max(1.0e-4, min(0.01, thickness * 1.0e-3))
    result = body
    for polygon in polygons:
        face_builder = BRepBuilderAPI_MakeFace(
            _wire_from_coords(polygon.exterior.coords, frame, outward, epsilon)
        )
        for ring in polygon.interiors:
            face_builder.Add(
                _wire_from_coords(ring.coords, frame, outward, epsilon)
            )
        cutter = BRepPrimAPI_MakePrism(
            face_builder.Face(),
            gp_Vec(*(inward[i] * (depth - thickness + epsilon) for i in range(3))),
        ).Shape()
        result = built_shape(BRepAlgoAPI_Cut(result, cutter), "shell")
    if not _usable_shell(body, result):
        raise CadError(
            "The hollow cavity could not be built cleanly.",
            suggestion="Use a thinner wall or simplify the opening profile.",
        )
    return result


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
        generic_result = None
        generic_error = None
        try:
            removed = TopTools_ListOfShape()
            for face in faces:
                removed.Append(face)
            builder = BRepOffsetAPI_MakeThickSolid()
            # Negative offset hollows inward, leaving the outer surface where it is.
            builder.MakeThickSolidByJoin(body, removed, -abs(thickness), 1.0e-3)
            generic_result = built_shape(builder, "shell")
        except BaseException as exc:  # noqa: BLE001 - OCCT exceptions cross Python
            generic_error = exc

        # OCCT sometimes reports a successful thick-solid operation while
        # returning the original body unchanged (notably smooth Heart and
        # Crescent profiles). Never accept a result unless it actually has an
        # interior and preserves the original exterior.
        if _usable_shell(body, generic_result):
            return self._emit(generic_result)

        # A silhouette extruded to a constant depth has a much more dependable
        # construction: offset the selected planar profile in 2-D, extrude that
        # interior downward, then subtract it. This handles every flat profile,
        # including curved and concave decorative shapes.
        if len(faces) == 1:
            with guard("shell"):
                return self._emit(_profile_cavity(body, faces[0], thickness))

        detail = ""
        if generic_error is not None:
            detail = f"{type(generic_error).__name__}: {generic_error}"
        raise CadError(
            "The selected faces did not produce a hollow body.",
            suggestion="Try opening one flat face or using a thinner wall.",
            detail=detail,
            operation="shell",
        )


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


#: Slack on the radius the tool shares with the face being moved. Two coaxial
#: cylinders of exactly the same radius are tangent surfaces, and a boolean
#: across tangent surfaces is where OCCT hands back an invalid solid. The
#: surplus always lies in material the operation is not responsible for, so it
#: changes nothing about the result. Same reasoning as threads.ENVELOPE_MARGIN.
RADIAL_MARGIN = 0.02
#: The thinnest wall this refuses to leave behind, in mm. Geometric, not a
#: printing rule -- printability is the print workspace's job to warn about.
MIN_WALL = 0.05


def _coaxial_faces(shape, info, internal: bool):
    """Cylindrical faces of *shape* on the same axis as *info*, largest first.

    What makes a tube behave like a tube: to know whether thinning its outer
    wall would break through, you have to know where its bore is.
    """
    from ..core.naming import sub_shapes

    from .detect import analyse_cylinder

    found = []
    for face in sub_shapes(shape, "face"):
        other = analyse_cylinder(face)
        if other is None or other.internal is not internal:
            continue
        aligned = abs(sum(a * b for a, b in zip(other.direction, info.direction)))
        if aligned < 0.999:
            continue
        # On the same axis, not merely parallel to it.
        offset = tuple(other.origin[i] - info.origin[i] for i in range(3))
        along = sum(offset[i] * info.direction[i] for i in range(3))
        sideways = math.sqrt(
            max(0.0, sum(v * v for v in offset) - along * along)
        )
        if sideways > 1e-6:
            continue
        found.append(other)
    found.sort(key=lambda c: c.radius, reverse=True)
    return found


def radial_ring(info, new_radius: float, adding: bool):
    """The tube of material between a round face and where it is moving to.

    Shared by the feature and by the drag preview, so what the ghost shows
    during the drag is the very shape the commit will use -- a preview computed
    a second way is a preview that can lie.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

    # Overlap the material that is already there when adding, and reach past
    # the face being moved when cutting. Either way the tool never shares a
    # surface with the body -- see RADIAL_MARGIN.
    outward = info.internal == adding
    overlap = info.radius + (RADIAL_MARGIN if outward else -RADIAL_MARGIN)
    inner, outer = min(new_radius, overlap), max(new_radius, overlap)

    ring = BRepPrimAPI_MakeCylinder(outer, info.length).Shape()
    if inner > 1e-6:
        ring = built_shape(
            BRepAlgoAPI_Cut(
                ring, BRepPrimAPI_MakeCylinder(inner, info.length).Shape()
            ),
            "resize",
        )
    return transformed(ring, axis_transform(info.origin, info.direction))


@register("resize_round")
class RoundPushPullFeature(_BodyOperation):
    """Move a cylindrical face along its radius: Push/Pull for round things.

    The flat-face Pull has never had a counterpart on a shaft, so the only way
    to make a cylinder or a tube thinner was to delete it and model it again at
    a new size. This is that counterpart, and it reads the same way: positive
    adds material, negative takes it away, whether the face is the outside of a
    shaft or the inside of a bore.

    The tool is an **annulus**, never a solid cylinder, and that is the whole
    reason this works on tubes. Turning a tube down with a solid cylinder would
    fill its bore on the way past; a ring between the old and the new radius
    touches only the wall it is asked to move.
    """

    label = "Resize"

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

        from .detect import analyse_cylinder

        body = ctx.shape(self, "body")
        delta = ctx.value(self, "delta", 0.0)
        face = ctx.resolve(self, "face")
        info = analyse_cylinder(face)
        if info is None:
            raise CadError(
                "This only works on round faces.",
                suggestion="Select the side of a shaft, a tube or a hole.",
            )
        if abs(delta) < 1e-9:
            return self._emit(body)

        radius = info.radius
        # A hole grows *inward* when material is added, which is the one place
        # the two cases differ; everything after this is common.
        new_radius = radius - delta if info.internal else radius + delta
        if new_radius <= MIN_WALL:
            raise CadError(
                f"That would take the {info.kind} down to ⌀{new_radius * 2:.2f} mm.",
                suggestion=f"Keep the diameter above {MIN_WALL * 2:.2f} mm.",
            )
        self._check_wall(body, info, new_radius, delta > 0)

        adding = delta > 0
        with guard("resize"):
            ring = radial_ring(info, new_radius, adding)
            result = built_shape(
                (BRepAlgoAPI_Fuse if adding else BRepAlgoAPI_Cut)(body, ring),
                "resize",
            )
            if not _has_solid(result):
                raise CadError(
                    "That would leave nothing behind.",
                    suggestion="Take less off, or delete the body instead.",
                )
            return self._emit(unify(result))

    @staticmethod
    def _check_wall(body, info, new_radius: float, adding: bool) -> None:
        """Refuse a move that would break through into a bore, and say why.

        Without this, thinning a tube past its own wall reports as a boolean
        that produced no solid -- true, and no use at all to someone who just
        wanted the wall a bit thinner than it can actually be.
        """
        if info.internal:
            walls = _coaxial_faces(body, info, internal=False)
            outer = walls[0].radius if walls else None
            if outer is not None and not adding and new_radius >= outer - MIN_WALL:
                raise CadError(
                    f"Opening the hole to ⌀{new_radius * 2:.2f} mm would break "
                    f"through the ⌀{outer * 2:.2f} mm wall around it.",
                    suggestion=f"Stay under ⌀{(outer - MIN_WALL) * 2:.2f} mm.",
                )
            return
        bores = _coaxial_faces(body, info, internal=True)
        bore = bores[0].radius if bores else None
        if bore is not None and not adding and new_radius <= bore + MIN_WALL:
            raise CadError(
                f"Thinning to ⌀{new_radius * 2:.2f} mm would break through the "
                f"⌀{bore * 2:.2f} mm bore inside it.",
                suggestion=f"Stay above ⌀{(bore + MIN_WALL) * 2:.2f} mm.",
            )


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


@register("move_many")
class MoveManyFeature(Feature):
    """Translate several bodies as one parametric/history operation."""

    label = "Move"

    def execute(self, ctx: BuildContext) -> dict:
        names = [str(name) for name in self.inputs.get("bodies", [])]
        if not names:
            raise CadError("Move needs at least one body.")
        transform = make_transform(translate=(
            ctx.value(self, "dx", 0.0),
            ctx.value(self, "dy", 0.0),
            ctx.value(self, "dz", 0.0),
        ))
        self.outputs = names
        return self.keep({
            name: transformed(ctx.named_shape(name), transform) for name in names
        })


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
def thread_origin(info, length: float, from_end: str):
    """Where a thread of *length* starts on the cylindrical face *info* describes.

    ``analyse_cylinder`` reports the face's ``vmin`` end, which is an artefact of
    how the surface happened to get built and means nothing to anyone looking at
    the model. A thread shorter than the face therefore landed on whichever end
    OCCT felt like, with no way to ask for the other one -- so a hole would come
    back threaded at the bottom when the whole point was to start a screw at the
    top.

    The choice is resolved against **world Z**, because top and bottom are what
    the user can see. A horizontal axis has no top, so there the face's own
    direction decides and today's behaviour is kept.
    """
    spare = info.length - length
    if spare <= 1e-9:
        return info.origin

    far = tuple(info.origin[i] + info.direction[i] * info.length for i in range(3))
    rise = far[2] - info.origin[2]
    far_is_top = rise > 1e-9
    horizontal = abs(rise) <= 1e-9
    if horizontal:
        # Nothing to be higher than: start where the face starts, as before.
        use_far = False
    else:
        use_far = far_is_top if from_end == "top" else not far_is_top
    if not use_far:
        return info.origin
    return tuple(info.origin[i] + info.direction[i] * spare for i in range(3))


def _on_axis(info, position, tolerance: float = 1e-4) -> bool:
    """Whether *position* lies on the axis of the cylinder *info* describes."""
    offset = tuple(position[i] - info.origin[i] for i in range(3))
    along = sum(offset[i] * info.direction[i] for i in range(3))
    perpendicular = math.dist(
        offset, tuple(info.direction[i] * along for i in range(3))
    )
    return perpendicular <= tolerance


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

        thread_size = None
        if style == "threaded":
            from .threads import required_bore, require_printable

            self.inputs["thread_modelled"] = False
            # Choose from the requested diameter, before adding clearance. The
            # internal thread adds metal to this bore; it cannot open its root
            # beyond the drilled wall afterwards.
            thread_size = self._thread_size(diameter)
            if thread_size is None:
                raise CadError(
                    f"No standard thread matches ⌀{diameter:.2f} mm.",
                    suggestion="Change the diameter or choose a standard size.",
                )
            require_printable(thread_size, str(self.inputs.get("form", "printed")))
            diameter = max(diameter, required_bore(
                thread_size, self.inputs.get("clearance", "normal")
            ))

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
            result = self._thread_the_bore(
                ctx, result, diameter, inward, position, size=thread_size
            )
        return self._emit(result)

    def _thread_the_bore(
        self, ctx: BuildContext, shape, diameter: float, inward, position=None,
        *, size=None,
    ):
        """Thread the bore that was just cut, over the material it passes through.

        The thread must span the *bore*, not the drill. A through hole is cut
        with a tool long enough to clear the whole bounding box, and threading
        that length asks for tens of turns of helix through empty space -- which
        fails to sweep, so the user silently got a plain hole. Measuring the
        cylindrical face the cut actually produced gives the real thickness.
        """
        from .threads import apply_thread, require_modelled, require_printable

        # False until the complete physical result exists. The geometry worker
        # returns portable feature inputs even on failure, so matching-part
        # discovery can never mistake the retained plain upstream body for a
        # successfully threaded one.
        self.inputs["thread_modelled"] = False
        self.inputs["thread_internal"] = True
        self.inputs.setdefault("form", "printed")
        self.inputs.setdefault("left_hand", False)

        bore = self._find_bore(shape, diameter, inward, position)
        if bore is None:
            raise CadError(
                "The hole was made, but its wall could not be measured, so no "
                "thread was added.",
                suggestion="Try a shorter blind hole or select another face.",
            )

        size = size or self._thread_size(diameter)
        if size is None:
            raise CadError(
                f"No standard thread matches ⌀{bore.diameter:.2f} mm, so the "
                "hole was left plain.",
                suggestion="Change the diameter or choose a standard size.",
            )

        length = ctx.value(self, "thread_length", bore.length) or bore.length
        length = min(length, bore.length)
        require_printable(size, str(self.inputs.get("form", "printed")))
        outcome = require_modelled(apply_thread(
            shape, size=size,
            origin=thread_origin(
                bore, length, str(self.inputs.get("from_end", "top"))
            ),
            direction=bore.direction,
            length=length, internal=True,
            clearance=self.inputs.get("clearance", "normal"),
            left_hand=bool(self.inputs.get("left_hand", False)),
            feature_diameter=bore.diameter,
            form=str(self.inputs.get("form", "printed")),
        ))
        if outcome.message:
            self._complain(ctx, outcome.message)
        self.inputs.setdefault("designation", size.designation)
        self.inputs["thread_modelled"] = True
        return outcome.shape

    def _thread_size(self, bore_diameter: float):
        """The size to cut: the one asked for, else the closest standard one.

        Asking matters. Left to itself the match is made on nominal diameter, so
        a ⌀5 hole gets M5 -- whose printed tooth is 0.25 mm deep, real geometry
        that is invisible on screen and finer than any nozzle resolves, which is
        what "it made the hole but there is no thread" was. There is no printable
        size at 5 mm to prefer instead; the way out is for the user to choose,
        which the Hole panel now lets them do.
        """
        from .thread_specs import best_match, by_designation

        designation = self.inputs.get("designation")
        if designation:
            size = by_designation(str(designation))
            if size is not None:
                return size
        return best_match(bore_diameter, internal=True)

    def _complain(self, ctx: BuildContext, message: str) -> None:
        """Warn, and keep the warning on the feature.

        ``ctx.warn`` alone reaches only the hint line, which the next selection
        change overwrites -- so the one explanation of why a thread is missing
        was gone before the user had looked at the model.
        """
        ctx.warn(message)
        self.message = message

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
    def _find_bore(shape, diameter: float, direction, position=None):
        """The cylindrical face this hole created: right size, right axis.

        *position* is the drill's own start point, and it is what separates this
        hole from a same-diameter one running parallel to it somewhere else in
        the part -- without it the longest bore won and the thread could land in
        a hole the user drilled ten minutes ago.
        """
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
            if position is not None and not _on_axis(info, position):
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
            x=ctx.value(self, "x", 0.0),
            y=ctx.value(self, "y", 0.0),
            x_anchor=str(self.inputs.get("x_anchor", "center")),
            y_anchor=str(self.inputs.get("y_anchor", "center")),
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
        from .threads import (
            apply_thread, require_modelled, require_printable, resize_target,
        )

        body = ctx.shape(self, "body")
        face = ctx.resolve(self, "face")
        info = analyse_cylinder(face)
        if info is None:
            raise CadError(
                "Threads go on round faces.",
                suggestion="Select the cylindrical face of a shaft or a hole.",
            )

        self.inputs["thread_modelled"] = False
        self.inputs["thread_internal"] = bool(info.internal)
        self.inputs.setdefault("form", "printed")
        self.inputs.setdefault("left_hand", False)

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
        length = min(length, info.length)
        form = str(self.inputs.get("form", "printed"))
        clearance = self.inputs.get("clearance", "normal")
        require_printable(size, form)
        origin = thread_origin(
            info, length, str(self.inputs.get("from_end", "top"))
        )
        # Threading a hole that was drilled for something else is an ordinary
        # thing to want. Opening it to the size first is what a tap drill is
        # for, and it is the difference between "this size is not on offer" and
        # a thread.
        body, diameter, resized = resize_target(
            body, info, size, length=length, origin=origin,
            clearance=clearance, form=form,
            resize=bool(self.inputs.get("resize", False)),
        )
        outcome = require_modelled(apply_thread(
            body, size=size,
            origin=origin,
            direction=info.direction,
            length=length, internal=info.internal,
            clearance=clearance,
            left_hand=bool(self.inputs.get("left_hand", False)),
            feature_diameter=diameter,
            form=form,
        ))
        if outcome.message:
            ctx.warn(outcome.message)
            self.message = outcome.message
        if resized:
            ctx.warn(resized)
            self.message = f"{resized} {self.message}".strip()
        self.inputs.setdefault("designation", size.designation)
        self.inputs["thread_modelled"] = True
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
        from .threads import apply_thread, require_modelled, require_printable

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

        self.inputs["thread_modelled_a"] = False
        self.inputs["thread_modelled_b"] = False
        self.inputs["thread_internal_a"] = bool(face_a.internal)
        self.inputs["thread_internal_b"] = bool(face_b.internal)
        self.inputs.setdefault("form", "printed")
        self.inputs.setdefault("left_hand", False)

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

        clearance = self.inputs.get("clearance", "normal")
        form = str(self.inputs.get("form", "printed"))
        require_printable(size, form)
        length = ctx.value(self, "length", 0.0) or min(male.length, female.length)

        outputs: dict[str, object] = {}
        for suffix, name, shape, info in (
            ("a", first_name, first, face_a),
            ("b", second_name, second, face_b),
        ):
            outcome = require_modelled(apply_thread(
                shape, size=size, origin=info.origin, direction=info.direction,
                length=min(length, info.length), internal=info.internal,
                clearance=clearance,
                left_hand=bool(self.inputs.get("left_hand", False)),
                feature_diameter=info.diameter,
                form=form,
            ))
            if outcome.message:
                ctx.warn(outcome.message)
            outputs[name] = outcome.shape
            self.inputs[f"thread_modelled_{suffix}"] = True

        self.inputs.setdefault("designation", size.designation)
        self.message = (
            f"{size.designation} pair, {clearance} clearance "
            f"({male.kind} ⌀{male.diameter:.2f} into {female.kind} ⌀{female.diameter:.2f})"
        )
        self.outputs = [first_name, second_name]
        return outputs
