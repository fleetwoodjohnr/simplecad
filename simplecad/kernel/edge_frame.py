"""Where to put a drag handle on an edge, and which way it should pull.

A fillet radius is a number, but nobody thinks in numbers when they are rounding
a corner -- they think "about that much". To let them say that with the mouse
the tool needs three things about the selected edge: a point to put the handle
on, a direction that unambiguously means *bigger*, and a limit to stop the drag
running away into geometry the kernel will refuse.

The direction is the bisector of the two adjacent faces' outward normals, which
points away from the material on a convex edge and into the void on a concave
one. Both are "away from the solid", so pulling outward means a larger fillet
either way round and the user never has to work out which kind of corner they
are looking at.
"""

from __future__ import annotations

import math

from .occ import bounding_box

#: A fillet may not exceed this fraction of the body's narrowest dimension.
#: A soft guard, not a correctness check -- the kernel decides what is really
#: buildable, and the tool shows a failed preview rather than pretending.
MAX_RADIUS_FRACTION = 0.49
#: Radii below this are treated as no fillet at all.
MIN_RADIUS = 0.05


def _normalise(vector):
    length = math.sqrt(sum(v * v for v in vector))
    if length < 1e-12:
        return None
    return tuple(v / length for v in vector)


def edge_midpoint(edge):
    """The point halfway along *edge*, by parameter."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve

    curve = BRepAdaptor_Curve(edge)
    middle = (curve.FirstParameter() + curve.LastParameter()) / 2.0
    point = curve.Value(middle)
    return (point.X(), point.Y(), point.Z())


def face_normal_at(face, point):
    """The outward normal of *face* nearest *point*, or None.

    Works on curved faces as well as flat ones, which matters because a fillet
    is just as often between a wall and a boss as between two walls.
    """
    from OCP.BRep import BRep_Tool
    from OCP.BRepGProp import BRepGProp_Face
    from OCP.gp import gp_Pnt, gp_Vec
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf

    try:
        surface = BRep_Tool.Surface_s(face)
        projection = GeomAPI_ProjectPointOnSurf(gp_Pnt(*point), surface)
        if projection.NbPoints() == 0:
            return None
        u, v = projection.LowerDistanceParameters()
        where, normal = gp_Pnt(), gp_Vec()
        # BRepGProp_Face applies the face's own orientation, so this is already
        # the outward normal -- flipping REVERSED faces here as well, which is
        # the obvious thing to write, turns every one of them inward.
        BRepGProp_Face(face).Normal(u, v, where, normal)
        return _normalise((normal.X(), normal.Y(), normal.Z()))
    except Exception:  # noqa: BLE001 - a degenerate face simply has no answer
        return None


def adjacent_faces(body, edge) -> list:
    """The faces of *body* that meet at *edge*."""
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

    mapping = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(body, TopAbs_EDGE, TopAbs_FACE, mapping)
    index = mapping.FindIndex(edge)
    if index <= 0:
        return []
    return list(mapping.FindFromIndex(index))


def edges_at_vertex(body, vertex) -> list:
    """Every edge of *body* meeting *vertex*, each once.

    What "fillet this corner" means: a corner is where three edges meet, and
    rounding it is rounding all three.

    The de-duplication is not tidiness. ``MapShapesAndAncestors`` lists an edge
    once for each face it belongs to, so a box corner comes back as six entries
    for three edges -- and a fillet then adds every edge to the builder twice
    and stores two references to each in the feature.
    """
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_VERTEX
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

    mapping = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(body, TopAbs_VERTEX, TopAbs_EDGE, mapping)
    index = mapping.FindIndex(vertex)
    if index <= 0:
        return []
    found, seen = [], set()
    for edge in mapping.FindFromIndex(index):
        key = edge.TShape()
        if key in seen:
            continue
        seen.add(key)
        found.append(edge)
    return found


def outward_direction(body, edge, at=None):
    """Which way "bigger" is for a fillet on *edge*. Falls back sensibly.

    When the adjacent faces cannot be read -- or their normals cancel, which
    happens on a seam -- the direction from the body's centre out to the edge is
    used instead. It is a worse answer geometrically and a perfectly good one
    for dragging, and having *a* direction always beats having no handle.
    """
    from OCP.TopoDS import TopoDS

    at = at or edge_midpoint(edge)
    total = (0.0, 0.0, 0.0)
    for face in adjacent_faces(body, edge):
        normal = face_normal_at(TopoDS.Face_s(face), at)
        if normal is not None:
            total = tuple(total[i] + normal[i] for i in range(3))
    direction = _normalise(total)
    if direction is not None:
        return direction

    low, high = bounding_box(body)
    center = tuple((low[i] + high[i]) / 2.0 for i in range(3))
    return _normalise(tuple(at[i] - center[i] for i in range(3))) or (0.0, 0.0, 1.0)


def max_radius(body) -> float:
    """A soft upper bound for a fillet on *body*."""
    low, high = bounding_box(body)
    narrowest = min(high[i] - low[i] for i in range(3))
    return max(MIN_RADIUS, narrowest * MAX_RADIUS_FRACTION)


def edge_drag_frame(body, edge):
    """``(anchor, direction, max_radius)`` for a handle on *edge*."""
    anchor = edge_midpoint(edge)
    return (anchor, outward_direction(body, edge, anchor), max_radius(body))
