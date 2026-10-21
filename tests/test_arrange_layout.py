"""The 2-D planner behind Auto-Arrange: patterns, spacing, boundaries, fit."""

from __future__ import annotations

import math
import time
from dataclasses import replace

import pytest
from shapely.geometry import Point, Polygon, box

from simplecad.kernel.arrange_layout import Settings, alternatives, check, placed, plan


def square(size, height=None):
    height = size if height is None else height
    return box(-size / 2, -height / 2, size / 2, height / 2)


def shapes(items, layout):
    return [placed(fp, p) for fp, p in zip(items, layout.placements)]


def assert_valid(items, layout, region, gap):
    assert layout.fits, layout.problems
    placed_shapes = shapes(items, layout)
    grown = region.buffer(1e-6)
    for shape in placed_shapes:
        assert grown.covers(shape)
    for i, a in enumerate(placed_shapes):
        for b in placed_shapes[i + 1:]:
            assert a.distance(b) >= gap - 1e-4


def test_identical_squares_fill_a_rectangle_with_equal_gaps():
    region = box(0, 0, 100, 60)
    items = [square(10)] * 6
    layout = plan(items, region, Settings(pattern="grid", spacing="auto", gap=2))
    assert_valid(items, layout, region, 2)
    assert (layout.rows, layout.columns) == (2, 3)
    xs = sorted({round(u, 6) for u, _v, _a in layout.placements})
    assert xs == pytest.approx([5, 50, 95])          # spread to the margins
    assert layout.gap == pytest.approx(35.0)


def test_fixed_grid_holds_the_gap_between_boundaries_not_centres():
    region = box(0, 0, 200, 200)
    items = [square(10), square(20), square(10), square(20)]
    layout = plan(items, region, Settings(pattern="grid", spacing="fixed", gap=4,
                                          rows=1, columns=4))
    assert_valid(items, layout, region, 4)
    placed_shapes = sorted(shapes(items, layout), key=lambda s: s.bounds[0])
    clear = [placed_shapes[i + 1].bounds[0] - placed_shapes[i].bounds[2] for i in range(3)]
    # Columns are as wide as their widest item, so a 10 next to a 20 gets the
    # gap from the cell edge -- never less than asked.
    assert min(clear) == pytest.approx(4.0)


def test_linear_fixed_gap_is_exact_for_mixed_widths():
    region = box(0, 0, 300, 40)
    items = [square(10), square(30, 10), square(5)]
    layout = plan(items, region, Settings(pattern="linear", spacing="fixed", gap=7.5))
    assert_valid(items, layout, region, 7.5)
    row = sorted(shapes(items, layout), key=lambda s: s.bounds[0])
    assert [row[i + 1].bounds[0] - row[i].bounds[2] for i in range(2)] == pytest.approx([7.5, 7.5])


def test_linear_auto_spreads_between_the_boundaries():
    region = box(0, 0, 100, 20)
    items = [square(10)] * 4
    layout = plan(items, region, Settings(pattern="linear", spacing="auto", gap=1))
    row = sorted(shapes(items, layout), key=lambda s: s.bounds[0])
    assert row[0].bounds[0] == pytest.approx(0.0)
    assert row[-1].bounds[2] == pytest.approx(100.0)
    assert layout.gap == pytest.approx(20.0)


def test_linear_direction_turns_the_whole_row():
    region = box(0, 0, 20, 100)
    items = [square(10)] * 3
    layout = plan(items, region, Settings(pattern="linear", spacing="fixed", gap=5,
                                          direction=90))
    assert_valid(items, layout, region, 5)
    us = [round(u, 6) for u, _v, _a in layout.placements]
    assert len(set(us)) == 1


def test_radial_auto_puts_every_item_at_one_radius():
    region = box(-50, -50, 50, 50)
    items = [square(8)] * 6
    layout = plan(items, region, Settings(pattern="radial", spacing="auto", gap=2))
    assert_valid(items, layout, region, 2)
    radii = [math.hypot(u, v) for u, v, _a in layout.placements]
    assert max(radii) - min(radii) == pytest.approx(0.0, abs=1e-6)
    assert layout.radius > 20


def test_radial_fixed_gap_rings_the_items_at_least_that_far_apart():
    region = box(-200, -200, 200, 200)
    items = [square(10)] * 8
    layout = plan(items, region, Settings(pattern="radial", spacing="fixed", gap=3,
                                          face_center=True))
    assert_valid(items, layout, region, 3)
    ring = shapes(items, layout)
    nearest = min(ring[i].distance(ring[(i + 1) % 8]) for i in range(8))
    assert nearest == pytest.approx(3.0, abs=0.3)


def test_radial_rings_a_washer_around_its_hole():
    region = Point(0, 0).buffer(40).difference(Point(0, 0).buffer(15))
    items = [square(6)] * 8
    layout = plan(items, region, Settings(pattern="radial", spacing="auto", gap=1))
    assert_valid(items, layout, region, 1)


def test_staggered_rows_are_offset_and_tighter_than_a_grid():
    region = box(0, 0, 200, 200)
    items = [Point(0, 0).buffer(5)] * 12
    grid = plan(items, region, Settings(pattern="grid", spacing="fixed", gap=1,
                                        rows=3, columns=4))
    honeycomb = plan(items, region, Settings(pattern="staggered", spacing="fixed", gap=1,
                                             rows=3, columns=4))
    assert_valid(items, honeycomb, region, 1)
    height = lambda layout: (max(s.bounds[3] for s in shapes(items, layout))
                             - min(s.bounds[1] for s in shapes(items, layout)))
    assert height(honeycomb) < height(grid) - 1.0
    rows = {}
    for u, v, _a in honeycomb.placements:
        rows.setdefault(round(v, 4), []).append(u)
    lefts = sorted((v, min(us)) for v, us in rows.items())
    assert abs(lefts[0][1] - lefts[1][1]) == pytest.approx(5.5, abs=1e-6)


def test_packed_fits_mixed_sizes_without_overlap():
    region = box(0, 0, 60, 60)
    items = [square(30), square(20), square(20), square(10), square(10), square(25, 10)]
    layout = plan(items, region, Settings(pattern="packed", gap=1))
    assert_valid(items, layout, region, 1)


def test_packed_rotation_is_what_makes_long_parts_fit():
    region = box(0, 0, 25, 100)
    items = [square(40, 10)] * 4
    assert not plan(items, region, Settings(pattern="packed", gap=1)).fits
    layout = plan(items, region, Settings(pattern="packed", gap=1, auto_rotate=True))
    assert_valid(items, layout, region, 1)


def test_irregular_footprints_nest_closer_than_their_boxes():
    ell = Polygon([(0, 0), (20, 0), (20, 6), (6, 6), (6, 20), (0, 20)])
    ell = Polygon([(x - 10, y - 10) for x, y in ell.exterior.coords])
    region = box(0, 0, 34, 20)
    items = [ell, ell]
    # Two 20x20 boxes need 41 mm; the true outlines turned 180 degrees interlock.
    assert not plan([square(20)] * 2, region, Settings(pattern="packed", gap=1)).fits
    layout = plan(items, region, Settings(pattern="packed", gap=1, auto_rotate=True))
    assert_valid(items, layout, region, 1)


def test_holes_are_avoided():
    region = box(0, 0, 100, 100).difference(box(30, 30, 70, 70))
    items = [square(10)] * 8
    for pattern in ("grid", "packed", "radial"):
        layout = plan(items, region, Settings(pattern=pattern, gap=2))
        assert_valid(items, layout, region, 2)
        assert not any(s.intersects(box(30, 30, 70, 70).buffer(-1e-6))
                       for s in shapes(items, layout))


def test_a_round_face_still_takes_a_grid():
    region = Point(0, 0).buffer(50)
    items = [square(12)] * 9
    layout = plan(items, region, Settings(pattern="grid", spacing="auto", gap=2))
    assert_valid(items, layout, region, 2)


def test_custom_rows_columns_and_column_fill():
    region = box(0, 0, 100, 100)
    items = [square(10)] * 6
    layout = plan(items, region, Settings(pattern="custom", spacing="fixed", gap=5, gap_y=10,
                                          rows=3, columns=2, fill="columns"))
    assert_valid(items, layout, region, 5)
    assert (layout.rows, layout.columns) == (3, 2)
    first_three_u = {round(u, 6) for u, _v, _a in layout.placements[:3]}
    assert len(first_three_u) == 1                    # filled down a column first
    vs = sorted({round(v, 6) for _u, v, _a in layout.placements})
    assert vs[1] - vs[0] == pytest.approx(20.0)        # 10 mm item + 10 mm row gap


def test_alignment_places_a_fixed_block_in_a_corner():
    region = box(0, 0, 100, 100)
    items = [square(10)] * 2
    layout = plan(items, region, Settings(pattern="linear", spacing="fixed", gap=2,
                                          align="bottom_left"))
    row = shapes(items, layout)
    assert min(s.bounds[0] for s in row) == pytest.approx(0.0)
    assert min(s.bounds[1] for s in row) == pytest.approx(0.0)


def test_contain_off_only_needs_the_centre_inside():
    region = box(0, 0, 30, 30)
    items = [square(10)] * 3      # 34 mm of row on a 30 mm face
    settings = Settings(pattern="linear", spacing="fixed", gap=2)
    assert not plan(items, region, settings).fits
    assert plan(items, region, replace(settings, contain=False)).fits


def test_what_cannot_fit_is_reported_with_alternatives_that_do():
    region = box(0, 0, 50, 50)
    items = [square(10)] * 16
    settings = Settings(pattern="grid", spacing="fixed", gap=5)
    layout = plan(items, region, settings)
    assert not layout.fits
    offers = alternatives(items, region, settings)
    assert offers
    for _label, patch in offers:
        assert plan(items, region, replace(settings, **patch)).fits
    # Sixteen 10 mm squares on 50 mm cannot keep a 5 mm gap; the largest
    # that fits is 10/3 mm.
    gap_offer = next(patch for label, patch in offers if "gap" in patch)
    assert gap_offer["gap"] == pytest.approx(10 / 3, abs=0.02)


def test_check_names_overlaps_and_escapes():
    region = box(0, 0, 20, 20)
    items = [square(10)] * 3
    problems = check(items, [(5, 5, 0), (8, 5, 0), (40, 40, 0)], region, gap=0)
    assert problems[0] == ("overlap", 1)
    assert problems[2] == ("outside", None)


def test_a_full_cylinder_spaces_items_across_the_seam():
    period = 100.0
    region = box(-period, 0, 2 * period, 30)
    items = [square(10)] * 5
    layout = plan(items, region, Settings(pattern="linear", spacing="auto", gap=1, period=period),
                  bounds=(0, 0, period, 30))
    assert layout.fits
    us = sorted(u for u, _v, _a in layout.placements)
    steps = [us[i + 1] - us[i] for i in range(4)] + [us[0] + period - us[-1]]
    assert steps == pytest.approx([20.0] * 5)


@pytest.mark.parametrize("pattern", ["grid", "packed", "staggered"])
def test_large_arrangements_stay_quick(pattern):
    region = box(0, 0, 400, 400)
    items = [square(12), square(8, 16), Point(0, 0).buffer(6)] * 50
    started = time.perf_counter()
    layout = plan(items, region, Settings(pattern=pattern, gap=2))
    elapsed = time.perf_counter() - started
    assert_valid(items, layout, region, 2)
    assert elapsed < 3.0, f"{pattern} took {elapsed:.2f} s for 150 items"
