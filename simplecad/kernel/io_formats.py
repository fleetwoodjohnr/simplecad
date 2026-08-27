"""Import and export.

STEP and STL come from OCCT. OBJ and 3MF are written here: this OCCT build has
no ``RWObj`` and no 3MF at all, and both formats are simple enough from a
triangulation that pulling in another dependency would be the worse trade.

3MF matters in particular -- it is the format modern slicers prefer, and
``Export for Printing`` produces it.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ElementTree
import zipfile
from dataclasses import dataclass

from ..core.errors import CadError, guard
from .tessellate import Mesh, triangulate

#: Extension -> human label, for file dialogs.
EXPORT_FORMATS = {
    ".step": "STEP (parametric solid)",
    ".stp": "STEP (parametric solid)",
    ".stl": "STL (mesh)",
    ".3mf": "3MF (mesh, for slicers)",
    ".obj": "OBJ (mesh)",
}


def quiet_kernel_messages() -> None:
    """Stop OCCT printing transfer statistics to the terminal.

    The STEP reader and writer are chatty by default. Users should see
    SimpleCAD's own messages, not the kernel's progress notes.
    """
    try:
        from OCP.Message import Message, Message_Gravity

        messenger = Message.DefaultMessenger_s()
        for printer in messenger.Printers():
            printer.SetTraceLevel(Message_Gravity.Message_Alarm)
    except Exception:  # noqa: BLE001 - never let logging config break export
        pass


def _compound(shapes):
    """Bundle several shapes into one compound."""
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    if len(shapes) == 1:
        return shapes[0]
    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    for shape in shapes:
        builder.Add(compound, shape)
    return compound


# ----------------------------------------------------------------------
# Export
# ----------------------------------------------------------------------
def export_shapes(shapes, path: str, *, deflection: float = 0.05) -> str:
    """Write *shapes* to *path*, choosing the writer from the extension."""
    shapes = [s for s in shapes if s is not None]
    if not shapes:
        raise CadError(
            "There is nothing to export.",
            suggestion="Create or unhide a body first.",
        )
    extension = os.path.splitext(path)[1].lower()
    writer = {
        ".step": export_step, ".stp": export_step,
        ".stl": export_stl, ".3mf": export_3mf, ".obj": export_obj,
    }.get(extension)
    if writer is None:
        raise CadError(
            f"SimpleCAD cannot write '{extension}' files.",
            suggestion="Use STEP, STL, 3MF or OBJ.",
        )
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    if writer is export_step:
        return writer(shapes, path)
    return writer(shapes, path, deflection=deflection)


def export_step(shapes, path: str) -> str:
    """STEP keeps the analytic solid, so the file stays useful in other CAD."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    quiet_kernel_messages()
    with guard("export"):
        writer = STEPControl_Writer()
        Interface_Static.SetCVal_s("write.step.unit", "MM")
        Interface_Static.SetCVal_s("write.step.schema", "AP214IS")
        for shape in shapes:
            writer.Transfer(shape, STEPControl_AsIs)
        status = writer.Write(path)
    # IFSelect_RetDone is 1, not 0 -- comparing against zero rejects every
    # successful write.
    if status != IFSelect_RetDone:
        raise CadError(
            "The STEP file could not be written.",
            suggestion="Check that the folder exists and is writable.",
        )
    return path


def export_stl(shapes, path: str, *, deflection: float = 0.05, binary: bool = True) -> str:
    from OCP.StlAPI import StlAPI_Writer

    from .tessellate import DEFAULT_ANGLE

    with guard("export"):
        from OCP.BRepMesh import BRepMesh_IncrementalMesh

        shape = _compound(shapes)
        BRepMesh_IncrementalMesh(shape, deflection, False, DEFAULT_ANGLE, True)
        writer = StlAPI_Writer()
        writer.ASCIIMode = not binary
        if not writer.Write(shape, path):
            raise CadError("The STL file could not be written.")
    return path


def export_obj(shapes, path: str, *, deflection: float = 0.05) -> str:
    mesh = triangulate(_compound(shapes), deflection)
    if mesh.is_empty:
        raise CadError("There is no surface to export.")
    with open(path, "w") as handle:
        handle.write("# SimpleCAD\n")
        for x, y, z in mesh.vertices:
            handle.write(f"v {x:.6f} {y:.6f} {z:.6f}\n")
        for a, b, c in mesh.triangles:
            handle.write(f"f {a + 1} {b + 1} {c + 1}\n")   # OBJ is 1-indexed
    return path


#: The 3MF core namespace.
MODEL_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"


def export_3mf(shapes, path: str, *, deflection: float = 0.05) -> str:
    """A minimal but valid 3MF: one object per body, millimetre units."""
    meshes = [(triangulate(shape, deflection), index)
              for index, shape in enumerate(shapes, start=1)]
    meshes = [(mesh, index) for mesh, index in meshes if not mesh.is_empty]
    if not meshes:
        raise CadError("There is no surface to export.")

    model = ElementTree.Element("model", {"unit": "millimeter", "xmlns": MODEL_NS})
    resources = ElementTree.SubElement(model, "resources")
    build = ElementTree.SubElement(model, "build")

    for mesh, index in meshes:
        obj = ElementTree.SubElement(
            resources, "object", {"id": str(index), "type": "model"}
        )
        mesh_element = ElementTree.SubElement(obj, "mesh")
        vertices = ElementTree.SubElement(mesh_element, "vertices")
        for x, y, z in mesh.vertices:
            ElementTree.SubElement(
                vertices, "vertex",
                {"x": f"{x:.6f}", "y": f"{y:.6f}", "z": f"{z:.6f}"},
            )
        triangles = ElementTree.SubElement(mesh_element, "triangles")
        for a, b, c in mesh.triangles:
            ElementTree.SubElement(
                triangles, "triangle", {"v1": str(a), "v2": str(b), "v3": str(c)}
            )
        ElementTree.SubElement(build, "item", {"objectid": str(index)})

    relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
        'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
        "</Relationships>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
        "</Types>"
    )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr(
            "3D/3dmodel.model",
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            + ElementTree.tostring(model, encoding="unicode"),
        )
    return path


# ----------------------------------------------------------------------
# Import
# ----------------------------------------------------------------------
#: Triangle count above which a mesh is not rebuilt as a solid.
#:
#: Rebuilding turns a triangle soup into real B-Rep topology, which is what
#: makes an imported mesh something you can split, boolean and shell rather
#: than merely look at. Building it from the mesh's own vertex indices is
#: linear -- measured at about 0.09 ms a triangle, so 15k triangles is 1.4 s
#: and 30k is 2.8 s -- which is what makes a limit this high reasonable at all.
#: (Asking BRepBuilderAPI_Sewing to rediscover the same topology geometrically
#: is not linear: on a real slicer mesh it went from 0.9 s at 2,200 triangles to
#: over six minutes at 2,600. That path survives only as a fallback.)
#:
#: The file is read twice -- once here for placement and once in the geometry
#: process during the rebuild -- so the budget is half of what the user waits.
#: Past this size the triangulation is kept as-is: the body still imports,
#: displays, moves and measures, and the feature says plainly that the heavier
#: operations will not work on it.
REBUILD_TRIANGLE_LIMIT = 40_000

#: Triangle count above which coplanar faces are not merged back together.
#:
#: Merging is what turns an imported box into six faces you can select, pull
#: and sketch on rather than twelve triangles you can do nothing with. It is
#: worth its cost twice over: on a real 21-part slicer project it took the face
#: count from 83,949 to 37,631, and everything downstream -- selection,
#: display, and snapping in particular, which is per-face and runs on every
#: mouse-move -- then does less than half the work for the rest of the session.
#:
#: It is a second pass over the topology, measured at about 0.07 ms a triangle
#: (6 s across that project's 89,000). Past this size a body is more use
#: quickly than tidily, and a mesh that large is usually a scan rather than
#: something anyone is about to pull a face on.
UNIFY_TRIANGLE_LIMIT = 20_000

#: Boundary edges times suspect vertices above which zero-area slivers are left
#: alone. Generous: a real part's slivers give a product in the hundreds.
MAX_SLIVER_REPAIR_WORK = 2_000_000


@dataclass(frozen=True)
class ImportedBody:
    """One body read out of a file, with whatever name the file gave it."""

    name: str
    shape: object
    #: Set when the format or the file forced a compromise worth mentioning.
    note: str = ""


def _explode_solids(shape, name: str) -> list[ImportedBody]:
    """Split a compound into its solids, so separate parts stay separate.

    A STEP assembly arrives as one compound; treating it as one body would
    weld a gearbox into a single lump the moment it was imported.
    """
    from OCP.TopAbs import TopAbs_SHELL, TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    if shape is None or shape.IsNull():
        return []
    found = []
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        found.append(explorer.Current())
        explorer.Next()
    if not found:
        # No solids: a surface model is still worth importing, as shells.
        explorer = TopExp_Explorer(shape, TopAbs_SHELL)
        while explorer.More():
            found.append(explorer.Current())
            explorer.Next()
    if not found:
        return [ImportedBody(name, shape)]
    if len(found) == 1:
        return [ImportedBody(name, found[0])]
    return [
        ImportedBody(f"{name} {index}", solid)
        for index, solid in enumerate(found, start=1)
    ]


def _base_name(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0] or "Imported"


# -- STEP ---------------------------------------------------------------
def _step_units_to_mm() -> None:
    """Read STEP files into millimetres whatever unit they declare.

    A STEP file carries its own unit, and a part modelled in inches that arrives
    scaled by 25.4 is not an import, it is a bug the user discovers at the
    printer.
    """
    from OCP.Interface import Interface_Static

    Interface_Static.SetCVal_s("xstep.cascade.unit", "MM")


def _step_with_names(path: str) -> list[ImportedBody]:
    """Read a STEP file through XCAF, which knows the part names."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDF import TDF_LabelSequence
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    document = TDocStd_Document(TCollection_ExtendedString("simplecad"))
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    if reader.ReadFile(path) != IFSelect_RetDone:
        raise CadError(f"'{os.path.basename(path)}' is not a readable STEP file.")
    if not reader.Transfer(document):
        return []

    tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    labels = TDF_LabelSequence()
    tool.GetFreeShapes(labels)

    fallback = _base_name(path)
    bodies: list[ImportedBody] = []
    for index in range(1, labels.Length() + 1):
        label = labels.Value(index)
        shape = tool.GetShape_s(label)
        if shape is None or shape.IsNull():
            continue
        name = fallback if labels.Length() == 1 else f"{fallback} {index}"
        attribute = TDataStd_Name()
        if label.FindAttribute(TDataStd_Name.GetID_s(), attribute):
            found = str(attribute.Get().ToExtString() or "").strip()
            if found and not _is_boilerplate(found):
                name = found
        bodies.extend(_explode_solids(shape, name))
    return bodies


#: Names exporters leave behind that say nothing about the part. Left alone,
#: every STEP file from a CAD package that does not name its bodies would import
#: as "Open CASCADE STEP translator 7.9 1", which is worse than the filename.
_BOILERPLATE = ("open cascade", "step translator", "compound", "shape", "unnamed")


def _is_boilerplate(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in _BOILERPLATE)


def import_step(path: str) -> list[ImportedBody]:
    quiet_kernel_messages()
    _step_units_to_mm()
    with guard("import"):
        try:
            bodies = _step_with_names(path)
        except CadError:
            raise
        except Exception:  # noqa: BLE001 - XCAF is a bonus; the plain reader is not
            bodies = []
        if bodies:
            return bodies

        from OCP.IFSelect import IFSelect_RetDone
        from OCP.STEPControl import STEPControl_Reader

        reader = STEPControl_Reader()
        if reader.ReadFile(path) != IFSelect_RetDone:
            raise CadError(
                f"'{os.path.basename(path)}' is not a readable STEP file.",
                suggestion="Re-export it as AP203 or AP214 and try again.",
            )
        reader.TransferRoots()
        return _explode_solids(reader.OneShape(), _base_name(path))


# -- IGES ---------------------------------------------------------------
def import_iges(path: str) -> list[ImportedBody]:
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.IGESControl import IGESControl_Reader

    quiet_kernel_messages()
    with guard("import"):
        reader = IGESControl_Reader()
        if reader.ReadFile(path) != IFSelect_RetDone:
            raise CadError(
                f"'{os.path.basename(path)}' is not a readable IGES file."
            )
        reader.TransferRoots()
        return _explode_solids(reader.OneShape(), _base_name(path))


# -- native B-Rep -------------------------------------------------------
def import_brep(path: str) -> list[ImportedBody]:
    from OCP.BRep import BRep_Builder
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape

    with guard("import"):
        shape = TopoDS_Shape()
        BRepTools.Read_s(shape, path, BRep_Builder())
        if shape.IsNull():
            raise CadError(
                f"'{os.path.basename(path)}' could not be read as a B-Rep file."
            )
        return _explode_solids(shape, _base_name(path))


# -- meshes -------------------------------------------------------------
def _components(vertices, triangles) -> list[list[tuple[int, int, int]]]:
    """Split the triangles into connected pieces, by shared vertex index.

    One STL routinely holds several disconnected objects, and each has to
    become its own shell -- otherwise a file with a lid and a base yields one
    body that is closed nowhere.
    """
    parent = list(range(len(vertices)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        a, b = find(i), find(j)
        if a != b:
            parent[b] = a

    for a, b, c in triangles:
        union(a, b)
        union(a, c)
    groups: dict[int, list] = {}
    for triangle in triangles:
        groups.setdefault(find(triangle[0]), []).append(triangle)
    return list(groups.values())


def _signed_volume(vertices, triangles) -> float:
    """Six times the enclosed volume. Negative means the winding is inside-out."""
    total = 0.0
    for a, b, c in triangles:
        ax, ay, az = vertices[a]
        bx, by, bz = vertices[b]
        cx, cy, cz = vertices[c]
        total += (
            ax * (by * cz - bz * cy)
            - ay * (bx * cz - bz * cx)
            + az * (bx * cy - by * cx)
        )
    return total


def _triangle_normal(vertices, a: int, b: int, c: int):
    """The outward unit normal of one triangle, or None if it has no area."""
    import math

    ax, ay, az = vertices[a]
    bx, by, bz = vertices[b]
    cx, cy, cz = vertices[c]
    ux, uy, uz = bx - ax, by - ay, bz - az
    vx, vy, vz = cx - ax, cy - ay, cz - az
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length < 1e-12:
        return None
    return (nx / length, ny / length, nz / length)


def _heal_slivers(vertices, triangles):
    """Remove zero-area triangles, and mend the T-junctions that leaves.

    Real slicer meshes contain fans of exactly collinear points -- triangles
    with three distinct corners and no area at all. They cannot become faces:
    there is no plane through a line. Simply dropping them is what turns a mesh
    that is closed in the file into a shell with a slit in it, because the
    edges those triangles paired are left used once.

    The slit is a T-junction: a corner of one triangle sitting part-way along
    another triangle's edge. The repair is the standard one -- split the
    offending edge at that point, so both sides agree where the edge is. Only
    vertices that took part in a sliver can be at fault, and there are a
    handful of those, so this costs a scan rather than a search.

    Returns ``(vertices, triangles)``, both possibly extended.
    """
    import math

    good, suspects = [], set()
    for a, b, c in triangles:
        if a == b or b == c or a == c:
            suspects.update((a, b, c))
            continue
        if _triangle_normal(vertices, a, b, c) is None:
            suspects.update((a, b, c))
            continue
        good.append((a, b, c))
    if not suspects or not good:
        return (list(vertices), good)

    # Only edges that the slivers left unpaired are candidates. Splitting an
    # edge that is already shared by two triangles would be busywork at best,
    # and where the new point is a corner of a neighbouring triangle it makes
    # the mesh non-manifold -- four faces meeting on one edge, which is worse
    # than the slit being mended.
    uses: dict[tuple[int, int], int] = {}
    for a, b, c in good:
        for u, v in ((a, b), (b, c), (c, a)):
            key = (u, v) if u < v else (v, u)
            uses[key] = uses.get(key, 0) + 1
    boundary = {key for key, count in uses.items() if count != 2}
    if not boundary:
        return (list(vertices), good)
    # The search below is boundary edges times suspect vertices. On a mesh that
    # is closed apart from a few slivers -- the case this exists for -- both are
    # tiny. On a mesh that is genuinely a surface, or one that is mostly
    # rubbish, both can be large, and mending it is not worth stalling the
    # import for: it imports as a shell, and says so.
    if len(boundary) * len(suspects) > MAX_SLIVER_REPAIR_WORK:
        return (list(vertices), good)

    def distance(i: int, j: int) -> float:
        return math.dist(vertices[i], vertices[j])

    def between(u: int, v: int) -> list[int]:
        """The suspect vertices lying strictly along the unpaired edge u-v."""
        key = (u, v) if u < v else (v, u)
        if key not in boundary:
            return []
        span = distance(u, v)
        if span <= 1e-12:
            return []
        found = []
        for w in suspects:
            if w == u or w == v:
                continue
            first, second = distance(u, w), distance(w, v)
            if first <= 1e-9 or second <= 1e-9:
                continue
            if abs(first + second - span) <= 1e-7 * max(span, 1.0):
                found.append((first, w))
        return [w for _d, w in sorted(found)]

    healed_vertices = list(vertices)
    healed: list[tuple[int, int, int]] = []
    for a, b, c in good:
        loop: list[int] = []
        for u, v in ((a, b), (b, c), (c, a)):
            loop.append(u)
            loop.extend(between(u, v))
        if len(loop) == 3:
            healed.append((a, b, c))
            continue
        # Fanned from the centroid rather than from a corner: a corner fan
        # would join points that are collinear with it along the very edge
        # being split, producing fresh zero-area triangles and no progress.
        # The centroid is inside the triangle and in its plane, so every
        # triangle of the fan has area and the same winding as the original.
        centre = len(healed_vertices)
        healed_vertices.append(tuple(
            sum(vertices[i][axis] for i in (a, b, c)) / 3.0 for axis in range(3)
        ))
        for index, corner in enumerate(loop):
            healed.append((centre, corner, loop[(index + 1) % len(loop)]))
    return (healed_vertices, healed)


def _shell_from_triangles(vertices, triangles):
    """Build one shell straight from the mesh's own connectivity.

    The point of doing it this way rather than through
    ``BRepBuilderAPI_Sewing``: a triangle list *already carries the topology*.
    Two triangles share an edge exactly when they name the same pair of vertex
    indices, so there is nothing to discover. Sewing throws that away, hands
    OCCT a pile of unrelated faces and asks it to find the coincidences
    geometrically -- which on a real slicer mesh from this project's own bug
    report went from 0.9 s at 2,200 triangles to over six minutes at 2,600.
    Building the vertices and edges once and sharing them is linear.

    Returns ``(shell, closed)``, or ``(None, False)`` if nothing could be built.
    """
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeVertex
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
    from OCP.TopoDS import TopoDS_Shell, TopoDS_Wire

    builder = BRep_Builder()
    made_vertices: dict[int, object] = {}
    made_edges: dict[tuple[int, int], object] = {}
    edge_uses: dict[tuple[int, int], int] = {}

    def vertex(index: int):
        found = made_vertices.get(index)
        if found is None:
            found = BRepBuilderAPI_MakeVertex(gp_Pnt(*vertices[index])).Vertex()
            made_vertices[index] = found
        return found

    def edge(a: int, b: int):
        """The shared edge for a vertex pair, oriented from *a* to *b*."""
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge

        key = (a, b) if a < b else (b, a)
        found = made_edges.get(key)
        if found is None:
            maker = BRepBuilderAPI_MakeEdge(vertex(key[0]), vertex(key[1]))
            if not maker.IsDone():
                return None
            found = maker.Edge()
            made_edges[key] = found
        edge_uses[key] = edge_uses.get(key, 0) + 1
        return found if (a, b) == key else found.Reversed()

    shell = TopoDS_Shell()
    builder.MakeShell(shell)
    faces = 0
    for a, b, c in triangles:
        # Everything that can reject a triangle is asked *before* its edges are
        # created, because creating one is what counts it towards closedness.
        # Rejecting afterwards leaves the edge counted for a face that was
        # never added, and the shell then claims to be closed with a hole in it.
        if a == b or b == c or a == c:
            continue                      # a degenerate triangle has no face
        # The plane is stated rather than inferred. Left to work it out from
        # the wire, OCCT picks a normal that does not reliably follow the
        # winding, and the shell comes out with its faces facing every which
        # way -- which reads as a negative volume and an inside-out solid. The
        # triangle's own cross product is the outward normal by construction.
        normal = _triangle_normal(vertices, a, b, c)
        if normal is None:
            continue                      # zero area: no plane, no face
        wire = TopoDS_Wire()
        builder.MakeWire(wire)
        pieces = [edge(a, b), edge(b, c), edge(c, a)]
        if any(piece is None for piece in pieces):
            continue
        for piece in pieces:
            builder.Add(wire, piece)
        try:
            plane = gp_Pln(gp_Pnt(*vertices[a]), gp_Dir(*normal))
            maker = BRepBuilderAPI_MakeFace(plane, wire, True)
        except Exception:  # noqa: BLE001 - a degenerate triangle has no plane
            continue
        if not maker.IsDone():
            continue
        builder.Add(shell, maker.Face())
        faces += 1

    if faces == 0:
        return (None, False)
    # Closed when every edge is used by exactly two triangles. Read off the
    # index bookkeeping rather than asked of the kernel: it is the same answer
    # and it costs nothing.
    closed = bool(edge_uses) and all(count == 2 for count in edge_uses.values())
    shell.Closed(closed)
    return (shell, closed)


def _solid_from_shell(shell, closed: bool, enclosed: float):
    """A solid from *shell*, or None if this piece is genuinely a surface.

    Perfect edge pairing is the fast answer, but not the only one worth
    accepting. Real slicer meshes carry zero-area slivers -- fans of collinear
    points -- and dropping those (they have no plane, so they cannot become
    faces) leaves the edge counts short by a slit of no width. The surface is
    closed in every sense that matters, and refusing to make a solid of it
    would cost the user Cut, Split and Hollow over nothing.

    So when the count says otherwise, the shell is asked to become a solid
    anyway and the result is checked against the mesh's *own* enclosed volume,
    which is arithmetic on the triangle list and cannot be argued with. If
    OCCT's solid agrees to within a hair, it really does bound that volume.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid

    from .occ import is_valid, volume

    try:
        maker = BRepBuilderAPI_MakeSolid(shell)
        if not maker.IsDone():
            return None
        solid = maker.Solid()
    except Exception:  # noqa: BLE001 - not every shell bounds a solid
        return None
    if closed:
        return solid
    if enclosed <= 0.0 or not is_valid(solid):
        return None
    try:
        built = volume(solid)
    except Exception:  # noqa: BLE001
        return None
    return solid if abs(built - enclosed) <= 1e-6 * max(enclosed, 1.0) else None


def _shape_from_mesh(mesh: Mesh) -> tuple[object, str]:
    """Turn a triangle soup into solids, one per connected piece.

    Returns the shape and a note, which is non-empty when the mesh was too big
    to rebuild or when some part of it does not close.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid

    from .occ import unify

    if len(mesh.triangles) > REBUILD_TRIANGLE_LIMIT:
        return (
            _triangulation_face(mesh),
            f"{len(mesh.triangles):,} triangles is too many to rebuild as a "
            "solid, so this body can be moved and measured but not cut.",
        )

    solids, open_shells = [], 0
    with guard("import"):
        vertices, triangles = _heal_slivers(mesh.vertices, mesh.triangles)
        for piece in _components(vertices, triangles):
            # A mesh wound inside-out builds an inside-out solid, which every
            # boolean afterwards gets backwards. The sign of the enclosed
            # volume says which way round it is, and flipping the winding is
            # cheaper than asking OCCT to re-orient the result.
            enclosed = _signed_volume(vertices, piece) / 6.0
            if enclosed < 0.0:
                piece = [(c, b, a) for a, b, c in piece]
                enclosed = -enclosed
            shell, closed = _shell_from_triangles(vertices, piece)
            if shell is None:
                continue
            solid = _solid_from_shell(shell, closed, enclosed)
            if solid is None:
                open_shells += 1
                solids.append(shell)
            else:
                # Merge the coplanar triangles back into the faces they were a
                # tessellation of. An imported box arrives with six faces you
                # can select, pull and sketch on rather than twelve triangles
                # you cannot do anything useful with -- and every later
                # operation, snapping included, has a fraction of the work.
                solids.append(unify(solid) if len(piece) <= UNIFY_TRIANGLE_LIMIT
                              else solid)

    if not solids:
        # Nothing came of the connectivity: fall back to asking the kernel to
        # find the coincidences itself. Slow, but it copes with a mesh whose
        # indices do not actually describe shared edges.
        return _sewn_from_mesh(mesh)

    note = ""
    if open_shells:
        note = (
            f"{open_shells} part(s) of this mesh do not close, so they stay "
            "surfaces and cannot be cut."
        )
    return (_compound(solids), note)


def _sewn_from_mesh(mesh: Mesh) -> tuple[object, str]:
    """The geometric fallback: one face per triangle, sewn by tolerance.

    Only reached when the index-built shell produced nothing at all, which
    means the file's triangles do not share vertex indices along their common
    edges. Kept because such files exist; not used otherwise, because it is
    orders of magnitude slower.
    """
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon,
        BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing,
    )
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    with guard("import"):
        sewing = BRepBuilderAPI_Sewing(1e-4)
        for a, b, c in mesh.triangles:
            try:
                polygon = BRepBuilderAPI_MakePolygon(
                    gp_Pnt(*mesh.vertices[a]),
                    gp_Pnt(*mesh.vertices[b]),
                    gp_Pnt(*mesh.vertices[c]),
                    True,
                )
                face = BRepBuilderAPI_MakeFace(polygon.Wire())
            except Exception:  # noqa: BLE001 - a degenerate triangle is skipped
                continue
            if face.IsDone():
                sewing.Add(face.Face())
        sewing.Perform()
        sewn = sewing.SewedShape()

    explorer = TopExp_Explorer(sewn, TopAbs_SHELL)
    shells = []
    while explorer.More():
        shells.append(TopoDS.Shell_s(explorer.Current()))
        explorer.Next()
    if not shells:
        return (sewn, "This mesh has no surface SimpleCAD could rebuild.")

    solids, open_shells = [], 0
    for shell in shells:
        if not shell.Closed():
            open_shells += 1
            solids.append(shell)
            continue
        try:
            builder = BRepBuilderAPI_MakeSolid(shell)
            solids.append(builder.Solid() if builder.IsDone() else shell)
        except Exception:  # noqa: BLE001 - fall back to the shell
            solids.append(shell)
    note = ""
    if open_shells:
        note = (
            f"{open_shells} part(s) of this mesh do not close, so they stay "
            "surfaces and cannot be cut."
        )
    return (_compound(solids), note)


def _triangulation_face(mesh: Mesh):
    """A single face carrying the raw triangulation. Display-quality only."""
    from OCP.BRep import BRep_Builder
    from OCP.Poly import Poly_Triangle, Poly_Triangulation
    from OCP.gp import gp_Pnt
    from OCP.TopoDS import TopoDS_Face

    triangulation = Poly_Triangulation(
        len(mesh.vertices), len(mesh.triangles), False
    )
    for index, (x, y, z) in enumerate(mesh.vertices, start=1):
        triangulation.SetNode(index, gp_Pnt(x, y, z))
    for index, (a, b, c) in enumerate(mesh.triangles, start=1):
        triangulation.SetTriangle(index, Poly_Triangle(a + 1, b + 1, c + 1))
    face = TopoDS_Face()
    builder = BRep_Builder()
    builder.MakeFace(face)
    builder.UpdateFace(face, triangulation)
    return face


def import_stl(path: str) -> list[ImportedBody]:
    """STL has no notion of separate objects, so this is always one body."""
    from OCP.RWStl import RWStl

    with guard("import"):
        triangulation = RWStl.ReadFile_s(path)
        if triangulation is None:
            raise CadError(
                f"'{os.path.basename(path)}' could not be read as STL.",
                suggestion="Check that it is a valid binary or ASCII STL.",
            )
        mesh = Mesh()
        for index in range(1, triangulation.NbNodes() + 1):
            point = triangulation.Node(index)
            mesh.vertices.append((point.X(), point.Y(), point.Z()))
        for index in range(1, triangulation.NbTriangles() + 1):
            a, b, c = triangulation.Triangle(index).Get()
            mesh.triangles.append((a - 1, b - 1, c - 1))
    if mesh.is_empty:
        raise CadError(f"'{os.path.basename(path)}' contains no triangles.")
    shape, note = _shape_from_mesh(mesh)
    return [ImportedBody(_base_name(path), shape, note)]


def _regrouped(vertices, groups) -> list[tuple[str, Mesh]]:
    """Turn globally-indexed groups into meshes with their own vertex lists."""
    out = []
    for name, triangles in groups:
        if not triangles:
            continue
        mesh = Mesh()
        remap: dict[int, int] = {}
        for triangle in triangles:
            mapped = []
            for index in triangle:
                if index not in remap:
                    if not 0 <= index < len(vertices):
                        break
                    remap[index] = len(mesh.vertices)
                    mesh.vertices.append(vertices[index])
                mapped.append(remap[index])
            if len(mapped) == 3:
                mesh.triangles.append(tuple(mapped))
        if not mesh.is_empty:
            out.append((name, mesh))
    return out


def import_obj(path: str) -> list[ImportedBody]:
    """OBJ, split on its own ``o``/``g`` statements into separate bodies."""
    vertices: list[tuple[float, float, float]] = []
    groups: list[tuple[str, list]] = []
    current: list = []
    name = _base_name(path)

    with open(path, errors="replace") as handle:
        for line in handle:
            parts = line.split()
            if not parts:
                continue
            keyword = parts[0]
            if keyword == "v" and len(parts) >= 4:
                vertices.append(tuple(float(v) for v in parts[1:4]))
            elif keyword in ("o", "g"):
                if current:
                    groups.append((name, current))
                    current = []
                name = " ".join(parts[1:]) or _base_name(path)
            elif keyword == "f":
                # Faces may be polygons and may carry texture/normal indices.
                # Negative indices count back from the end of the vertex list.
                indices = []
                for token in parts[1:]:
                    try:
                        value = int(token.split("/")[0])
                    except ValueError:
                        continue
                    indices.append(value - 1 if value > 0 else len(vertices) + value)
                for i in range(1, len(indices) - 1):
                    current.append((indices[0], indices[i], indices[i + 1]))
    if current:
        groups.append((name, current))
    if not vertices or not groups:
        raise CadError(
            f"'{os.path.basename(path)}' contains no faces.",
            suggestion="Check that the file is a mesh OBJ, not a material file.",
        )

    bodies = []
    for group_name, mesh in _regrouped(vertices, groups):
        shape, note = _shape_from_mesh(mesh)
        bodies.append(ImportedBody(group_name, shape, note))
    if not bodies:
        raise CadError(f"'{os.path.basename(path)}' contains no usable faces.")
    return bodies


#: 3MF unit names, as a multiplier into millimetres.
UNIT_SCALE = {
    "micron": 0.001, "millimeter": 1.0, "centimeter": 10.0,
    "inch": 25.4, "foot": 304.8, "meter": 1000.0,
}

#: The production extension: what lets one 3MF spread its meshes over several
#: parts, with the root holding nothing but references. Every current slicer --
#: Bambu Studio, OrcaSlicer, ElegooSlicer, PrusaSlicer's project files -- writes
#: this, so it is the common case rather than an exotic one.
PRODUCTION_NS = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
#: OPC relationships, which is how a package says which part is the model.
RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
MODEL_REL_TYPE = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
#: Object types that describe scaffolding rather than the part being printed.
SKIPPED_OBJECT_TYPES = {"support", "solidsupport", "other"}
#: How deep ``<components>`` may nest before the file is called malformed.
MAX_3MF_DEPTH = 16


def _3mf_root_part(archive: zipfile.ZipFile) -> str:
    """Which part inside the package is the model.

    Asked of the package's own relationships rather than guessed from the file
    names. Guessing -- taking the first entry ending in ``.model`` -- is what
    used to pick a production-extension file's *reference* part, which holds no
    mesh at all, and report the whole file as having no triangles.
    """
    names = archive.namelist()
    try:
        root = ElementTree.fromstring(archive.read("_rels/.rels"))
    except Exception:  # noqa: BLE001 - a package without relationships
        root = None
    if root is not None:
        for relationship in root.iter(f"{{{RELS_NS}}}Relationship"):
            if relationship.get("Type") != MODEL_REL_TYPE:
                continue
            target = (relationship.get("Target") or "").lstrip("/")
            if target in names:
                return target
    for candidate in ("3D/3dmodel.model", "3d/3dmodel.model"):
        if candidate in names:
            return candidate
    found = next((n for n in names if n.lower().endswith(".model")), None)
    if found is None:
        raise CadError(
            f"'{os.path.basename(archive.filename or '')}' has no 3MF model part.",
            suggestion="It may be a renamed archive rather than a 3MF file.",
        )
    return found


def _3mf_part(archive: zipfile.ZipFile, cache: dict, name: str):
    """The parsed ``(root, objects-by-id)`` for one model part, read once."""
    key = name.lstrip("/")
    found = cache.get(key)
    if found is None:
        root = ElementTree.fromstring(archive.read(key))
        objects = {
            obj.get("id"): obj
            for obj in root.iter(f"{{{MODEL_NS}}}object")
            if obj.get("id")
        }
        found = (root, objects)
        cache[key] = found
    return found


def _3mf_transform(text: str | None):
    """A 3MF transform as 12 floats, or None for the identity.

    3MF writes a 4x3 matrix in row-major order and multiplies points as row
    vectors, so the last three numbers are the translation.
    """
    if not text:
        return None
    parts = text.split()
    if len(parts) != 12:
        return None
    try:
        return tuple(float(value) for value in parts)
    except ValueError:
        return None


def _3mf_compose(first, second):
    """The transform that applies *first* and then *second*."""
    if first is None:
        return second
    if second is None:
        return first
    out = []
    for row in range(4):
        base = [first[row * 3 + k] for k in range(3)]
        for column in range(3):
            value = sum(base[k] * second[k * 3 + column] for k in range(3))
            if row == 3:
                value += second[9 + column]
            out.append(value)
    return tuple(out)


def _3mf_apply(matrix, point):
    if matrix is None:
        return point
    x, y, z = point
    return (
        x * matrix[0] + y * matrix[3] + z * matrix[6] + matrix[9],
        x * matrix[1] + y * matrix[4] + z * matrix[7] + matrix[10],
        x * matrix[2] + y * matrix[5] + z * matrix[8] + matrix[11],
    )


def _3mf_component_path(component, current: str) -> str:
    """Which part a ``<component>`` points at, defaulting to its own."""
    direct = component.get(f"{{{PRODUCTION_NS}}}path")
    if direct:
        return direct.lstrip("/")
    # The production namespace may be bound to any prefix, and some writers
    # leave the attribute unqualified, so fall back to matching the local name.
    for key, value in component.attrib.items():
        if key == "path" or key.endswith("}path"):
            return value.lstrip("/")
    return current


def _3mf_slicer_names(archive: zipfile.ZipFile) -> dict[str, str]:
    """Object id -> name, from the slicer's own settings part if there is one.

    Not part of the 3MF standard, but it is where Bambu Studio and its
    derivatives put the name the user actually gave the object -- so an import
    says "empty_2_B" rather than "Atlas Box 1".
    """
    names: dict[str, str] = {}
    for candidate in ("Metadata/model_settings.config",):
        try:
            root = ElementTree.fromstring(archive.read(candidate))
        except Exception:  # noqa: BLE001 - absent, or not a slicer's file
            continue
        for obj in root.iter("object"):
            identifier = obj.get("id")
            if not identifier:
                continue
            for entry in obj.findall("metadata"):
                if entry.get("key") == "name" and entry.get("value"):
                    names[identifier] = entry.get("value")
                    break
    return names


def _3mf_mesh_into(mesh: Mesh, obj, matrix, scale: float) -> None:
    """Append this object's own ``<mesh>`` to *mesh*, transformed and scaled."""
    element = obj.find(f"{{{MODEL_NS}}}mesh")
    if element is None:
        return
    offset = len(mesh.vertices)
    added = 0
    for vertex in element.iter(f"{{{MODEL_NS}}}vertex"):
        try:
            point = (
                float(vertex.get("x")), float(vertex.get("y")),
                float(vertex.get("z")),
            )
        except (TypeError, ValueError):
            return
        moved = _3mf_apply(matrix, point)
        mesh.vertices.append(tuple(value * scale for value in moved))
        added += 1
    for triangle in element.iter(f"{{{MODEL_NS}}}triangle"):
        try:
            indices = (
                int(triangle.get("v1")), int(triangle.get("v2")),
                int(triangle.get("v3")),
            )
        except (TypeError, ValueError):
            continue
        if any(not 0 <= index < added for index in indices):
            continue
        mesh.triangles.append(tuple(offset + index for index in indices))


def _3mf_gather(
    archive: zipfile.ZipFile, cache: dict, part: str, object_id: str,
    matrix, scale: float, mesh: Mesh, seen: tuple, depth: int = 0,
) -> None:
    """Collect one object's geometry, following ``<components>`` across parts."""
    if depth > MAX_3MF_DEPTH:
        raise CadError(
            "This 3MF nests its parts too deeply to read.",
            suggestion="It may reference itself. Re-export it from the slicer.",
        )
    key = (part, object_id)
    if key in seen:
        raise CadError(
            "This 3MF refers to itself and cannot be read.",
            suggestion="Re-export it from the program that made it.",
        )
    _root, objects = _3mf_part(archive, cache, part)
    obj = objects.get(object_id)
    if obj is None:
        return
    if (obj.get("type") or "model").lower() in SKIPPED_OBJECT_TYPES:
        return

    _3mf_mesh_into(mesh, obj, matrix, scale)
    components = obj.find(f"{{{MODEL_NS}}}components")
    if components is None:
        return
    for component in components.findall(f"{{{MODEL_NS}}}component"):
        target = component.get("objectid")
        if not target:
            continue
        _3mf_gather(
            archive, cache, _3mf_component_path(component, part), target,
            _3mf_compose(_3mf_transform(component.get("transform")), matrix),
            scale, mesh, seen + (key,), depth + 1,
        )


def import_3mf(path: str) -> list[ImportedBody]:
    """3MF, one body per object on the build plate.

    Handles the production extension, where the root part holds only
    references and the meshes live in parts of their own.
    """
    with zipfile.ZipFile(path) as archive:
        cache: dict = {}
        part = _3mf_root_part(archive)
        root, objects = _3mf_part(archive, cache, part)
        scale = UNIT_SCALE.get((root.get("unit") or "millimeter").lower(), 1.0)
        labels = _3mf_slicer_names(archive)

        # What the file says to build, in the order it says to build it. Only
        # falls back to every object when there is no build section, because a
        # slicer's resources include the parts that make up each object and
        # importing those as well would duplicate the whole model.
        items = [
            (item.get("objectid"), item.get("name") or "",
             _3mf_transform(item.get("transform")))
            for item in root.iter(f"{{{MODEL_NS}}}item")
            if item.get("objectid")
        ]
        if not items:
            items = [(identifier, "", None) for identifier in objects]

        bodies: list[ImportedBody] = []
        for index, (identifier, item_name, matrix) in enumerate(items, start=1):
            mesh = Mesh()
            _3mf_gather(
                archive, cache, part, identifier, matrix, scale, mesh, ()
            )
            if mesh.is_empty:
                continue
            obj = objects.get(identifier)
            name = (
                labels.get(identifier)
                or item_name
                or (obj.get("name") if obj is not None else "")
                or _base_name(path)
            )
            shape, note = _shape_from_mesh(mesh)
            bodies.append(ImportedBody(name, shape, note))

    if not bodies:
        raise CadError(
            f"'{os.path.basename(path)}' describes {len(items)} object(s) but "
            "no mesh SimpleCAD could read.",
            suggestion="Re-export it from the slicer or CAD package that made it.",
        )
    # Disambiguate now rather than leaving the document to do it, so the names
    # in the tree match the objects in the file.
    seen: dict[str, int] = {}
    unique: list[ImportedBody] = []
    for body in bodies:
        count = seen.get(body.name, 0) + 1
        seen[body.name] = count
        unique.append(
            body if count == 1
            else ImportedBody(f"{body.name} {count}", body.shape, body.note)
        )
    return unique


def import_gltf(path: str) -> list[ImportedBody]:
    """glTF / GLB, when this OCCT build wraps the reader."""
    from OCP.Message import Message_ProgressRange
    from OCP.RWGltf import RWGltf_CafReader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDF import TDF_LabelSequence
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    with guard("import"):
        document = TDocStd_Document(TCollection_ExtendedString("simplecad"))
        reader = RWGltf_CafReader()
        reader.SetDocument(document)
        reader.SetSystemLengthUnit(0.001)     # glTF is metres; we work in mm
        # Not None: these bindings type the progress argument, so passing None
        # raises TypeError and every glTF import failed with a message about
        # the geometry rather than about the call.
        if not reader.Perform(path, Message_ProgressRange()):
            raise CadError(f"'{os.path.basename(path)}' could not be read.")
        tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
        labels = TDF_LabelSequence()
        tool.GetFreeShapes(labels)
        bodies = []
        for index in range(1, labels.Length() + 1):
            shape = tool.GetShape_s(labels.Value(index))
            if shape is not None and not shape.IsNull():
                bodies.extend(
                    _explode_solids(shape, f"{_base_name(path)} {index}")
                )
    if not bodies:
        raise CadError(f"'{os.path.basename(path)}' contains no geometry.")
    return bodies


#: extension -> reader. Built at import time so a format this OCCT build cannot
#: actually read is never offered in the file dialog and then found wanting.
def _readers() -> dict:
    readers = {
        ".step": import_step, ".stp": import_step,
        ".iges": import_iges, ".igs": import_iges,
        ".brep": import_brep,
        ".stl": import_stl,
        ".obj": import_obj,
        ".3mf": import_3mf,
    }
    try:
        from OCP.RWGltf import RWGltf_CafReader  # noqa: F401

        readers[".gltf"] = import_gltf
        readers[".glb"] = import_gltf
    except Exception:  # noqa: BLE001 - not every build wraps it
        pass
    return readers


READERS = _readers()

#: Human labels, for the file dialog and for error messages.
IMPORT_FORMATS = {
    ".step": "STEP", ".stp": "STEP", ".stl": "STL", ".obj": "OBJ",
    ".3mf": "3MF", ".iges": "IGES", ".igs": "IGES", ".brep": "B-Rep",
    ".gltf": "glTF", ".glb": "glTF",
}


def import_filter() -> str:
    """A Qt file-dialog filter covering exactly what can actually be read."""
    groups: dict[str, list[str]] = {}
    for extension in READERS:
        groups.setdefault(IMPORT_FORMATS.get(extension, "Other"), []).append(
            f"*{extension}"
        )
    everything = " ".join(sorted(p for patterns in groups.values() for p in patterns))
    parts = [f"All supported 3D files ({everything})"]
    parts += [
        f"{label} ({' '.join(sorted(patterns))})"
        for label, patterns in sorted(groups.items())
    ]
    parts.append("All files (*)")
    return ";;".join(parts)


def import_bodies(path: str) -> list[ImportedBody]:
    """Read *path* into separate bodies, one per object the format describes."""
    if not os.path.exists(path):
        raise CadError(
            f"'{os.path.basename(path)}' could not be found.",
            suggestion="It may have been moved or renamed since it was imported.",
        )
    if os.path.getsize(path) == 0:
        raise CadError(f"'{os.path.basename(path)}' is empty.")
    extension = os.path.splitext(path)[1].lower()
    reader = READERS.get(extension)
    if reader is None:
        known = ", ".join(sorted({IMPORT_FORMATS[e] for e in READERS}))
        raise CadError(
            f"SimpleCAD cannot read '{extension or os.path.basename(path)}' files.",
            suggestion=f"Supported formats are {known}.",
        )
    bodies = [b for b in reader(path) if b.shape is not None and not b.shape.IsNull()]
    if not bodies:
        raise CadError(
            f"'{os.path.basename(path)}' contains no geometry SimpleCAD can use.",
            suggestion="Check that the file has solids or a closed mesh in it.",
        )
    return bodies


def import_shape(path: str):
    """Read a file into a single shape. Kept for callers that want one lump."""
    return _compound([body.shape for body in import_bodies(path)])
