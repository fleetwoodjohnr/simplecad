"""Single-line text raised from or engraved into a selected planar face."""

from __future__ import annotations

import math

from ..core.document import BodyRef, BuildContext, Feature, register
from ..core.errors import CadError, guard
from ..core.params import dependencies
from ..core.units import Dimension
from .occ import (
    area, bounding_box, built_shape, is_valid, make_transform, transformed,
    unify, volume,
)

OVERLAP = 0.02
FAMILIES = frozenset({"sans-serif", "serif", "monospace"})
NUMERIC_INPUTS = frozenset({
    "text_height", "depth", "offset_x", "offset_y", "rotation",
})


def _unit(vector):
    length = math.sqrt(sum(value * value for value in vector))
    if length < 1e-12:
        raise CadError("The selected face has no usable orientation.")
    return tuple(value / length for value in vector)


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _frame(normal):
    """A stable, readable X axis for a face with outward *normal*."""
    normal = _unit(normal)
    if abs(normal[2]) > 0.9:
        x_axis = (1.0, 0.0, 0.0)
    else:
        world_up = (0.0, 0.0, 1.0)
        along = sum(world_up[i] * normal[i] for i in range(3))
        y_axis = _unit(tuple(world_up[i] - along * normal[i] for i in range(3)))
        x_axis = _unit(_cross(y_axis, normal))
    return normal, x_axis


def _face_transform(origin, normal, x_axis):
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf

    source = gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1), gp_Dir(1, 0, 0))
    target = gp_Ax3(gp_Pnt(*origin), gp_Dir(*normal), gp_Dir(*x_axis))
    transform = gp_Trsf()
    transform.SetDisplacement(source, target)
    return transform


def _overlapping_faces(shape) -> bool:
    """Whether a display glyph contains overlapping filled components."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from ..core.naming import sub_shapes

    faces = sub_shapes(shape, "face")
    for index, first in enumerate(faces):
        for second in faces[index + 1:]:
            first_box, second_box = bounding_box(first), bounding_box(second)
            if any(
                min(first_box[1][axis], second_box[1][axis])
                - max(first_box[0][axis], second_box[0][axis]) <= 1e-7
                for axis in (0, 1)
            ):
                continue
            try:
                common = built_shape(BRepAlgoAPI_Common(first, second), "font")
                if area(common) > 1e-7:
                    return True
            except BaseException:  # noqa: BLE001 - this candidate is simply unsuitable
                return True
    return False


def _rendered_text(text: str, family: str, height: float):
    from OCP.Font import Font_FontAspect_Regular
    from OCP.StdPrs import StdPrs_BRepFont
    from OCP.TCollection import TCollection_AsciiString

    from ..core.fonts import modeling_families

    for concrete in modeling_families(family):
        font = StdPrs_BRepFont()
        try:
            if not font.FindAndInit(
                TCollection_AsciiString(concrete), Font_FontAspect_Regular, height
            ):
                continue
            glyphs = []
            cursor = 0.0
            suitable = True
            for index, character in enumerate(text):
                if not character.isspace():
                    glyph = font.RenderGlyph(character)
                    if (
                        glyph is None or glyph.IsNull() or not is_valid(glyph)
                        or _overlapping_faces(glyph)
                    ):
                        suitable = False
                        break
                    glyphs.append((glyph, cursor))
                next_character = text[index + 1] if index + 1 < len(text) else "\0"
                cursor += font.AdvanceX(character, next_character)
            if suitable and glyphs:
                return concrete, glyphs
        except BaseException:  # noqa: BLE001 - try the next registered font
            continue
    raise CadError(
        f"The {family.replace('-', ' ')} fonts on this computer cannot build this text.",
        suggestion="Try another font family or remove unsupported characters.",
    )


def _solid_count(shape) -> int:
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    count = 0
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        count += 1
        explorer.Next()
    return count


@register("text")
class TextFeature(Feature):
    """Fuse or cut font outlines on a durable planar-face reference."""

    label = "Text"
    dimensions = {"rotation": Dimension.ANGLE}

    def parameter_names(self) -> set[str]:
        names: set[str] = set()
        for key in NUMERIC_INPUTS:
            value = self.inputs.get(key)
            if isinstance(value, str) and not isinstance(value, BodyRef):
                names |= dependencies(value)
        return names

    def execute(self, ctx: BuildContext) -> dict:
        from OCP.BRepAlgoAPI import (
            BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse,
        )
        from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
        from OCP.gp import gp_Vec

        from .detect import analyse_plane

        name = str(self.inputs.get("body") or "")
        if not name:
            raise CadError("This text has no body to work on.")
        body = ctx.shape(self, "body")
        face = ctx.resolve(self, "face")
        info = analyse_plane(face)
        if info is None:
            raise CadError(
                "Text can only be placed on a flat face.",
                suggestion="Select one planar face.",
            )

        text = str(self.inputs.get("text", ""))
        if "\n" in text or "\r" in text:
            raise CadError("Text must be a single line.")
        if not text.strip():
            raise CadError("Enter some text before creating it.")
        family = str(self.inputs.get("font_family", "sans-serif"))
        if family not in FAMILIES:
            raise CadError(f"'{family}' is not an available text family.")
        mode = str(self.inputs.get("mode", "raised"))
        if mode not in {"raised", "engraved"}:
            raise CadError(f"'{mode}' is not a text operation.")

        height = ctx.value(self, "text_height", 8.0)
        depth = ctx.value(self, "depth", 1.0)
        if height <= 0:
            raise CadError("Text height must be greater than zero.")
        if depth <= 0:
            raise CadError("Text depth must be greater than zero.")

        concrete, glyphs = _rendered_text(text, family, height)
        positioned = [
            transformed(glyph, make_transform(translate=(advance, 0.0, 0.0)))
            for glyph, advance in glyphs
        ]
        boxes = [bounding_box(glyph) for glyph in positioned]
        low_x = min(box[0][0] for box in boxes)
        high_x = max(box[1][0] for box in boxes)
        low_y = min(box[0][1] for box in boxes)
        high_y = max(box[1][1] for box in boxes)
        offset_x = ctx.value(self, "offset_x", 0.0)
        offset_y = ctx.value(self, "offset_y", 0.0)
        rotation = ctx.value(self, "rotation", 0.0)
        local = make_transform(
            translate=(
                offset_x - (low_x + high_x) / 2.0,
                offset_y - (low_y + high_y) / 2.0,
                -OVERLAP if mode == "raised" else OVERLAP,
            ),
            rotate_axis=(0, 0, 1),
            rotate_degrees=rotation,
        )
        normal, x_axis = _frame(info.normal)
        placement = _face_transform(info.center, normal, x_axis)
        profiles = [
            transformed(transformed(glyph, local), placement) for glyph in positioned
        ]

        original_volume = volume(body)
        original_solids = _solid_count(body)
        result = body
        direction_scale = (
            depth + OVERLAP if mode == "raised" else -(depth + OVERLAP)
        )
        direction = gp_Vec(*(component * direction_scale for component in normal))
        boolean = BRepAlgoAPI_Fuse if mode == "raised" else BRepAlgoAPI_Cut
        with guard("text"):
            if mode == "engraved":
                # A blind engraving needs material immediately beyond its
                # requested floor. Probe a thin slice straddling that floor;
                # if any of it falls outside the body, the text would become a
                # through-cut (or run off the selected face) instead.
                probe_half = min(OVERLAP, depth * 0.25)
                shift = -(depth + OVERLAP - probe_half)
                probe_move = make_transform(translate=tuple(
                    component * shift for component in normal
                ))
                probe_direction = gp_Vec(*(
                    -2.0 * probe_half * component for component in normal
                ))
                for profile in profiles:
                    probe_profile = transformed(profile, probe_move)
                    probe = built_shape(
                        BRepPrimAPI_MakePrism(probe_profile, probe_direction),
                        "text depth",
                    )
                    inside = built_shape(
                        BRepAlgoAPI_Common(body, probe), "text depth"
                    )
                    probe_volume = volume(probe)
                    if (
                        probe_volume <= 1e-10
                        or volume(inside) < probe_volume * 0.999
                    ):
                        raise CadError(
                            "The engraving is deeper than the material beneath the text.",
                            suggestion=(
                                "Reduce its depth or move it farther onto the selected face."
                            ),
                        )
            for profile in profiles:
                tool = built_shape(BRepPrimAPI_MakePrism(profile, direction), "text")
                result = built_shape(boolean(result, tool), "text")
            result = unify(result)

        if not is_valid(result) or _solid_count(result) == 0:
            raise CadError(
                "The text operation did not leave a valid solid.",
                suggestion="Reduce its size or depth, or move it farther onto the face.",
            )
        difference = volume(result) - original_volume
        if mode == "raised":
            if difference <= 1e-7:
                raise CadError(
                    "The raised text does not overlap the selected face.",
                    suggestion="Move it onto the face or reduce its size.",
                )
            if _solid_count(result) > original_solids:
                raise CadError(
                    "Some raised letters are detached from the body.",
                    suggestion="Move the text onto the face or reduce its size.",
                )
        elif difference >= -1e-7:
            raise CadError(
                "The engraving does not overlap the selected face.",
                suggestion="Move it onto the face or reduce its size.",
            )

        self.message = f"{mode.title()} {text!r} in {concrete}"
        self.outputs = [name]
        return {name: result}
