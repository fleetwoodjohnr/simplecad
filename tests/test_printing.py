"""Printability analysis.

Every check reports; none refuses. The spec is explicit that export must never
be blocked, so these tests assert on *severity and message*, and that nothing
here can raise on the way to an export.
"""

from __future__ import annotations

import math

import pytest

from simplecad.kernel.printing import (
    Severity, analyse, best_print_orientation, center_on_plate, check_build_volume,
    check_disconnected, check_on_plate, check_overhangs, check_thin_walls,
    check_tiny_features, find_slicer, place_on_plate,
)
from simplecad.kernel.occ import bounding_box, make_transform, transformed
from simplecad.kernel.thread_specs import printer_profile


@pytest.fixture
def profile():
    return printer_profile()


def box(dx, dy, dz, at=(0.0, 0.0, 0.0)):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    return BRepPrimAPI_MakeBox(gp_Pnt(*at), dx, dy, dz).Shape()


def cone(bottom, top, height):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    return BRepPrimAPI_MakeCone(
        gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), bottom, top, height
    ).Shape()


# ----------------------------------------------------------------------
def test_the_default_printer_is_the_centauri_carbon(profile):
    assert profile["name"] == "Elegoo Centauri Carbon 2"
    assert profile["build_volume"] == {"x": 256.0, "y": 256.0, "z": 256.0}


def test_a_part_that_fits_says_so(profile):
    finding = check_build_volume([box(50, 50, 20)], profile)
    assert finding.severity is Severity.OK
    assert "50.0 x 50.0 x 20.0" in finding.message


def test_a_part_too_big_for_the_plate_is_flagged(profile):
    finding = check_build_volume([box(300, 50, 20)], profile)
    assert finding.severity is Severity.WARNING
    assert "bigger than" in finding.message
    assert "300.0 mm" in finding.detail and "256 mm" in finding.detail


def test_a_part_below_the_plate_is_flagged():
    finding = check_on_plate([box(10, 10, 10, at=(0, 0, -4))])
    assert finding.severity is Severity.WARNING
    assert "4.0 mm below" in finding.message


def test_a_floating_part_is_a_note_not_a_warning():
    finding = check_on_plate([box(10, 10, 10, at=(0, 0, 12))])
    assert finding.severity is Severity.NOTE
    assert "floats" in finding.message


# ----------------------------------------------------------------------
def test_a_box_resting_on_the_plate_has_no_overhang():
    """Its base faces straight down but the plate holds it up."""
    finding = check_overhangs(box(50, 50, 20), 50.0)
    assert finding.severity is Severity.OK, finding.message


@pytest.mark.parametrize(
    "top_radius,height,flagged",
    [(20.0, 30.0, False),    # 33.7 degrees from vertical -- prints fine
     (20.0, 15.0, True),     # 53.1 degrees -- needs support
     (30.0, 10.0, True)],    # 71.6 degrees -- definitely
)
def test_overhang_flagging_follows_the_angle(top_radius, height, flagged):
    finding = check_overhangs(cone(0.0, top_radius, height), 50.0)
    assert (finding.severity is not Severity.OK) is flagged, finding.message


def test_thin_walls_are_measured_between_opposed_faces():
    finding = check_thin_walls(box(40, 40, 0.4), minimum=0.8)
    assert finding.severity is Severity.WARNING
    assert "0.40 mm" in finding.message
    assert "0.80 mm" in finding.detail


def test_a_thick_part_passes_the_wall_check():
    finding = check_thin_walls(box(40, 40, 10), minimum=0.8)
    assert finding.severity is Severity.OK
    assert "10.00 mm" in finding.message


def test_tiny_features_are_noted():
    finding = check_tiny_features(box(40, 40, 0.2), minimum=0.6)
    assert finding.severity is Severity.NOTE
    assert "0.200 mm" in finding.detail


def test_separate_lumps_are_noticed():
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    builder.Add(compound, box(10, 10, 10))
    builder.Add(compound, box(10, 10, 10, at=(50, 0, 0)))

    finding = check_disconnected(compound)
    assert finding.severity is Severity.NOTE
    assert "2 separate pieces" in finding.message


# ----------------------------------------------------------------------
def test_placement_drops_a_floating_part_onto_the_plate():
    shapes = [box(10, 10, 10, at=(0, 0, 37))]
    assert place_on_plate(shapes) == pytest.approx((0.0, 0.0, -37.0))


def test_centering_puts_the_part_in_the_middle_of_the_plate(profile):
    shapes = [box(40, 20, 10, at=(0, 0, 5))]
    moved = transformed(shapes[0], make_transform(
        translate=center_on_plate(shapes, profile)
    ))
    low, high = bounding_box(moved)
    assert (low[0] + high[0]) / 2 == pytest.approx(128.0, abs=1e-6)
    assert (low[1] + high[1]) / 2 == pytest.approx(128.0, abs=1e-6)
    assert low[2] == pytest.approx(0.0, abs=1e-6)


def test_the_suggested_orientation_avoids_overhangs(profile):
    """A cone pointing down should be turned over."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    pointing_down = BRepPrimAPI_MakeCone(
        gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 0.0, 25.0, 12.0
    ).Shape()
    before = check_overhangs(pointing_down, 50.0)
    assert before.severity is not Severity.OK, "the test shape must start bad"

    axis, angle = best_print_orientation(pointing_down, profile)
    turned = pointing_down if not angle else transformed(
        pointing_down, make_transform(rotate_axis=axis, rotate_degrees=angle)
    )
    after = check_overhangs(turned, 50.0)
    assert after.severity.rank() < before.severity.rank(), (
        f"orientation did not improve: {before.message} -> {after.message}"
    )


# ----------------------------------------------------------------------
def test_a_clean_part_analyses_clean(profile):
    analysis = analyse([box(50, 50, 20)], profile)
    assert analysis.ok, [f.message for f in analysis.warnings()]
    assert analysis.summary() == "Ready to print."


def test_an_oversized_part_analyses_with_a_warning_but_still_reports(profile):
    analysis = analyse([box(300, 50, 20)], profile)
    assert not analysis.ok
    assert analysis.worst is Severity.WARNING
    assert "1 thing" in analysis.summary() or "thing(s)" in analysis.summary()
    # Nothing here raises: export is never blocked.
    assert all(isinstance(f.message, str) for f in analysis.findings)


def test_analysing_nothing_is_harmless(profile):
    analysis = analyse([], profile)
    assert "nothing to print" in analysis.findings[0].message.lower()


def test_slicer_detection_returns_a_runnable_command_or_none():
    found = find_slicer()
    if found is None:
        pytest.skip("no slicer installed on this machine")
    name, command = found
    assert isinstance(name, str) and name
    assert isinstance(command, list) and command
