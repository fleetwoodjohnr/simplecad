"""Reusable, double-ended printed clip joints.

A clip joint is deliberately one feature with several outputs: it seats two
selected planar faces, cuts the same socket into both parts, and emits one
separate connector for every requested position.  Keeping that relationship in
one node means changing the spacing or fit updates every participant together.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..core.document import BuildContext, Feature, register
from ..core.errors import CadError, guard
from ..core.units import Dimension
from .align import planar_frame, solve
from .occ import built_shape, is_valid, transformed, unify, volume


MAX_CONNECTORS = 64

SIZE_PRESETS = {
    "small": {
        "arm_width": 1.2, "slot": 0.8, "thickness": 2.0,
        "engagement": 6.0, "bridge": 1.0, "barb": 0.4,
    },
    "medium": {
        "arm_width": 1.8, "slot": 1.0, "thickness": 3.0,
        "engagement": 8.0, "bridge": 1.2, "barb": 0.6,
    },
    "large": {
        "arm_width": 2.4, "slot": 1.2, "thickness": 4.0,
        "engagement": 12.0, "bridge": 1.6, "barb": 0.8,
    },
}

RETENTION_PRESETS = {
    "easy": {"barb_scale": 0.75, "removal_angle": 25.0},
    "standard": {"barb_scale": 1.0, "removal_angle": 35.0},
    "firm": {"barb_scale": 1.25, "removal_angle": 45.0},
}


@dataclass(frozen=True)
class ClipSpec:
    arm_width: float
    slot: float
    thickness: float
    engagement: float
    bridge: float
    barb: float
    removal_angle: float
    clearance: float

    @property
    def shank_width(self) -> float:
        return self.slot + 2.0 * self.arm_width

    @property
    def maximum_width(self) -> float:
        return self.shank_width + 2.0 * self.barb

    @property
    def reach(self) -> float:
        return self.engagement + self.bridge / 2.0


def layout_points(
    mode: str,
    anchors,
    *,
    count: int = 2,
    rows: int = 2,
    columns: int = 2,
    spacing_mode: str = "equal",
    spacing: float = 10.0,
) -> list[tuple[float, float]]:
    """Expand face-local anchors into manual, row, or grid positions."""
    points = [tuple(map(float, point[:2])) for point in (anchors or ())]
    if mode == "manual":
        return points[:MAX_CONNECTORS]
    if len(points) < 2:
        return []
    first, second = points[0], points[1]
    if mode == "row":
        count = max(1, min(MAX_CONNECTORS, int(count)))
        dx, dy = second[0] - first[0], second[1] - first[1]
        if spacing_mode == "fixed":
            length = math.hypot(dx, dy)
            if length < 1.0e-9:
                return [first]
            step = float(spacing)
            ux, uy = dx / length, dy / length
            return [(first[0] + ux * step * i, first[1] + uy * step * i)
                    for i in range(count)]
        if count == 1:
            return [first]
        return [
            (first[0] + dx * i / (count - 1), first[1] + dy * i / (count - 1))
            for i in range(count)
        ]
    if mode == "grid":
        rows = max(1, int(rows))
        columns = max(1, int(columns))
        if rows * columns > MAX_CONNECTORS:
            raise CadError(f"A clip joint supports at most {MAX_CONNECTORS} connectors.")
        result = []
        for row in range(rows):
            v = 0.0 if rows == 1 else row / (rows - 1)
            for column in range(columns):
                u = 0.0 if columns == 1 else column / (columns - 1)
                result.append((
                    first[0] + (second[0] - first[0]) * u,
                    first[1] + (second[1] - first[1]) * v,
                ))
        return result
    raise CadError(f"Unknown clip layout: {mode}")


def _fit_clearance(value, ctx: BuildContext, feature: Feature) -> float:
    if isinstance(value, str) and value.lower() in ("press", "snug", "sliding"):
        from .calibration import effective_fits
        from .thread_specs import printer_profile

        return float(effective_fits(printer_profile()["id"])[value.lower()])
    if isinstance(value, (int, float)):
        return float(value)
    # Advanced mode accepts the same parameter expressions as every other tool.
    temporary_key = "_resolved_clip_clearance"
    feature.inputs[temporary_key] = value
    try:
        return ctx.value(feature, temporary_key, 0.15)
    finally:
        feature.inputs.pop(temporary_key, None)


def resolve_spec(feature: Feature, ctx: BuildContext) -> ClipSpec:
    size = str(feature.inputs.get("size", "medium")).lower()
    retention = str(feature.inputs.get("retention", "standard")).lower()
    material = str(feature.inputs.get("material", "petg")).lower()
    if size not in SIZE_PRESETS:
        raise CadError(f"Unknown clip size: {size}")
    if retention not in RETENTION_PRESETS:
        raise CadError(f"Unknown clip retention: {retention}")
    values = dict(SIZE_PRESETS[size])
    retained = RETENTION_PRESETS[retention]
    if material == "pla":
        values["engagement"] *= 1.25
        values["barb"] *= 0.65
    elif material != "petg":
        raise CadError("Clip material must be PLA or PETG.")
    values["barb"] *= retained["barb_scale"]
    values["removal_angle"] = retained["removal_angle"]

    for key in (
        "arm_width", "slot", "thickness", "engagement", "bridge", "barb",
        "removal_angle",
    ):
        if key in feature.inputs:
            values[key] = ctx.value(feature, key, values[key])
    values["clearance"] = _fit_clearance(
        feature.inputs.get("clearance", "snug"), ctx, feature
    )
    if any(values[key] <= 0.0 for key in (
        "arm_width", "slot", "thickness", "engagement", "bridge", "barb",
    )):
        raise CadError("Clip dimensions must be greater than zero.")
    if (
        values["slot"] + 2.0 * values["arm_width"] + values["clearance"] <= 0.0
        or values["thickness"] + values["clearance"] <= 0.0
    ):
        raise CadError("That clearance makes the socket smaller than zero.")
    if not 10.0 <= values["removal_angle"] <= 70.0:
        raise CadError("The removal ramp angle must be between 10° and 70°.")
    return ClipSpec(**values)


def _prong_profile(spec: ClipSpec, sign: int, side: int):
    """One tapered arm and its asymmetric retaining bump in canonical X/Z."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Pnt, gp_Vec

    root_inner = spec.slot / 2.0
    root_outer = root_inner + spec.arm_width
    tip_inner = root_inner
    tip_outer = root_outer - spec.arm_width * 0.15
    insertion = spec.barb / math.tan(math.radians(30.0))
    removal = spec.barb / math.tan(math.radians(spec.removal_angle))
    ramp_total = insertion + removal
    if ramp_total > spec.engagement * 0.42:
        factor = spec.engagement * 0.42 / ramp_total
        insertion *= factor
        removal *= factor
    peak = spec.engagement - insertion
    before_peak = peak - removal
    z0 = spec.bridge / 2.0
    root_radius = spec.arm_width * 0.25
    inner_center = (root_inner - root_radius, z0 + root_radius)
    outer_center = (root_outer - root_radius, z0 + root_radius)
    coords = []
    # The two quarter-arcs soften both sides of the cantilever root. This is
    # the stress-critical part of a repeatedly removable clip; the 25% radius
    # scales with every size preset instead of becoming a magic fixed fillet.
    for angle in (0.0, -22.5, -45.0, -67.5, -90.0):
        radians = math.radians(angle)
        coords.append((
            inner_center[0] + root_radius * math.cos(radians),
            inner_center[1] + root_radius * math.sin(radians),
        ))
    for angle in (-90.0, -67.5, -45.0, -22.5, 0.0):
        radians = math.radians(angle)
        coords.append((
            outer_center[0] + root_radius * math.cos(radians),
            outer_center[1] + root_radius * math.sin(radians),
        ))
    coords.extend([
        (tip_outer, z0 + before_peak),
        (tip_outer + spec.barb, z0 + peak),
        (tip_outer, z0 + spec.engagement),
        (tip_inner, z0 + spec.engagement),
    ])
    if side < 0:
        coords = [(-x, z) for x, z in coords]
    if sign < 0:
        coords = [(x, -z) for x, z in coords]

    polygon = BRepBuilderAPI_MakePolygon()
    for x, z in coords:
        polygon.Add(gp_Pnt(x, -spec.thickness / 2.0, z))
    polygon.Close()
    face = BRepBuilderAPI_MakeFace(polygon.Wire()).Face()
    return BRepPrimAPI_MakePrism(face, gp_Vec(0, spec.thickness, 0)).Shape()


def make_connector(spec: ClipSpec):
    """Build the separate two-handed connector in its canonical frame."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    bridge = BRepPrimAPI_MakeBox(
        gp_Pnt(-spec.shank_width / 2.0, -spec.thickness / 2.0, -spec.bridge / 2.0),
        spec.shank_width, spec.thickness, spec.bridge,
    ).Shape()
    shape = bridge
    for sign in (-1, 1):
        for side in (-1, 1):
            shape = built_shape(
                BRepAlgoAPI_Fuse(shape, _prong_profile(spec, sign, side)),
                "clip connector",
            )
    result = unify(shape)
    if not is_valid(result):
        raise CadError("The requested clip dimensions do not make a valid connector.")
    return result


def make_socket(spec: ClipSpec, sign: int, *, inset: float = -0.02):
    """Stepped socket cutter for one end of a connector."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    narrow_width = spec.shank_width + spec.clearance
    wide_width = spec.maximum_width + spec.clearance
    height = spec.thickness + spec.clearance
    total = spec.reach - inset
    catch = min(spec.engagement * 0.46, max(spec.barb * 2.2, spec.engagement * 0.25))
    narrow_length = max(spec.bridge / 2.0, total - catch)
    if sign > 0:
        narrow_z = inset
        catch_z = inset + narrow_length
    else:
        narrow_z = -(inset + narrow_length)
        catch_z = -(inset + total)
    narrow = BRepPrimAPI_MakeBox(
        gp_Pnt(-narrow_width / 2.0, -height / 2.0, narrow_z),
        narrow_width, height, narrow_length,
    ).Shape()
    pocket = BRepPrimAPI_MakeBox(
        gp_Pnt(-wide_width / 2.0, -height / 2.0, catch_z),
        wide_width, height, total - narrow_length,
    ).Shape()
    return built_shape(BRepAlgoAPI_Fuse(narrow, pocket), "clip socket")


def _placement_transform(point, frame, rotation_degrees: float):
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf

    angle = math.radians(rotation_degrees)
    x_axis = tuple(
        frame.x_axis[i] * math.cos(angle) + frame.y_axis[i] * math.sin(angle)
        for i in range(3)
    )
    source = gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1), gp_Dir(1, 0, 0))
    target = gp_Ax3(gp_Pnt(*point), gp_Dir(*frame.normal), gp_Dir(*x_axis))
    result = gp_Trsf()
    result.SetDisplacement(source, target)
    return result


def _world_point(frame, point):
    return frame.point(point[0], point[1])


def _face_polygon(face):
    # Reuse the well-tested planar boundary sampler used by Hollow. It retains
    # holes and curved trimmed boundaries rather than accepting UV bounds.
    from OCP.TopoDS import TopoDS

    from .operations import _face_profile

    return _face_profile(TopoDS.Face_s(face), 0.035)


def _footprint(point, frame, spec: ClipSpec, rotation_degrees: float):
    from shapely.geometry import Polygon

    half_x = (spec.maximum_width + spec.clearance) / 2.0
    half_y = (spec.thickness + spec.clearance) / 2.0
    angle = math.radians(rotation_degrees)
    cosine, sine = math.cos(angle), math.sin(angle)
    world = []
    for x, y in ((-half_x, -half_y), (half_x, -half_y),
                 (half_x, half_y), (-half_x, half_y)):
        rx, ry = x * cosine - y * sine, x * sine + y * cosine
        world.append(frame.point(point[0] + rx, point[1] + ry))
    return world, Polygon([
        (point[0] + x * cosine - y * sine, point[1] + x * sine + y * cosine)
        for x, y in ((-half_x, -half_y), (half_x, -half_y),
                     (half_x, half_y), (-half_x, half_y))
    ])


def _project_polygon(world_points, local_frame):
    from shapely.geometry import Polygon

    origin, x_axis, y_axis = local_frame
    points = []
    for point in world_points:
        delta = tuple(point[i] - origin[i] for i in range(3))
        points.append((
            sum(delta[i] * x_axis[i] for i in range(3)),
            sum(delta[i] * y_axis[i] for i in range(3)),
        ))
    return Polygon(points)


def _validate_placements(points, frame, face_a, face_b, spec, rotation):
    target_profile, target_local = _face_polygon(face_b)
    moving_profile, moving_local = _face_polygon(face_a)
    target_profile = target_profile.buffer(1.0e-6)
    moving_profile = moving_profile.buffer(1.0e-6)
    footprints = []
    for index, point in enumerate(points):
        world, local = _footprint(point, frame, spec, rotation)
        if not target_profile.covers(_project_polygon(world, target_local)):
            raise CadError(
                f"Clip {index + 1} does not fit on the target face.",
                suggestion="Move it farther from the edge or choose a smaller clip.",
            )
        if not moving_profile.covers(_project_polygon(world, moving_local)):
            raise CadError(
                f"Clip {index + 1} does not fit on the moving face after alignment.",
                suggestion="Move it into the area shared by both faces.",
            )
        if any(local.intersection(other).area > 1.0e-7 for other in footprints):
            raise CadError(
                f"Clip {index + 1} overlaps another clip.",
                suggestion="Increase the spacing or use fewer clips.",
            )
        footprints.append(local)


def _contained(body, cutter) -> bool:
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common

    common = built_shape(BRepAlgoAPI_Common(body, cutter), "clip depth check")
    expected = volume(cutter)
    return expected > 1.0e-8 and volume(common) >= expected * 0.985


@register("clip_joint")
class ClipJointFeature(Feature):
    """Seat two parts, cut paired sockets, and emit removable connectors."""

    label = "Clip Joint"
    dimensions = {"rotation": Dimension.ANGLE, "removal_angle": Dimension.ANGLE}

    def execute(self, ctx: BuildContext) -> dict[str, object]:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

        first_name = str(self.inputs.get("body_a", ""))
        second_name = str(self.inputs.get("body_b", ""))
        if not first_name or not second_name or first_name == second_name:
            raise CadError("A clip joint needs two different bodies.")
        first = ctx.shape(self, "body_a")
        second = ctx.shape(self, "body_b")
        face_a = ctx.resolve(self, "face_a")
        face_b = ctx.resolve(self, "face_b")

        placement = solve(
            face_a, face_b, operation="stack",
            x=ctx.value(self, "align_x", 0.0),
            y=ctx.value(self, "align_y", 0.0),
        )
        aligned_first = transformed(first, placement.transform)
        aligned_face_a = transformed(face_a, placement.transform)
        frame = planar_frame(face_b)
        spec = resolve_spec(self, ctx)
        rotation = ctx.value(self, "rotation", 0.0)
        anchors = self.inputs.get("anchors") or []
        points = layout_points(
            str(self.inputs.get("layout", "manual")), anchors,
            count=int(self.inputs.get("count", 2)),
            rows=int(self.inputs.get("rows", 2)),
            columns=int(self.inputs.get("columns", 2)),
            spacing_mode=str(self.inputs.get("spacing_mode", "equal")),
            spacing=ctx.value(self, "spacing", 10.0),
        )
        if not points:
            raise CadError(
                "Place at least one clip on the target face.",
                suggestion="Click the face to add a clip location.",
            )
        if len(points) > MAX_CONNECTORS:
            raise CadError(f"A clip joint supports at most {MAX_CONNECTORS} connectors.")
        connector_names = [str(name) for name in self.inputs.get("connector_names", [])]
        if len(connector_names) < len(points):
            raise CadError("The clip joint is missing connector output names.")

        active = [
            (point, connector_names[index])
            for index, point in enumerate(points)
            if connector_names[index] not in self.dropped
        ]
        _validate_placements(
            [point for point, _name in active], frame,
            aligned_face_a, face_b, spec, rotation,
        )

        result_a, result_b = aligned_first, second
        connectors = {}
        canonical_connector = make_connector(spec)
        # Depth validation starts just inside the face, avoiding the deliberate
        # 0.02 mm cutter overlap used to make the final booleans robust.
        check_a = make_socket(spec, 1, inset=0.02)
        check_b = make_socket(spec, -1, inset=0.02)
        socket_a = make_socket(spec, 1)
        socket_b = make_socket(spec, -1)
        with guard("clip joint"):
            for index, (point, name) in enumerate(active):
                transform = _placement_transform(_world_point(frame, point), frame, rotation)
                tool_a = transformed(socket_a, transform)
                tool_b = transformed(socket_b, transform)
                if not _contained(result_a, transformed(check_a, transform)):
                    raise CadError(
                        f"Clip {index + 1} would break through the moving part.",
                        suggestion="Use a smaller clip or a thicker part.",
                    )
                if not _contained(result_b, transformed(check_b, transform)):
                    raise CadError(
                        f"Clip {index + 1} would break through the target part.",
                        suggestion="Use a smaller clip or a thicker part.",
                    )
                result_a = built_shape(BRepAlgoAPI_Cut(result_a, tool_a), "clip socket")
                result_b = built_shape(BRepAlgoAPI_Cut(result_b, tool_b), "clip socket")
                connectors[name] = transformed(canonical_connector, transform)

        result_a, result_b = unify(result_a), unify(result_b)
        if not is_valid(result_a) or not is_valid(result_b):
            raise CadError("The clip sockets did not leave valid printable parts.")
        self.outputs = [first_name, second_name] + [name for _point, name in active]
        self.message = f"Aligned both parts and created {len(active)} removable clip(s)."
        return self.keep({first_name: result_a, second_name: result_b, **connectors})
