"""Unit handling.

SimpleCAD works internally in millimetres and degrees. Everything the user types
is parsed into those internal units at the edge, so no other module ever has to
ask "what unit is this number in?".
"""

from __future__ import annotations

import re
from enum import Enum


class Dimension(str, Enum):
    """The physical dimension a quantity measures."""

    LENGTH = "length"
    ANGLE = "angle"
    SCALAR = "scalar"


#: Conversion factors *into* the internal unit for each dimension.
LENGTH_UNITS: dict[str, float] = {
    "mm": 1.0,
    "millimeter": 1.0,
    "millimetre": 1.0,
    "cm": 10.0,
    "centimeter": 10.0,
    "centimetre": 10.0,
    "m": 1000.0,
    "meter": 1000.0,
    "metre": 1000.0,
    "in": 25.4,
    "inch": 25.4,
    '"': 25.4,
    "ft": 304.8,
    "foot": 304.8,
    "thou": 0.0254,
    "mil": 0.0254,
}

ANGLE_UNITS: dict[str, float] = {
    "deg": 1.0,
    "degree": 1.0,
    "degrees": 1.0,
    "°": 1.0,
    "rad": 180.0 / 3.141592653589793,
    "radian": 180.0 / 3.141592653589793,
    "radians": 180.0 / 3.141592653589793,
}

#: The unit each dimension is stored in internally.
INTERNAL_UNIT = {
    Dimension.LENGTH: "mm",
    Dimension.ANGLE: "deg",
    Dimension.SCALAR: "",
}

_UNIT_SUFFIX = re.compile(
    r"^\s*(?P<value>[-+0-9.eE]+)\s*(?P<unit>[a-zA-Z°\"]*)\s*$"
)


def unit_factor(unit: str, dimension: Dimension) -> float:
    """Return the factor converting *unit* into the internal unit.

    Raises ``KeyError`` if the unit is not valid for the dimension.
    """
    if not unit:
        return 1.0
    table = {
        Dimension.LENGTH: LENGTH_UNITS,
        Dimension.ANGLE: ANGLE_UNITS,
        Dimension.SCALAR: {},
    }[dimension]
    return table[unit.lower() if unit != "°" else unit]


def known_unit(unit: str) -> Dimension | None:
    """Return the dimension *unit* belongs to, or ``None`` if unrecognised."""
    if not unit:
        return None
    lowered = unit.lower()
    if lowered in LENGTH_UNITS or unit in LENGTH_UNITS:
        return Dimension.LENGTH
    if lowered in ANGLE_UNITS or unit in ANGLE_UNITS:
        return Dimension.ANGLE
    return None


def parse_quantity(text: str, dimension: Dimension = Dimension.LENGTH) -> float:
    """Parse a bare literal such as ``"12.5"``, ``"12.5 mm"`` or ``'0.5"'``.

    This handles only literals; anything with operators or names goes through
    :mod:`simplecad.core.params`. Returns the value in internal units.
    """
    match = _UNIT_SUFFIX.match(text)
    if not match:
        raise ValueError(f"{text!r} is not a number")
    value = float(match.group("value"))
    unit = match.group("unit")
    if not unit:
        return value
    try:
        return value * unit_factor(unit, dimension)
    except KeyError:
        raise ValueError(
            f"{unit!r} is not a valid {dimension.value} unit"
        ) from None


def format_quantity(
    value: float, dimension: Dimension = Dimension.LENGTH, decimals: int = 2
) -> str:
    """Render an internal value for display, trimming trailing zeros."""
    text = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    if text in ("", "-"):
        text = "0"
    suffix = INTERNAL_UNIT[dimension]
    if dimension is Dimension.ANGLE:
        return f"{text}°"
    return f"{text} {suffix}".strip()
