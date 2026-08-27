"""Measurement: the tool reports what the selection means."""

from __future__ import annotations

import math

import pytest

from simplecad.core.naming import fingerprint, sub_shapes
from simplecad.kernel.detect import analyse_cylinder, analyse_plane
from simplecad.kernel.measure import measure, minimum_distance


def box(dx, dy, dz, at=(0.0, 0.0, 0.0)):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    return BRepPrimAPI_MakeBox(gp_Pnt(*at), dx, dy, dz).Shape()


def cylinder(radius, height, at=(0.0, 0.0, 0.0)):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    return BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(*at), gp_Dir(0, 0, 1)), radius, height
    ).Shape()


def reading(measurement, label):
    return next((r for r in measurement.readings if r.label == label), None)


def round_face(shape):
    return next(f for f in sub_shapes(shape, "face") if analyse_cylinder(f))


# ----------------------------------------------------------------------
def test_nothing_selected_says_so():
    assert measure([]).subject == "Nothing selected"
    assert measure([]).is_empty()


def test_a_body_reports_volume_area_and_extent():
    result = measure([("body", box(40, 30, 20))])
    assert reading(result, "Volume").value == pytest.approx(24000.0)
    assert reading(result, "Surface area").value == pytest.approx(5200.0)
    assert "40" in reading(result, "Bounding box").text
    assert result.headline.label == "Volume"


def test_a_flat_face_reports_area_and_normal():
    shape = box(40, 30, 20)
    top = max(sub_shapes(shape, "face"), key=lambda f: fingerprint(f, "face").center[2])
    result = measure([("face", top)])
    assert reading(result, "Area").value == pytest.approx(1200.0)
    assert "0.000, 0.000, 1.000" in reading(result, "Normal").text


def test_a_round_face_leads_with_its_diameter():
    result = measure([("face", round_face(cylinder(6.0, 25.0)))])
    assert result.headline.label == "Diameter"
    assert result.headline.value == pytest.approx(12.0)
    assert reading(result, "Radius").value == pytest.approx(6.0)
    assert reading(result, "Length").value == pytest.approx(25.0)


def test_a_straight_edge_reports_its_length_and_ends():
    shape = box(40, 30, 20)
    edge = next(
        e for e in sub_shapes(shape, "edge")
        if fingerprint(e, "edge").measure == pytest.approx(40.0)
    )
    result = measure([("edge", edge)])
    assert result.headline.label == "Length"
    assert result.headline.value == pytest.approx(40.0)
    assert reading(result, "From") is not None


def test_a_circular_edge_reports_radius_and_diameter():
    circle = next(
        e for e in sub_shapes(cylinder(6.0, 25.0), "edge")
        if fingerprint(e, "edge").geometry == "circle"
    )
    result = measure([("edge", circle)])
    assert reading(result, "Radius").value == pytest.approx(6.0)
    assert reading(result, "Diameter").value == pytest.approx(12.0)


def test_a_vertex_reports_its_position():
    vertex = sub_shapes(box(10, 10, 10), "vertex")[0]
    result = measure([("vertex", vertex)])
    assert result.headline.label == "Position"


# ----------------------------------------------------------------------
def test_two_bodies_report_the_gap_between_them():
    result = measure([("body", box(10, 10, 10)), ("body", box(10, 10, 10, at=(25, 0, 0)))])
    assert result.headline.label == "Minimum distance"
    assert result.headline.value == pytest.approx(15.0)


def test_two_round_faces_report_centre_distance():
    """The classic case: two holes, and you want the pitch between them."""
    first = round_face(cylinder(3.0, 10.0, at=(0, 0, 0)))
    second = round_face(cylinder(3.0, 10.0, at=(45, 0, 0)))
    result = measure([("face", first), ("face", second)])
    assert reading(result, "Centre distance").value == pytest.approx(45.0)
    assert reading(result, "ΔX").value == pytest.approx(45.0)
    assert reading(result, "ΔY") is None, "no offset means no delta to report"


def test_two_faces_report_the_angle_between_them():
    shape = box(40, 30, 20)
    top = max(sub_shapes(shape, "face"), key=lambda f: fingerprint(f, "face").center[2])
    side = next(
        f for f in sub_shapes(shape, "face")
        if analyse_plane(f) and analyse_plane(f).normal == pytest.approx((1.0, 0.0, 0.0))
    )
    result = measure([("face", top), ("face", side)])
    assert reading(result, "Angle").value == pytest.approx(90.0, abs=1e-6)


def test_touching_shapes_are_described_as_touching():
    result = measure([("body", box(10, 10, 10)), ("body", box(10, 10, 10, at=(10, 0, 0)))])
    assert result.headline.label == "Touching"
    assert result.headline.value == pytest.approx(0.0, abs=1e-9)


def test_three_or_more_reports_the_overall_extent():
    shapes = [("body", box(5, 5, 5, at=(x, 0, 0))) for x in (0, 20, 40)]
    result = measure(shapes)
    assert result.subject == "3 items"
    assert result.headline.value == pytest.approx(45.0)


def test_minimum_distance_is_measured_not_estimated():
    gap = minimum_distance(cylinder(5.0, 10.0), cylinder(5.0, 10.0, at=(30, 0, 0)))
    assert gap == pytest.approx(20.0, abs=1e-6)


def test_measuring_a_solid_does_not_raise_on_the_face_analysers():
    """analyse_* must return None for a non-face, not throw."""
    assert analyse_cylinder(box(10, 10, 10)) is None
    assert analyse_plane(box(10, 10, 10)) is None
    assert analyse_cylinder(sub_shapes(box(10, 10, 10), "edge")[0]) is None
