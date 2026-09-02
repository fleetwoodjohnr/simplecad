"""Thread standards, size recommendation, and printable clearance.

The point of this module is that a user should never have to look up a table.
Select a cylindrical face, and SimpleCAD works out whether it is a shaft or a
hole, measures it, and offers the standard sizes that fit -- a roughly 6 mm hole
suggests **M6 x 1.0**.

Sizes live in ``data/threads/*.json`` and clearances in ``data/fits.json``, as
data rather than code, so adding a standard or retuning a printer profile never
means editing Python.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from functools import lru_cache

DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
)
#: Loaded in this order. ``printed`` leads because SimpleCAD makes parts for
#: a printer: an ISO tooth at a small pitch is a support-hungry sliver in
#: plastic, so the printable coarse series is what should be offered first.
STANDARD_FILES = ("printed", "iso_metric", "unc", "unf", "bsp", "npt")


@dataclass(frozen=True)
class ThreadSize:
    """One row of a thread table."""

    designation: str
    diameter: float          # nominal major diameter, mm
    pitch: float             # mm per turn
    standard: str            # human label, e.g. "ISO Metric"
    code: str                # machine code, e.g. "iso_metric"
    angle: float = 60.0      # thread profile included angle, degrees
    series: str = ""
    taper: float = 0.0       # NPT and friends
    form: str = "iso"        # tooth shape: "printed" or "iso"

    @property
    def tooth_depth(self) -> float:
        """Radial root-to-crest depth of the tooth actually built.

        Form-aware, unlike the ISO properties below: a printed tooth is a
        45-degree trapezoid and its depth follows from the pitch and the flats,
        not from the sharp-triangle height an ISO thread is truncated out of.
        """
        from .threads import thread_form

        return thread_form(self.pitch, self.angle, self.form).depth

    # -- derived geometry (ISO 68-1 style, valid for 55 and 60 degree forms) --
    @property
    def height(self) -> float:
        """H, the height of the sharp (untruncated) profile triangle."""
        return self.pitch / (2.0 * math.tan(math.radians(self.angle / 2.0)))

    @property
    def external_minor(self) -> float:
        """d3: root diameter of a bolt."""
        return self.diameter - 2.0 * (17.0 / 24.0) * self.height

    @property
    def internal_minor(self) -> float:
        """D1: the bore a nut is cut into -- also the tap drill size."""
        return self.diameter - 2.0 * (5.0 / 8.0) * self.height

    @property
    def pitch_diameter(self) -> float:
        """D2: where tooth and gap are equal."""
        return self.diameter - 2.0 * (3.0 / 8.0) * self.height

    @property
    def tap_drill(self) -> float:
        return self.internal_minor

    def label(self) -> str:
        return self.designation

    def describe(self) -> str:
        return (
            f"{self.designation}  ·  {self.pitch:.2f} mm pitch  ·  "
            f"tap drill {self.tap_drill:.1f} mm"
        )

    def to_dict(self) -> dict:
        return {
            "designation": self.designation,
            "diameter": self.diameter,
            "pitch": self.pitch,
            "standard": self.standard,
            "code": self.code,
            "angle": self.angle,
            "series": self.series,
            "taper": self.taper,
            "form": self.form,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ThreadSize":
        return cls(
            designation=data["designation"],
            diameter=float(data["diameter"]),
            pitch=float(data["pitch"]),
            standard=data.get("standard", ""),
            code=data.get("code", ""),
            angle=float(data.get("angle", 60.0)),
            series=data.get("series", ""),
            taper=float(data.get("taper", 0.0)),
            form=data.get("form", "iso"),
        )


@lru_cache(maxsize=1)
def load_sizes() -> tuple[ThreadSize, ...]:
    """Every size from every standard, in file order."""
    sizes: list[ThreadSize] = []
    for code in STANDARD_FILES:
        path = os.path.join(DATA_DIR, "threads", f"{code}.json")
        if not os.path.exists(path):
            continue
        with open(path) as handle:
            table = json.load(handle)
        for entry in table["sizes"]:
            sizes.append(
                ThreadSize(
                    designation=entry["designation"],
                    diameter=float(entry["diameter"]),
                    pitch=float(entry["pitch"]),
                    standard=table["standard"],
                    code=table["code"],
                    angle=float(table.get("angle", 60.0)),
                    series=entry.get("series", ""),
                    taper=float(table.get("taper", 0.0)),
                    form=table.get("form", "iso"),
                )
            )
    return tuple(sizes)


def standards() -> list[tuple[str, str]]:
    """``(code, label)`` for each standard, for a picker."""
    seen: dict[str, str] = {}
    for size in load_sizes():
        seen.setdefault(size.code, size.standard)
    return list(seen.items())


def by_designation(designation: str) -> ThreadSize | None:
    return next(
        (s for s in load_sizes() if s.designation.lower() == designation.lower()), None
    )


@dataclass(frozen=True)
class Recommendation:
    size: ThreadSize
    fit_error: float        # mm the nominal differs from what was measured
    exact: bool

    @property
    def designation(self) -> str:
        return self.size.designation

    def describe(self) -> str:
        if self.exact:
            return f"{self.size.designation} — exact match"
        return f"{self.size.designation} — {self.fit_error:+.2f} mm"


#: Standards ranked ahead of others when sizes are near-equally close. The
#: printable series leads because SimpleCAD makes parts to print, and metric
#: leads the rest because SimpleCAD works in millimetres; without this a 6.2 mm
#: hole would offer 1/4-20 above M6 purely on arithmetic.
PREFERENCE = ("printed", "iso_metric", "unc", "unf", "bsp", "npt")
#: How much closer a less-preferred standard must be to win, in mm.
PREFERENCE_BIAS = 0.25


def recommend(
    measured_diameter: float,
    *,
    internal: bool = False,
    code: str | None = None,
    limit: int = 5,
    tolerance: float = 2.0,
) -> list[Recommendation]:
    """Standard sizes that suit a measured cylindrical face.

    Both a hole and a shaft are matched against the **nominal (major)
    diameter**, which is what users expect: a 6 mm hole should offer M6, not the
    M7 you would get by treating the hole as a tap drill. For printed threads
    that is also the geometrically right answer -- the internal thread is cut
    into a bore of major diameter, with the crests protruding inward.
    """
    if measured_diameter <= 0:
        return []
    candidates = [s for s in load_sizes() if code is None or s.code == code]
    scored = []
    for size in candidates:
        error = size.diameter - measured_diameter
        if abs(error) > tolerance:
            continue
        scored.append(
            Recommendation(size, error, abs(error) < 1e-3)
        )
    def rank(recommendation: Recommendation) -> tuple:
        try:
            preference = PREFERENCE.index(recommendation.size.code)
        except ValueError:
            preference = len(PREFERENCE)
        return (
            abs(recommendation.fit_error) + preference * PREFERENCE_BIAS,
            preference,
            -recommendation.size.pitch,  # coarse pitches print best
        )

    scored.sort(key=rank)
    return scored[:limit]


def best_match(measured_diameter: float, **kwargs) -> ThreadSize | None:
    found = recommend(measured_diameter, **kwargs)
    return found[0].size if found else None


# ----------------------------------------------------------------------
# Clearance and fits
# ----------------------------------------------------------------------
@lru_cache(maxsize=1)
def _fits_table() -> dict:
    with open(os.path.join(DATA_DIR, "fits.json")) as handle:
        return json.load(handle)


def clearance_presets() -> dict[str, dict]:
    return _fits_table()["thread_clearance"]


def default_clearance_preset() -> str:
    return _fits_table().get("default_thread_clearance", "normal")


def clearance_for(preset: str | float) -> float:
    """Diametral clearance in mm for a preset name, or a raw number."""
    if isinstance(preset, (int, float)):
        value = float(preset)
        if value < 0.0:
            raise ValueError("Thread clearance cannot be negative.")
        return value
    table = clearance_presets()
    entry = table.get(preset)
    if entry is None:
        raise ValueError(f"Unknown thread-clearance preset: {preset}")
    return float(entry["clearance"])


def effective_clearance_for(preset: str | float) -> float:
    """Clearance after applying the active printer's measured Normal value."""
    if isinstance(preset, (int, float)):
        return clearance_for(preset)
    from .calibration import effective_thread_clearance

    return effective_thread_clearance(printer_profile()["id"], preset)


def fit_presets() -> dict[str, dict]:
    return _fits_table()["fits"]


def fit_clearance(preset: str | float) -> float:
    if isinstance(preset, (int, float)):
        return float(preset)
    table = fit_presets()
    entry = table.get(preset) or table[_fits_table().get("default_fit", "snug")]
    return float(entry["clearance"])


@lru_cache(maxsize=8)
def printer_profile(name: str = "elegoo_centauri_carbon_2") -> dict:
    path = os.path.join(DATA_DIR, "printers", f"{name}.json")
    with open(path) as handle:
        return json.load(handle)
