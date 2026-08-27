"""Sketches producing geometry: profiles, extrude and revolve."""

from __future__ import annotations

import math

import pytest

from simplecad.core.document import Document
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel import sketch_features  # noqa: F401 - registers types
from simplecad.kernel.occ import bounding_box, is_valid, volume
from simplecad.kernel.sketch_features import ExtrudeFeature, RevolveFeature, SketchFeature
from simplecad.sketch.sketch import Sketch, SketchPlane
from simplecad.sketch.to_occ import faces, profile_face, wires


def rectangle_sketch(width=40.0, height=25.0, plane="XY") -> Sketch:
    sketch = Sketch("Profile", SketchPlane.named(plane))
    lines = sketch.add_rectangle(0, 0, width, height)
    sketch.constrain("fix", [sketch.points[lines[0].start].id])
    sketch.constrain("distance", [lines[0].start, lines[0].end], width)
    sketch.constrain("distance", [lines[1].start, lines[1].end], height)
    return sketch


# ----------------------------------------------------------------------
def test_a_closed_rectangle_makes_one_wire():
    assert len(wires(rectangle_sketch())) == 1


def test_a_rectangle_makes_a_face_of_the_right_area():
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(profile_face(rectangle_sketch(40, 25)), props)
    assert props.Mass() == pytest.approx(40 * 25, rel=1e-9)


def test_a_circle_inside_a_rectangle_becomes_a_hole():
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    sketch = rectangle_sketch(40, 25)
    centre = sketch.add_point(20, 12.5)
    sketch.add_circle(centre, 5.0)

    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(profile_face(sketch), props)
    expected = 40 * 25 - math.pi * 25
    assert props.Mass() == pytest.approx(expected, rel=1e-4), (
        "the inner loop must be cut out, not added as a second face"
    )


def test_an_open_profile_reports_a_useful_error():
    from simplecad.core.errors import CadError

    sketch = Sketch()
    a, b = sketch.add_point(0, 0), sketch.add_point(10, 0)
    sketch.add_line(a, b)
    with pytest.raises(CadError) as caught:
        profile_face(sketch)
    assert "closed" in str(caught.value).lower()


def test_construction_geometry_is_left_out_of_the_profile():
    sketch = rectangle_sketch(40, 25)
    centre = sketch.add_point(20, 12.5)
    sketch.add_circle(centre, 5.0, construction=True)
    assert len(wires(sketch)) == 1, "construction geometry must not form a loop"


# ----------------------------------------------------------------------
def _extruded(sketch, **inputs):
    document = Document("T")
    feature = document.add_feature(SketchFeature(inputs={"sketch": sketch}))
    document.add_feature(
        ExtrudeFeature(
            inputs={"sketch": feature.name, **inputs}, outputs=["Solid"]
        )
    )
    report = Rebuilder(document).rebuild()
    assert report.ok, report.summary()
    return document


def test_extruding_a_rectangle_gives_the_expected_volume():
    document = _extruded(rectangle_sketch(40, 25), distance=10)
    shape = document.bodies["Solid"].shape
    assert is_valid(shape)
    assert volume(shape) == pytest.approx(40 * 25 * 10, rel=1e-9)


def test_extruding_a_profile_with_a_hole_leaves_the_hole():
    sketch = rectangle_sketch(40, 25)
    centre = sketch.add_point(20, 12.5)
    sketch.add_circle(centre, 5.0)
    document = _extruded(sketch, distance=10)
    expected = (40 * 25 - math.pi * 25) * 10
    assert volume(document.bodies["Solid"].shape) == pytest.approx(expected, rel=1e-4)


def test_a_symmetric_extrude_straddles_the_sketch_plane():
    document = _extruded(rectangle_sketch(40, 25), distance=10, symmetric=True)
    low, high = bounding_box(document.bodies["Solid"].shape)
    assert low[2] == pytest.approx(-5.0, abs=1e-6)
    assert high[2] == pytest.approx(5.0, abs=1e-6)


def test_extruding_on_the_xz_plane_goes_the_right_way():
    document = _extruded(rectangle_sketch(30, 20, plane="XZ"), distance=8)
    low, high = bounding_box(document.bodies["Solid"].shape)
    assert (high[1] - low[1]) == pytest.approx(8.0, abs=1e-6), (
        "an XZ sketch must extrude along Y"
    )


def test_a_sketch_dimension_can_be_driven_by_a_document_parameter():
    """The point of a parametric sketcher: change a parameter, geometry follows."""
    document = Document("T")
    document.parameters.set("width", "40")
    sketch = rectangle_sketch(40, 25)
    feature = document.add_feature(SketchFeature(inputs={"sketch": sketch}))
    document.add_feature(
        ExtrudeFeature(
            inputs={"sketch": feature.name, "distance": "width / 4"},
            outputs=["Solid"],
        )
    )
    builder = Rebuilder(document)
    assert builder.rebuild().ok
    assert volume(document.bodies["Solid"].shape) == pytest.approx(40 * 25 * 10)

    document.parameters.set("width", "80")
    builder.invalidate_parameter("width")
    assert builder.rebuild().ok
    assert volume(document.bodies["Solid"].shape) == pytest.approx(40 * 25 * 20)


def test_revolving_a_rectangle_makes_a_ring():
    document = Document("T")
    sketch = Sketch("Profile", SketchPlane.named("XZ"))
    sketch.add_rectangle(10, 0, 14, 6)      # offset from the Z axis
    feature = document.add_feature(SketchFeature(inputs={"sketch": sketch}))
    document.add_feature(
        RevolveFeature(
            inputs={
                "sketch": feature.name, "angle": 360,
                "axis_origin": (0.0, 0.0, 0.0), "axis_direction": (0.0, 0.0, 1.0),
            },
            outputs=["Ring"],
        )
    )
    report = Rebuilder(document).rebuild()
    assert report.ok, report.summary()

    shape = document.bodies["Ring"].shape
    assert is_valid(shape)
    # Pappus: a 4x6 rectangle whose centroid is 12 from the axis.
    assert volume(shape) == pytest.approx(4 * 6 * 2 * math.pi * 12, rel=1e-3)


def test_a_conflicting_sketch_fails_with_the_solver_message():
    document = Document("T")
    sketch = Sketch()
    a, b = sketch.add_point(0, 0), sketch.add_point(10, 0)
    line = sketch.add_line(a, b)
    sketch.constrain("fix", [a.id])
    sketch.constrain("horizontal", [line.id])
    sketch.constrain("distance", [a.id, b.id], 30.0)
    sketch.constrain("distance", [a.id, b.id], 50.0)
    document.add_feature(SketchFeature(inputs={"sketch": sketch}))

    report = Rebuilder(document).rebuild()
    assert not report.ok
    assert "cannot all be satisfied" in report.summary()


def test_an_open_line_left_in_a_sketch_is_not_part_of_the_profile():
    """Users leave stray construction lines around; the profile must ignore them."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    sketch = rectangle_sketch(40, 25)
    stray_a = sketch.add_point(0, -20)
    stray_b = sketch.add_point(40, -20)
    sketch.add_line(stray_a, stray_b)

    assert len(wires(sketch)) == 1, "the open line must not count as a loop"
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(profile_face(sketch), props)
    assert props.Mass() == pytest.approx(40 * 25, rel=1e-9)


def test_a_profile_with_a_hole_and_a_stray_line_still_works():
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    sketch = rectangle_sketch(40, 25)
    centre = sketch.add_point(20, 12.5)
    sketch.add_circle(centre, 5.0)
    stray_a = sketch.add_point(0, -20)
    stray_b = sketch.add_point(40, -20)
    sketch.add_line(stray_a, stray_b)

    assert len(wires(sketch)) == 2
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(profile_face(sketch), props)
    assert props.Mass() == pytest.approx(40 * 25 - math.pi * 25, rel=1e-4)
