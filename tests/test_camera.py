"""The camera maths.

These are the invariants that make orbiting feel like a CAD viewport rather
than a free-flying camera: the horizon never tilts, the model never flips
through a pole, and turning does not quietly change how far away you are.

All pure arithmetic, so none of it needs a GL context -- which is the point of
keeping the maths out of the widget.
"""

from __future__ import annotations

import math
import random

import pytest

from simplecad.ui.viewport.camera import (
    PITCH_LIMIT, bbox_center, constrained_up, orbit_state, pitch_of,
    state_for_direction, view_direction, _dot, _length, _normalise, _slerp,
    _sub,
)


def _view(eye, center):
    return _sub(center, eye)


class TestNoRoll:
    """The horizon must stay level, always."""

    def test_up_is_perpendicular_to_the_view(self):
        eye, center, pivot = (100.0, -100.0, 80.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        random.seed(11)
        for _ in range(500):
            eye, center, up = orbit_state(
                eye, center, pivot,
                random.uniform(-0.4, 0.4), random.uniform(-0.4, 0.4),
            )
            assert abs(_dot(up, _normalise(_view(eye, center)))) < 1e-9

    def test_no_roll_accumulates_over_many_orbits(self):
        eye, center, pivot = (60.0, 40.0, 25.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        random.seed(23)
        worst = 0.0
        for _ in range(500):
            eye, center, up = orbit_state(
                eye, center, pivot,
                random.uniform(-0.3, 0.3), random.uniform(-0.3, 0.3),
            )
            ideal = constrained_up(_view(eye, center))
            worst = max(worst, max(abs(a - b) for a, b in zip(up, ideal)))
        # Not "small" -- zero. Up is recomputed from the view direction every
        # time rather than carried forward, so there is nothing to drift.
        assert worst < 1e-9

    def test_a_pure_yaw_keeps_up_vertical(self):
        eye, center, pivot = (100.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        for _ in range(40):
            eye, center, up = orbit_state(eye, center, pivot, 0.15, 0.0)
            assert up == pytest.approx((0.0, 0.0, 1.0), abs=1e-9)


class TestPitchClamp:
    """The view must never cross a pole."""

    @pytest.mark.parametrize("direction", [1.0, -1.0])
    def test_slamming_at_a_pole_stops_short_of_it(self, direction):
        eye, center, pivot = (0.0, -100.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        for _ in range(200):
            eye, center, _up = orbit_state(eye, center, pivot, 0.0, direction * 0.5)
        pitch = pitch_of(_view(eye, center))
        assert abs(pitch) <= PITCH_LIMIT + 1e-9
        assert abs(pitch) == pytest.approx(PITCH_LIMIT, abs=1e-6)

    def test_the_view_never_inverts(self):
        """A flip shows up as the vertical component changing sign."""
        eye, center, pivot = (0.0, -100.0, 10.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        random.seed(5)
        for _ in range(400):
            eye, center, up = orbit_state(
                eye, center, pivot, random.uniform(-1.0, 1.0), 0.9
            )
            assert up[2] > 0.0


class TestDistanceIsPreserved:
    def test_orbit_does_not_zoom(self):
        eye, center, pivot = (120.0, -90.0, 70.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        start = _length(_sub(eye, pivot))
        random.seed(3)
        for _ in range(300):
            eye, center, _up = orbit_state(
                eye, center, pivot,
                random.uniform(-0.5, 0.5), random.uniform(-0.2, 0.2),
            )
            assert _length(_sub(eye, pivot)) == pytest.approx(start, rel=1e-12)

    def test_a_full_sweep_comes_back_to_where_it_started(self):
        eye, center, pivot = (100.0, -100.0, 80.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        start = eye
        for _ in range(720):
            eye, center, _up = orbit_state(
                eye, center, pivot, 2 * math.pi / 720, 0.0
            )
        assert eye == pytest.approx(start, abs=1e-9)


class TestPivot:
    def test_orbiting_a_far_pivot_does_not_lurch(self):
        """A tiny drag must produce a tiny movement, not a jump.

        Snapping the view centre onto the pivot -- the obvious implementation --
        makes the first pixel of the first drag teleport the camera whenever the
        pivot is not already what the camera is aimed at, which it usually is
        not.
        """
        eye, center, pivot = (100.0, -100.0, 80.0), (30.0, 12.0, 5.0), (0.0, 0.0, 0.0)
        moved, _c, _u = orbit_state(eye, center, pivot, 1e-4, 0.0)
        assert _length(_sub(moved, eye)) < 0.05

    def test_orbit_turns_about_the_pivot_not_the_origin(self):
        pivot = (50.0, 50.0, 0.0)
        eye, center = (150.0, 50.0, 0.0), (50.0, 50.0, 0.0)
        eye, center, _up = orbit_state(eye, center, pivot, math.pi / 2, 0.0)
        assert center == pytest.approx(pivot, abs=1e-9)
        assert _length(_sub(eye, pivot)) == pytest.approx(100.0)


class TestStandardViews:
    """``constrained_up`` has to agree with the orientations the app ships."""

    @pytest.mark.parametrize(
        "projection,expected_up",
        [
            ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),    # front
            ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),     # back
            ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),     # right
            ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),     # top -- the degenerate case
            ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),    # bottom
        ],
    )
    def test_derived_up_matches_the_named_view(self, projection, expected_up):
        looking = tuple(-v for v in projection)
        assert constrained_up(looking) == pytest.approx(expected_up, abs=1e-9)

    def test_re_aiming_keeps_the_framing(self):
        """A standard view changes orientation, not how far away you are."""
        state = ((100.0, -100.0, 80.0), (10.0, 5.0, 2.0), (0.0, 0.0, 1.0), 42.0)
        target = state_for_direction(state, (0.0, 0.0, -1.0))
        assert target[1] == state[1]                       # same centre
        assert target[3] == state[3]                       # same scale
        assert _length(_sub(target[0], target[1])) == pytest.approx(
            _length(_sub(state[0], state[1]))
        )
        assert view_direction(target) == pytest.approx((0.0, 0.0, -1.0), abs=1e-9)


class TestSlerp:
    def test_antipodal_directions_do_not_divide_by_zero(self):
        """Front to Back is exactly 180 degrees, and comes up in normal use."""
        midpoint = _slerp((1.0, 0.0, 0.0), (-1.0, 0.0, 0.0), 0.5)
        assert _length(midpoint) == pytest.approx(1.0)

    def test_the_arc_stays_on_the_unit_sphere(self):
        a, b = _normalise((1.0, -1.0, 1.0)), _normalise((0.0, 0.0, -1.0))
        for step in range(11):
            assert _length(_slerp(a, b, step / 10.0)) == pytest.approx(1.0)


class TestBboxCenter:
    def test_ignores_null_and_empty_input(self):
        assert bbox_center([]) is None
        assert bbox_center([None]) is None

    def test_finds_the_middle_of_a_box(self):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

        shape = BRepPrimAPI_MakeBox(50.0, 40.0, 20.0).Shape()
        assert bbox_center([shape]) == pytest.approx((25.0, 20.0, 10.0), abs=1e-6)
