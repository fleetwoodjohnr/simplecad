"""Turning a solved sketch into OCCT geometry.

Profiles are assembled by walking the sketch's non-construction entities into
wires, then closed wires into faces. A sketch can hold several closed loops --
an outline with holes in it -- so the outer loop becomes the face and the rest
become its holes, decided by area.
"""

from __future__ import annotations

import math

from ..core.errors import CadError, guard

#: Points closer than this in the sketch plane are treated as the same point.
WELD = 1e-6


def _point3d(plane, x: float, y: float):
    from OCP.gp import gp_Pnt

    return gp_Pnt(*plane.to_3d(x, y))


def entity_edges(sketch, entity) -> list:
    """OCCT edges for one sketch entity, placed on the sketch plane."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.gp import gp_Ax2, gp_Circ, gp_Dir

    plane = sketch.plane
    if entity.kind == "spline":
        return _spline_edges(sketch, entity)
    if entity.kind == "ellipse":
        return _ellipse_edges(sketch, entity)
    if entity.kind == "line":
        start = sketch.points[entity.start]
        end = sketch.points[entity.end]
        if math.hypot(end.x - start.x, end.y - start.y) < WELD:
            return []
        return [
            BRepBuilderAPI_MakeEdge(
                _point3d(plane, start.x, start.y), _point3d(plane, end.x, end.y)
            ).Edge()
        ]

    centre = sketch.points[entity.center]
    axis = gp_Ax2(
        _point3d(plane, centre.x, centre.y),
        gp_Dir(*plane.normal),
        gp_Dir(*plane.x_axis),
    )
    circle = gp_Circ(axis, entity.radius)
    if entity.kind == "circle":
        return [BRepBuilderAPI_MakeEdge(circle).Edge()]
    if entity.kind == "arc":
        return [
            BRepBuilderAPI_MakeEdge(
                circle, entity.start_angle, entity.end_angle
            ).Edge()
        ]
    return []


def _ellipse_edges(sketch, entity) -> list:
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.gp import gp_Ax2, gp_Dir, gp_Elips

    centre = sketch.points[entity.center]
    plane = sketch.plane
    major, minor = max(entity.major, entity.minor), min(entity.major, entity.minor)
    if minor <= 0 or major <= 0:
        return []
    # Rotate the reference direction within the plane to orient the major axis.
    rotated = _in_plane_direction(plane, entity.rotation)
    axis = gp_Ax2(
        _point3d(plane, centre.x, centre.y), gp_Dir(*plane.normal), gp_Dir(*rotated)
    )
    return [BRepBuilderAPI_MakeEdge(gp_Elips(axis, major, minor)).Edge()]


def _in_plane_direction(plane, angle: float) -> tuple[float, float, float]:
    x_axis, y_axis = plane.x_axis, plane.y_axis()
    return tuple(
        x_axis[i] * math.cos(angle) + y_axis[i] * math.sin(angle) for i in range(3)
    )


def _spline_edges(sketch, entity) -> list:
    """A B-spline interpolating the control points, in order."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.GeomAPI import GeomAPI_PointsToBSpline
    from OCP.TColgp import TColgp_Array1OfPnt

    coordinates = [sketch.points[pid] for pid in entity.points if pid in sketch.points]
    if len(coordinates) < 2:
        return []
    if len(coordinates) == 2:
        # Two points describe a line, and the spline fitter needs three.
        return [
            BRepBuilderAPI_MakeEdge(
                _point3d(sketch.plane, coordinates[0].x, coordinates[0].y),
                _point3d(sketch.plane, coordinates[1].x, coordinates[1].y),
            ).Edge()
        ]
    array = TColgp_Array1OfPnt(1, len(coordinates))
    for index, point in enumerate(coordinates, start=1):
        array.SetValue(index, _point3d(sketch.plane, point.x, point.y))
    try:
        curve = GeomAPI_PointsToBSpline(array).Curve()
    except Exception:  # noqa: BLE001 - a degenerate run of points is not a curve
        return []
    return [BRepBuilderAPI_MakeEdge(curve).Edge()]


def wires(sketch) -> list:
    """Every wire the sketch's profile geometry forms.

    Edges are handed to ``ShapeAnalysis_FreeBounds``, which sorts connected
    chains into wires -- rather than trying to trace loops here, where sketch
    topology and OCCT's tolerance would have to agree exactly.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire
    from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_HSequenceOfShape

    edges = []
    for entity in sketch.profile_entities():
        edges.extend(entity_edges(sketch, entity))
    if not edges:
        return []

    with guard("sketch"):
        sequence = TopTools_HSequenceOfShape()
        for edge in edges:
            sequence.Append(edge)
        closed = TopTools_HSequenceOfShape()
        open_wires = TopTools_HSequenceOfShape()
        ShapeAnalysis_FreeBounds.ConnectEdgesToWires_s(
            sequence, 1e-6, False, closed
        )

        found = []
        for index in range(1, closed.Length() + 1):
            wire = TopoDS.Wire_s(closed.Value(index))
            if is_closed(wire):
                found.append(wire)
        return found


def is_closed(wire, tolerance: float = 1e-6) -> bool:
    """Do the wire's ends meet?

    ``ConnectEdgesToWires`` returns open chains alongside closed ones, so this
    is checked explicitly. Relying on face construction to reject the open ones
    works by accident, and would stop working the moment OCCT got better at
    building faces from open wires.
    """
    from OCP.BRep import BRep_Tool
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS_Vertex

    first, last = TopoDS_Vertex(), TopoDS_Vertex()
    TopExp.Vertices_s(wire, first, last)
    if first.IsNull() or last.IsNull():
        return True          # no free ends at all
    return BRep_Tool.Pnt_s(first).Distance(BRep_Tool.Pnt_s(last)) <= tolerance


def faces(sketch) -> list:
    """Planar faces from the sketch's closed loops, holes cut out.

    A wire fully inside another is a hole in it, not a separate face -- which is
    what makes "circle inside a rectangle" extrude into a plate with a hole
    rather than two overlapping solids.
    """
    from OCP.BRep import BRep_Tool
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    found = wires(sketch)
    if not found:
        raise CadError(
            "This sketch has no closed profile.",
            suggestion="Close the outline, or join the ends of the open edges.",
        )

    sized = []
    for wire in found:
        try:
            face = BRepBuilderAPI_MakeFace(wire, True)
            if not face.IsDone():
                continue
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(face.Face(), props)
            sized.append((props.Mass(), wire))
        except Exception:  # noqa: BLE001 - a non-planar loop simply cannot be a face
            continue
    if not sized:
        raise CadError(
            "This sketch's profile could not be turned into a face.",
            suggestion="Check that the outline is closed and does not cross itself.",
        )

    sized.sort(key=lambda item: item[0], reverse=True)
    outer_area = sized[0][0]
    with guard("sketch"):
        builder = BRepBuilderAPI_MakeFace(sized[0][1], True)
        for _area, wire in sized[1:]:
            # An inner wire only cuts a hole when it runs opposite to the outer
            # one. Added the same way round, OCCT treats it as more material and
            # the face comes out *larger*. Reverse, then confirm by area rather
            # than trusting the sketch's winding order.
            builder.Add(_reversed_wire(wire))
            if _face_area(builder) > outer_area:
                builder = BRepBuilderAPI_MakeFace(sized[0][1], True)
                builder.Add(wire)
        return [builder.Face()]


def _reversed_wire(wire):
    from OCP.TopoDS import TopoDS

    return TopoDS.Wire_s(wire.Reversed())


def _face_area(builder) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    if not builder.IsDone():
        return float("inf")
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(builder.Face(), props)
    return props.Mass()


def profile_face(sketch):
    """The single face a sketch extrudes from."""
    return faces(sketch)[0]
