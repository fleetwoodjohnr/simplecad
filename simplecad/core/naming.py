"""Stable references to faces, edges and vertices across rebuilds.

This is the component the whole parametric premise rests on. When Sketch1
changes and Extrude1 -> Fillet1 -> Hole1 rebuild, the fillet must still land on
the edge the user picked. OCCT gives no stable identity for sub-shapes: the
order of ``TopExp`` iteration can change when upstream geometry changes, so a
reference stored as "edge #7" silently becomes a different edge. This is the
problem that dogged FreeCAD for a decade.

Resolution runs in three tiers:

1. **History maps** -- OCCT's own ``Generated``/``Modified``/``IsDeleted``
   records, forwarded through :class:`ShapeHistory`. Authoritative when the
   operation provides them.
2. **Geometric fingerprint** -- surface/curve type, size, centre, axis, radius,
   and for edges the sorted fingerprints of the two adjacent faces. The best
   match above a confidence threshold wins.
3. **Give up honestly** -- raise :class:`ReferenceLost` rather than pick
   something plausible. A fillet on the wrong edge is worse than one that stops
   and asks the user to re-pick.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Iterable

from .errors import ReferenceLost

#: Distances closer than this are treated as identical (millimetres).
POSITION_TOLERANCE = 1e-6
#: Below this score a candidate is never accepted as a match.
MATCH_THRESHOLD = 0.55
#: Above this score the search stops early -- it is certainly the same shape.
CERTAIN_SCORE = 0.999


def _kind_enum(kind: str):
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_VERTEX

    return {"face": TopAbs_FACE, "edge": TopAbs_EDGE, "vertex": TopAbs_VERTEX}[kind]


def sub_shapes(shape, kind: str) -> list:
    """Every sub-shape of *kind* in *shape*, in OCCT's traversal order.

    The order is deliberately treated as a hint only -- see the module docstring.
    """
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    mapping = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, _kind_enum(kind), mapping)
    return [mapping.FindKey(i) for i in range(1, mapping.Extent() + 1)]


@dataclass(frozen=True)
class Fingerprint:
    """A geometric description of a sub-shape that survives small edits."""

    kind: str
    geometry: str = "unknown"
    measure: float = 0.0
    center: tuple[float, float, float] = (0.0, 0.0, 0.0)
    direction: tuple[float, float, float] | None = None
    radius: float | None = None
    neighbours: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "geometry": self.geometry,
            "measure": self.measure,
            "center": list(self.center),
            "direction": list(self.direction) if self.direction else None,
            "radius": self.radius,
            "neighbours": list(self.neighbours),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Fingerprint":
        direction = data.get("direction")
        return cls(
            kind=data["kind"],
            geometry=data.get("geometry", "unknown"),
            measure=float(data.get("measure", 0.0)),
            center=tuple(data.get("center", (0.0, 0.0, 0.0))),
            direction=tuple(direction) if direction else None,
            radius=data.get("radius"),
            neighbours=tuple(data.get("neighbours", ())),
        )

    def summary(self) -> str:
        """A short neighbour key that is invariant under resizing.

        Deliberately excludes area and centre: those move when a parameter
        changes, which would make neighbour matching actively misleading
        precisely when it is needed most.
        """
        if self.direction is None:
            return self.geometry
        dx, dy, dz = (round(v, 3) + 0.0 for v in self.direction)
        return f"{self.geometry}:{dx},{dy},{dz}"


def _point(gp_point) -> tuple[float, float, float]:
    return (gp_point.X(), gp_point.Y(), gp_point.Z())


def _direction(gp_dir) -> tuple[float, float, float]:
    """Sign-normalise an axis whose direction is arbitrary (a line, say)."""
    vector = (gp_dir.X(), gp_dir.Y(), gp_dir.Z())
    for component in vector:
        if abs(component) > 1e-9:
            return vector if component > 0 else tuple(-v for v in vector)
    return vector


def _oriented(gp_dir, face) -> tuple[float, float, float]:
    """A face's *outward* normal, honouring the face's orientation.

    Sign matters here and must not be normalised away: the top and bottom faces
    of a box are parallel and equal in area, and their opposed outward normals
    are the only thing that reliably tells them apart.
    """
    from OCP.TopAbs import TopAbs_REVERSED

    vector = (gp_dir.X(), gp_dir.Y(), gp_dir.Z())
    if face.Orientation() == TopAbs_REVERSED:
        vector = tuple(-v for v in vector)
    return vector


def fingerprint(shape, kind: str, *, with_neighbours=None) -> Fingerprint:
    """Describe *shape* geometrically.

    ``with_neighbours`` is the parent shape; when given, an edge also records
    the faces on either side of it, which disambiguates otherwise identical
    edges (the four vertical edges of a box, for instance).
    """
    if kind == "face":
        return _face_fingerprint(shape)
    if kind == "edge":
        base = _edge_fingerprint(shape)
        if with_neighbours is not None:
            neighbours = tuple(sorted(_adjacent_faces(shape, with_neighbours)))
            base = replace(base, neighbours=neighbours)
        return base
    return _vertex_fingerprint(shape)


def _face_fingerprint(face) -> Fingerprint:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp
    from OCP.GeomAbs import (
        GeomAbs_BSplineSurface, GeomAbs_BezierSurface, GeomAbs_Cone,
        GeomAbs_Cylinder, GeomAbs_Plane, GeomAbs_Sphere, GeomAbs_Torus,
    )
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    center = _point(props.CentreOfMass())
    area = props.Mass()

    adaptor = BRepAdaptor_Surface(as_face(face))
    kind = adaptor.GetType()
    names = {
        GeomAbs_Plane: "plane",
        GeomAbs_Cylinder: "cylinder",
        GeomAbs_Cone: "cone",
        GeomAbs_Sphere: "sphere",
        GeomAbs_Torus: "torus",
        GeomAbs_BezierSurface: "bezier",
        GeomAbs_BSplineSurface: "bspline",
    }
    geometry = names.get(kind, "surface")
    direction: tuple[float, float, float] | None = None
    radius: float | None = None
    try:
        if kind == GeomAbs_Plane:
            direction = _oriented(adaptor.Plane().Axis().Direction(), face)
        elif kind == GeomAbs_Cylinder:
            cylinder = adaptor.Cylinder()
            direction = _oriented(cylinder.Axis().Direction(), face)
            radius = cylinder.Radius()
        elif kind == GeomAbs_Cone:
            cone = adaptor.Cone()
            direction = _oriented(cone.Axis().Direction(), face)
            radius = cone.RefRadius()
        elif kind == GeomAbs_Sphere:
            sphere = adaptor.Sphere()
            radius = sphere.Radius()
        elif kind == GeomAbs_Torus:
            torus = adaptor.Torus()
            direction = _oriented(torus.Axis().Direction(), face)
            radius = torus.MajorRadius()
    except Exception:  # noqa: BLE001 - degenerate surfaces still get a fingerprint
        pass
    return Fingerprint("face", geometry, area, center, direction, radius)


def _edge_fingerprint(edge) -> Fingerprint:
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepGProp import BRepGProp
    from OCP.GeomAbs import (
        GeomAbs_BSplineCurve, GeomAbs_BezierCurve, GeomAbs_Circle,
        GeomAbs_Ellipse, GeomAbs_Hyperbola, GeomAbs_Line, GeomAbs_Parabola,
    )
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.LinearProperties_s(edge, props)
    center = _point(props.CentreOfMass())
    length = props.Mass()

    geometry = "curve"
    direction: tuple[float, float, float] | None = None
    radius: float | None = None
    try:
        adaptor = BRepAdaptor_Curve(as_edge(edge))
        kind = adaptor.GetType()
        names = {
            GeomAbs_Line: "line",
            GeomAbs_Circle: "circle",
            GeomAbs_Ellipse: "ellipse",
            GeomAbs_Hyperbola: "hyperbola",
            GeomAbs_Parabola: "parabola",
            GeomAbs_BezierCurve: "bezier",
            GeomAbs_BSplineCurve: "bspline",
        }
        geometry = names.get(kind, "curve")
        if kind == GeomAbs_Line:
            direction = _direction(adaptor.Line().Direction())
        elif kind == GeomAbs_Circle:
            circle = adaptor.Circle()
            direction = _direction(circle.Axis().Direction())
            radius = circle.Radius()
        elif kind == GeomAbs_Ellipse:
            ellipse = adaptor.Ellipse()
            direction = _direction(ellipse.Axis().Direction())
            radius = ellipse.MajorRadius()
    except Exception:  # noqa: BLE001
        pass
    return Fingerprint("edge", geometry, length, center, direction, radius)


def _vertex_fingerprint(vertex) -> Fingerprint:
    from OCP.BRep import BRep_Tool

    point = BRep_Tool.Pnt_s(as_vertex(vertex))
    return Fingerprint("vertex", "point", 0.0, _point(point))


def _adjacent_faces(edge, parent) -> list[str]:
    """Summaries of the faces meeting at *edge*, for disambiguation."""
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

    mapping = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(parent, TopAbs_EDGE, TopAbs_FACE, mapping)
    if not mapping.Contains(edge):
        return []
    return [_face_fingerprint(face).summary() for face in mapping.FindFromKey(edge)]


# ----------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------
def _closeness(a: float, b: float, scale: float) -> float:
    """1.0 when equal, decaying smoothly with the difference."""
    return 1.0 / (1.0 + abs(a - b) / max(scale, 1e-9))


def similarity(left: Fingerprint, right: Fingerprint) -> float:
    """Score two fingerprints in 0..1. Different kinds always score 0."""
    if left.kind != right.kind:
        return 0.0
    # A plane never becomes a cylinder through a parameter change; treating a
    # geometry-type change as "similar" is how wrong matches get accepted.
    if left.geometry != right.geometry:
        return 0.0

    distance = math.dist(left.center, right.center)
    scale = max(math.sqrt(max(left.measure, right.measure, 1.0)), 1.0)
    scores = [(_closeness(distance, 0.0, scale), 3.0)]

    # Ratio, not difference: a face 85x larger is a different face, however
    # close its centre happens to fall.
    biggest = max(abs(left.measure), abs(right.measure))
    ratio = (
        min(abs(left.measure), abs(right.measure)) / biggest if biggest > 1e-12 else 1.0
    )
    scores.append((ratio, 3.0))

    if left.direction and right.direction:
        dot = sum(a * b for a, b in zip(left.direction, right.direction))
        # Map -1..1 onto 0..1 so an opposed normal scores zero rather than one,
        # then square it so a perpendicular face (0.5) is penalised properly
        # instead of sitting halfway to a match.
        aligned = max(0.0, min(1.0, (dot + 1.0) / 2.0))
        scores.append((aligned * aligned, 2.0))
    if left.radius is not None and right.radius is not None:
        scores.append((_closeness(left.radius, right.radius, max(left.radius, 1.0)), 1.5))
    if left.neighbours and right.neighbours:
        shared = len(set(left.neighbours) & set(right.neighbours))
        total = max(len(left.neighbours), len(right.neighbours))
        scores.append((shared / total if total else 0.0, 2.0))

    weight = sum(w for _s, w in scores)
    return sum(s * w for s, w in scores) / weight if weight else 0.0


# ----------------------------------------------------------------------
# References
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class SubShapeRef:
    """A durable reference to one face, edge or vertex of a feature's output.

    ``index_hint`` is only ever a fast path; correctness comes from
    ``fingerprint`` and, where available, the operation's history maps.
    """

    feature_id: str
    kind: str
    fingerprint: Fingerprint
    index_hint: int = -1
    role: str = ""
    body: str = ""

    def to_dict(self) -> dict:
        return {
            "feature_id": self.feature_id,
            "kind": self.kind,
            "fingerprint": self.fingerprint.to_dict(),
            "index_hint": self.index_hint,
            "role": self.role,
            "body": self.body,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SubShapeRef":
        return cls(
            feature_id=data["feature_id"],
            kind=data["kind"],
            fingerprint=Fingerprint.from_dict(data["fingerprint"]),
            index_hint=int(data.get("index_hint", -1)),
            role=data.get("role", ""),
            body=data.get("body", ""),
        )


def make_ref(
    parent_shape,
    sub_shape,
    feature_id: str,
    *,
    kind: str | None = None,
    role: str = "",
    body: str = "",
) -> SubShapeRef:
    """Build a reference to *sub_shape* within *parent_shape*."""
    kind = kind or shape_kind(sub_shape)
    candidates = sub_shapes(parent_shape, kind)
    index = -1
    for position, candidate in enumerate(candidates):
        if candidate.IsSame(sub_shape):
            index = position
            break
    return SubShapeRef(
        feature_id=feature_id,
        kind=kind,
        fingerprint=fingerprint(sub_shape, kind, with_neighbours=parent_shape),
        index_hint=index,
        role=role,
        body=body,
    )


def as_face(shape):
    """Downcast to ``TopoDS_Face``. OCCT's adaptors will not accept a bare shape."""
    from OCP.TopoDS import TopoDS

    return TopoDS.Face_s(shape)


def as_edge(shape):
    from OCP.TopoDS import TopoDS

    return TopoDS.Edge_s(shape)


def as_vertex(shape):
    from OCP.TopoDS import TopoDS

    return TopoDS.Vertex_s(shape)


def shape_kind(shape) -> str:
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_VERTEX

    return {
        TopAbs_FACE: "face",
        TopAbs_EDGE: "edge",
        TopAbs_VERTEX: "vertex",
    }.get(shape.ShapeType(), "shape")


class ShapeHistory:
    """Tier 1: forward sub-shape identity through an OCCT operation.

    Wraps whichever history an operation exposes -- a ``BRepBuilderAPI_MakeShape``
    subclass, or a ``BRepTools_History`` from a boolean -- behind one interface.
    """

    def __init__(self, builder=None, history=None) -> None:
        self._builder = builder
        self._history = history

    def modified(self, shape) -> list:
        out = []
        for source in (self._history, self._builder):
            if source is None:
                continue
            try:
                for item in source.Modified(shape):
                    out.append(item)
            except Exception:  # noqa: BLE001 - not all builders implement it
                continue
        return out

    def generated(self, shape) -> list:
        out = []
        for source in (self._history, self._builder):
            if source is None:
                continue
            try:
                for item in source.Generated(shape):
                    out.append(item)
            except Exception:  # noqa: BLE001
                continue
        return out

    def is_deleted(self, shape) -> bool:
        for source in (self._history, self._builder):
            if source is None:
                continue
            try:
                if source.IsDeleted(shape):
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def follow(self, shape):
        """Where *shape* ended up after the operation, or None if it vanished."""
        if self.is_deleted(shape):
            return None
        for candidate in self.modified(shape) or self.generated(shape):
            return candidate
        return shape


def resolve(
    ref: SubShapeRef,
    shape,
    *,
    history: ShapeHistory | None = None,
    threshold: float = MATCH_THRESHOLD,
):
    """Find the sub-shape *ref* denotes in *shape*.

    Raises :class:`ReferenceLost` rather than returning a doubtful match.
    """
    candidates = sub_shapes(shape, ref.kind)
    if not candidates:
        raise ReferenceLost(
            f"The {ref.kind} this feature depends on no longer exists.",
            suggestion="Re-select the geometry for this feature.",
        )

    # Tier 1: follow the operation's own history when we have it.
    if history is not None and 0 <= ref.index_hint < len(candidates):
        followed = history.follow(candidates[ref.index_hint])
        if followed is not None:
            for candidate in candidates:
                if candidate.IsSame(followed):
                    return candidate

    # Fast path: the hinted index still fingerprints as the same shape.
    if 0 <= ref.index_hint < len(candidates):
        hinted = candidates[ref.index_hint]
        score = similarity(
            ref.fingerprint, fingerprint(hinted, ref.kind, with_neighbours=shape)
        )
        if score >= CERTAIN_SCORE:
            return hinted

    # Tier 2: best geometric match anywhere in the shape.
    best = None
    best_score = 0.0
    runner_up = 0.0
    for candidate in candidates:
        score = similarity(
            ref.fingerprint, fingerprint(candidate, ref.kind, with_neighbours=shape)
        )
        if score > best_score:
            best, best_score, runner_up = candidate, score, best_score
        elif score > runner_up:
            runner_up = score

    if best is not None and best_score >= threshold:
        return best

    # Tier 3: refuse to guess.
    raise ReferenceLost(
        f"The {ref.kind} this feature was built on can no longer be found.",
        suggestion="The shape changed too much. Re-select the "
        f"{ref.kind} to repair this feature.",
        detail=f"best score {best_score:.3f}, runner-up {runner_up:.3f}",
    )


def try_resolve(ref: SubShapeRef, shape, **kwargs):
    """:func:`resolve` but returning None instead of raising."""
    try:
        return resolve(ref, shape, **kwargs)
    except ReferenceLost:
        return None
