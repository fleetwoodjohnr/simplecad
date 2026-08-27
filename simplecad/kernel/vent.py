"""Vents: a hex grid of holes, as a plate or as a cut through an existing wall.

This is the fan grille every printed enclosure needs. Hexagons rather than
circles because that is what the packing wants -- a hex lattice of hexagons
leaves a constant wall between neighbours in every direction, so the grille has
one wall thickness rather than three, and an FDM printer reproduces it evenly.

The one performance rule here: build **one** compound of every hole and cut it
in a single boolean. A 60 mm vent is well over a hundred holes, and cutting them
one at a time turns seconds of work into minutes.
"""

from __future__ import annotations

import math

from ..core.document import BuildContext, Feature, register
from ..core.errors import CadError, guard
from .occ import built_shape, make_transform, transformed

#: Below this the grille stops being printable on an FDM machine.
MIN_WALL = 0.4
MIN_ACROSS_FLATS = 0.8


def hex_positions(
    width: float,
    height: float,
    across_flats: float,
    wall: float,
    margin: float,
) -> list[tuple[float, float]]:
    """Centres of every hole that fits, in a rectangle centred on the origin.

    Rows are offset by half a pitch, which is what makes it a hex lattice rather
    than a square one. A hole is kept only if its whole footprint clears the
    margin: a grille with half-holes chewed out of its border looks like a
    mistake, and on a real part the border is what carries the load.
    """
    pitch = across_flats + wall
    if pitch <= 0:
        return []
    # Circumradius: how far a corner reaches from the centre. That, not the
    # across-flats size, is what has to clear the border.
    circum = across_flats / math.sqrt(3.0)
    row_step = pitch * math.sqrt(3.0) / 2.0

    half_w = width / 2.0 - margin
    half_h = height / 2.0 - margin
    if half_w <= 0 or half_h <= 0:
        return []

    positions: list[tuple[float, float]] = []
    rows = int(half_h / row_step) + 2
    columns = int(half_w / pitch) + 2
    for row in range(-rows, rows + 1):
        y = row * row_step
        # Odd rows shift by half a pitch -- the offset that turns a square
        # lattice into a hexagonal one.
        offset = (pitch / 2.0) if row % 2 else 0.0
        for column in range(-columns, columns + 1):
            x = column * pitch + offset
            if abs(x) + across_flats / 2.0 > half_w:
                continue
            if abs(y) + circum > half_h:
                continue
            positions.append((x, y))
    return positions


def _hex_prism(across_flats: float, depth: float):
    """One hexagonal prism, centred on the origin, running along +Z from -depth/2."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Pnt, gp_Vec

    circum = across_flats / math.sqrt(3.0)
    polygon = BRepBuilderAPI_MakePolygon()
    for index in range(6):
        angle = math.pi / 6.0 + index * math.pi / 3.0
        polygon.Add(
            gp_Pnt(circum * math.cos(angle), circum * math.sin(angle), -depth / 2.0)
        )
    polygon.Close()
    face = BRepBuilderAPI_MakeFace(polygon.Wire()).Face()
    return BRepPrimAPI_MakePrism(face, gp_Vec(0.0, 0.0, depth)).Shape()


def hex_grid_compound(
    width: float,
    height: float,
    depth: float,
    across_flats: float,
    wall: float,
    margin: float,
):
    """Every hole as a single compound, ready for one boolean cut.

    Returns ``(compound, count)``. The compound is centred on the origin and
    spans *depth* along Z, so the caller positions it rather than this doing it.
    """
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    positions = hex_positions(width, height, across_flats, wall, margin)
    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    if not positions:
        return compound, 0

    prototype = _hex_prism(across_flats, depth)
    for x, y in positions:
        builder.Add(compound, transformed(prototype, make_transform(translate=(x, y, 0.0))))
    return compound, len(positions)


def _check(across_flats: float, wall: float, margin: float) -> None:
    if across_flats < MIN_ACROSS_FLATS:
        raise CadError(
            f"A {across_flats:.2f} mm hole is too small to print cleanly.",
            suggestion=f"Use at least {MIN_ACROSS_FLATS:.1f} mm across flats.",
        )
    if wall < MIN_WALL:
        raise CadError(
            f"A {wall:.2f} mm wall between holes is thinner than a printed line.",
            suggestion=f"Use at least {MIN_WALL:.1f} mm.",
        )
    if margin < 0:
        raise CadError("The border cannot be negative.")


def _no_holes_error(what: str) -> CadError:
    return CadError(
        f"No vent holes fit inside this {what}.",
        suggestion="Use a smaller hole size, a thinner wall, or a smaller border.",
    )


@register("vent_plate")
class VentPlateFeature(Feature):
    """A flat plate perforated with a hex grid -- a fan grille on its own."""

    label = "Vent"

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.gp import gp_Pnt

        width = ctx.value(self, "width", 60.0)
        depth = ctx.value(self, "depth", 60.0)
        thickness = ctx.value(self, "thickness", 3.0)
        across_flats = ctx.value(self, "across_flats", 5.0)
        wall = ctx.value(self, "wall", 1.2)
        margin = ctx.value(self, "margin", 2.0)
        for value, what in ((width, "width"), (depth, "depth"), (thickness, "thickness")):
            if value <= 0:
                raise CadError(
                    f"The {what} must be greater than zero.",
                    suggestion=f"Enter a positive {what}.",
                )
        _check(across_flats, wall, margin)

        with guard("vent"):
            plate = BRepPrimAPI_MakeBox(
                gp_Pnt(-width / 2.0, -depth / 2.0, 0.0), width, depth, thickness
            ).Shape()
            # Overshoot the plate so the cut leaves clean faces top and bottom.
            holes, count = hex_grid_compound(
                width, depth, thickness * 4.0, across_flats, wall, margin
            )
            if not count:
                raise _no_holes_error("plate")
            holes = transformed(
                holes, make_transform(translate=(0.0, 0.0, thickness / 2.0))
            )
            result = built_shape(BRepAlgoAPI_Cut(plate, holes), "vent")

        name = self.outputs[0] if self.outputs else self.name
        self.outputs = [name]
        placement = make_transform(
            translate=(
                ctx.value(self, "x", 0.0),
                ctx.value(self, "y", 0.0),
                ctx.value(self, "z", 0.0),
            )
        )
        return {name: transformed(result, placement)}


@register("vent_cut")
class VentCutFeature(Feature):
    """Perforate a flat face of an existing body with the same hex grid."""

    label = "Vent"

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf

        from .detect import analyse_plane

        body = ctx.shape(self, "body")
        name = self.inputs.get("body")
        if not name:
            raise CadError("This vent has no body to cut into.")

        # resolve() hands back a single shape for a single reference -- the same
        # shape PushPullFeature relies on, so keep the idiom identical.
        from OCP.TopoDS import TopoDS

        resolved = ctx.resolve(self, "face")
        if resolved is None:
            raise CadError(
                "Select the flat face to vent.",
                suggestion="Pick the wall the fan blows through.",
            )
        face = TopoDS.Face_s(resolved)
        info = analyse_plane(face)
        if info is None:
            raise CadError(
                "A vent needs a flat face.",
                suggestion="Pick a planar wall, not a curved one.",
            )

        across_flats = ctx.value(self, "across_flats", 5.0)
        wall = ctx.value(self, "wall", 1.2)
        margin = ctx.value(self, "margin", 2.0)
        _check(across_flats, wall, margin)

        width, height = _face_extent(face)
        # The grid is built flat on Z=0 and then carried onto the face, so the
        # packing maths never has to know about the face's orientation.
        thickness = _body_depth(body, info.normal)
        holes, count = hex_grid_compound(
            width, height, thickness * 4.0 + 10.0, across_flats, wall, margin
        )
        if not count:
            raise _no_holes_error("face")

        placement = gp_Trsf()
        placement.SetTransformation(
            gp_Ax3(gp_Pnt(*info.center), gp_Dir(*info.normal)), gp_Ax3()
        )
        holes = transformed(holes, placement)

        with guard("vent"):
            result = built_shape(BRepAlgoAPI_Cut(body, holes), "vent")

        self.outputs = [str(name)]
        return {str(name): result}


def _face_extent(face) -> tuple[float, float]:
    """The face's size in its own plane, as (u, v) in mm."""
    from OCP.BRepTools import BRepTools

    u_min, u_max, v_min, v_max = BRepTools.UVBounds_s(face)
    return (abs(u_max - u_min), abs(v_max - v_min))


def _body_depth(shape, direction) -> float:
    """How far the body reaches along *direction* -- how deep the cut must go."""
    from .occ import bounding_box

    low, high = bounding_box(shape)
    return sum(abs((high[i] - low[i]) * direction[i]) for i in range(3)) or 10.0
