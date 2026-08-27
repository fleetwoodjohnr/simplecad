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


def effective_thread_clearance(printer_id: str, preset: str = "normal") -> float:
    from .thread_specs import clearance_for

    measured = load_measurements(printer_id)
    if measured and "measured_thread_clearance" in measured:
        return float(measured["measured_thread_clearance"])
    return clearance_for(preset)
