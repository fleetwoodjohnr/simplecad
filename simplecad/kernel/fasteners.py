"""Create Matching Part: bolts, nuts and threaded holes from an existing thread.

The point is that the user never specifies the thread twice. Select a threaded
hole and ask for a bolt, and the bolt arrives at the same designation with the
clearance on the correct side -- because SimpleCAD reads the thread that is
already there rather than asking again.

Head and nut dimensions come from ``data/fasteners.json`` (ISO 4014 and ISO
4032), so adding a size is a data edit.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from functools import lru_cache

from ..core.errors import CadError, guard
from .occ import built_shape, make_transform, transformed, unify
from .thread_specs import DATA_DIR, ThreadSize, by_designation


@lru_cache(maxsize=1)
def fastener_table() -> dict:
    with open(os.path.join(DATA_DIR, "fasteners.json")) as handle:
        return json.load(handle)


def head_for(size: ThreadSize) -> dict:
    """Head and nut dimensions for a thread size.

    Sizes outside the table fall back to the usual proportions rather than
    refusing: a hex head is about 1.6 diameters across the flats and 0.7
    diameters tall, which is right to within a millimetre across the range.
    """
    table = fastener_table()["hex"]
    key = size.designation.split("x")[0].strip()
    if key in table:
        return table[key]
    diameter = size.diameter
    return {
        "across_flats": round(diameter * 1.6, 2),
        "head_height": round(diameter * 0.7, 2),
        "nut_height": round(diameter * 0.87, 2),
        "socket_head_diameter": round(diameter * 1.5, 2),
        "socket_head_height": diameter,
    }


@dataclass(frozen=True)
class Pairing:
    """The complement of an existing thread."""

    size: ThreadSize
    internal: bool          # what the *new* part needs
    clearance: str | float

    def describe(self) -> str:
        kind = "internal" if self.internal else "external"
        return f"{self.size.designation} {kind} thread"


def complement(size: ThreadSize, existing_internal: bool,
               clearance: str | float = "normal") -> Pairing:
    """What the mating part needs, given a thread that already exists.

    Same designation -- male and female of a pair always share one. What flips
    is which side is cut and which side carries the printable clearance, and
    the clearance always goes on the internal side so the external part stays
    at nominal.
    """
    return Pairing(size=size, internal=not existing_internal, clearance=clearance)


# ----------------------------------------------------------------------
# Geometry
# ----------------------------------------------------------------------
def _hex_prism(across_flats: float, height: float):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Pnt, gp_Vec

    radius = across_flats / (2.0 * math.cos(math.pi / 6))
    polygon = BRepBuilderAPI_MakePolygon()
    for index in range(6):
        angle = 2.0 * math.pi * index / 6.0
        polygon.Add(gp_Pnt(radius * math.cos(angle), radius * math.sin(angle), 0.0))
    polygon.Close()
    face = BRepBuilderAPI_MakeFace(polygon.Wire()).Face()
    return built_shape(BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, height)), "hex")


def make_bolt(size: ThreadSize, length: float, thread_length: float | None = None,
              head: str = "hex", form: str = "printed",
              left_hand: bool = False) -> object:
    """A bolt: head, shank, and a modelled thread over the last *thread_length*."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

    from .threads import apply_thread, require_modelled

    if length <= 0:
        raise CadError("A bolt needs a length greater than zero.")
    dimensions = head_for(size)
    thread_length = min(thread_length or length, length)

    with guard("bolt"):
        if head == "socket":
            crown = BRepPrimAPI_MakeCylinder(
                dimensions["socket_head_diameter"] / 2.0,
                dimensions["socket_head_height"],
            ).Shape()
            head_height = dimensions["socket_head_height"]
        else:
            head_height = dimensions["head_height"]
            crown = _hex_prism(dimensions["across_flats"], head_height)

        shank = transformed(
            BRepPrimAPI_MakeCylinder(size.diameter / 2.0, length).Shape(),
            make_transform(translate=(0.0, 0.0, head_height)),
        )
        body = built_shape(BRepAlgoAPI_Fuse(crown, shank), "bolt")

    # Thread the free end of the shank.
    start = head_height + length - thread_length
    outcome = require_modelled(apply_thread(
        body, size=size, origin=(0.0, 0.0, start), direction=(0.0, 0.0, 1.0),
        length=thread_length, internal=False, feature_diameter=size.diameter,
        form=form, left_hand=left_hand,
    ))
    return unify(outcome.shape)


def make_nut(size: ThreadSize, clearance: str | float = "normal",
             height: float | None = None, form: str = "printed",
             left_hand: bool = False) -> object:
    """A hex nut with a matching internal thread."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

    from .thread_specs import clearance_for
    from .threads import apply_thread, require_modelled

    dimensions = head_for(size)
    thickness = height or dimensions["nut_height"]
    bore = size.diameter + clearance_for(clearance)

    with guard("nut"):
        blank = _hex_prism(dimensions["across_flats"], thickness)
        drill = transformed(
            BRepPrimAPI_MakeCylinder(bore / 2.0, thickness + 4.0).Shape(),
            make_transform(translate=(0.0, 0.0, -2.0)),
        )
        drilled = built_shape(BRepAlgoAPI_Cut(blank, drill), "nut")

    outcome = require_modelled(apply_thread(
        drilled, size=size, origin=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0),
        length=thickness, internal=True, clearance=clearance,
        feature_diameter=bore, form=form, left_hand=left_hand,
    ))
    return unify(outcome.shape)


def make_threaded_hole_tool(size: ThreadSize, depth: float,
                            clearance: str | float = "normal") -> object:
    """The solid to cut from a body to leave a threaded hole.

    Returned as a tool rather than applied directly, so the caller can place it
    wherever the user clicked.
    """
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

    from .thread_specs import clearance_for

    bore = size.diameter + clearance_for(clearance)
    with guard("threaded hole"):
        return BRepPrimAPI_MakeCylinder(bore / 2.0, depth).Shape()


# ----------------------------------------------------------------------
# Features
# ----------------------------------------------------------------------
def _resolve_size(feature, ctx, key: str = "designation") -> ThreadSize:
    designation = feature.inputs.get(key)
    size = by_designation(str(designation)) if designation else None
    if size is None:
        raise CadError(
            "This feature does not know which thread to match.",
            suggestion="Select a threaded feature first, or pick a size.",
        )
    return size


def register_features() -> None:
    """Imported for the side effect of registering the fastener features."""


from ..core.document import BodyRef, BuildContext, Feature, register  # noqa: E402


@register("matching_bolt")
class MatchingBoltFeature(Feature):
    """A bolt that fits an existing threaded hole."""

    label = "Bolt"

    def execute(self, ctx: BuildContext) -> dict:
        size = _resolve_size(self, ctx)
        length = ctx.value(self, "length", size.diameter * 3.0)
        thread_length = ctx.value(self, "thread_length", 0.0) or None
        self.inputs["thread_modelled"] = False
        self.inputs["thread_internal"] = False
        self.inputs.setdefault("form", "printed")
        self.inputs.setdefault("left_hand", False)
        shape = make_bolt(
            size, length, thread_length, str(self.inputs.get("head", "hex")),
            str(self.inputs.get("form", "printed")),
            bool(self.inputs.get("left_hand", False)),
        )
        shape = _placed(self, ctx, shape)
        self.inputs["thread_modelled"] = True
        name = self.outputs[0] if self.outputs else self.name
        self.outputs = [name]
        self.message = f"{size.designation} x {length:g} mm bolt"
        return {name: shape}


@register("matching_nut")
class MatchingNutFeature(Feature):
    """A nut that fits an existing threaded shaft."""

    label = "Nut"

    def execute(self, ctx: BuildContext) -> dict:
        size = _resolve_size(self, ctx)
        height = ctx.value(self, "height", 0.0) or None
        self.inputs["thread_modelled"] = False
        self.inputs["thread_internal"] = True
        self.inputs.setdefault("form", "printed")
        self.inputs.setdefault("left_hand", False)
        shape = make_nut(
            size, str(self.inputs.get("clearance", "normal")), height,
            str(self.inputs.get("form", "printed")),
            bool(self.inputs.get("left_hand", False)),
        )
        shape = _placed(self, ctx, shape)
        self.inputs["thread_modelled"] = True
        name = self.outputs[0] if self.outputs else self.name
        self.outputs = [name]
        self.message = f"{size.designation} nut"
        return {name: shape}


@register("matching_thread")
class ApplyMatchingThreadFeature(Feature):
    """Put the complement of an existing thread onto another part's face."""

    label = "Matching Thread"

    def execute(self, ctx: BuildContext) -> dict:
        from .detect import analyse_cylinder
        from .threads import apply_thread, require_modelled

        name = str(self.inputs["body"])
        body = ctx.shape(self, "body")
        face = ctx.resolve(self, "face")
        info = analyse_cylinder(face)
        if info is None:
            raise CadError(
                "A matching thread needs a round face.",
                suggestion="Select the shaft or hole to thread.",
            )

        self.inputs["thread_modelled"] = False
        self.inputs["thread_internal"] = bool(info.internal)
        self.inputs.setdefault("form", "printed")
        self.inputs.setdefault("left_hand", False)

        size = _resolve_size(self, ctx)
        clearance = str(self.inputs.get("clearance", "normal"))
        length = ctx.value(self, "length", 0.0) or info.length
        outcome = require_modelled(apply_thread(
            body, size=size, origin=info.origin, direction=info.direction,
            length=min(length, info.length), internal=info.internal,
            clearance=clearance, feature_diameter=info.diameter,
            form=str(self.inputs.get("form", "printed")),
            left_hand=bool(self.inputs.get("left_hand", False)),
        ))
        if outcome.message:
            ctx.warn(outcome.message)
        self.message = (
            f"{size.designation} {'internal' if info.internal else 'external'} "
            f"thread to match"
        )
        self.inputs["thread_modelled"] = True
        self.outputs = [name]
        return {name: outcome.shape}


def _placed(feature: Feature, ctx: BuildContext, shape):
    """Apply the optional stable placement shared by generated fasteners."""
    position = tuple(ctx.value(feature, key, 0.0) for key in ("x", "y", "z"))
    if all(abs(value) <= 1e-12 for value in position):
        return shape
    return transformed(shape, make_transform(translate=position))
