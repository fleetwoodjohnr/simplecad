"""Replace a target body's surface region with an aligned second body."""

from __future__ import annotations

from ..core.document import BodyRef, BuildContext, Feature, register
from ..core.errors import CadError
from ..core.units import Dimension
from .align import _dot, _projected_bounds, _scale, solve, translation
from .occ import built_shape, is_valid, transformed, unify, volume
from .operations import _face_profile, _wire_from_coords


def _solid_count(shape) -> int:
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    count = 0
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        count += 1
        explorer.Next()
    return count


def build_section_replacement(
    target,
    target_face,
    replacement,
    replacement_face,
    *,
    moving_anchor: str = "center",
    target_anchor: str = "center",
    u: float = 0.0,
    v: float = 0.0,
    boundary: float = 0.5,
    setback: float = 0.0,
):
    """Return ``(integrated, removed, placed)`` for a section replacement.

    The replacement's selected face is first seated on the target face.  Its
    automatic outer silhouette becomes a through-cutter, inset by *boundary*
    so a narrow joining flange remains.  The replacement is then sunk until its
    outermost point is flush, plus the requested positive setback.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.TopoDS import TopoDS
    from OCP.gp import gp_Vec
    from shapely import buffer as planar_buffer
    from shapely.geometry import Polygon

    from .detect import analyse_plane

    target_info = analyse_plane(target_face)
    replacement_info = analyse_plane(replacement_face)
    if target_info is None or replacement_info is None:
        raise CadError(
            "Section Replace needs one flat face on each object.",
            suggestion="Select the vent's mounting face and the target surface.",
        )
    if boundary < 0.0:
        raise CadError("The replacement boundary cannot be negative.")

    placement = solve(
        replacement_face,
        target_face,
        operation="stack",
        moving_anchor=moving_anchor,
        target_anchor=target_anchor,
        u=u,
        v=v,
    ).transform
    seated = transformed(replacement, placement)
    seated_face = TopoDS.Face_s(transformed(replacement_face, placement))

    normal = target_info.normal
    _, exterior = _projected_bounds(seated, normal, target_info.center)
    insertion = max(0.0, exterior) + setback
    inset = translation(_scale(normal, -insertion))
    placed = transformed(seated, inset)

    # Use only the outer silhouette. Internal loops are openings in a vent and
    # deliberately must not preserve target material beneath them.
    profile, frame = _face_profile(seated_face, max(0.005, min(0.05, boundary / 10.0 or 0.02)))
    silhouette = Polygon(profile.exterior)
    if boundary:
        silhouette = planar_buffer(
            silhouette, -boundary, join_style="mitre", mitre_limit=2.0
        )
    if silhouette.is_empty or silhouette.geom_type != "Polygon":
        raise CadError(
            "The boundary closes the replacement opening.",
            suggestion="Use a smaller boundary value.",
        )

    inward = _scale(normal, -1.0)
    _, target_depth = _projected_bounds(target, inward, target_info.center)
    depth = max(target_depth, 0.01) + 0.2
    wire = _wire_from_coords(silhouette.exterior.coords, frame, normal, 0.1)
    face = BRepBuilderAPI_MakeFace(wire).Face()
    cutter = built_shape(
        BRepPrimAPI_MakePrism(face, gp_Vec(*_scale(inward, depth))),
        "section replacement boundary",
    )
    removed = built_shape(BRepAlgoAPI_Common(target, cutter), "section replacement preview")
    remaining = built_shape(BRepAlgoAPI_Cut(target, cutter), "section replacement cut")
    result = unify(built_shape(BRepAlgoAPI_Fuse(remaining, placed), "section replacement"))

    if not is_valid(result) or _solid_count(result) != 1:
        raise CadError(
            "The replacement does not make one valid connected body.",
            suggestion="Reduce the boundary or setback, or move the replacement farther onto the face.",
        )
    if volume(removed) <= 1.0e-8:
        raise CadError(
            "The replacement boundary does not overlap the target.",
            suggestion="Move it onto the selected target face.",
        )
    return result, removed, placed


@register("section_replace")
class SectionReplaceFeature(Feature):
    """Integrate one aligned object into a cleared region of another."""

    label = "Section Replace"
    dimensions = {
        "u": Dimension.LENGTH,
        "v": Dimension.LENGTH,
        "boundary": Dimension.LENGTH,
        "setback": Dimension.LENGTH,
    }

    def _build(self, ctx: BuildContext):
        target = ctx.shape(self, "target")
        replacement = ctx.shape(self, "replacement")
        return build_section_replacement(
            target,
            ctx.resolve(self, "target_face"),
            replacement,
            ctx.resolve(self, "replacement_face"),
            moving_anchor=str(self.inputs.get("moving_anchor", "center")),
            target_anchor=str(self.inputs.get("target_anchor", "center")),
            u=ctx.value(self, "u", 0.0),
            v=ctx.value(self, "v", 0.0),
            boundary=ctx.value(self, "boundary", 0.5),
            setback=ctx.value(self, "setback", 0.0),
        )

    def execute(self, ctx: BuildContext) -> dict:
        result, _removed, _placed = self._build(ctx)
        target_name = str(self.inputs["target"])
        self.outputs = [target_name]
        self.message = "Replaced the target section and integrated the inserted body."
        return {target_name: result}

    def preview_parts(self, ctx: BuildContext) -> dict:
        result, removed, placed = self._build(ctx)
        return {"result": result, "removed": removed, "replacement": placed}

    def consumed_bodies(self) -> list[str]:
        return [str(self.inputs["replacement"])]
