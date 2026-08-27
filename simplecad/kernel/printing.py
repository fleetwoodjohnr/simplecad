"""Printability: will this actually come off the machine?

Every check here reports a **warning**, never a refusal. The spec is explicit
that export must not be blocked, and it is right to be: a wall the analysis
thinks is thin may be exactly what the user wants, and a part that overhangs its
build plate may be about to be rotated. The job is to tell them what a printer
will make of it, in the terms they think in -- millimetres, degrees, and the
name of the printer.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import Enum

from .occ import bounding_box, is_valid, volume
from .tessellate import Mesh, triangle_normal, triangulate


class Severity(str, Enum):
    OK = "ok"
    NOTE = "note"
    WARNING = "warning"

    def rank(self) -> int:
        return {"ok": 0, "note": 1, "warning": 2}[self.value]


@dataclass
class Finding:
    """One thing worth knowing before printing."""

    check: str
    severity: Severity
    message: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.severity is Severity.OK


@dataclass
class Analysis:
    findings: list[Finding] = field(default_factory=list)

    @property
    def worst(self) -> Severity:
        return max((f.severity for f in self.findings), key=lambda s: s.rank(),
                   default=Severity.OK)

    @property
    def ok(self) -> bool:
        return self.worst is Severity.OK

    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is not Severity.OK]

    def summary(self) -> str:
        problems = self.warnings()
        if not problems:
            return "Ready to print."
        return f"{len(problems)} thing(s) to check before printing."


# ----------------------------------------------------------------------
# Individual checks
# ----------------------------------------------------------------------
def check_build_volume(shapes, profile: dict) -> Finding:
    """Does everything fit in the machine?"""
    volume_spec = profile["build_volume"]
    limits = (volume_spec["x"], volume_spec["y"], volume_spec["z"])
    if not shapes:
        return Finding("build_volume", Severity.OK, "Nothing to print yet.")

    lows, highs = zip(*(bounding_box(s) for s in shapes))
    low = tuple(min(v[i] for v in lows) for i in range(3))
    high = tuple(max(v[i] for v in highs) for i in range(3))
    size = tuple(high[i] - low[i] for i in range(3))

    over = [
        f"{'XYZ'[i]} is {size[i]:.1f} mm, the plate allows {limits[i]:.0f} mm"
        for i in range(3) if size[i] > limits[i] + 1e-6
    ]
    if over:
        return Finding(
            "build_volume", Severity.WARNING,
            f"This is bigger than the {profile['name']}'s build volume.",
            "; ".join(over),
        )
    return Finding(
        "build_volume", Severity.OK,
        f"Fits the {profile['name']}: "
        f"{size[0]:.1f} x {size[1]:.1f} x {size[2]:.1f} mm.",
    )


def check_on_plate(shapes) -> Finding:
    """Is anything floating above the plate, or sunk below it?"""
    if not shapes:
        return Finding("on_plate", Severity.OK, "Nothing to print yet.")
    lows = [bounding_box(s)[0][2] for s in shapes]
    lowest = min(lows)
    if lowest < -1e-3:
        return Finding(
            "on_plate", Severity.WARNING,
            f"Part of the model is {abs(lowest):.1f} mm below the build plate.",
            "Use Place on Build Plate.",
        )
    if lowest > 0.05:
        return Finding(
            "on_plate", Severity.NOTE,
            f"The model floats {lowest:.1f} mm above the plate.",
            "Use Place on Build Plate.",
        )
    return Finding("on_plate", Severity.OK, "Sitting on the build plate.")


def check_geometry(shape) -> Finding:
    """Valid, closed, manifold solid?"""
    from OCP.BRepCheck import BRepCheck_Analyzer

    if not is_valid(shape):
        return Finding(
            "geometry", Severity.WARNING,
            "This body has invalid geometry.",
            "Slicers may produce odd results. Check the most recent features.",
        )
    from ..core.naming import sub_shapes

    open_edges = _free_edges(shape)
    if open_edges:
        return Finding(
            "geometry", Severity.WARNING,
            f"The surface is not closed — {open_edges} open edge(s).",
            "A printer needs a watertight solid.",
        )
    return Finding("geometry", Severity.OK, "Solid and watertight.")


def _free_edges(shape) -> int:
    """Edges belonging to only one face: holes in the surface."""
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

    mapping = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, mapping)
    loose = 0
    for index in range(1, mapping.Extent() + 1):
        faces = mapping.FindFromIndex(index)
        if faces.Extent() < 2:
            from OCP.BRep import BRep_Tool
            from OCP.TopoDS import TopoDS

            edge = TopoDS.Edge_s(mapping.FindKey(index))
            if not BRep_Tool.Degenerated_s(edge):
                loose += 1
    return loose


def check_disconnected(shape) -> Finding:
    """Several separate lumps in one body print as separate objects."""
    from ..core.naming import sub_shapes

    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    mapping = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_SOLID, mapping)
    count = mapping.Extent()
    if count > 1:
        return Finding(
            "disconnected", Severity.NOTE,
            f"This body is {count} separate pieces.",
            "They will print as separate objects unless they are joined.",
        )
    return Finding("disconnected", Severity.OK, "One connected solid.")


def check_overhangs(
    shape,
    max_angle: float,
    deflection: float = 0.3,
    plate_tolerance: float = 0.25,
) -> Finding:
    """How much of the surface leans past what prints without support?

    Measured off the triangulation rather than the analytic faces, because that
    is what the slicer sees, and because it handles curved surfaces -- where the
    overhang is a band on a sphere, not a property of the face.
    """
    mesh = triangulate(shape, deflection)
    if mesh.is_empty:
        return Finding("overhangs", Severity.OK, "Nothing to check.")

    # Overhang is measured from vertical, so a face leaning *max_angle* past
    # vertical has a downward normal component of sin(max_angle). Using
    # cos(180 - angle) here instead flags a 45 degree face as a 50 degree
    # problem, which is a different -- and wrong -- threshold.
    limit = -math.sin(math.radians(max_angle))
    # A downward face lying on the build plate is supported by it, not
    # overhanging. Without this every box reports its own base as a problem.
    plate = mesh.bounds()[0][2] + max(plate_tolerance, deflection)

    steep = 0.0
    total = 0.0
    for triangle in mesh.triangles:
        area = _triangle_area(mesh, triangle)
        total += area
        normal = triangle_normal(mesh, triangle)
        if normal[2] >= limit:
            continue
        height = min(mesh.vertices[i][2] for i in triangle)
        if height <= plate:
            continue                      # resting on the plate
        steep += area
    if total <= 0:
        return Finding("overhangs", Severity.OK, "Nothing to check.")

    fraction = steep / total
    if fraction > 0.15:
        return Finding(
            "overhangs", Severity.WARNING,
            f"{fraction * 100:.0f}% of the surface overhangs by more than "
            f"{max_angle:.0f}°.",
            "It will need supports, or rotating.",
        )
    if fraction > 0.02:
        return Finding(
            "overhangs", Severity.NOTE,
            f"{fraction * 100:.0f}% of the surface overhangs by more than "
            f"{max_angle:.0f}°.",
            "Light support may help.",
        )
    return Finding("overhangs", Severity.OK, "No significant overhangs.")


def _triangle_area(mesh: Mesh, triangle) -> float:
    a, b, c = (mesh.vertices[i] for i in triangle)
    u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    cross = (
        u[1] * v[2] - u[2] * v[1],
        u[2] * v[0] - u[0] * v[2],
        u[0] * v[1] - u[1] * v[0],
    )
    return 0.5 * math.sqrt(sum(component * component for component in cross))


def check_thin_walls(shape, minimum: float) -> Finding:
    """Look for slabs thinner than the printer can lay down.

    Approximated by measuring between pairs of parallel, opposed planar faces.
    That is the common case a user actually hits -- a wall, a rib, a flange --
    and it is honest about what it does not cover: a true minimum-thickness test
    would need a medial-axis computation OCCT does not offer.
    """
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    from ..core.naming import fingerprint, sub_shapes

    planes = []
    for face in sub_shapes(shape, "face"):
        print_ = fingerprint(face, "face")
        if print_.geometry == "plane" and print_.direction:
            planes.append((face, print_))
    if len(planes) < 2:
        return Finding("thin_walls", Severity.OK, "No thin walls found.")

    thinnest = None
    for index, (face_a, print_a) in enumerate(planes):
        for face_b, print_b in planes[index + 1:]:
            dot = sum(x * y for x, y in zip(print_a.direction, print_b.direction))
            if dot > -0.999:                # not facing each other
                continue
            try:
                measure = BRepExtrema_DistShapeShape(face_a, face_b)
                measure.Perform()
                if not measure.IsDone():
                    continue
                gap = measure.Value()
            except Exception:  # noqa: BLE001
                continue
            if gap > 1e-6 and (thinnest is None or gap < thinnest):
                thinnest = gap

    if thinnest is None:
        return Finding("thin_walls", Severity.OK, "No thin walls found.")
    if thinnest < minimum:
        return Finding(
            "thin_walls", Severity.WARNING,
            f"The thinnest wall is {thinnest:.2f} mm.",
            f"Below {minimum:.2f} mm this printer will struggle. "
            "Thicken it, or accept a weak wall.",
        )
    return Finding(
        "thin_walls", Severity.OK, f"Thinnest wall {thinnest:.2f} mm."
    )


def check_tiny_features(shape, minimum: float) -> Finding:
    """Details smaller than the nozzle simply will not appear."""
    from ..core.naming import fingerprint, sub_shapes

    tiny = [
        p.measure for e in sub_shapes(shape, "edge")
        if 0 < (p := fingerprint(e, "edge")).measure < minimum
    ]
    if tiny:
        return Finding(
            "tiny_features", Severity.NOTE,
            f"{len(tiny)} feature(s) are smaller than {minimum:.2f} mm.",
            f"The smallest is {min(tiny):.3f} mm and will not print cleanly.",
        )
    return Finding("tiny_features", Severity.OK, "No features below the nozzle size.")


# ----------------------------------------------------------------------
def analyse(shapes, profile: dict) -> Analysis:
    """Run every check over the visible bodies."""
    analysis = Analysis()
    shapes = [s for s in shapes if s is not None]
    if not shapes:
        analysis.findings.append(
            Finding("empty", Severity.NOTE, "There is nothing to print yet.")
        )
        return analysis

    analysis.findings.append(check_build_volume(shapes, profile))
    analysis.findings.append(check_on_plate(shapes))
    for shape in shapes:
        analysis.findings.append(check_geometry(shape))
        analysis.findings.append(check_disconnected(shape))
        analysis.findings.append(
            check_overhangs(shape, profile.get("max_overhang_angle", 50.0))
        )
        analysis.findings.append(
            check_thin_walls(shape, profile.get("min_wall_thickness", 0.8))
        )
        analysis.findings.append(
            check_tiny_features(shape, profile.get("min_feature_size", 0.6))
        )
    # Collapse duplicate all-clear findings from multiple bodies.
    seen: dict[tuple[str, str], Finding] = {}
    unique: list[Finding] = []
    for finding in analysis.findings:
        key = (finding.check, finding.message)
        if finding.ok and key in seen:
            continue
        seen[key] = finding
        unique.append(finding)
    analysis.findings = unique
    return analysis


# ----------------------------------------------------------------------
# Placement
# ----------------------------------------------------------------------
def place_on_plate(shapes) -> tuple[float, float, float]:
    """The translation that drops the model onto z = 0, keeping x and y."""
    if not shapes:
        return (0.0, 0.0, 0.0)
    lowest = min(bounding_box(s)[0][2] for s in shapes)
    return (0.0, 0.0, -lowest)


def center_on_plate(shapes, profile: dict) -> tuple[float, float, float]:
    """The translation that centres the model on the plate and sits it down."""
    if not shapes:
        return (0.0, 0.0, 0.0)
    lows, highs = zip(*(bounding_box(s) for s in shapes))
    low = tuple(min(v[i] for v in lows) for i in range(3))
    high = tuple(max(v[i] for v in highs) for i in range(3))
    plate = profile["build_volume"]
    return (
        plate["x"] / 2.0 - (low[0] + high[0]) / 2.0,
        plate["y"] / 2.0 - (low[1] + high[1]) / 2.0,
        -low[2],
    )


def best_print_orientation(shape, profile: dict) -> tuple[tuple[float, float, float], float]:
    """The axis and angle that leaves the least overhanging surface.

    Only the six axis-aligned orientations are tried. That is not an exhaustive
    search, but it is the choice a person actually makes -- which face goes down
    -- and it runs fast enough to offer as a button.
    """
    from .occ import make_transform, transformed

    limit = profile.get("max_overhang_angle", 50.0)
    candidates = [
        ((1.0, 0.0, 0.0), 0.0),
        ((1.0, 0.0, 0.0), 90.0),
        ((1.0, 0.0, 0.0), 180.0),
        ((1.0, 0.0, 0.0), 270.0),
        ((0.0, 1.0, 0.0), 90.0),
        ((0.0, 1.0, 0.0), 270.0),
    ]
    best = candidates[0]
    best_score = None
    for axis, angle in candidates:
        candidate = shape
        if angle:
            candidate = transformed(
                shape, make_transform(rotate_axis=axis, rotate_degrees=angle)
            )
        finding = check_overhangs(candidate, limit, deflection=0.6)
        score = {"ok": 0.0, "note": 1.0, "warning": 2.0}[finding.severity.value]
        # Break ties on how flat the part sits: a lower profile prints better.
        low, high = bounding_box(candidate)
        score += (high[2] - low[2]) / 1000.0
        if best_score is None or score < best_score:
            best, best_score = (axis, angle), score
    return best


# ----------------------------------------------------------------------
# Slicers
# ----------------------------------------------------------------------
#: Slicers to look for, in preference order.
SLICERS = (
    ("OrcaSlicer", ("orca-slicer", "OrcaSlicer", "orcaslicer"),
     "com.orcaslicer.OrcaSlicer"),
    ("ElegooSlicer", ("elegoo-slicer", "ElegooSlicer", "elegooslicer"),
     "com.elegoo.ElegooSlicer"),
    ("PrusaSlicer", ("prusa-slicer", "PrusaSlicer"), "com.prusa3d.PrusaSlicer"),
    ("Cura", ("cura", "UltiMaker-Cura"), "com.ultimaker.cura"),
)


def find_slicer() -> tuple[str, list[str]] | None:
    """The first slicer available on this machine, as (name, command prefix)."""
    for name, binaries, flatpak in SLICERS:
        for binary in binaries:
            path = shutil.which(binary)
            if path:
                return (name, [path])
        if shutil.which("flatpak") and _flatpak_installed(flatpak):
            return (name, ["flatpak", "run", flatpak])
    return None


def _flatpak_installed(app_id: str) -> bool:
    try:
        result = subprocess.run(
            ["flatpak", "info", app_id],
            capture_output=True, timeout=8, check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def open_in_slicer(path: str) -> str:
    """Hand a file to the slicer. Returns the name of the one used."""
    from ..core.errors import CadError

    found = find_slicer()
    if found is None:
        raise CadError(
            "No slicer was found on this machine.",
            suggestion="Install OrcaSlicer or ElegooSlicer, then try again.",
        )
    name, command = found
    try:
        subprocess.Popen(
            command + [path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise CadError(
            f"{name} could not be started.", detail=str(exc)
        ) from exc
    return name
