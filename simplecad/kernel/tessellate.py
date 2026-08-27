"""Triangulating B-Rep shapes.

Used for mesh export (STL, OBJ, 3MF) and for the printability checks, which
reason about the surface a printer will actually see rather than the analytic
surfaces the kernel stores.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Chord height, in mm, between the true surface and its triangulation.
#: 0.05 keeps a 6 mm thread readable without producing enormous meshes.
DEFAULT_DEFLECTION = 0.05
#: Maximum angle between adjacent facet normals, radians.
DEFAULT_ANGLE = 0.35


@dataclass
class Mesh:
    """A triangle soup with welded vertices."""

    vertices: list[tuple[float, float, float]] = field(default_factory=list)
    triangles: list[tuple[int, int, int]] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.triangles)

    @property
    def is_empty(self) -> bool:
        return not self.triangles

    def bounds(self):
        if not self.vertices:
            return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        xs, ys, zs = zip(*self.vertices)
        return ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))


def triangulate(
    shape,
    deflection: float = DEFAULT_DEFLECTION,
    angle: float = DEFAULT_ANGLE,
) -> Mesh:
    """Mesh *shape*, welding vertices shared between faces."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    BRepMesh_IncrementalMesh(shape, deflection, False, angle, True)

    mesh = Mesh()
    index_of: dict[tuple[int, int, int], int] = {}

    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is None:
            explorer.Next()
            continue

        transform = location.Transformation()
        reversed_face = face.Orientation() == TopAbs_REVERSED
        local: dict[int, int] = {}

        for node in range(1, triangulation.NbNodes() + 1):
            point = triangulation.Node(node).Transformed(transform)
            # Weld on a 1 nm grid so faces meeting at an edge share vertices --
            # STL and 3MF readers treat unwelded meshes as non-manifold.
            key = (
                round(point.X() * 1e6),
                round(point.Y() * 1e6),
                round(point.Z() * 1e6),
            )
            existing = index_of.get(key)
            if existing is None:
                existing = len(mesh.vertices)
                index_of[key] = existing
                mesh.vertices.append((point.X(), point.Y(), point.Z()))
            local[node] = existing

        for index in range(1, triangulation.NbTriangles() + 1):
            a, b, c = triangulation.Triangle(index).Get()
            if reversed_face:
                a, c = c, a          # keep outward winding
            va, vb, vc = local[a], local[b], local[c]
            if va != vb and vb != vc and va != vc:
                mesh.triangles.append((va, vb, vc))
        explorer.Next()
    return mesh


def triangle_normal(mesh: Mesh, triangle) -> tuple[float, float, float]:
    import math

    a, b, c = (mesh.vertices[i] for i in triangle)
    u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    n = (
        u[1] * v[2] - u[2] * v[1],
        u[2] * v[0] - u[0] * v[2],
        u[0] * v[1] - u[1] * v[0],
    )
    length = math.sqrt(sum(component * component for component in n))
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return (n[0] / length, n[1] / length, n[2] / length)
