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
#: Triangle count above which a mesh is not sewn into a solid.
#:
#: Sewing turns a triangle soup into real B-Rep topology, which is what makes an
#: imported mesh something you can split, boolean and shell rather than merely
#: look at. It is also quadratic-ish in practice, and a 200k-triangle scan would
#: hang the application for minutes. Past this size the triangulation is kept
#: as-is: the body still imports, displays, moves and measures, and the feature
#: says plainly that the heavier operations will not work on it.
SEW_TRIANGLE_LIMIT = 40_000


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
def _shape_from_mesh(mesh: Mesh) -> tuple[object, str]:
    """Sew a triangle soup into a shell, and a solid when it closes.

    Returns the shape and a note, which is non-empty when the mesh was too big
    to sew and had to be kept as a plain triangulation.
    """
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon,
        BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing,
    )
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    if len(mesh.triangles) > SEW_TRIANGLE_LIMIT:
        return (
            _triangulation_face(mesh),
            f"{len(mesh.triangles):,} triangles is too many to rebuild as a "
            "solid, so this body can be moved and measured but not cut.",
        )

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

    # Every closed shell becomes a solid. This is the step that makes an
    # imported mesh usable by Split and the booleans rather than merely
    # visible -- and it has to run per shell, because one STL routinely holds
    # several disconnected objects and turning only the lone-shell case into a
    # solid would leave every multi-part file un-editable.
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


def import_3mf(path: str) -> list[ImportedBody]:
    """3MF, one body per ``<object>``, honouring the declared unit."""
    with zipfile.ZipFile(path) as archive:
        name = next(
            (n for n in archive.namelist() if n.lower().endswith(".model")), None
        )
        if name is None:
            raise CadError(f"'{os.path.basename(path)}' has no 3MF model part.")
        root = ElementTree.fromstring(archive.read(name))

    scale = UNIT_SCALE.get((root.get("unit") or "millimeter").lower(), 1.0)
    labels = {}
    for item in root.iter(f"{{{MODEL_NS}}}item"):
        if item.get("objectid"):
            labels[item.get("objectid")] = item.get("name") or ""

    bodies: list[ImportedBody] = []
    for obj in root.iter(f"{{{MODEL_NS}}}object"):
        mesh = Mesh()
        for element in obj.iter(f"{{{MODEL_NS}}}vertex"):
            mesh.vertices.append((
                float(element.get("x")) * scale,
                float(element.get("y")) * scale,
                float(element.get("z")) * scale,
            ))
        for element in obj.iter(f"{{{MODEL_NS}}}triangle"):
            mesh.triangles.append((
                int(element.get("v1")), int(element.get("v2")),
                int(element.get("v3")),
            ))
        if mesh.is_empty:
            continue
        identifier = obj.get("id") or str(len(bodies) + 1)
        label = (
            obj.get("name") or labels.get(identifier) or
            (_base_name(path) if len(bodies) == 0 else
             f"{_base_name(path)} {identifier}")
        )
        shape, note = _shape_from_mesh(mesh)
        bodies.append(ImportedBody(label, shape, note))

    if not bodies:
        raise CadError(f"'{os.path.basename(path)}' contains no triangles.")
    if len(bodies) > 1:
        # Disambiguate now rather than leaving the document to do it, so the
        # names in the tree match the objects in the file.
        bodies = [
            ImportedBody(f"{_base_name(path)} {index}", body.shape, body.note)
            if body.name == _base_name(path) else body
            for index, body in enumerate(bodies, start=1)
        ]
    return bodies


def import_gltf(path: str) -> list[ImportedBody]:
    """glTF / GLB, when this OCCT build wraps the reader."""
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
        if not reader.Perform(path, None):
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
