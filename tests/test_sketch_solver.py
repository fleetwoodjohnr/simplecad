"""The sketch constraint solver.

Two things are being checked: that constraints are actually satisfied after a
solve, and that the reported state is trustworthy. The second matters as much as
the first -- "fully constrained" is the signal a user relies on before building
on a sketch, so it must come from the Jacobian's rank rather than from counting
constraints.
"""

from __future__ import annotations

import math

import pytest

from simplecad.sketch.solver import SketchState, degrees_of_freedom, solve
from simplecad.sketch.sketch import Sketch, SketchPlane


def length(sketch, line) -> float:
    a, b = sketch.points[line.start], sketch.points[line.end]
    return math.hypot(b.x - a.x, b.y - a.y)


def test_an_empty_sketch_is_trivially_solved():
    result = solve(Sketch())
    assert result.solved and result.dof == 0


def test_an_unconstrained_sketch_reports_its_freedom():
    sketch = Sketch()
    a, b = sketch.add_point(0, 0), sketch.add_point(10, 3)
    sketch.add_line(a, b)
    result = solve(sketch)
    assert result.state is SketchState.UNCONSTRAINED
    assert result.dof == 4          # two points, two coordinates each


def test_horizontal_makes_a_line_horizontal():
    sketch = Sketch()
    a, b = sketch.add_point(0, 0), sketch.add_point(10, 7)
    line = sketch.add_line(a, b)
    sketch.constrain("horizontal", [line.id])

    assert solve(sketch).solved
    assert sketch.points[a.id].y == pytest.approx(sketch.points[b.id].y, abs=1e-7)


def test_distance_sets_the_length():
    sketch = Sketch()
    a, b = sketch.add_point(0, 0), sketch.add_point(3, 4)
    line = sketch.add_line(a, b)
    sketch.constrain("distance", [a.id, b.id], 25.0)

    assert solve(sketch).solved
    assert length(sketch, line) == pytest.approx(25.0, abs=1e-6)


def test_perpendicular_gives_a_right_angle():
    sketch = Sketch()
    a, b, c = sketch.add_point(0, 0), sketch.add_point(10, 0), sketch.add_point(3, 9)
    first = sketch.add_line(a, b)
    second = sketch.add_line(a, c)
    sketch.constrain("perpendicular", [first.id, second.id])

    assert solve(sketch).solved
    ab = (sketch.points[b.id].x - sketch.points[a.id].x,
          sketch.points[b.id].y - sketch.points[a.id].y)
    ac = (sketch.points[c.id].x - sketch.points[a.id].x,
          sketch.points[c.id].y - sketch.points[a.id].y)
    assert ab[0] * ac[0] + ab[1] * ac[1] == pytest.approx(0.0, abs=1e-6)


def test_parallel_lines_stay_parallel():
    sketch = Sketch()
    a, b = sketch.add_point(0, 0), sketch.add_point(10, 1)
    c, d = sketch.add_point(0, 5), sketch.add_point(9, 8)
    first, second = sketch.add_line(a, b), sketch.add_line(c, d)
    sketch.constrain("parallel", [first.id, second.id])

    assert solve(sketch).solved
    ab = (sketch.points[b.id].x - sketch.points[a.id].x,
          sketch.points[b.id].y - sketch.points[a.id].y)
    cd = (sketch.points[d.id].x - sketch.points[c.id].x,
          sketch.points[d.id].y - sketch.points[c.id].y)
    assert ab[0] * cd[1] - ab[1] * cd[0] == pytest.approx(0.0, abs=1e-5)


def test_equal_makes_two_lines_the_same_length():
    sketch = Sketch()
    a, b = sketch.add_point(0, 0), sketch.add_point(10, 0)
    c, d = sketch.add_point(0, 5), sketch.add_point(3, 5)
    first, second = sketch.add_line(a, b), sketch.add_line(c, d)
    sketch.constrain("equal", [first.id, second.id])

    assert solve(sketch).solved
    assert length(sketch, first) == pytest.approx(length(sketch, second), abs=1e-6)


def test_radius_and_diameter():
    sketch = Sketch()
    centre = sketch.add_point(0, 0)
    circle = sketch.add_circle(centre, 3.0)
    sketch.constrain("radius", [circle.id], 12.5)
    assert solve(sketch).solved
    assert sketch.entities[circle.id].radius == pytest.approx(12.5, abs=1e-7)

    sketch.constraints.clear()
    sketch.constrain("diameter", [circle.id], 8.0)
    assert solve(sketch).solved
    assert sketch.entities[circle.id].radius == pytest.approx(4.0, abs=1e-7)


def test_tangent_puts_a_line_against_a_circle():
    sketch = Sketch()
    centre = sketch.add_point(0, 0)
    circle = sketch.add_circle(centre, 5.0)
    a, b = sketch.add_point(-20, 8), sketch.add_point(20, 8)
    line = sketch.add_line(a, b)
    sketch.constrain("fix", [centre.id])
    sketch.constrain("radius", [circle.id], 5.0)
    sketch.constrain("horizontal", [line.id])
    sketch.constrain("tangent", [line.id, circle.id])

    assert solve(sketch).solved
    assert abs(sketch.points[a.id].y) == pytest.approx(5.0, abs=1e-6)


def test_angle_between_two_lines():
    sketch = Sketch()
    a, b, c = sketch.add_point(0, 0), sketch.add_point(10, 0), sketch.add_point(8, 3)
    first, second = sketch.add_line(a, b), sketch.add_line(a, c)
    sketch.constrain("angle", [first.id, second.id], 45.0)

    assert solve(sketch).solved
    ab = (sketch.points[b.id].x - sketch.points[a.id].x,
          sketch.points[b.id].y - sketch.points[a.id].y)
    ac = (sketch.points[c.id].x - sketch.points[a.id].x,
          sketch.points[c.id].y - sketch.points[a.id].y)
    angle = math.degrees(math.atan2(ab[0] * ac[1] - ab[1] * ac[0],
                                    ab[0] * ac[0] + ab[1] * ac[1]))
    assert angle == pytest.approx(45.0, abs=1e-4)


def test_midpoint_and_coincident():
    sketch = Sketch()
    a, b = sketch.add_point(0, 0), sketch.add_point(10, 4)
    m = sketch.add_point(9, 9)
    line = sketch.add_line(a, b)
    sketch.constrain("fix", [a.id])
    sketch.constrain("fix", [b.id])
    sketch.constrain("midpoint", [m.id, line.id])

    assert solve(sketch).solved
    assert (sketch.points[m.id].x, sketch.points[m.id].y) == pytest.approx((5.0, 2.0))


def test_symmetric_mirrors_two_points_about_a_line():
    sketch = Sketch()
    axis_a, axis_b = sketch.add_point(0, 0), sketch.add_point(0, 10)
    axis = sketch.add_line(axis_a, axis_b)
    left, right = sketch.add_point(-4, 5), sketch.add_point(9, 2)
    sketch.constrain("fix", [axis_a.id])
    sketch.constrain("fix", [axis_b.id])
    sketch.constrain("fix", [left.id])
    sketch.constrain("symmetric", [left.id, right.id, axis.id])

    assert solve(sketch).solved
    assert sketch.points[right.id].x == pytest.approx(4.0, abs=1e-6)
    assert sketch.points[right.id].y == pytest.approx(5.0, abs=1e-6)


# ----------------------------------------------------------------------
# State reporting
# ----------------------------------------------------------------------
def test_a_fully_dimensioned_rectangle_reports_fully_constrained():
    sketch = Sketch()
    lines = sketch.add_rectangle(0, 0, 40, 25)
    corner = sketch.points[lines[0].start]
    sketch.constrain("fix", [corner.id])
    sketch.constrain("distance", [lines[0].start, lines[0].end], 40.0)
    sketch.constrain("distance", [lines[1].start, lines[1].end], 25.0)

    result = solve(sketch)
    assert result.solved, result.message
    assert result.state is SketchState.FULLY, result.message
    assert result.dof == 0
    assert length(sketch, lines[0]) == pytest.approx(40.0, abs=1e-6)
    assert length(sketch, lines[1]) == pytest.approx(25.0, abs=1e-6)


def test_a_rectangle_missing_a_dimension_is_partially_constrained():
    sketch = Sketch()
    lines = sketch.add_rectangle(0, 0, 40, 25)
    sketch.constrain("fix", [sketch.points[lines[0].start].id])
    sketch.constrain("distance", [lines[0].start, lines[0].end], 40.0)

    result = solve(sketch)
    assert result.state is SketchState.PARTIAL
    assert result.dof == 1, result.message
    assert "1 degree of freedom" in result.message


def test_contradictory_dimensions_are_reported_as_conflicting():
    """Two different lengths on one line cannot both hold."""
    sketch = Sketch()
    a, b = sketch.add_point(0, 0), sketch.add_point(10, 0)
    sketch.add_line(a, b)
    sketch.constrain("fix", [a.id])
    sketch.constrain("horizontal", [sketch.entities[list(sketch.entities)[0]].id])
    first = sketch.constrain("distance", [a.id, b.id], 30.0)
    second = sketch.constrain("distance", [a.id, b.id], 50.0)

    result = solve(sketch)
    assert result.state is SketchState.CONFLICTING
    assert not result.ok
    assert "cannot all be satisfied" in result.message
    assert {first.id, second.id} & set(result.conflicting)


def test_a_duplicated_dimension_is_reported_as_redundant_not_conflicting():
    """Same length twice: satisfiable, but one carries no information."""
    sketch = Sketch()
    a, b = sketch.add_point(0, 0), sketch.add_point(10, 0)
    line = sketch.add_line(a, b)
    sketch.constrain("fix", [a.id])
    sketch.constrain("horizontal", [line.id])
    sketch.constrain("distance", [a.id, b.id], 30.0)
    sketch.constrain("distance", [a.id, b.id], 30.0)

    result = solve(sketch)
    assert result.solved
    assert result.state is SketchState.OVER
    assert "repeat information" in result.message
    assert length(sketch, line) == pytest.approx(30.0, abs=1e-6)


def test_counting_constraints_would_get_this_wrong():
    """The reason state comes from the Jacobian and not a tally.

    Four constraints on four free parameters *looks* fully constrained, but two
    of them say the same thing, so a degree of freedom survives.
    """
    sketch = Sketch()
    a, b = sketch.add_point(0, 0), sketch.add_point(10, 0)
    line = sketch.add_line(a, b)
    sketch.constrain("fix", [a.id])              # 2 residuals
    sketch.constrain("horizontal", [line.id])    # 1
    sketch.constrain("horizontal", [line.id])    # 1, duplicate

    result = solve(sketch)
    dof, rank, rows = degrees_of_freedom(sketch)
    assert rows == 4 and rank == 3, "the duplicate must not add rank"
    assert result.dof == 1
    assert result.state is SketchState.OVER


def test_solving_is_stable_when_run_twice():
    sketch = Sketch()
    lines = sketch.add_rectangle(0, 0, 40, 25)
    sketch.constrain("fix", [sketch.points[lines[0].start].id])
    sketch.constrain("distance", [lines[0].start, lines[0].end], 40.0)
    sketch.constrain("distance", [lines[1].start, lines[1].end], 25.0)
    solve(sketch)
    first = sketch.to_vector()
    solve(sketch)
    assert sketch.to_vector() == pytest.approx(first, abs=1e-9)


def test_sketch_round_trips_through_json():
    import json

    sketch = Sketch("Profile", SketchPlane.named("XZ"))
    lines = sketch.add_rectangle(0, 0, 30, 20)
    sketch.constrain("distance", [lines[0].start, lines[0].end], 30.0)
    solve(sketch)

    restored = Sketch.from_dict(json.loads(json.dumps(sketch.to_dict())))
    assert restored.name == "Profile"
    assert len(restored.points) == len(sketch.points)
    assert len(restored.constraints) == len(sketch.constraints)
    assert restored.plane.normal == pytest.approx(sketch.plane.normal)
    assert solve(restored).solved


# ----------------------------------------------------------------------
# Ellipse and spline
# ----------------------------------------------------------------------
def test_an_ellipse_carries_solvable_parameters():
    sketch = Sketch()
    centre = sketch.add_point(0, 0)
    ellipse = sketch.add_ellipse(centre, 20.0, 10.0)

    layout = dict.fromkeys(sketch.parameter_layout())
    assert (ellipse.id, "major") in layout
    assert (ellipse.id, "minor") in layout
    assert solve(sketch).solved


def test_an_ellipse_normalises_its_radii():
    """Major is the longer one, whichever way round it was drawn."""
    sketch = Sketch()
    centre = sketch.add_point(0, 0)
    ellipse = sketch.add_ellipse(centre, 8.0, 20.0)
    assert ellipse.major == pytest.approx(20.0)
    assert ellipse.minor == pytest.approx(8.0)


def test_a_spline_adds_no_parameters_of_its_own():
    """Its shape lives entirely in its control points, which are already free."""
    sketch = Sketch()
    points = [sketch.add_point(x, y) for x, y in ((0, 0), (10, 8), (20, -4))]
    before = len(sketch.to_vector())
    sketch.add_spline(points)
    assert len(sketch.to_vector()) == before


def test_spline_control_points_can_be_constrained_like_any_other():
    sketch = Sketch()
    points = [sketch.add_point(x, y) for x, y in ((0, 0), (10, 8), (20, -4))]
    sketch.add_spline(points)
    sketch.constrain("fix", [points[0].id])
    sketch.constrain("distance", [points[0].id, points[2].id], 30.0)

    assert solve(sketch).solved
    assert math.hypot(
        sketch.points[points[2].id].x - sketch.points[points[0].id].x,
        sketch.points[points[2].id].y - sketch.points[points[0].id].y,
    ) == pytest.approx(30.0, abs=1e-6)


def test_ellipse_and_spline_round_trip_through_json():
    import json

    sketch = Sketch("Curves")
    centre = sketch.add_point(0, 0)
    sketch.add_ellipse(centre, 20.0, 10.0, rotation=0.5)
    points = [sketch.add_point(x, y) for x, y in ((0, 30), (10, 38), (20, 26))]
    sketch.add_spline(points)

    restored = Sketch.from_dict(json.loads(json.dumps(sketch.to_dict())))
    kinds = sorted(e.kind for e in restored.entities.values())
    assert kinds == ["ellipse", "spline"]
    ellipse = next(e for e in restored.entities.values() if e.kind == "ellipse")
    assert ellipse.rotation == pytest.approx(0.5)
    spline = next(e for e in restored.entities.values() if e.kind == "spline")
    assert len(spline.points) == 3
