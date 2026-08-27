"""Threads: standards, recommendation, geometry, and -- above all -- fit.

The last test here is the one that matters. SimpleCAD promises that a generated
thread pair assembles without the user calculating tolerances, so the pair is
checked by intersecting the bolt with the nut's metal and asserting they do not
share space.
"""

from __future__ import annotations

import math

import pytest

from simplecad.kernel.detect import analyse_cylinder, cylindrical_faces
from simplecad.kernel.occ import bounding_box, is_valid, volume
from simplecad.kernel.thread_specs import (
    by_designation, clearance_for, clearance_presets, load_sizes, recommend,
)
from simplecad.kernel.threads import apply_thread, thread_solid


def plate_with_hole(size=40.0, thickness=10.0, hole_radius=3.0):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    plate = BRepPrimAPI_MakeBox(size, size, thickness).Shape()
    bore = BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(size / 2, size / 2, -1), gp_Dir(0, 0, 1)),
        hole_radius, thickness + 2,
    ).Shape()
    cut = BRepAlgoAPI_Cut(plate, bore)
    cut.Build()
    return cut.Shape()


def shaft(radius=3.0, height=12.0, at=(0.0, 0.0, 0.0)):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    return BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(*at), gp_Dir(0, 0, 1)), radius, height
    ).Shape()


# ----------------------------------------------------------------------
# Standards
# ----------------------------------------------------------------------
def test_all_five_standards_load():
    codes = {size.code for size in load_sizes()}
    assert codes == {"iso_metric", "unc", "unf", "bsp", "npt"}
    assert len(load_sizes()) > 80


def test_iso_derived_dimensions_match_the_standard():
    m6 = by_designation("M6")
    assert m6.pitch == pytest.approx(1.0)
    # ISO 68-1: H = P * sqrt(3) / 2 for a 60-degree form.
    assert m6.height == pytest.approx(1.0 * math.sqrt(3) / 2, rel=1e-6)
    assert m6.tap_drill == pytest.approx(4.917, abs=0.01)   # 5.0 mm drill
    assert m6.pitch_diameter == pytest.approx(5.350, abs=0.01)


def test_a_six_millimetre_hole_suggests_m6():
    """The example the product spec calls out by name."""
    best = recommend(6.0, internal=True)[0]
    assert best.designation == "M6"
    assert best.exact
    assert best.size.pitch == pytest.approx(1.0)


def test_recommendation_prefers_metric_on_a_near_tie():
    assert recommend(6.2, internal=True)[0].designation == "M6"


def test_a_genuine_imperial_size_still_wins_when_exact():
    assert recommend(0.25 * 25.4, internal=False)[0].designation == "1/4-20"


def test_twelve_millimetre_shaft_suggests_m12_coarse():
    best = recommend(12.0, internal=False)[0]
    assert best.designation == "M12"
    assert best.size.pitch == pytest.approx(1.75)


# ----------------------------------------------------------------------
# Detection
# ----------------------------------------------------------------------
def test_a_bore_is_recognised_as_a_hole():
    faces = cylindrical_faces(plate_with_hole())
    assert len(faces) == 1
    info = faces[0][1]
    assert info.internal is True
    assert info.diameter == pytest.approx(6.0)
    assert info.length == pytest.approx(10.0)


def test_a_cylinder_is_recognised_as_a_shaft():
    info = cylindrical_faces(shaft(6.0, 15.0))[0][1]
    assert info.internal is False
    assert info.diameter == pytest.approx(12.0)


# ----------------------------------------------------------------------
# Geometry
# ----------------------------------------------------------------------
@pytest.mark.parametrize("designation,length", [("M3", 6.0), ("M6", 10.0), ("M12", 16.0), ("M20", 20.0)])
def test_thread_solid_is_valid_and_correctly_sized(designation, length):
    size = by_designation(designation)
    solid = thread_solid(size.diameter, size.pitch, length)

    assert is_valid(solid)
    low, high = bounding_box(solid)
    assert high[0] == pytest.approx(size.diameter / 2, abs=0.05)

    core = math.pi * (size.external_minor / 2) ** 2 * length
    full = math.pi * (size.diameter / 2) ** 2 * length
    assert core * 0.95 < volume(solid) < full


def test_external_thread_removes_material_from_a_shaft():
    size = by_designation("M6")
    blank = shaft(size.diameter / 2, 12.0)
    before = volume(blank)

    result = apply_thread(
        blank, size=size, origin=(0, 0, 0), direction=(0, 0, 1),
        length=10.0, internal=False,
    )
    assert result.modelled
    assert is_valid(result.shape)
    assert volume(result.shape) < before


def test_internal_thread_adds_material_to_a_hole():
    size = by_designation("M6")
    plate = plate_with_hole(hole_radius=size.diameter / 2)
    before = volume(plate)

    result = apply_thread(
        plate, size=size, origin=(20, 20, 0), direction=(0, 0, 1),
        length=10.0, internal=True, clearance="normal",
    )
    assert result.modelled
    assert is_valid(result.shape)
    assert volume(result.shape) > before, "nut metal must protrude into the bore"


# ----------------------------------------------------------------------
# The promise: a generated pair fits
# ----------------------------------------------------------------------
def _interference(bolt, nut) -> float:
    """Volume the two solids share. Near-zero magnitudes are boolean noise.

    OCCT can return a small *negative* volume for a degenerate intersection of
    complex helical shells, so the magnitude is what matters, not the sign.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common

    common = BRepAlgoAPI_Common(bolt, nut)
    common.Build()
    return abs(volume(common.Shape()))


def _pair(designation: str, clearance, length: float = 10.0):
    """A bolt at nominal and the nut cut to accept it."""
    from simplecad.kernel.occ import make_transform, transformed

    size = by_designation(designation)
    bolt = thread_solid(size.diameter, size.pitch, length)
    plate = plate_with_hole(size=40.0, thickness=length, hole_radius=size.diameter / 2)
    nut = apply_thread(
        plate, size=size, origin=(20, 20, 0), direction=(0, 0, 1),
        length=length, internal=True, clearance=clearance,
    )
    assert nut.modelled
    seated = transformed(bolt, make_transform(translate=(20.0, 20.0, 0.0)))
    return seated, nut.shape, size


@pytest.mark.slow
@pytest.mark.parametrize("designation", ["M6", "M12"])
def test_a_matched_pair_assembles_without_interference(designation):
    """A bolt at nominal must not collide with its nut's thread metal."""
    bolt, nut, size = _pair(designation, "normal")
    overlap = _interference(bolt, nut)
    # Scale the allowance to the part: 0.01% of the bolt is unambiguously noise.
    allowance = max(volume(bolt) * 1e-4, 1e-3)
    assert overlap < allowance, (
        f"{designation} pair interferes by {overlap:.4f} mm3 (allowed {allowance:.4f})"
    )


@pytest.mark.slow
def test_the_interference_check_can_actually_detect_interference():
    """Control for the test above.

    Without this, a fit test that always measured zero would pass whether or not
    the clearance logic worked. Cutting the nut undersize must show up.
    """
    clean_bolt, clean_nut, _ = _pair("M6", "normal")
    clean = _interference(clean_bolt, clean_nut)

    tight_bolt, tight_nut, _ = _pair("M6", -0.30)   # nut deliberately undersize
    clashing = _interference(tight_bolt, tight_nut)

    assert clashing > 0.5, "an undersize nut must visibly interfere"
    assert clashing > clean * 20, (
        f"interference {clashing:.4f} is not clearly above the clean pair's {clean:.4f}"
    )


@pytest.mark.slow
def test_tighter_clearance_leaves_less_room():
    """Clearance must actually change the geometry, in the right direction."""
    size = by_designation("M6")
    volumes = {}
    for preset in ("tight", "normal", "loose"):
        plate = plate_with_hole(thickness=10.0, hole_radius=size.diameter / 2)
        result = apply_thread(
            plate, size=size, origin=(20, 20, 0), direction=(0, 0, 1),
            length=10.0, internal=True, clearance=preset,
        )
        volumes[preset] = volume(result.shape)

    assert clearance_for("tight") < clearance_for("normal") < clearance_for("loose")
    # More clearance = a wider bore = less metal left in the nut.
    assert volumes["tight"] > volumes["normal"] > volumes["loose"]


def test_clearance_presets_cover_the_documented_range():
    presets = clearance_presets()
    assert set(presets) == {"tight", "normal", "loose", "very_loose"}
    assert presets["normal"]["clearance"] == pytest.approx(0.20)


# ----------------------------------------------------------------------
# Threading a feature that is not exactly nominal
# ----------------------------------------------------------------------
def _shaft_diameters(shape) -> list[float]:
    return sorted({round(info.diameter, 2) for _f, info in cylindrical_faces(shape)})


def test_an_oversize_shaft_is_turned_down_to_nominal():
    """A die cuts the shaft to size. So should we.

    Threading a 12.4 mm shaft as M12 must not leave a sleeve of the original
    diameter wrapped around the thread.
    """
    size = by_designation("M12")
    blank = shaft(12.4 / 2, 20.0)
    result = apply_thread(
        blank, size=size, origin=(0, 0, 0), direction=(0, 0, 1),
        length=20.0, internal=False, feature_diameter=12.4,
    )
    assert result.modelled and is_valid(result.shape)
    assert 12.4 not in _shaft_diameters(result.shape), (
        "an unthreaded sleeve at the original diameter survived"
    )
    assert "turned down" in result.message


def test_an_undersize_shaft_says_the_thread_is_shallow():
    size = by_designation("M12")
    blank = shaft(11.6 / 2, 20.0)
    result = apply_thread(
        blank, size=size, origin=(0, 0, 0), direction=(0, 0, 1),
        length=20.0, internal=False, feature_diameter=11.6,
    )
    assert result.modelled and is_valid(result.shape)
    assert "not reach full depth" in result.message
    assert "smaller size" in result.message


def test_an_exact_shaft_gets_no_warning():
    size = by_designation("M12")
    blank = shaft(6.0, 20.0)
    result = apply_thread(
        blank, size=size, origin=(0, 0, 0), direction=(0, 0, 1),
        length=20.0, internal=False, feature_diameter=12.0,
    )
    assert result.modelled
    assert result.message == ""
