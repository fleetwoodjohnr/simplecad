"""Snap points: the places on a model a measurement wants to start and end.

Measuring between two arbitrary points on a surface is almost never what anyone
means. They mean corner to corner, hole centre to hole centre, edge midpoint to
the face opposite. So rather than let the cursor land anywhere, this enumerates
the places that mean something and the tool jumps to the nearest one.

Candidates are ranked, and priority breaks ties before distance does: a corner
sitting a few pixels further away than a point on the edge running into it is
still what the user is pointing at.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: Higher wins when two candidates are a similar distance from the cursor.
#:
#: The two negative entries are the fallbacks, and they are what stop the tool
#: appearing broken. Everything above zero is a *named* place -- a corner, a
#: hole centre -- and demanding one within a few pixels means that clicking in
#: the middle of a face finds nothing, so the click does nothing and the cursor
#: shows nothing. ``surface`` and ``on_edge`` are wherever the cursor ray
#: genuinely meets the model, ranked below every named place so they only ever
#: win when there is no named place anywhere near.
PRIORITY = {
    "vertex": 40,
    "center": 30,
    "midpoint": 20,
    "face": 10,
    "edge": 0,
    "on_edge": -5,
    "surface": -10,
}

#: How the tool names each kind of snap to the user.
LABELS = {
    "vertex": "corner",
    "center": "centre",
    "midpoint": "midpoint",
    "face": "face centre",
    "edge": "on edge",
    "on_edge": "on edge",
    "surface": "on face",
}

#: Above this many edges, on-edge snapping is skipped. Ray-to-curve extrema are
#: cheap individually and this runs on every mouse-move; a modelled thread has
#: thousands of edges and would turn the cursor to treacle.
MAX_EDGES_FOR_RAY = 64

#: Above this many faces, the named snaps on a shape are skipped.
#:
#: A backstop rather than the main defence. Face centres cost a surface
#: integration each (``analyse_plane`` runs ``BRepGProp.SurfaceProperties``), so
#: the work grows with the model while the mouse keeps sending events every few
#: milliseconds. This used to be 400, which is low enough that any real
#: imported part lost every corner and centre it had -- and losing them silently
#: is what made point-to-point measuring look broken on exactly the models
#: people measure. The viewport now times each snap and turns the effort down
#: on a body that cannot afford it (see occt_view.SNAP_BUDGET), so this only has
#: to stop the pathological case of enumerating a whole mesh.
MAX_FACES_FOR_SNAPS = 2500

#: Ancestor maps, keyed on the body they were built from. Rebuilding one is a
#: full traversal of the body, and the cursor sits over the same body for
#: thousands of consecutive mouse-moves.
_ANCESTORS: dict = {}
#: Enough for the bodies plausibly under one cursor, not a leak.
_ANCESTOR_LIMIT = 8


def clear_caches() -> None:
    """Drop memoised topology. Called when the model is rebuilt."""
    _ANCESTORS.clear()


@dataclass(frozen=True)
class SnapPoint:
    """One place worth snapping to."""

    position: tuple[float, float, float]
    kind: str

    @property
    def priority(self) -> int:
        return PRIORITY.get(self.kind, 0)

    @property
    def label(self) -> str:
        return LABELS.get(self.kind, self.kind)


def _point(pnt) -> tuple[float, float, float]:
    return (pnt.X(), pnt.Y(), pnt.Z())


def _vertices(shape) -> list[SnapPoint]:
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    found, seen = [], set()
    explorer = TopExp_Explorer(shape, TopAbs_VERTEX)
    while explorer.More():
        pnt = BRep_Tool.Pnt_s(TopoDS.Vertex_s(explorer.Current()))
        key = tuple(round(v, 6) for v in _point(pnt))
        if key not in seen:
            seen.add(key)
            found.append(SnapPoint(_point(pnt), "vertex"))
        explorer.Next()
    return found


def _edge_points(edge) -> list[SnapPoint]:
    """Midpoint of any edge, plus the centre of a circular one."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Circle

    found: list[SnapPoint] = []
    try:
        curve = BRepAdaptor_Curve(edge)
        first, last = curve.FirstParameter(), curve.LastParameter()
        found.append(SnapPoint(_point(curve.Value((first + last) / 2.0)), "midpoint"))
        if curve.GetType() == GeomAbs_Circle:
            # A hole's centre is the thing people actually measure between.
            found.append(
                SnapPoint(_point(curve.Circle().Location()), "center")
            )
    except Exception:  # noqa: BLE001 - a degenerate edge simply offers no snaps
        pass
    return found


def _edges_of(shape) -> list[SnapPoint]:
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    found, seen = [], set()
    explorer = TopExp_Explorer(shape, TopAbs_EDGE)
    while explorer.More():
        for snap in _edge_points(TopoDS.Edge_s(explorer.Current())):
            key = (snap.kind,) + tuple(round(v, 6) for v in snap.position)
            if key not in seen:
                seen.add(key)
                found.append(snap)
        explorer.Next()
    return found


def _face_points(shape) -> list[SnapPoint]:
    """Centres of the faces in *shape* -- flat ones and round ones alike."""
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    from .detect import analyse_cylinder, analyse_plane

    found: list[SnapPoint] = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        plane = analyse_plane(face)
        if plane is not None:
            found.append(SnapPoint(plane.center, "face"))
        else:
            cylinder = analyse_cylinder(face)
            if cylinder is not None:
                # The centre of the bore, halfway along it: what "the middle of
                # this hole" means when you point at its wall.
                origin, direction = cylinder.origin, cylinder.direction
                half = cylinder.length / 2.0
                found.append(SnapPoint(
                    tuple(origin[i] + direction[i] * half for i in range(3)),
                    "center",
                ))
        explorer.Next()
    return found


def _edge_face_map(parent):
    """Edge -> the faces using it, for *parent*, built at most once per body."""
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

    key = parent.TShape()
    found = _ANCESTORS.get(key)
    if found is None:
        found = TopTools_IndexedDataMapOfShapeListOfShape()
        TopExp.MapShapesAndAncestors_s(parent, TopAbs_EDGE, TopAbs_FACE, found)
        if len(_ANCESTORS) >= _ANCESTOR_LIMIT:
            _ANCESTORS.pop(next(iter(_ANCESTORS)))
        _ANCESTORS[key] = found
    return found


def _neighbours_of(face, parent) -> list:
    """The faces sharing an edge with *face*, within *parent*."""
    from OCP.TopAbs import TopAbs_EDGE

    mapping = _edge_face_map(parent)
    found, seen = [], set()
    explorer = _explore(face, TopAbs_EDGE)
    for edge in explorer:
        index = mapping.FindIndex(edge)
        if index <= 0:
            continue
        for other in mapping.FindFromIndex(index):
            if other.IsSame(face):
                continue
            key = other.TShape()
            if key in seen:
                continue
            seen.add(key)
            found.append(other)
    return found


def _count(shape, kind, limit: int = 10_000) -> int:
    """How many sub-shapes of *kind* are in *shape*, giving up past *limit*."""
    from OCP.TopExp import TopExp_Explorer

    total = 0
    explorer = TopExp_Explorer(shape, kind)
    while explorer.More() and total <= limit:
        total += 1
        explorer.Next()
    return total


def _explore(shape, kind) -> list:
    from OCP.TopExp import TopExp_Explorer

    found = []
    explorer = TopExp_Explorer(shape, kind)
    while explorer.More():
        found.append(explorer.Current())
        explorer.Next()
    return found


def snap_points(shape, parent=None) -> list[SnapPoint]:
    """Every snap candidate on *shape*, most specific kinds first.

    Works on whatever the viewport hands back -- a vertex, an edge, a face or a
    whole body -- because the explorers simply find nothing of a kind the shape
    does not contain.

    *parent* is the body the shape belongs to, and when it is given the faces
    *adjacent* to a detected face contribute their snaps too. Point at a box
    near a corner and OCCT quite reasonably reports the face under the cursor;
    the corner is on that face, but the midpoints of the edges running away from
    it are on the neighbours, and those are exactly what a user aiming near an
    edge is reaching for. Deliberately one ring of neighbours and no more --
    enumerating the whole body would be correct and far too slow.
    """
    from OCP.TopAbs import TopAbs_FACE, TopAbs_VERTEX
    from OCP.TopoDS import TopoDS

    if shape is None or shape.IsNull():
        return []
    if shape.ShapeType() == TopAbs_VERTEX:
        from OCP.BRep import BRep_Tool

        pnt = BRep_Tool.Pnt_s(TopoDS.Vertex_s(shape))
        return [SnapPoint(_point(pnt), "vertex")]

    if _count(shape, TopAbs_FACE, MAX_FACES_FOR_SNAPS + 1) > MAX_FACES_FOR_SNAPS:
        # Too big to enumerate on a mouse-move. ray_snaps still answers, so the
        # indicator keeps following the cursor -- see MAX_FACES_FOR_SNAPS.
        return []

    found = _vertices(shape) + _edges_of(shape) + _face_points(shape)
    if parent is not None and shape.ShapeType() == TopAbs_FACE:
        try:
            seen = {(s.kind,) + tuple(round(v, 6) for v in s.position) for s in found}
            for neighbour in _neighbours_of(TopoDS.Face_s(shape), parent):
                for snap in _vertices(neighbour) + _edges_of(neighbour):
                    key = (snap.kind,) + tuple(round(v, 6) for v in snap.position)
                    if key not in seen:
                        seen.add(key)
                        found.append(snap)
        except Exception:  # noqa: BLE001 - neighbours are a bonus, never required
            pass
    found.sort(key=lambda s: -s.priority)
    return found


def ray_snaps(shape, ray, parent=None) -> list[SnapPoint]:
    """Where the cursor ray actually meets *shape*.

    The continuity fallbacks: with these there is always a point under the
    cursor while it is over the model, so the indicator never blinks out and a
    click never silently does nothing. Both are ranked below every named snap in
    :data:`PRIORITY`, so they cede to a corner or a centre whenever one is near.
    """
    origin, direction = ray
    found: list[SnapPoint] = []
    surface = _surface_hit(shape, origin, direction)
    if surface is None and parent is not None:
        surface = _surface_hit(parent, origin, direction)
    if surface is not None:
        found.append(SnapPoint(surface, "surface"))
    on_edge = _edge_hit(shape, origin, direction)
    if on_edge is not None:
        found.append(SnapPoint(on_edge, "on_edge"))
    return found


def _surface_hit(shape, origin, direction):
    """The nearest point where the ray enters *shape*'s surface, or None."""
    from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt

    if shape is None or shape.IsNull():
        return None
    try:
        line = gp_Lin(gp_Pnt(*origin), gp_Dir(*direction))
        finder = BRepIntCurveSurface_Inter()
        finder.Init(shape, line, 1e-7)
        best = None
        while finder.More():
            where = finder.W()
            if where > 0.0 and (best is None or where < best[0]):
                point = finder.Pnt()
                best = (where, (point.X(), point.Y(), point.Z()))
            finder.Next()
    except Exception:  # noqa: BLE001 - an edge has no surface to hit
        return None
    return best[1] if best is not None else None


def _edge_hit(shape, origin, direction):
    """The point on the nearest edge of *shape* closest to the ray, or None."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.Geom import Geom_Line
    from OCP.GeomAPI import GeomAPI_ExtremaCurveCurve
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopoDS import TopoDS

    edges = _explore(shape, TopAbs_EDGE)
    if not edges or len(edges) > MAX_EDGES_FOR_RAY:
        return None
    try:
        line = Geom_Line(gp_Lin(gp_Pnt(*origin), gp_Dir(*direction)))
    except Exception:  # noqa: BLE001
        return None

    best = None
    for shape_edge in edges:
        try:
            edge = TopoDS.Edge_s(shape_edge)
            adaptor = BRepAdaptor_Curve(edge)
            first, last = adaptor.FirstParameter(), adaptor.LastParameter()
            curve = BRep_Tool.Curve_s(edge, 0.0, 0.0)
            if curve is None:
                continue
            extrema = GeomAPI_ExtremaCurveCurve(curve, line)
            if extrema.NbExtrema() == 0:
                continue
            # BRep_Tool hands back the *underlying* curve, which for a straight
            # edge is an infinite line, so the extremum can sit well off the end
            # of the edge the user can actually see. Clamping to the edge's own
            # parameter range is what keeps the snap on the model.
            parameter, _on_ray = extrema.LowerDistanceParameters()
            parameter = max(first, min(last, parameter))
            point = adaptor.Value(parameter)
            distance = extrema.LowerDistance()
            if best is None or distance < best[0]:
                best = (distance, (point.X(), point.Y(), point.Z()))
        except Exception:  # noqa: BLE001 - a degenerate edge offers no snap
            continue
    return best[1] if best is not None else None


def nearest(candidates, project, cursor, radius: float = 22.0):
    """The best snap for a cursor position, or None if nothing is close.

    *project* maps a 3D point to screen pixels. Distance is measured on screen,
    not in the model, so the snap radius stays the same however far you have
    zoomed in -- a snap that gets harder to hit as you zoom out is not a snap.
    """
    best, best_score = None, None
    for snap in candidates:
        screen = project(snap.position)
        if screen is None:
            continue
        distance = math.hypot(screen[0] - cursor[0], screen[1] - cursor[1])
        if distance > radius:
            continue
        # Priority dominates; distance only separates equals. Subtracting the
        # distance in pixels from a priority spaced 10 apart means a corner wins
        # over a midpoint unless the midpoint is more than 10 px closer.
        score = snap.priority - distance
        if best_score is None or score > best_score:
            best, best_score = snap, score
    return best
