"""Parametric placement of several independent bodies in one straight row."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.document import BuildContext, Feature, register
from ..core.errors import CadError
from .align import (
    _dot, _scale, _sub, planar_frame, rotation_between, support_face, translation,
)
from .detect import analyse_plane
from .occ import bounding_box, transformed


def _add(*vectors):
    return tuple(sum(vector[i] for vector in vectors) for i in range(3))


def projected_bounds(shape, axis) -> tuple[float, float]:
    """The exact axis-aligned bounds of *shape* along an arbitrary direction."""
    rotate = rotation_between(axis, (1.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    low, high = bounding_box(transformed(shape, rotate))
    return (low[0], high[0])


def projected_center(shape, axis) -> float:
    low, high = projected_bounds(shape, axis)
    return (low + high) / 2.0


@dataclass(frozen=True)
class Arrangement:
    shapes: dict[str, object]
    gap: float
    order: tuple[str, ...]
    warnings: tuple[str, ...] = ()


def _oriented_and_seated(name, shape, target_face, orient: bool):
    frame = planar_frame(target_face)
    warnings = []
    placed = shape
    if orient:
        support = support_face(shape, target_face)
        info = analyse_plane(support) if support is not None else None
        if info is None:
            warnings.append(
                f"{name} has no flat support face; its rotation was preserved."
            )
        else:
            rotate = rotation_between(
                info.normal, _scale(frame.normal, -1.0), info.center
            )
            placed = transformed(placed, rotate)
            support = transformed(support, rotate)
            support_info = analyse_plane(support)
            distance = _dot(_sub(frame.center, support_info.center), frame.normal)
            placed = transformed(placed, translation(_scale(frame.normal, distance)))
            return placed, warnings

    # Preserved orientations (and non-planar fallbacks) are seated by their
    # geometric support extent rather than by a guessed face.
    low, _high = projected_bounds(placed, frame.normal)
    plane = _dot(frame.center, frame.normal)
    placed = transformed(placed, translation(_scale(frame.normal, plane - low)))
    return placed, warnings


def arrange_shapes(
    items,
    *,
    mode: str = "equal_gaps",
    axis: str = "x",
    gap: float = 10.0,
    target_face=None,
    orient: bool = True,
    normal_offset: float = 0.0,
    group_offset=(0.0, 0.0, 0.0),
) -> Arrangement:
    """Return separately named bodies distributed and aligned in one row."""
    items = [(str(name), shape) for name, shape in items if shape is not None]
    minimum = 3 if mode == "equal_gaps" else 2
    if len(items) < minimum:
        raise CadError(
            "Equal spacing needs at least three objects."
            if mode == "equal_gaps" else "A fixed gap needs at least two objects."
        )
    if mode not in ("equal_gaps", "fixed_gap"):
        raise CadError("That arrangement mode is not available.")
    if mode == "fixed_gap" and gap < 0.0:
        raise CadError("The clear gap cannot be negative.")

    warnings = []
    if target_face is not None:
        frame = planar_frame(target_face)
        row_axis = frame.x_axis if axis == "x" else frame.y_axis
        cross_axes = (frame.y_axis,) if axis == "x" else (frame.x_axis,)
        prepared = []
        for name, shape in items:
            placed, notes = _oriented_and_seated(name, shape, target_face, orient)
            if normal_offset:
                placed = transformed(
                    placed, translation(_scale(frame.normal, normal_offset))
                )
            prepared.append((name, placed))
            warnings.extend(notes)
    else:
        axes = {
            "x": ((1.0, 0.0, 0.0), ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0))),
            "y": ((0.0, 1.0, 0.0), ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0))),
            "z": ((0.0, 0.0, 1.0), ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))),
        }
        row_axis, cross_axes = axes.get(axis, axes["x"])
        prepared = items

    ordered = sorted(prepared, key=lambda item: projected_center(item[1], row_axis))
    bounds = {name: projected_bounds(shape, row_axis) for name, shape in ordered}
    widths = {name: bounds[name][1] - bounds[name][0] for name, _shape in ordered}

    first_name = ordered[0][0]
    cursor = bounds[first_name][0]
    if mode == "equal_gaps":
        available = bounds[ordered[-1][0]][1] - cursor
        clear = (available - sum(widths.values())) / (len(ordered) - 1)
        if clear < -1e-6:
            raise CadError(
                "The outer objects are too close for clear equal gaps.",
                suggestion="Move them farther apart or use Fixed gap.",
            )
        gap = max(0.0, clear)

    desired_centers = {}
    for index, (name, _shape) in enumerate(ordered):
        desired_centers[name] = cursor + widths[name] / 2.0
        cursor += widths[name]
        if index + 1 < len(ordered):
            cursor += gap

    # A shared average centreline keeps the row in place while removing the
    # accidental stagger in every perpendicular direction.
    cross_targets = {
        cross: sum(projected_center(shape, cross) for _name, shape in ordered)
        / len(ordered)
        for cross in cross_axes
    }
    outputs = {}
    for name, shape in ordered:
        shift = _scale(
            row_axis, desired_centers[name] - projected_center(shape, row_axis)
        )
        for cross in cross_axes:
            shift = _add(
                shift,
                _scale(cross, cross_targets[cross] - projected_center(shape, cross)),
            )
        shift = _add(shift, tuple(float(v) for v in group_offset))
        outputs[name] = transformed(shape, translation(shift))
    return Arrangement(outputs, float(gap), tuple(name for name, _ in ordered), tuple(warnings))


@register("arrange")
class ArrangeFeature(Feature):
    """Arrange several bodies without fusing or renaming any of them."""

    label = "Arrange"

    def execute(self, ctx: BuildContext) -> dict:
        names = [str(value) for value in self.inputs.get("bodies", [])]
        items = [(name, ctx.named_shape(name)) for name in names]
        target_face = ctx.resolve(self, "target_face", required=False)
        if target_face is not None:
            frame = planar_frame(target_face)
            offset = _add(
                _scale(frame.x_axis, ctx.value(self, "move_x", 0.0)),
                _scale(frame.y_axis, ctx.value(self, "move_y", 0.0)),
                _scale(frame.normal, ctx.value(self, "move_normal", 0.0)),
            )
        else:
            offset = (
                ctx.value(self, "dx", 0.0),
                ctx.value(self, "dy", 0.0),
                ctx.value(self, "dz", 0.0),
            )
        result = arrange_shapes(
            items,
            mode=str(self.inputs.get("mode", "equal_gaps")),
            axis=str(self.inputs.get("axis", "x")),
            gap=ctx.value(self, "gap", 10.0),
            target_face=target_face,
            orient=bool(self.inputs.get("orient", True)),
            normal_offset=ctx.value(self, "normal_offset", 0.0),
            group_offset=offset,
        )
        for warning in result.warnings:
            ctx.warn(warning)
        self.outputs = list(names)
        self.message = f"Arranged {len(names)} objects with {result.gap:g} mm gaps."
        return {name: result.shapes[name] for name in names}


__all__ = ["ArrangeFeature", "Arrangement", "arrange_shapes", "projected_bounds"]
