"""Split: one solid in, two independent solids out.

The operation a functional 3D-printing workflow reaches for constantly -- a
bracket taller than the plate gets cut in half and printed as two parts. So the
result has to be two *bodies*, not one body containing two lumps: each half must
be separately selectable, movable, groupable and deletable afterwards, which is
exactly what a feature with two named outputs gives.

The cut is made with OCCT's ``BRepAlgoAPI_Splitter`` against an oversized planar
face, then the resulting solids are sorted onto one side or the other by which
side of the plane their centre of mass falls. Sorting by centre of mass rather
than by construction order is what makes the operation stable: a part with a
hole in it can come back as several solids per side, and they still have to end
up in the right two piles.
"""

from __future__ import annotations

import math

from ..core.document import BuildContext, Feature, register
from ..core.errors import CadError, guard
from .occ import bounding_box, center_of_mass, unify

#: How far past the body the cutting face is extended, as a fraction of the
#: body's diagonal. Generous on purpose: a face that stops short of the surface
#: makes the splitter succeed and quietly leave the two halves joined.
PLANE_MARGIN = 0.75


def _unit(vector):
    length = math.sqrt(sum(v * v for v in vector))
    if length < 1e-12:
        raise CadError(
            "The split plane has no direction.",
            suggestion="Choose X, Y, Z or a flat face to cut along.",
        )
    return tuple(v / length for v in vector)


def cutting_face(body, point, normal):
    """A planar face through *point*, comfortably larger than *body*."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt

    low, high = bounding_box(body)
    reach = math.dist(low, high) * (1.0 + PLANE_MARGIN) + 10.0
    plane = gp_Pln(gp_Pnt(*point), gp_Dir(*normal))
    return BRepBuilderAPI_MakeFace(plane, -reach, reach, -reach, reach).Face()


def _solids(shape) -> list:
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    found = []
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        found.append(TopoDS.Solid_s(explorer.Current()))
        explorer.Next()
    return found


def _merge(solids):
    """One shape from several solids -- a compound only when it has to be."""
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    if not solids:
        return None
    if len(solids) == 1:
        return solids[0]
    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    for solid in solids:
        builder.Add(compound, solid)
    return compound


def split_solid(body, point, normal):
    """Cut *body* with the plane at *point*. Returns ``(below, above)``.

    "Below" is the side the plane normal points away from. Either half may come
    back as None when the plane misses the body entirely, which the caller has
    to treat as an error rather than as an empty body.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Splitter
    from OCP.TopTools import TopTools_ListOfShape

    normal = _unit(normal)
    with guard("split"):
        arguments = TopTools_ListOfShape()
        arguments.Append(body)
        tools = TopTools_ListOfShape()
        tools.Append(cutting_face(body, point, normal))

        splitter = BRepAlgoAPI_Splitter()
        splitter.SetArguments(arguments)
        splitter.SetTools(tools)
        splitter.SetRunParallel(True)
        splitter.Build()
        if not splitter.IsDone():
            raise CadError(
                "The split could not be completed.",
                suggestion="Move the cut so it passes cleanly through the part.",
            )
        result = splitter.Shape()

    below, above = [], []
    for solid in _solids(result):
        centre = center_of_mass(solid)
        offset = sum((centre[i] - point[i]) * normal[i] for i in range(3))
        (above if offset > 0 else below).append(solid)

    return (_merge(below), _merge(above))


def side_extents(body, point, normal) -> tuple[float, float]:
    """How thick each side of the cut is, in mm, from the bounding box.

    Read off the bounds rather than from the cut geometry, because this is what
    the readout shows *while the plane is being dragged* -- sixty times a second
    is no place for a boolean. It is the honest number for the overwhelmingly
    common case of a cut through a solid part, and it never stalls the drag.
    """
    normal = _unit(normal)
    low, high = bounding_box(body)
    corners = [
        (x, y, z)
        for x in (low[0], high[0])
        for y in (low[1], high[1])
        for z in (low[2], high[2])
    ]
    offsets = [
        sum((corner[i] - point[i]) * normal[i] for i in range(3))
        for corner in corners
    ]
    return (max(0.0, -min(offsets)), max(0.0, max(offsets)))


@register("split")
class SplitFeature(Feature):
    """Divide a body in two with a plane.

    One node with **two** outputs, like ``ThreadedConnectionFeature`` -- which
    is the whole reason the feature graph is a DAG over the document rather than
    a linear history per body.
    """

    label = "Split"

    def consumed_bodies(self) -> list[str]:
        """The part that was split no longer exists -- its two halves do."""
        source = self.inputs.get("body")
        return [str(source)] if source else []

    def execute(self, ctx: BuildContext) -> dict:
        body = ctx.shape(self, "body")
        normal = _unit(tuple(self.inputs.get("normal") or (1.0, 0.0, 0.0)))
        origin = tuple(self.inputs.get("origin") or (0.0, 0.0, 0.0))
        position = ctx.value(self, "position", 0.0)
        point = tuple(origin[i] + normal[i] * position for i in range(3))

        source = str(self.inputs.get("body"))
        names = list(self.inputs.get("names") or [])
        if len(names) != 2:
            names = [f"{source} A", f"{source} B"]

        below, above = split_solid(body, point, normal)
        if below is None or above is None:
            reach = side_extents(body, point, normal)
            raise CadError(
                "The cut misses the body, so there is nothing to divide.",
                suggestion=(
                    f"Move the plane between 0 and "
                    f"{reach[0] + reach[1]:.2f} mm along the part."
                ),
            )

        self.message = (
            f"{names[0]} and {names[1]}, cut at {position:.2f} mm"
        )
        self.outputs = list(names)
        return self.keep({names[0]: unify(below), names[1]: unify(above)})
