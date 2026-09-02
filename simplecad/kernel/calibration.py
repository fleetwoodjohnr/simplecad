"""Fit & Clearance calibration.

Printed clearances are a property of *your* machine, not of the CAD. So instead
of shipping numbers and hoping, SimpleCAD generates a test model: a row of pins
and matching holes at a spread of clearances, each labelled with its value. You
print it, find the first pair that fits the way you want, and tell SimpleCAD
that number. From then on Snug Fit means what it means on your printer.

The measured values are written to a printer profile in
``~/.local/share/simplecad/printers/``, which overrides the shipped defaults.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from ..core.errors import CadError, guard
from .occ import built_shape, make_transform, transformed, unify

#: Clearances the calibration model sweeps, in millimetres.
DEFAULT_SWEEP = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50)
#: Nominal pin diameter for the test.
PIN_DIAMETER = 10.0
#: How tall the pins and plate are.
PIN_HEIGHT = 8.0
PLATE_THICKNESS = 5.0
#: Spacing between test positions.
PITCH = 18.0

USER_PROFILE_DIR = os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
    "simplecad", "printers",
)


@dataclass(frozen=True)
class CalibrationModel:
    """The two printed pieces, plus what each position means."""

    pins: object
    plate: object
    clearances: tuple[float, ...]
    pin_diameter: float

    def describe(self) -> str:
        values = ", ".join(f"{c:.2f}" for c in self.clearances)
        return (
            f"{len(self.clearances)} test fits at {values} mm, "
            f"on a {self.pin_diameter:.0f} mm pin"
        )


@dataclass(frozen=True)
class ThreadCalibrationModel:
    """A P30 plug and female gauges spanning the supported fit presets."""

    pieces: tuple[tuple[str, object], ...]
    clearances: tuple[float, ...]
    designation: str

    def describe(self) -> str:
        values = ", ".join(f"{value:.2f}" for value in self.clearances)
        return f"{self.designation} thread gauges at {values} mm diametral clearance"


def _label(text: str, height: float, depth: float):
    """Raised text, for marking each test position.

    Falls back to nothing if the font cannot be turned into geometry -- a
    missing label is a cosmetic loss, and refusing to build the calibration
    model over it would not be.
    """
    try:
        from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
        from OCP.Font import Font_BRepTextBuilder, Font_FontMgr, Font_FontAspect_Regular
        from OCP.gp import gp_Ax3, gp_Pnt, gp_Vec
        from OCP.NCollection import NCollection_Utf8String
        from OCP.StdPrs import StdPrs_BRepFont

        font = StdPrs_BRepFont()
        if not font.Init("sans-serif", Font_FontAspect_Regular, height):
            return None
        builder = Font_BRepTextBuilder()
        shape = builder.Perform(font, NCollection_Utf8String(text))
        return shape if shape is not None and not shape.IsNull() else None
    except Exception:  # noqa: BLE001 - labels are optional
        return None


def build_model(
    clearances=DEFAULT_SWEEP,
    pin_diameter: float = PIN_DIAMETER,
) -> CalibrationModel:
    """Generate the pin strip and the matching plate.

    The pins are all at nominal; the holes carry the clearance. That mirrors how
    SimpleCAD applies clearance everywhere else, so what you measure here is
    directly what the fit presets mean.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    clearances = tuple(clearances)
    if not clearances:
        raise CadError("A calibration model needs at least one clearance.")
    width = PITCH * len(clearances)

    with guard("calibration"):
        # The pin strip: a base bar with a row of nominal pins.
        bar = BRepPrimAPI_MakeBox(
            gp_Pnt(0, 0, 0), width, PITCH, PLATE_THICKNESS
        ).Shape()
        pins = bar
        for index in range(len(clearances)):
            centre = gp_Pnt(PITCH * (index + 0.5), PITCH / 2.0, PLATE_THICKNESS)
            pin = BRepPrimAPI_MakeCylinder(
                gp_Ax2(centre, gp_Dir(0, 0, 1)), pin_diameter / 2.0, PIN_HEIGHT
            ).Shape()
            pins = built_shape(BRepAlgoAPI_Fuse(pins, pin), "calibration")

        # The plate: the same row of holes, each opened by its clearance.
        plate = BRepPrimAPI_MakeBox(
            gp_Pnt(0, 0, 0), width, PITCH, PLATE_THICKNESS
        ).Shape()
        for index, clearance in enumerate(clearances):
            centre = gp_Pnt(PITCH * (index + 0.5), PITCH / 2.0, -1.0)
            hole = BRepPrimAPI_MakeCylinder(
                gp_Ax2(centre, gp_Dir(0, 0, 1)),
                (pin_diameter + clearance) / 2.0,
                PLATE_THICKNESS + 2.0,
            ).Shape()
            plate = built_shape(BRepAlgoAPI_Cut(plate, hole), "calibration")

            engraving = _label(f"{clearance:.2f}", 4.0, 0.6)
            if engraving is not None:
                placed = transformed(
                    engraving,
                    make_transform(
                        translate=(PITCH * index + 2.0, 1.5, PLATE_THICKNESS)
                    ),
                )
                marked = BRepAlgoAPI_Fuse(plate, placed)
                marked.Build()
                if marked.IsDone():
                    plate = marked.Shape()

        return CalibrationModel(
            pins=unify(pins), plate=unify(plate),
            clearances=clearances, pin_diameter=pin_diameter,
        )


def build_thread_model(
    clearances=(0.40, 0.60, 0.80, 1.00), designation: str = "P30"
) -> ThreadCalibrationModel:
    """Build a short plug and separately named gauges for real thread tuning.

    The pieces are deliberately separate and arranged on the active printer's
    plate. Their object names carry the value into 3MF/slicer workflows even
    when a host has no modelling font available for physical labels.
    """
    from .fasteners import make_bolt, make_nut
    from .thread_specs import by_designation

    size = by_designation(designation)
    if size is None:
        raise CadError(f"Unknown calibration thread {designation}.")
    values = tuple(float(value) for value in clearances)
    if not values or any(value < 0.0 for value in values):
        raise CadError("Thread calibration needs non-negative clearance values.")

    thread_length = size.pitch * 2.0
    plug = make_bolt(
        size, thread_length, thread_length=thread_length, form="printed"
    )
    pieces: list[tuple[str, object]] = [
        (f"{designation} plug", transformed(
            plug, make_transform(translate=(30.0, 30.0, 0.0))
        ))
    ]
    spacing = max(55.0, size.diameter * 1.8)
    for index, value in enumerate(values):
        gauge = make_nut(
            size, clearance=value, height=thread_length, form="printed"
        )
        x = 30.0 + spacing * (1 + index % 2)
        y = 30.0 + spacing * (index // 2)
        pieces.append((
            f"{designation} gauge {value:.2f} mm",
            transformed(gauge, make_transform(translate=(x, y, 0.0))),
        ))
    return ThreadCalibrationModel(tuple(pieces), values, designation)


# ----------------------------------------------------------------------
# Saving what the print told you
# ----------------------------------------------------------------------
def user_profile_path(printer_id: str) -> str:
    os.makedirs(USER_PROFILE_DIR, exist_ok=True)
    return os.path.join(USER_PROFILE_DIR, f"{printer_id}.json")


def save_measurements(
    printer_id: str,
    base_profile: dict,
    fits: dict[str, float],
    thread_clearance: float | None = None,
) -> str:
    """Write measured clearances as a user profile that overrides the defaults."""
    profile = dict(base_profile)
    profile["id"] = printer_id
    profile["calibrated"] = True
    profile["measured_fits"] = {k: float(v) for k, v in fits.items()}
    if thread_clearance is not None:
        profile["measured_thread_clearance"] = float(thread_clearance)

    path = user_profile_path(printer_id)
    temporary = path + ".part"
    with open(temporary, "w") as handle:
        json.dump(profile, handle, indent=2)
        handle.write("\n")
    os.replace(temporary, path)
    return path


def load_measurements(printer_id: str) -> dict | None:
    """Measured values for a printer, if it has been calibrated."""
    path = os.path.join(USER_PROFILE_DIR, f"{printer_id}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def effective_fits(printer_id: str) -> dict[str, float]:
    """Fit clearances for this printer: measured where available, else shipped."""
    from .thread_specs import fit_presets

    values = {key: entry["clearance"] for key, entry in fit_presets().items()}
    measured = load_measurements(printer_id)
    if measured:
        values.update(measured.get("measured_fits", {}))
    return values


def effective_thread_clearance(
    printer_id: str, preset: str | float = "normal"
) -> float:
    """Resolved diametral allowance, preserving preset spacing after tuning.

    Calibration records the Normal thread fit. Tight/Loose/Very Loose move by
    the same delta, so choosing a different feel remains meaningful on a tuned
    machine instead of every preset collapsing onto one measured number.
    """
    from .thread_specs import clearance_for

    if isinstance(preset, (int, float)):
        return clearance_for(preset)
    shipped = clearance_for(preset)
    measured = load_measurements(printer_id)
    if measured and "measured_thread_clearance" in measured:
        normal = float(measured["measured_thread_clearance"])
        shipped_normal = clearance_for("normal")
        return max(0.10, normal + shipped - shipped_normal)
    return shipped
