"""The naming layer is what makes the parametric history trustworthy.

Every test here mutates a shape the way a user editing a parameter would, then
checks that a reference taken before the edit still points at the *same* face or
edge afterwards -- not merely at *a* face.
"""

from __future__ import annotations

import math

import pytest

from simplecad.core.errors import ReferenceLost
from simplecad.core.naming import (
    Fingerprint, SubShapeRef, fingerprint, make_ref, resolve, similarity,
    sub_shapes, try_resolve,
)


def box(dx: float, dy: float, dz: float):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    return BRepPrimAPI_MakeBox(dx, dy, dz).Shape()


def cylinder(radius: float, height: float):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

    return BRepPrimAPI_MakeCylinder(radius, height).Shape()


def face_center(face) -> tuple[float, float, float]:
    return fingerprint(face, "face").center


def top_face(shape, height: float):
    """The face whose centre sits at z == height."""
    for face in sub_shapes(shape, "face"):
        if abs(face_center(face)[2] - height) < 1e-6:
            return face
    raise AssertionError("no top face found")


# ----------------------------------------------------------------------
def test_sub_shapes_counts_box_topology():
    shape = box(10, 20, 30)
    assert len(sub_shapes(shape, "face")) == 6
    assert len(sub_shapes(shape, "edge")) == 12
    assert len(sub_shapes(shape, "vertex")) == 8


def test_fingerprint_describes_a_plane():
    shape = box(10, 20, 30)
    print = fingerprint(top_face(shape, 30), "face")
    assert print.kind == "face"
    assert print.geometry == "plane"
    assert print.measure == pytest.approx(200.0)
    assert print.center == pytest.approx((5.0, 10.0, 30.0))
    assert print.direction == pytest.approx((0.0, 0.0, 1.0))


def test_fingerprint_describes_a_cylinder():
    prints = [fingerprint(f, "face") for f in sub_shapes(cylinder(5, 20), "face")]
    lateral = [p for p in prints if p.geometry == "cylinder"]
    assert len(lateral) == 1
    assert lateral[0].radius == pytest.approx(5.0)


def test_similarity_rejects_a_different_geometry_type():
    plane = Fingerprint("face", "plane", 100.0, (0, 0, 0), (0, 0, 1))
    cyl = Fingerprint("face", "cylinder", 100.0, (0, 0, 0), (0, 0, 1), 4.0)
    assert similarity(plane, cyl) == 0.0


def test_reference_survives_a_parameter_change():
    """The whole point: resize the base box, keep pointing at the top face."""
    original = box(60, 40, 20)
    ref = make_ref(original, top_face(original, 20), "Box1", role="box:+Z")

    widened = box(80, 40, 20)
    found = resolve(ref, widened)

    assert fingerprint(found, "face").center[2] == pytest.approx(20.0)
    assert fingerprint(found, "face").measure == pytest.approx(80 * 40)


def test_reference_survives_a_height_change():
    original = box(60, 40, 20)
    ref = make_ref(original, top_face(original, 20), "Box1")

    taller = box(60, 40, 35)
    found = resolve(ref, taller)
    assert fingerprint(found, "face").center[2] == pytest.approx(35.0)


def test_reference_does_not_drift_to_the_wrong_parallel_face():
    """Top and bottom faces are parallel and equal in area -- the classic trap."""
    original = box(60, 40, 20)
    top_ref = make_ref(original, top_face(original, 20), "Box1")
    bottom_ref = make_ref(original, top_face(original, 0), "Box1")

    taller = box(60, 40, 26)
    assert fingerprint(resolve(top_ref, taller), "face").center[2] == pytest.approx(26.0)
    assert fingerprint(resolve(bottom_ref, taller), "face").center[2] == pytest.approx(0.0)


def test_edge_reference_uses_adjacent_faces_to_disambiguate():
    """A box's four vertical edges are identical but for their neighbours."""
    original = box(60, 40, 20)
    edges = [
        e for e in sub_shapes(original, "edge")
        if fingerprint(e, "edge").direction == pytest.approx((0.0, 0.0, 1.0))
    ]
    assert len(edges) == 4
    chosen = min(edges, key=lambda e: fingerprint(e, "edge").center[:2])
    ref = make_ref(original, chosen, "Box1")

    taller = box(60, 40, 30)
    found = resolve(ref, taller)
    found_center = fingerprint(found, "edge").center
    assert found_center[0] == pytest.approx(0.0)
    assert found_center[1] == pytest.approx(0.0)
    assert fingerprint(found, "edge").measure == pytest.approx(30.0)


def test_reference_is_lost_rather_than_wrong():
    """A cylinder has no planar side face; refuse to substitute one."""
    original = box(60, 40, 20)
    side = next(
        f for f in sub_shapes(original, "face")
        if fingerprint(f, "face").direction == pytest.approx((1.0, 0.0, 0.0))
    )
    ref = make_ref(original, side, "Box1")

    with pytest.raises(ReferenceLost):
        resolve(ref, cylinder(5, 20))


def test_try_resolve_returns_none_instead_of_raising():
    original = box(60, 40, 20)
    ref = make_ref(original, top_face(original, 20), "Box1")
    assert try_resolve(ref, cylinder(3, 3)) is None


def test_ref_round_trips_through_json():
    import json

    original = box(60, 40, 20)
    ref = make_ref(original, top_face(original, 20), "Box1", role="box:+Z")
    restored = SubShapeRef.from_dict(json.loads(json.dumps(ref.to_dict())))

    assert restored == ref
    assert fingerprint(resolve(restored, box(60, 40, 20)), "face").center[2] == 20.0
