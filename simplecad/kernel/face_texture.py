"""Printable relief patterns attached to one selected planar or cylindrical face.

Patterns are laid out in a metric two-dimensional face domain.  The same
layout is used for the viewport guide and for the eventual B-Rep boolean, so
clearances do not change when the exact preview arrives.
"""

from __future__ import annotations

from .occ import boolean_builder

import math
from dataclasses import dataclass
from typing import Callable

from shapely import constrained_delaunay_triangles
from shapely.affinity import rotate as rotate_2d, translate as translate_2d
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from ..core.document import BodyRef, BuildContext, Feature, register
from ..core.errors import CadError, guard
from ..core.params import dependencies
from ..core.units import Dimension
from .occ import built_shape, compound, is_valid, unify, volume

MAX_MOTIFS = 4000
OVERLAP = 0.02
NUMERIC_INPUTS = frozenset({
    "size", "spacing", "depth", "rotation", "offset_u", "offset_v",
    "edge_clearance", "hole_clearance", "text_clearance", "other_clearance",
})


@dataclass(frozen=True)
class PatternDefinition:
    label: str
    generator: Callable
    default_mode: str = "raised"


PACKAGES: dict[str, dict[str, PatternDefinition]] = {}


def register_texture_package(package_id: str, patterns: dict[str, PatternDefinition]) -> None:
    """Register a stable ID and generators for a future texture package."""
    if not package_id or not patterns:
        raise ValueError("A texture package needs an ID and at least one pattern.")
    if package_id in PACKAGES:
        raise ValueError(f"Texture package {package_id!r} is already registered.")
    PACKAGES[package_id] = dict(patterns)


def _cells(bounds, pitch):
    reach = max(abs(value) for value in bounds) + pitch * 3
    count = math.ceil(reach / pitch)
    if (2 * count + 1) ** 2 > MAX_MOTIFS * 16:
        raise CadError("This texture is too dense for the selected face.", suggestion="Increase its size or spacing.")
    for row in range(-count, count + 1):
        for column in range(-count, count + 1):
            yield column * pitch, row * pitch, row


def _strokes(bounds, size, spacing, angles, width_factor=0.23, wave=False):
    pitch = size + spacing
    radius = math.hypot(bounds[2] - bounds[0], bounds[3] - bounds[1]) + pitch * 2
    n = math.ceil(radius / pitch) + 1
    if n * len(angles) > MAX_MOTIFS:
        raise CadError("This texture is too dense for the selected face.", suggestion="Increase its size or spacing.")
    width = max(0.08, size * width_factor)
    shapes = []
    for angle in angles:
        for index in range(-n, n + 1):
            y = index * pitch
            if wave:
                samples = max(24, math.ceil(radius * 4 / max(size, 0.5)))
                points = [
                    (-radius + 2 * radius * k / samples,
                     y + size * 0.28 * math.sin(2 * math.pi * (-radius + 2 * radius * k / samples) / max(size * 3, 0.1)))
                    for k in range(samples + 1)
                ]
            else:
                points = [(-radius, y), (radius, y)]
            line = LineString(points).buffer(width / 2, cap_style="flat", join_style="round")
            shapes.append(rotate_2d(line, angle, origin=(0, 0)))
    return shapes


def _repeated(bounds, size, spacing, kind):
    pitch = size + spacing
    shapes = []
    for x, y, row in _cells(bounds, pitch):
        if kind in {"hexagon", "vent"} and row % 2:
            x += pitch / 2
        if kind in {"dots", "raised_bumps"}:
            motif = Point(x, y).buffer(size / 2, resolution=12)
        elif kind == "hexagon":
            radius = size / math.sqrt(3)
            points = [
                (x + radius * math.cos(math.pi / 6 + i * math.pi / 3),
                 y + radius * math.sin(math.pi / 6 + i * math.pi / 3))
                for i in range(6)
            ]
            motif = Polygon(points).boundary.buffer(max(0.08, size * 0.12) / 2, join_style="mitre")
        elif kind == "diamond":
            diamond = Polygon([(x, y + size / 2), (x + size / 2, y),
                               (x, y - size / 2), (x - size / 2, y)])
            motif = diamond.boundary.buffer(max(0.08, size * 0.13) / 2, join_style="mitre")
        elif kind == "geometric":
            triangle = Polygon([(x, y + size / 2), (x + size / 2, y - size / 2),
                                (x - size / 2, y - size / 2)])
            motif = triangle.boundary.buffer(max(0.08, size * 0.12) / 2)
        elif kind == "vent":
            motif = LineString([(x - size * 0.35, y), (x + size * 0.35, y)]).buffer(size * 0.15)
        else:  # chevron grip
            motif = LineString([
                (x - size / 2, y - size / 4), (x, y + size / 4),
                (x + size / 2, y - size / 4),
            ]).buffer(max(0.08, size * 0.12), cap_style="round", join_style="round")
        shapes.append(motif)
        if len(shapes) > MAX_MOTIFS:
            raise CadError("This texture is too dense for the selected face.", suggestion="Increase its size or spacing.")
    return shapes


def _builtin_generator(kind):
    def generate(bounds, size, spacing):
        if kind == "horizontal_lines":
            return _strokes(bounds, size, spacing, (0,))
        if kind == "vertical_lines":
            return _strokes(bounds, size, spacing, (90,))
        if kind == "diagonal_lines":
            return _strokes(bounds, size, spacing, (45,))
        if kind == "crosshatch":
            return _strokes(bounds, size, spacing, (45, -45))
        if kind == "grid":
            return _strokes(bounds, size, spacing, (0, 90))
        if kind == "knurling":
            return _strokes(bounds, size, spacing, (45, -45), width_factor=0.38)
        if kind == "ribbed":
            return _strokes(bounds, size, spacing, (0,), width_factor=0.55)
        if kind == "wave":
            return _strokes(bounds, size, spacing, (0,), wave=True)
        if kind == "recessed_grooves":
            return _strokes(bounds, size, spacing, (0,), width_factor=0.18)
        return _repeated(bounds, size, spacing, kind)
    return generate


_BUILTIN = (
    ("horizontal_lines", "Horizontal lines", "raised"),
    ("vertical_lines", "Vertical lines", "raised"),
    ("diagonal_lines", "Diagonal lines", "raised"),
    ("crosshatch", "Crosshatch", "raised"),
    ("grid", "Grid", "raised"),
    ("hexagon", "Hexagon / honeycomb", "raised"),
    ("diamond", "Diamond pattern", "raised"),
    ("knurling", "Knurling", "raised"),
    ("ribbed", "Ribbed texture", "raised"),
    ("wave", "Wave pattern", "raised"),
    ("dots", "Dots", "recessed"),
    ("raised_bumps", "Raised bumps", "raised"),
    ("recessed_grooves", "Recessed grooves", "recessed"),
    ("geometric", "Geometric pattern", "raised"),
    ("vent", "Vent-style pattern", "recessed"),
    ("grip", "Grip texture", "raised"),
)
register_texture_package("builtin.v1", {
    key: PatternDefinition(label, _builtin_generator(key), mode)
    for key, label, mode in _BUILTIN
})


def _polygons(shape):
    if shape.is_empty:
        return []
    if shape.geom_type == "Polygon":
        return [shape] if shape.area > 1e-8 else []
    if hasattr(shape, "geoms"):
        return [polygon for part in shape.geoms for polygon in _polygons(part)]
    return []


def _solid_count(shape) -> int:
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    count = 0
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        count += 1
        explorer.Next()
    return count


@dataclass
class TextureLayout:
    domain: object
    allowed: object
    protected: list[tuple[str, object]]
    motifs: list[Polygon]
    mapping: object


class PlanarMapping:
    def __init__(self, face):
        from .operations import _face_profile
        from .detect import analyse_plane

        self.domain, self.frame = _face_profile(face, 0.035)
        self.normal = analyse_plane(face).normal
        self.period = None

    def point(self, u, v, normal=0.0):
        origin, x_axis, y_axis = self.frame
        return tuple(
            origin[i] + x_axis[i] * u + y_axis[i] * v + self.normal[i] * normal
            for i in range(3)
        )


class CylindricalMapping:
    def __init__(self, face):
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.BRepTools import BRepTools
        from .detect import analyse_cylinder

        self.face = face
        self.adaptor = BRepAdaptor_Surface(face)
        cylinder = self.adaptor.Cylinder()
        self.radius = cylinder.Radius()
        self.info = analyse_cylinder(face)
        self.u_min, self.u_max, self.v_min, self.v_max = BRepTools.UVBounds_s(face)
        self.origin = cylinder.Position().Location()
        position = cylinder.Position()
        self.x = position.XDirection()
        self.y = position.YDirection()
        self.axis = position.Direction()
        self.outward_sign = -1.0 if self.info.internal else 1.0
        self.period = 2 * math.pi * self.radius if self.u_max - self.u_min >= 2 * math.pi - 1e-4 else None
        self.domain = self._profile()

    def point(self, s, t, normal=0.0):
        theta = self.u_min + s / self.radius
        radius = self.radius + self.outward_sign * normal
        v = self.v_min + t
        return (
            self.origin.X() + self.axis.X() * v + radius * (self.x.X() * math.cos(theta) + self.y.X() * math.sin(theta)),
            self.origin.Y() + self.axis.Y() * v + radius * (self.x.Y() * math.cos(theta) + self.y.Y() * math.sin(theta)),
            self.origin.Z() + self.axis.Z() * v + radius * (self.x.Z() * math.cos(theta) + self.y.Z() * math.sin(theta)),
        )

    def _profile(self):
        from OCP.BRepAdaptor import BRepAdaptor_Curve2d
        from OCP.BRepTools import BRepTools_WireExplorer
        from OCP.TopAbs import TopAbs_REVERSED, TopAbs_WIRE
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS

        rings = []
        wires = TopExp_Explorer(self.face, TopAbs_WIRE)
        while wires.More():
            explorer = BRepTools_WireExplorer(TopoDS.Wire_s(wires.Current()), self.face)
            points = []
            while explorer.More():
                edge = TopoDS.Edge_s(explorer.Current())
                curve = BRepAdaptor_Curve2d(edge, self.face)
                first, last = curve.FirstParameter(), curve.LastParameter()
                count = max(2, math.ceil(abs(last - first) * self.radius / 0.35))
                count = min(count, 512)
                sampled = []
                for index in range(count + 1):
                    p = curve.Value(first + (last - first) * index / count)
                    sampled.append(((p.X() - self.u_min) * self.radius, p.Y() - self.v_min))
                if edge.Orientation() == TopAbs_REVERSED:
                    sampled.reverse()
                if points and math.dist(points[-1], sampled[-1]) < math.dist(points[-1], sampled[0]):
                    sampled.reverse()
                for point in sampled:
                    if not points or math.dist(points[-1], point) > 1e-7:
                        points.append(point)
                explorer.Next()
            if len(points) >= 3:
                if math.dist(points[0], points[-1]) > 1e-7:
                    points.append(points[0])
                rings.append(points)
            wires.Next()
        if not rings:
            return box(0, 0, (self.u_max - self.u_min) * self.radius, self.v_max - self.v_min)
        rings.sort(key=lambda ring: abs(Polygon(ring).area), reverse=True)
        profile = Polygon(rings[0], rings[1:])
        if not profile.is_valid:
            profile = profile.buffer(0)
        if profile.is_empty:
            raise CadError("The cylindrical face has no usable boundary.")
        return profile


def mapping_for_face(face):
    from .detect import analyse_cylinder, analyse_plane
    from OCP.TopoDS import TopoDS

    face = TopoDS.Face_s(face)

    if analyse_plane(face) is not None:
        return PlanarMapping(face)
    if analyse_cylinder(face) is not None:
        return CylindricalMapping(face)
    raise CadError("Textures need a flat or cylindrical face.", suggestion="Select one planar face or a curved cylinder wall.")


def _text_regions(document, body_name, mapping, feature_id=None):
    """Map authored text envelopes into the selected face's metric coordinates."""
    if not isinstance(mapping, PlanarMapping):
        return []
    from .text import _frame

    regions = []
    origin, axis_u, axis_v = mapping.frame
    source = document.features
    if feature_id:
        graph = document.prepare_graph()
        target = graph.features.get(feature_id)
        if target is not None:
            needed, todo = set(), list(graph.forward.get(feature_id, ()))
            while todo:
                fid = todo.pop()
                if fid not in needed:
                    needed.add(fid)
                    todo.extend(graph.forward.get(fid, ()))
            source = [graph.features[fid] for fid in needed if fid in graph.features]
    for feature in source:
        if feature.suppressed or feature.type_name != "text" or str(feature.inputs.get("body", "")) != body_name:
            continue
        ref = feature.inputs.get("face")
        if ref is None or not hasattr(ref, "fingerprint"):
            continue
        center = ref.fingerprint.center
        normal, text_x = _frame(ref.fingerprint.direction or mapping.normal)
        text_y = (normal[1] * text_x[2] - normal[2] * text_x[1],
                  normal[2] * text_x[0] - normal[0] * text_x[2],
                  normal[0] * text_x[1] - normal[1] * text_x[0])
        def number(key, default):
            try:
                raw = feature.inputs.get(key, default)
                return float(raw) if isinstance(raw, (int, float)) else document.parameters.evaluate(str(raw), feature.dimensions.get(key, Dimension.LENGTH))
            except Exception:
                return float(default)
        height = number("text_height", 8)
        width = height * max(1, len(str(feature.inputs.get("text", "")))) * 1.2
        envelope = rotate_2d(
            box(-width / 2, -height, width / 2, height),
            number("rotation", 0), origin=(0, 0),
        )
        offset_x = number("offset_x", 0)
        offset_y = number("offset_y", 0)
        projected = []
        for x, y in envelope.exterior.coords:
            point = tuple(
                center[i]
                + text_x[i] * (offset_x + x)
                + text_y[i] * (offset_y + y)
                for i in range(3)
            )
            relative = tuple(point[i] - origin[i] for i in range(3))
            projected.append((
                sum(relative[i] * axis_u[i] for i in range(3)),
                sum(relative[i] * axis_v[i] for i in range(3)),
            ))
        regions.append(Polygon(projected))
    return regions


def layout_for_face(face, settings: dict, document=None, body_name="", mapping=None, feature_id=None) -> TextureLayout:
    mapping = mapping or mapping_for_face(face)
    domain = mapping.domain
    size = float(settings.get("size", 3.0))
    spacing = float(settings.get("spacing", 2.0))
    depth = float(settings.get("depth", 0.6))
    edge = float(settings.get("edge_clearance", 3.0))
    hole = float(settings.get("hole_clearance", 2.0))
    text = float(settings.get("text_clearance", 1.5))
    other = float(settings.get("other_clearance", 2.0))
    if size <= 0 or depth <= 0 or spacing < 0 or min(edge, hole, text, other) < 0:
        raise CadError("Texture size and depth must be positive; spacing and clearances cannot be negative.")
    if size < 0.1:
        raise CadError("The texture size is too small to build reliably.")
    package = str(settings.get("package", "builtin.v1"))
    pattern = str(settings.get("pattern", "horizontal_lines"))
    definition = PACKAGES.get(package, {}).get(pattern)
    if definition is None:
        raise CadError(f"Texture pattern {package}/{pattern} is unavailable.")
    mode = str(settings.get("mode", definition.default_mode))
    if mode not in {"raised", "recessed", "through"} or (mode == "through" and pattern != "vent"):
        raise CadError("Choose Raised or Recessed; Through is available for vent patterns.")

    domain_parts = _polygons(domain)
    outer_geometry = unary_union([Polygon(part.exterior) for part in domain_parts])
    if mapping.period is not None:
        # The seam is a surface parameter boundary, not a physical edge.
        periodic_outer = unary_union([
            outer_geometry,
            translate_2d(outer_geometry, xoff=mapping.period),
            translate_2d(outer_geometry, xoff=-mapping.period),
        ])
        outer = periodic_outer.buffer(-edge, join_style="mitre").intersection(
            box(domain.bounds[0], domain.bounds[1] - edge,
                domain.bounds[2], domain.bounds[3] + edge)
        )
    else:
        outer = outer_geometry.buffer(-edge, join_style="mitre")
    if outer.is_empty:
        raise CadError("The edge clearance leaves no room for a texture.")
    text_regions = _text_regions(document, body_name, mapping, feature_id) if document is not None else []
    protected = []
    for ring in (ring for part in domain_parts for ring in part.interiors):
        region = Polygon(ring)
        if region.is_empty or region.area < 1e-8:
            continue
        circularity = 4 * math.pi * region.area / max(region.length ** 2, 1e-12)
        kind = "text" if any(region.intersects(area) for area in text_regions) else "hole" if circularity > 0.87 else "other"
        distance = {"hole": hole, "text": text, "other": other}[kind]
        protected.append((kind, region, region.buffer(distance, quad_segs=32)))
    if protected:
        exclusions = unary_union([buffered for _kind, _source, buffered in protected])
        if mapping.period is not None:
            exclusions = unary_union([exclusions, translate_2d(exclusions, xoff=mapping.period), translate_2d(exclusions, xoff=-mapping.period)])
        allowed = outer.difference(exclusions)
    else:
        allowed = outer
    if allowed.is_empty:
        raise CadError("The selected clearances leave no room for a texture.")
    offset_u = float(settings.get("offset_u", 0.0))
    offset_v = float(settings.get("offset_v", 0.0))
    rotation = float(settings.get("rotation", 0.0))
    center_u = (allowed.bounds[0] + allowed.bounds[2]) / 2
    center_v = (allowed.bounds[1] + allowed.bounds[3]) / 2
    centered_bounds = (
        allowed.bounds[0] - center_u, allowed.bounds[1] - center_v,
        allowed.bounds[2] - center_u, allowed.bounds[3] - center_v,
    )
    generators = definition.generator(centered_bounds, size, spacing)
    if not generators:
        raise CadError("No pattern elements fit on this face.")
    raw = unary_union([
        translate_2d(
            rotate_2d(motif, rotation, origin=(0, 0)),
            xoff=center_u + offset_u, yoff=center_v + offset_v,
        )
        for motif in generators
    ])
    if mapping.period is not None:
        seam = domain.bounds[0]
        end = domain.bounds[2]
        reach = max(size * 2, spacing)
        crossing_left = raw.intersection(box(seam - reach, domain.bounds[1] - reach,
                                             seam, domain.bounds[3] + reach))
        crossing_right = raw.intersection(box(end, domain.bounds[1] - reach,
                                              end + reach, domain.bounds[3] + reach))
        raw = unary_union([
            raw,
            translate_2d(crossing_left, xoff=mapping.period),
            translate_2d(crossing_right, xoff=-mapping.period),
        ])
    motifs = _polygons(raw.intersection(allowed))
    if not motifs:
        raise CadError("No pattern elements fit inside the clearances.", suggestion="Reduce spacing or clearance, or change the offset.")
    return TextureLayout(domain, allowed, protected, motifs, mapping)


def _planar_tools(layout: TextureLayout, depth: float, mode: str, *, probe=False):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec
    from .operations import _wire_from_coords

    mapping = layout.mapping
    normal = mapping.normal
    if probe:
        start = -(depth + 0.005)
        length = -0.01
    else:
        start = -OVERLAP if mode == "raised" else OVERLAP
        length = depth + OVERLAP if mode == "raised" else -(depth + OVERLAP)
        if mode == "through":
            length = -depth
    tools = []
    for polygon in layout.motifs:
        face = BRepBuilderAPI_MakeFace(_wire_from_coords(polygon.exterior.coords, mapping.frame, normal, start))
        for ring in polygon.interiors:
            face.Add(_wire_from_coords(ring.coords, mapping.frame, normal, start))
        if not face.IsDone():
            raise CadError("A texture element could not be built.")
        tool = built_shape(BRepPrimAPI_MakePrism(
            face.Face(), gp_Vec(*(normal[i] * length for i in range(3)))
        ), "texture element")
        tools.append(tool)
    return compound(tools)


def _triangle_face(points):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    from OCP.gp import gp_Pnt

    wire = BRepBuilderAPI_MakePolygon()
    for point in points:
        wire.Add(gp_Pnt(*point))
    wire.Close()
    face = BRepBuilderAPI_MakeFace(wire.Wire())
    if not face.IsDone():
        raise CadError("A wrapped texture facet could not be built.")
    return face.Face()


def _wrapped_piece_solid(polygon: Polygon, mapping: CylindricalMapping, outer: float, inner: float, step: float):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing
    from OCP.ShapeFix import ShapeFix_Solid
    from OCP.TopoDS import TopoDS

    facets = []
    boundary = {}
    first_strip = math.floor(polygon.bounds[0] / step)
    last_strip = math.ceil(polygon.bounds[2] / step)
    for index in range(first_strip, last_strip):
        slice_region = box(index * step, polygon.bounds[1] - 0.1,
                           (index + 1) * step, polygon.bounds[3] + 0.1)
        for piece in _polygons(polygon.intersection(slice_region)):
            for triangle in constrained_delaunay_triangles(piece).geoms:
                coords = list(triangle.exterior.coords)[:3]
                if triangle.area < 1e-9:
                    continue
                top = [mapping.point(s, t, outer) for s, t in coords]
                bottom = [mapping.point(s, t, inner) for s, t in coords]
                facets.append(_triangle_face(top))
                facets.append(_triangle_face(list(reversed(bottom))))
                for a, b in zip(coords, coords[1:] + coords[:1]):
                    key = tuple(sorted((tuple(round(v, 7) for v in a), tuple(round(v, 7) for v in b))))
                    if key in boundary:
                        count, pair = boundary[key]
                        boundary[key] = (count + 1, pair)
                    else:
                        boundary[key] = (1, (a, b))
    for count, (first, second) in boundary.values():
        if count != 1 or math.dist(first, second) < 1e-8:
            continue
        a = mapping.point(*first, inner)
        b = mapping.point(*second, inner)
        c = mapping.point(*second, outer)
        d = mapping.point(*first, outer)
        facets.append(_triangle_face((a, b, c)))
        facets.append(_triangle_face((a, c, d)))
    sewing = BRepBuilderAPI_Sewing(1e-5)
    for facet in facets:
        sewing.Add(facet)
    sewing.Perform()
    sewn = sewing.SewedShape()
    if sewn.IsNull():
        raise CadError("A wrapped texture element could not be closed.")
    try:
        shell = TopoDS.Shell_s(sewn)
    except Exception as exc:
        raise CadError("A wrapped texture element did not form a shell.") from exc
    maker = BRepBuilderAPI_MakeSolid(shell)
    if not maker.IsDone():
        raise CadError("A wrapped texture element did not form a solid.")
    fixer = ShapeFix_Solid(maker.Solid())
    fixer.Perform()
    solid = fixer.Solid()
    if not is_valid(solid) or volume(solid) <= 1e-9:
        raise CadError("A wrapped texture element is not a valid solid.")
    return solid


def _wall_depth(body, mapping: CylindricalMapping):
    """Find the nearest coaxial surface on the material side of the face."""
    from ..core.naming import sub_shapes
    from .detect import analyse_cylinder

    selected = mapping.info
    candidates = []
    for face in sub_shapes(body, "face"):
        info = analyse_cylinder(face)
        if info is None or info.internal == selected.internal:
            continue
        if abs(sum(a * b for a, b in zip(info.direction, selected.direction))) < 0.999:
            continue
        delta = tuple(info.origin[i] - selected.origin[i] for i in range(3))
        along = sum(delta[i] * selected.direction[i] for i in range(3))
        distance = math.sqrt(sum((delta[i] - along * selected.direction[i]) ** 2 for i in range(3)))
        if distance > 1e-3:
            continue
        difference = selected.radius - info.radius if not selected.internal else info.radius - selected.radius
        if difference > 0.05:
            candidates.append(difference)
    return min(candidates) if candidates else None


def _cylindrical_tools(layout: TextureLayout, depth: float, mode: str, body, *, probe=False):
    mapping = layout.mapping
    if probe:
        outer, inner = -depth - 0.005, -depth - 0.02
    elif mode == "raised":
        outer, inner = depth, -0.08
    else:
        outer, inner = 0.08, -depth
    if mode == "through" and not probe:
        wall = _wall_depth(body, mapping)
        if wall is None:
            raise CadError("Through vents need an identifiable opposite cylindrical wall.", suggestion="Choose Recessed, or select a tube or curved panel wall.")
        inner = -wall - 0.08
    if mapping.radius + mapping.outward_sign * outer <= 0 or mapping.radius + mapping.outward_sign * inner <= 0:
        raise CadError("The texture depth reaches the cylinder axis.")
    # Curved triangles need a bounded angular chord error.  Vertical strips
    # make every facet narrow enough while preserving clipped motif boundaries.
    step = min(2.0, max(0.25, math.sqrt(8 * mapping.radius * 0.025)))
    solids = []
    for motif in layout.motifs:
        if motif.area < 1e-7:
            continue
        solids.append(_wrapped_piece_solid(motif, mapping, outer, inner, step))
        if len(solids) > MAX_MOTIFS:
            raise CadError("This wrapped texture is too dense.", suggestion="Increase its size or spacing.")
    return compound(solids)


@register("face_texture")
class FaceTextureFeature(Feature):
    label = "Face Texture"
    dimensions = {"rotation": Dimension.ANGLE}

    def parameter_names(self) -> set[str]:
        names = set()
        for key in NUMERIC_INPUTS:
            raw = self.inputs.get(key)
            if isinstance(raw, str) and not isinstance(raw, BodyRef):
                names |= dependencies(raw)
        return names

    def _settings(self, ctx: BuildContext) -> dict:
        values = {
            key: ctx.value(self, key, default)
            for key, default in (
                ("size", 3.0), ("spacing", 2.0), ("depth", 0.6),
                ("rotation", 0.0), ("offset_u", 0.0), ("offset_v", 0.0),
                ("edge_clearance", 3.0), ("hole_clearance", 2.0),
                ("text_clearance", 1.5), ("other_clearance", 2.0),
            )
        }
        values.update({key: self.inputs.get(key, default) for key, default in (
            ("package", "builtin.v1"), ("pattern", "horizontal_lines"), ("mode", "raised"),
        )})
        return values

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from .occ import bounding_box

        name = str(self.inputs.get("body") or "")
        if not name:
            raise CadError("The texture has no body to work on.")
        body = ctx.shape(self, "body")
        face = ctx.resolve(self, "face")
        settings = self._settings(ctx)
        layout = layout_for_face(face, settings, ctx.document, name, feature_id=ctx.feature_id)
        depth = settings["depth"]
        mode = settings["mode"]
        if isinstance(layout.mapping, CylindricalMapping):
            tools = _cylindrical_tools(layout, depth, mode, body)
        else:
            if mode == "through":
                low, high = bounding_box(body)
                depth = math.dist(low, high) * 2 + 1.0
            tools = _planar_tools(layout, depth, mode)
        if tools is None:
            raise CadError("No solid texture elements fit this face.")
        before = volume(body)
        with guard("face texture"):
            if mode == "recessed":
                probe = (
                    _planar_tools(layout, settings["depth"], mode, probe=True)
                    if isinstance(layout.mapping, PlanarMapping)
                    else _cylindrical_tools(layout, settings["depth"], mode, body, probe=True)
                )
                overlap = built_shape(boolean_builder(BRepAlgoAPI_Common, body, probe), "texture depth")
                if volume(overlap) < volume(probe) * 0.995:
                    raise CadError("The recessed texture is deeper than the material below it.", suggestion="Reduce depth or use Through on a vent pattern.")
            result = built_shape(
                boolean_builder(BRepAlgoAPI_Fuse if mode == "raised" else BRepAlgoAPI_Cut, body, tools),
                "face texture",
            )
            # A cylinder's deliberately faceted relief has many small faces.
            # ShapeUpgrade_UnifySameDomain becomes superlinear on that topology
            # and may stall for minutes; the boolean result is already valid.
            if isinstance(layout.mapping, PlanarMapping):
                result = unify(result)
        solids_before = _solid_count(body)
        solids_after = _solid_count(result)
        if (
            not is_valid(result)
            or solids_after == 0
            or (mode == "raised" and solids_after != solids_before)
            or abs(volume(result) - before) < 1e-7
        ):
            raise CadError("The texture did not produce a valid printable solid.", suggestion="Try a larger size or smaller depth.")
        if solids_after > solids_before:
            ctx.warn(f"This texture leaves {solids_after} disconnected solids; check them before printing.")
        self.outputs = [name]
        self.message = f"{PACKAGES[settings['package']][settings['pattern']].label} on one face"
        return {name: result}
