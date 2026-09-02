"""Snap points: the places a point-to-point measurement can land.

The interesting property is not that snapping finds *something* -- it is that it
finds the right kind of thing and rejects a cursor that is nowhere near. Both
are checked here, the second by driving :func:`nearest` with a fake projection
so the ranking can be tested without a live 3D view.
"""

from __future__ import annotations

import math
from collections import Counter

import pytest

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder

from simplecad.kernel.snapping import (
    SnapPoint, inferred_snap, nearest, snap_points,
)


@pytest.fixture
def box():
    return BRepPrimAPI_MakeBox(40.0, 30.0, 20.0).Shape()


def kinds(shape) -> Counter:
    return Counter(s.kind for s in snap_points(shape))


def test_a_box_offers_its_corners_midpoints_and_face_centres(box):
    found = kinds(box)
    assert found["vertex"] == 8       # eight corners
    assert found["midpoint"] == 12    # twelve edges
    assert found["face"] == 6         # six faces


def test_corners_land_exactly_on_the_geometry(box):
    corners = {
        tuple(round(v, 6) for v in s.position)
        for s in snap_points(box) if s.kind == "vertex"
    }
    expected = {
        (x, y, z)
        for x in (0.0, 40.0) for y in (0.0, 30.0) for z in (0.0, 20.0)
    }
    assert corners == expected


def test_a_cylinder_offers_the_centres_of_its_round_edges():
    cylinder = BRepPrimAPI_MakeCylinder(10.0, 25.0).Shape()
    centres = sorted(
        round(s.position[2], 6)
        for s in snap_points(cylinder) if s.kind == "center"
    )
    # Both end circles, plus the middle of the bore from the round face.
    assert 0.0 in centres and 25.0 in centres
    assert 12.5 in centres


def test_snap_points_are_ranked_most_specific_first(box):
    ranked = [s.kind for s in snap_points(box)]
    assert ranked[0] == "vertex", "a corner must outrank an edge midpoint"


def test_nearest_prefers_a_corner_over_a_closer_midpoint():
    """Priority beats raw distance, within reason.

    Aiming between a corner and a midpoint should give the corner -- that is
    what makes snapping feel like it read your mind rather than your pixels.
    """
    corner = SnapPoint((0.0, 0.0, 0.0), "vertex")
    midpoint = SnapPoint((1.0, 0.0, 0.0), "midpoint")
    positions = {corner.position: (100.0, 100.0), midpoint.position: (96.0, 100.0)}
    picked = nearest(
        [corner, midpoint], lambda p: positions[p], (95.0, 100.0)
    )
    assert picked is corner


def test_nearest_gives_up_when_nothing_is_close():
    corner = SnapPoint((0.0, 0.0, 0.0), "vertex")
    picked = nearest(
        [corner], lambda p: (0.0, 0.0), (500.0, 500.0), radius=18.0
    )
    assert picked is None


def test_distance_between_two_snaps_is_the_real_distance(box):
    corners = [s for s in snap_points(box) if s.kind == "vertex"]
    low = min(corners, key=lambda s: sum(s.position))
    high = max(corners, key=lambda s: sum(s.position))
    measured = math.dist(low.position, high.position)
    assert measured == pytest.approx(math.sqrt(40**2 + 30**2 + 20**2), abs=1e-9)


def test_corners_remember_the_straight_edges_that_meet_there(box):
    origin = next(
        snap for snap in snap_points(box)
        if snap.kind == "vertex" and snap.position == pytest.approx((0.0, 0.0, 0.0))
    )
    assert set(origin.directions) == {
        (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0),
    }


def test_second_point_magnetically_infers_a_world_axis():
    reference = SnapPoint((0.0, 0.0, 0.0), "vertex")
    raw = SnapPoint((10.0, 0.4, 0.0), "surface")
    inferred, lock = inferred_snap(
        reference, raw, lambda point: point[:2], (10.0, 0.4)
    )
    assert inferred.position == pytest.approx((10.0, 0.0, 0.0))
    assert inferred.inference == "X"
    assert lock is not None


def test_second_point_can_follow_a_rotated_geometry_edge():
    diagonal = math.sqrt(0.5)
    reference = SnapPoint(
        (0.0, 0.0, 0.0), "vertex", ((diagonal, diagonal, 0.0),)
    )
    raw = SnapPoint((5.0, 5.3, 0.0), "surface")
    inferred, _lock = inferred_snap(
        reference, raw, lambda point: point[:2], (5.0, 5.3)
    )
    assert inferred.position == pytest.approx((5.15, 5.15, 0.0))
    assert inferred.inference == "Parallel"


def test_named_geometry_beats_alignment_inference():
    reference = SnapPoint((0.0, 0.0, 0.0), "vertex")
    corner = SnapPoint((10.0, 0.4, 0.0), "vertex")
    inferred, lock = inferred_snap(
        reference, corner, lambda point: point[:2], (10.0, 0.4)
    )
    assert inferred is corner
    assert lock is None


def test_inference_has_hysteresis_and_shift_bypasses_it():
    reference = SnapPoint((0.0, 0.0, 0.0), "vertex")
    first = SnapPoint((10.0, 0.4, 0.0), "surface")
    _inferred, lock = inferred_snap(
        reference, first, lambda point: point[:2], (10.0, 0.4)
    )
    drifting = SnapPoint((10.0, 8.0, 0.0), "surface")
    held, lock = inferred_snap(
        reference, drifting, lambda point: point[:2], (10.0, 8.0), locked=lock
    )
    assert held.inference == "X", "the guide must not flicker at its capture edge"

    free, cleared = inferred_snap(
        reference, drifting, lambda point: point[:2], (10.0, 8.0),
        locked=lock, bypass=True,
    )
    assert free is drifting
    assert cleared is None
