"""Reusable, double-ended printed clip joints.

A clip joint is deliberately one feature with several outputs: it seats two
selected planar faces, cuts the same socket into both parts, and emits one
separate connector for every requested position.  Keeping that relationship in
one node means changing the spacing or fit updates every participant together.
"""

from __future__ import annotations

from .occ import boolean_builder

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
                boolean_builder(BRepAlgoAPI_Fuse, shape, _prong_profile(spec, sign, side)),
                "clip connector",
            )
    result = unify(shape)
    if not is_valid(result):
        raise CadError("The requested clip dimensions do not make a valid connector.")
    return result


def make_socket(spec: ClipSpec, sign: int, *, inset: float = -0.02, extra_depth: float = 0.0):
    """Stepped socket cutter for one end of a connector.

    *extra_depth* lengthens the catch pocket past the connector tip, so a
    printed part that is a little long in Z (or a little elephant's foot on
    the mating face) still lets the faces close.
    """
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
    if sign < 0:
        catch_z -= extra_depth
    pocket = BRepPrimAPI_MakeBox(
        gp_Pnt(-wide_width / 2.0, -height / 2.0, catch_z),
        wide_width, height, total - narrow_length + extra_depth,
    ).Shape()
    return built_shape(boolean_builder(BRepAlgoAPI_Fuse, narrow, pocket), "clip socket")


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

    common = built_shape(boolean_builder(BRepAlgoAPI_Common, body, cutter), "clip depth check")
    expected = volume(cutter)
    return expected > 1.0e-8 and volume(common) >= expected * 0.985


@register("clip_joint")
class ClipJointFeature(Feature):
    """Seat two parts, cut paired sockets, and emit removable connectors."""

    label = "Clip Joint"
    dimensions = {"rotation": Dimension.ANGLE, "removal_angle": Dimension.ANGLE}

    def execute(self, ctx: BuildContext) -> dict[str, object]:
        if self.inputs.get("clip_version"):
            return execute_joint(self, ctx)
        return self._execute_legacy(ctx)

    def _execute_legacy(self, ctx: BuildContext) -> dict[str, object]:
        """Version-1 clip joints: seat A onto B, then clip at clicked points.

        Kept exactly as it was so projects made with it rebuild unchanged.
        """
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
                result_a = built_shape(boolean_builder(BRepAlgoAPI_Cut, result_a, tool_a), "clip socket")
                result_b = built_shape(boolean_builder(BRepAlgoAPI_Cut, result_b, tool_b), "clip socket")
                connectors[name] = transformed(canonical_connector, transform)

        result_a, result_b = unify(result_a), unify(result_b)
        if not is_valid(result_a) or not is_valid(result_b):
            raise CadError("The clip sockets did not leave valid printable parts.")
        self.outputs = [first_name, second_name] + [name for _point, name in active]
        self.message = f"Aligned both parts and created {len(active)} removable clip(s)."
        return self.keep({first_name: result_a, second_name: result_b, **connectors})


# ----------------------------------------------------------------------
# Version 2: Join Parts -- two mated faces in, aligned printable joints out
# ----------------------------------------------------------------------
#
# The faces are expected to be touching already -- the normal case is a part
# that has just been split for printing. Nothing moves: bringing two parts
# together is an Align feature of its own, added by the panel when needed,
# so the joint never silently relocates a part the user has positioned.
#
# Every style is built in one canonical frame and then placed:
#   origin  on the mating plane
#   +Z      out of the part that carries the male half, into its mate
#   X       along the seam (the direction clips are strung out in)
#   Y       across the seam (usually the wall thickness)

STYLES = {
    "snap_tab": "Snap tab",
    "connector": "Printed connector",
    "tab_slot": "Tab & slot",
}

#: Keyed tab-and-slot: a straight key with a detent bump either side.
KEY_PRESETS = {
    "small": {"width": 6.0, "thickness": 2.4, "length": 6.0, "bump": 0.3},
    "medium": {"width": 8.0, "thickness": 3.0, "length": 8.0, "bump": 0.4},
    "large": {"width": 12.0, "thickness": 4.0, "length": 11.0, "bump": 0.5},
}
KEY_RETENTION = {"easy": 0.7, "standard": 1.0, "firm": 1.3}

#: Largest bending strain a cantilever may see while it snaps in, for a joint
#: meant to be opened again. PLA is brittle; PETG takes about twice as much.
ALLOWED_STRAIN = {"pla": 0.02, "petg": 0.04}

DEFAULTS = {
    "edge_offset": 1.0,      # from the edge of the shared face to the wall
    "min_wall": 0.8,         # two perimeters of a 0.4 mm nozzle
    "depth_clearance": 0.3,  # extra pocket depth past the tip, for Z error
    "lead_in": 0.4,          # 45° chamfer at the pocket mouth
    "spacing": 80.0,         # Auto: about one joint per this much seam
}


@dataclass(frozen=True)
class JointSpec:
    """Everything the geometry of one joint depends on, in millimetres."""

    style: str
    clip: ClipSpec | None       # prong geometry (snap_tab, connector)
    key: dict | None            # key geometry (tab_slot)
    clearance: float
    depth_clearance: float
    lead_in: float
    min_wall: float
    material: str

    @property
    def footprint(self) -> tuple[float, float]:
        """Width along and across the seam that the female pocket occupies."""
        if self.style == "tab_slot":
            return (self.key["width"] + self.clearance,
                    self.key["thickness"] + 2.0 * self.key["bump"] + self.clearance)
        return (self.clip.maximum_width + self.clearance, self.clip.thickness + self.clearance)

    @property
    def mouth(self) -> tuple[float, float]:
        """The pocket's opening on the mating face, before its lead-in."""
        if self.style == "tab_slot":
            return (self.key["width"] + self.clearance, self.key["thickness"] + self.clearance)
        return (self.clip.shank_width + self.clearance, self.clip.thickness + self.clearance)

    @property
    def depth(self) -> float:
        if self.style == "tab_slot":
            return self.key["length"] + self.depth_clearance
        return self.clip.reach + self.depth_clearance

    @property
    def keep_out(self) -> tuple[float, float]:
        """The face area a joint claims: pocket plus its surrounding wall.

        The minimum wall stands around the full pocket. The lead-in chamfer
        only opens the top fraction of a millimetre of the mouth, so it needs
        one printed line of wall beside it, not the full minimum.
        """
        width, across = self.footprint
        lead = 2.0 * self.lead_in + 0.8
        return (max(width + 2.0 * self.min_wall, self.mouth[0] + lead),
                max(across + 2.0 * self.min_wall, self.mouth[1] + lead))


def resolve_joint_spec(feature: Feature, ctx: BuildContext) -> JointSpec:
    style = str(feature.inputs.get("style", "snap_tab"))
    if style not in STYLES:
        raise CadError(f"Unknown joint style: {style}")
    material = str(feature.inputs.get("material", "petg")).lower()
    if material not in ALLOWED_STRAIN:
        raise CadError("Clip material must be PLA or PETG.")
    clearance = _fit_clearance(feature.inputs.get("clearance", "snug"), ctx, feature)
    extras = {key: ctx.value(feature, key, DEFAULTS[key])
              for key in ("depth_clearance", "lead_in", "min_wall")}
    if extras["min_wall"] < 0.4:
        raise CadError("The minimum wall must be at least 0.4 mm -- one printed line.")
    if extras["depth_clearance"] < 0.0 or extras["lead_in"] < 0.0:
        raise CadError("Depth clearance and lead-in cannot be negative.")
    if clearance < -0.2:
        raise CadError("That clearance is tighter than any printer can press together.")
    clip = key = None
    if style == "tab_slot":
        size = str(feature.inputs.get("size", "medium")).lower()
        retention = str(feature.inputs.get("retention", "standard")).lower()
        if size not in KEY_PRESETS or retention not in KEY_RETENTION:
            raise CadError("Unknown key size or retention.")
        key = dict(KEY_PRESETS[size])
        key["bump"] *= KEY_RETENTION[retention] * (0.7 if material == "pla" else 1.0)
        for name, source in (("width", "key_width"), ("thickness", "key_thickness"),
                             ("length", "key_length"), ("bump", "bump")):
            if source in feature.inputs:
                key[name] = ctx.value(feature, source, key[name])
        if min(key["width"], key["thickness"], key["length"]) <= 0.0 or key["bump"] < 0.0:
            raise CadError("Key dimensions must be greater than zero.")
        if key["bump"] > key["thickness"] * 0.3:
            raise CadError("The detent bump is too large for the key.",
                           suggestion="Use a thicker key or an easier retention.")
    else:
        clip = resolve_spec(feature, ctx)
        clip = ClipSpec(**{**clip.__dict__, "clearance": clearance})
        strain = 1.5 * clip.arm_width * clip.barb / (clip.engagement ** 2)
        if strain > ALLOWED_STRAIN[material]:
            raise CadError(
                f"These arms would bend {strain * 100:.1f}% to snap in; "
                f"{material.upper()} tolerates about {ALLOWED_STRAIN[material] * 100:.0f}%.",
                suggestion="Choose an easier retention, a longer engagement or thinner arms.",
            )
    return JointSpec(style=style, clip=clip, key=key, clearance=clearance,
                     material=material, **extras)


# -- canonical geometry -------------------------------------------------
def _box(x0, y0, z0, dx, dy, dz):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    return BRepPrimAPI_MakeBox(gp_Pnt(x0, y0, z0), dx, dy, dz).Shape()


def _fuse(*shapes):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

    result = shapes[0]
    for shape in shapes[1:]:
        result = built_shape(boolean_builder(BRepAlgoAPI_Fuse, result, shape), "joint")
    return result


def _lead_in(width, across, lead):
    """A 45° frustum widening the pocket mouth by *lead* on every side."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
    from OCP.gp import gp_Pnt

    def ring(half_x, half_y, z):
        polygon = BRepBuilderAPI_MakePolygon()
        for x, y in ((-half_x, -half_y), (half_x, -half_y), (half_x, half_y), (-half_x, half_y)):
            polygon.Add(gp_Pnt(x, y, z))
        polygon.Close()
        return polygon.Wire()

    loft = BRepOffsetAPI_ThruSections(True, True)
    loft.AddWire(ring(width / 2.0 + lead + 0.02, across / 2.0 + lead + 0.02, -0.02))
    loft.AddWire(ring(width / 2.0, across / 2.0, lead))
    loft.Build()
    return loft.Shape()


def _key_bumps(key, radius_growth=0.0, length_growth=0.0):
    """Two half-round detents across the key's broad faces, near its tip."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    bump = key["bump"]
    if bump <= 0.0:
        return []
    radius = max(bump * 2.0, 0.6) + radius_growth
    z = key["length"] * 0.62
    span = key["width"] * 0.8 + length_growth
    shapes = []
    for side in (-1.0, 1.0):
        # The cylinder's axis sits inside the key so only *bump* stands proud.
        y = side * (key["thickness"] / 2.0 - (radius - radius_growth) + bump)
        axis = gp_Ax2(gp_Pnt(-span / 2.0, y, z), gp_Dir(1, 0, 0))
        shapes.append(BRepPrimAPI_MakeCylinder(axis, radius, span).Shape())
    return shapes


def male_half(spec: JointSpec, root: float = 0.3):
    """The part that stands proud of the male face (snap_tab, tab_slot)."""
    if spec.style == "tab_slot":
        key = spec.key
        body = _box(-key["width"] / 2.0, -key["thickness"] / 2.0, -root,
                    key["width"], key["thickness"], key["length"] + root)
        # A small chamfer-like step at the tip keeps the key from catching.
        return unify(_fuse(body, *_key_bumps(key)))
    clip = spec.clip
    base = _box(-clip.shank_width / 2.0, -clip.thickness / 2.0, -root,
                clip.shank_width, clip.thickness, clip.bridge / 2.0 + root)
    arms = [_prong_profile(clip, 1, side) for side in (-1, 1)]
    return unify(_fuse(base, *arms))


def female_cutter(spec: JointSpec, *, inset: float = -0.02):
    """The pocket cut into the mating part, lead-in and depth slack included."""
    if spec.style == "tab_slot":
        key = spec.key
        width, across = spec.mouth
        pocket = _box(-width / 2.0, -across / 2.0, inset, width, across, spec.depth - inset)
        grooves = _key_bumps(key, radius_growth=spec.clearance / 2.0,
                             length_growth=spec.clearance)
        shapes = [pocket, *grooves]
    else:
        shapes = [make_socket(spec.clip, 1, inset=inset, extra_depth=spec.depth_clearance)]
    if spec.lead_in > 0.0 and inset < 0.0:
        shapes.append(_lead_in(*spec.mouth, spec.lead_in))
    return _fuse(*shapes) if len(shapes) > 1 else shapes[0]


def wall_check_cutter(spec: JointSpec):
    """The pocket grown by the minimum wall on every side but the mouth."""
    width, across = spec.keep_out
    depth = spec.depth + spec.min_wall
    return _box(-width / 2.0, -across / 2.0, 0.05, width, across, depth - 0.05)


# -- mating and placement ------------------------------------------------
@dataclass(frozen=True)
class Seam:
    """Two touching planar faces and the region they share."""

    region: object                 # shapely geometry in the seam's 2-D frame
    origin: tuple
    x_axis: tuple
    y_axis: tuple
    normal_a: tuple                # out of body A, into body B
    frame_b: object                # planar_frame(face_b), for manual anchors

    def world(self, u, v):
        return tuple(self.origin[i] + u * self.x_axis[i] + v * self.y_axis[i] for i in range(3))

    def direction(self, du, dv):
        return tuple(du * self.x_axis[i] + dv * self.y_axis[i] for i in range(3))

    def local(self, point):
        delta = tuple(point[i] - self.origin[i] for i in range(3))
        return (sum(delta[i] * self.x_axis[i] for i in range(3)),
                sum(delta[i] * self.y_axis[i] for i in range(3)))


def _dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


def mate(face_a, face_b, *, gap_tolerance: float = 0.05) -> Seam:
    """Check two faces touch, and describe the area they share.

    Raises a :class:`CadError` that says what is wrong -- curved, tilted,
    apart, or not overlapping -- rather than producing a joint in mid-air.
    """
    from .detect import analyse_plane

    info_a, info_b = analyse_plane(face_a), analyse_plane(face_b)
    if info_a is None or info_b is None:
        raise CadError(
            "Clips join flat faces; one of the selected faces is curved.",
            suggestion="Pick the flat faces where the two parts meet.",
        )
    alignment = _dot(info_a.normal, info_b.normal)
    if alignment > -0.9994:   # about 2°
        angle = math.degrees(math.acos(max(-1.0, min(1.0, -alignment))))
        raise CadError(
            f"The two faces are not facing each other ({angle:.1f}° apart).",
            suggestion="Pick the faces where the parts meet, or use Bring together first.",
        )
    gap = _dot(tuple(info_a.center[i] - info_b.center[i] for i in range(3)), info_b.normal)
    if abs(gap) > gap_tolerance:
        raise CadError(
            f"The two faces are {abs(gap):.2f} mm apart.",
            suggestion="Use Bring together to seat one part on the other first.",
        )
    profile_b, (origin, x_axis, y_axis) = _face_polygon(face_b)
    profile_a, local_a = _face_polygon(face_a)
    seam = Seam(region=None, origin=origin, x_axis=x_axis, y_axis=y_axis,
                normal_a=tuple(info_a.normal), frame_b=planar_frame(face_b))
    projected_a = _reproject(profile_a, local_a, seam)
    region = profile_b.buffer(0).intersection(projected_a.buffer(0))
    if region.is_empty or region.area < 1.0e-6:
        raise CadError(
            "The two faces do not overlap.",
            suggestion="Pick faces that lie against each other.",
        )
    return Seam(region=region, origin=origin, x_axis=x_axis, y_axis=y_axis,
                normal_a=tuple(info_a.normal), frame_b=seam.frame_b)


def _reproject(polygon, local, seam):
    from shapely.geometry import MultiPolygon, Polygon

    origin, x_axis, y_axis = local

    def convert(coords):
        out = []
        for u, v in coords:
            world = tuple(origin[i] + u * x_axis[i] + v * y_axis[i] for i in range(3))
            out.append(seam.local(world))
        return out

    def one(poly):
        return Polygon(convert(poly.exterior.coords), [convert(r.coords) for r in poly.interiors])

    if isinstance(polygon, MultiPolygon):
        return MultiPolygon([one(p) for p in polygon.geoms])
    return one(polygon)


@dataclass(frozen=True)
class Placement:
    u: float
    v: float
    angle: float   # direction of the joint's X axis in the seam frame, radians
    ok: bool = True
    reason: str = ""


def _rectangle(u, v, angle, width, across):
    from shapely.geometry import Polygon

    c, s = math.cos(angle), math.sin(angle)
    corners = []
    for x, y in ((-width / 2, -across / 2), (width / 2, -across / 2),
                 (width / 2, across / 2), (-width / 2, across / 2)):
        corners.append((u + x * c - y * s, v + x * s + y * c))
    return Polygon(corners)


def _polygons(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    return [g for g in getattr(geometry, "geoms", []) if g.geom_type == "Polygon" and not g.is_empty]


def narrowest_width(region) -> float:
    """How wide the shared face is, in mm, for explaining a failure.

    A rim (a face with a hole in it) is as wide as its wall: twice its area
    over its perimeter. A solid face is as wide as the largest circle in it.
    """
    try:
        polygons = _polygons(region)
        if any(p.interiors for p in polygons):
            return min(2.0 * p.area / p.length for p in polygons)
        from shapely import maximum_inscribed_circle

        return min(2.0 * maximum_inscribed_circle(p).length for p in polygons)
    except Exception:  # noqa: BLE001 - only used to explain a failure
        return 0.0


def plan_clips(seam: Seam, spec: JointSpec, *, count: int = 0, spacing: float = 80.0,
               edge_offset: float = 1.0, shift: float = 0.0, anchors=(),
               rotation: float = 0.0) -> tuple[list[Placement], list[str]]:
    """Where the joints go on a seam, and anything the user should know.

    Automatic placement strings joints along the seam: the shared face is
    shrunk by the edge offset and half the joint's keep-out width, and joints
    are spaced evenly along what is left -- the centre line of a thin rim, or
    a path just inside the edge of a broad face. Each joint's long axis
    follows that path, so a clip on a 3 mm wall lies along the wall rather
    than across it. Holes, text and anything else interrupting the face are
    already holes in the region, so joints keep clear of them too.

    *anchors* switches to manual placement: joints at those points (in the
    target face's frame), each turned to the nearest seam direction.
    """
    width, across = spec.keep_out
    allowed = seam.region.buffer(-edge_offset, join_style=2) if edge_offset > 0 else seam.region
    # A hair inside the limit, so a joint centred on the path is not rejected
    # by rounding for touching the edge it was placed against.
    core = seam.region.buffer(-(edge_offset + across / 2.0 + 0.02), join_style=2)
    rings = [p.exterior for p in _polygons(core) if p.exterior.length > 1.0e-6]
    notes: list[str] = []
    if not rings and not anchors:
        narrow = narrowest_width(seam.region)
        need = across + 2.0 * edge_offset
        raise CadError(
            (f"The shared face is only {narrow:.1f} mm wide; this joint needs {need:.1f} mm "
             "including walls and edge offset.") if narrow else
            f"This joint needs {need:.1f} mm of shared face including walls and edge offset.",
            suggestion="Choose a smaller size, the Tab & slot style, a thinner minimum wall "
                       "or a smaller edge offset.",
        )
    placed: list = []

    def fits(u, v, angle):
        rect = _rectangle(u, v, angle, width, across)
        if not allowed.covers(rect):
            return False
        return all(not rect.intersects(other) for other in placed)

    def tangent_at(ring, distance):
        total = ring.length
        a = ring.interpolate((distance - 0.5) % total)
        b = ring.interpolate((distance + 0.5) % total)
        return math.atan2(b.y - a.y, b.x - a.x)

    placements: list[Placement] = []
    if anchors:
        from shapely.geometry import Point

        for index, anchor in enumerate(anchors):
            world = seam.frame_b.point(float(anchor[0]), float(anchor[1]))
            u, v = seam.local(world)
            angle = math.radians(rotation)
            if rings:
                ring = min(rings, key=lambda r: r.distance(Point(u, v)))
                angle += tangent_at(ring, ring.project(Point(u, v)))
            ok = fits(u, v, angle)
            placements.append(Placement(u, v, angle, ok, "" if ok else
                f"Clip {index + 1} does not fit there: it needs {across:.1f} mm of face "
                "clear of edges, holes and other clips."))
            if ok:
                placed.append(_rectangle(u, v, angle, width, across))
        return placements, notes

    total = sum(r.length for r in rings)
    step_min = width + 0.5
    if count <= 0:
        count = max(len(rings), int(round(total / max(spacing, step_min))))
        if count == 1 and total > 6.0 * width:
            count = 2   # two joints stop the parts turning about one
    count = max(1, min(count, MAX_CONNECTORS))
    # Share the joints between separate islands in proportion to their length.
    shares = [max(1, int(round(count * r.length / total))) for r in rings]
    while sum(shares) > count and max(shares) > 1:
        shares[shares.index(max(shares))] -= 1
    requested = sum(shares)
    for ring, share in zip(rings, shares):
        pitch = ring.length / share
        for index in range(share):
            target = (shift + (index + 0.5) * pitch) % ring.length
            # Slide off corners and obstacles, nearest first, up to half a pitch.
            found = None
            reach = min(pitch / 2.0, ring.length / 2.0)
            steps = int(reach / 0.5) + 1
            for k in range(steps):
                for sign in ((1,) if k == 0 else (1, -1)):
                    distance = (target + sign * k * 0.5) % ring.length
                    point = ring.interpolate(distance)
                    angle = tangent_at(ring, distance) + math.radians(rotation)
                    if fits(point.x, point.y, angle):
                        found = Placement(point.x, point.y, angle)
                        break
                if found:
                    break
            if found is not None:
                placements.append(found)
                placed.append(_rectangle(found.u, found.v, found.angle, width, across))
    if not placements:
        raise CadError(
            "No joint fits on this seam once edges, holes and walls are kept clear.",
            suggestion="Choose a smaller size or reduce the edge offset.",
        )
    if len(placements) < requested:
        notes.append(f"Placed {len(placements)} of {requested} joints; "
                     "the rest had no room clear of corners, holes and each other.")
    return placements, notes


def joint_transform(seam: Seam, placement: Placement, male_normal):
    """Canonical joint frame -> world, for one placement."""
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf

    x_dir = seam.direction(math.cos(placement.angle), math.sin(placement.angle))
    z_dir = tuple(male_normal)
    # Re-orthogonalise X against Z; the seam frame is exact, but be safe.
    dot = _dot(x_dir, z_dir)
    x_dir = tuple(x_dir[i] - dot * z_dir[i] for i in range(3))
    source = gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1), gp_Dir(1, 0, 0))
    target = gp_Ax3(gp_Pnt(*seam.world(placement.u, placement.v)), gp_Dir(*z_dir), gp_Dir(*x_dir))
    result = gp_Trsf()
    result.SetDisplacement(source, target)
    return result


def execute_joint(feature: Feature, ctx: BuildContext) -> dict[str, object]:
    """Build a version-2 joint: one logical feature writing into both parts."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse

    first_name = str(feature.inputs.get("body_a", ""))
    second_name = str(feature.inputs.get("body_b", ""))
    if not first_name or not second_name or first_name == second_name:
        raise CadError("Joining needs faces on two different parts.")
    body_a = ctx.shape(feature, "body_a")
    body_b = ctx.shape(feature, "body_b")
    face_a = ctx.resolve(feature, "face_a")
    face_b = ctx.resolve(feature, "face_b")
    spec = resolve_joint_spec(feature, ctx)
    if feature.inputs.get("seat"):
        # "Bring together": set A down on B first, keeping the side-to-side
        # offset it had when the joint was made, so only the gap closes.
        placement = solve(face_a, face_b, operation="stack",
                          x=ctx.value(feature, "seat_x", 0.0),
                          y=ctx.value(feature, "seat_y", 0.0))
        body_a = transformed(body_a, placement.transform)
        face_a = transformed(face_a, placement.transform)
    seam = mate(face_a, face_b)
    placements, notes = plan_clips(
        seam, spec,
        count=int(feature.inputs.get("count", 0) or 0),
        spacing=ctx.value(feature, "spacing", DEFAULTS["spacing"]),
        edge_offset=ctx.value(feature, "edge_offset", DEFAULTS["edge_offset"]),
        shift=ctx.value(feature, "shift", 0.0),
        anchors=feature.inputs.get("anchors") or () if feature.inputs.get("placement") == "manual" else (),
        rotation=ctx.value(feature, "rotation", 0.0),
    )
    male_on_a = str(feature.inputs.get("male", "a")) != "b"
    normal_a = seam.normal_a
    male_normal = normal_a if male_on_a else tuple(-c for c in normal_a)
    feature.annotations = {
        "style": spec.style,
        "footprint": list(spec.footprint),
        "keep_out": list(spec.keep_out),
        "male": "a" if male_on_a else "b",
        "normal": list(male_normal),
        "clips": [{"at": list(seam.world(p.u, p.v)),
                   "x": list(seam.direction(math.cos(p.angle), math.sin(p.angle))),
                   "ok": p.ok, "reason": p.reason} for p in placements],
    }
    for warning in notes:
        ctx.warn(warning)
    failures = [p for p in placements if not p.ok]
    if failures:
        raise CadError(failures[0].reason, suggestion="Move it, or choose a smaller size.")

    male, female = (body_a, body_b) if male_on_a else (body_b, body_a)
    connector_names = [str(n) for n in feature.inputs.get("connector_names", [])]
    if spec.style == "connector" and len(connector_names) < len(placements):
        raise CadError("The joint is missing names for its printed connectors.",
                       suggestion="Open the joint and apply it again.")
    cutter = female_cutter(spec)
    check = wall_check_cutter(spec)
    part = male_half(spec) if spec.style != "connector" else None
    connector = make_connector(spec.clip) if spec.style == "connector" else None
    if connector is not None:
        # The male side is a socket as well; mirror the pocket through the seam.
        from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir, gp_Trsf
        mirror = gp_Trsf()
        mirror.SetMirror(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)))
        male_cutter = transformed(cutter, mirror)
        male_check = transformed(check, mirror)
    connectors = {}
    with guard("join parts"):
        for index, placement in enumerate(placements):
            transform = joint_transform(seam, placement, male_normal)
            label = f"Clip {index + 1}"
            if not _contained(female, transformed(check, transform)):
                raise CadError(
                    f"{label} would break through the "
                    f"{'second' if male_on_a else 'first'} part or leave a wall under "
                    f"{spec.min_wall:.1f} mm.",
                    suggestion="Choose a smaller size, reduce the minimum wall, or move the joint.",
                )
            female = built_shape(boolean_builder(BRepAlgoAPI_Cut, female, transformed(cutter, transform)), "joint pocket")
            if connector is not None:
                if not _contained(male, transformed(male_check, transform)):
                    raise CadError(
                        f"{label} would break through the "
                        f"{'first' if male_on_a else 'second'} part or leave a wall under "
                        f"{spec.min_wall:.1f} mm.",
                        suggestion="Choose a smaller size, reduce the minimum wall, or move the joint.",
                    )
                male = built_shape(boolean_builder(BRepAlgoAPI_Cut, male, transformed(male_cutter, transform)), "joint pocket")
                name = connector_names[index]
                if name not in feature.dropped:
                    connectors[name] = transformed(connector, transform)
            else:
                male = built_shape(boolean_builder(BRepAlgoAPI_Fuse, male, transformed(part, transform)), "joint tab")
    male, female = unify(male), unify(female)
    if not is_valid(male) or not is_valid(female):
        raise CadError("The joint did not leave valid printable parts.",
                       suggestion="Try a different size or move the joints away from other features.")
    result_a, result_b = (male, female) if male_on_a else (female, male)
    feature.outputs = [first_name, second_name] + list(connectors)
    kind = STYLES[spec.style].lower()
    feature.message = f"Joined with {len(placements)} {kind}{'s' if len(placements) != 1 else ''}."
    return feature.keep({first_name: result_a, second_name: result_b, **connectors})


def seat_offsets(face_a, face_b) -> tuple[float, float]:
    """The in-plane offset of A's face from B's, as Stack measures it.

    Stored on a joint that brings two parts together, so seating them closes
    the gap without also sliding one part across the other.
    """
    from .detect import analyse_plane

    info_a = analyse_plane(face_a)
    frame = planar_frame(face_b)
    delta = tuple(info_a.center[i] - frame.center[i] for i in range(3))
    return _dot(delta, frame.x_axis), _dot(delta, frame.y_axis)


def facing(face_a, face_b) -> tuple[bool, float]:
    """(faces point at each other, gap between them in mm) -- for the panel."""
    from .detect import analyse_plane

    info_a, info_b = analyse_plane(face_a), analyse_plane(face_b)
    if info_a is None or info_b is None:
        return False, 0.0
    opposed = _dot(info_a.normal, info_b.normal) <= -0.9994
    gap = _dot(tuple(info_a.center[i] - info_b.center[i] for i in range(3)), info_b.normal)
    return opposed, gap


def find_seam(shape_a, shape_b, *, gap_tolerance: float = 0.05, face_limit: int = 5000):
    """The pair of planar faces where two parts touch, largest contact first.

    Two halves of a split print meet on faces that are hidden inside the
    assembly, so asking the user to click them is asking the impossible.
    Instead a click anywhere on each part is enough: faces are bucketed by
    their plane, so only faces lying in the same plane with opposite normals
    are ever compared. Returns ``(face_a, face_b)`` or None.
    """
    from .detect import analyse_plane
    from ..core.naming import sub_shapes

    faces_a, faces_b = sub_shapes(shape_a, "face"), sub_shapes(shape_b, "face")
    if len(faces_a) + len(faces_b) > face_limit:
        return None

    def planes(faces):
        found = []
        for face in faces:
            info = analyse_plane(face)
            if info is not None:
                offset = _dot(info.normal, info.center)
                found.append((face, info, offset))
        return found

    buckets: dict = {}
    for face, info, offset in planes(faces_b):
        key = tuple(round(c, 2) for c in info.normal)
        buckets.setdefault(key, []).append((face, info, offset))
    best = None
    for face, info, offset in planes(faces_a):
        key = tuple(round(-c, 2) for c in info.normal)
        for other, other_info, other_offset in buckets.get(key, ()):
            if abs(offset + other_offset) > gap_tolerance:
                continue
            try:
                seam = mate(face, other, gap_tolerance=gap_tolerance)
            except CadError:
                continue
            area = seam.region.area
            if best is None or area > best[0]:
                best = (area, face, other)
    return None if best is None else (best[1], best[2])
