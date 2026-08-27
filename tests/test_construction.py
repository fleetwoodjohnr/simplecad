"""Construction geometry, and sketching on a face."""

from __future__ import annotations

import math

import pytest

from simplecad.core.document import BodyRef, Document
from simplecad.core.naming import fingerprint, make_ref, sub_shapes
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel import construction  # noqa: F401 - registers the features
from simplecad.kernel.construction import (
    AxisFeature, MidplaneFeature, OffsetPlaneFeature, PlaneAtAngleFeature,
    PlaneThroughPointsFeature, PointFeature, TangentPlaneFeature, plane_from_face,
)
from simplecad.kernel.detect import analyse_plane
from simplecad.kernel.primitives import BoxFeature, CylinderFeature


def built(document):
    report = Rebuilder(document).rebuild()
    assert report.ok, report.summary()
    return document


def result_of(document, feature):
    return document.bodies.get(f"__construction__{feature.name}") or _from_context(
        document, feature
    )


def _from_context(document, feature):
    """Construction results are internal, so read them from a fresh rebuild."""
    from simplecad.core.document import BuildContext

    builder = Rebuilder(document)
    builder.rebuild()
    context = BuildContext(document)
    for other in document.topological_order():
        outputs = other.execute(context)
        context.bodies.update(outputs)
        if other is feature:
            return next(iter(outputs.values()))
    return None


@pytest.fixture
def plate():
    document = Document("T")
    feature = document.add_feature(
        BoxFeature(inputs={"width": 40, "depth": 30, "height": 20}, outputs=["Plate"])
    )
    built(document)
    return document, feature


def top_face(document, name="Plate"):
    shape = document.bodies[name].shape
    return max(sub_shapes(shape, "face"), key=lambda f: fingerprint(f, "face").center[2])


# ----------------------------------------------------------------------
def test_an_offset_plane_sits_the_right_distance_from_its_face(plate):
    document, box = plate
    face = top_face(document)
    feature = document.add_feature(
        OffsetPlaneFeature(
            inputs={
                "face": make_ref(document.bodies["Plate"].shape, face, box.id,
                                 body="Plate"),
                "distance": 15,
            }
        )
    )
    plane = _from_context(document, feature)
    assert plane.origin[2] == pytest.approx(35.0)      # 20 high + 15 offset
    assert plane.normal == pytest.approx((0.0, 0.0, 1.0))
    assert "15 mm" in feature.message


def test_a_negative_offset_goes_the_other_way(plate):
    document, box = plate
    face = top_face(document)
    feature = document.add_feature(
        OffsetPlaneFeature(
            inputs={
                "face": make_ref(document.bodies["Plate"].shape, face, box.id,
                                 body="Plate"),
                "distance": -5,
            }
        )
    )
    assert _from_context(document, feature).origin[2] == pytest.approx(15.0)


def test_a_midplane_sits_halfway_between_two_faces(plate):
    document, box = plate
    shape = document.bodies["Plate"].shape
    top = top_face(document)
    bottom = min(
        sub_shapes(shape, "face"), key=lambda f: fingerprint(f, "face").center[2]
    )
    feature = document.add_feature(
        MidplaneFeature(
            inputs={
                "faces": [
                    make_ref(shape, top, box.id, body="Plate"),
                    make_ref(shape, bottom, box.id, body="Plate"),
                ]
            }
        )
    )
    plane = _from_context(document, feature)
    assert plane.origin[2] == pytest.approx(10.0)
    assert "20.00 mm" in feature.message


def test_a_plane_at_an_angle_tilts_away_from_its_face(plate):
    document, box = plate
    shape = document.bodies["Plate"].shape
    top = top_face(document)
    edge = next(
        e for e in sub_shapes(shape, "edge")
        if (p := fingerprint(e, "edge")).geometry == "line"
        and abs(p.center[2] - 20.0) < 1e-6
        and p.direction == pytest.approx((1.0, 0.0, 0.0))
    )
    feature = document.add_feature(
        PlaneAtAngleFeature(
            inputs={
                "face": make_ref(shape, top, box.id, body="Plate"),
                "edge": make_ref(shape, edge, box.id, body="Plate"),
                "angle": 30,
            }
        )
    )
    plane = _from_context(document, feature)
    # Tilted 30 degrees from vertical, so the normal's Z is cos(30).
    assert abs(plane.normal[2]) == pytest.approx(math.cos(math.radians(30)), abs=1e-6)
    assert "30" in feature.message


def test_a_plane_through_three_points():
    document = Document("T")
    feature = document.add_feature(
        PlaneThroughPointsFeature(
            inputs={"points": [(0, 0, 0), (10, 0, 0), (0, 10, 0)]}
        )
    )
    plane = _from_context(document, feature)
    assert plane.normal == pytest.approx((0.0, 0.0, 1.0))


def test_three_collinear_points_are_refused_clearly():
    from simplecad.core.errors import CadError

    document = Document("T")
    document.add_feature(
        PlaneThroughPointsFeature(
            inputs={"points": [(0, 0, 0), (5, 0, 0), (10, 0, 0)]}
        )
    )
    report = Rebuilder(document).rebuild()
    assert not report.ok
    assert "in a line" in report.summary()


def test_a_tangent_plane_touches_the_cylinder():
    document = Document("T")
    post = document.add_feature(
        CylinderFeature(inputs={"radius": 8, "height": 20}, outputs=["Post"])
    )
    built(document)
    from simplecad.kernel.detect import cylindrical_faces

    face = cylindrical_faces(document.bodies["Post"].shape)[0][0]
    feature = document.add_feature(
        TangentPlaneFeature(
            inputs={
                "face": make_ref(document.bodies["Post"].shape, face, post.id,
                                 body="Post"),
                "around": 0,
            }
        )
    )
    plane = _from_context(document, feature)
    # The origin sits one radius out from the axis.
    assert math.hypot(plane.origin[0], plane.origin[1]) == pytest.approx(8.0, abs=1e-6)
    assert "⌀16.00" in feature.message


def test_an_axis_runs_down_a_cylinder():
    document = Document("T")
    post = document.add_feature(
        CylinderFeature(inputs={"radius": 5, "height": 30, "x": 12}, outputs=["Post"])
    )
    built(document)
    from simplecad.kernel.detect import cylindrical_faces

    face = cylindrical_faces(document.bodies["Post"].shape)[0][0]
    feature = document.add_feature(
        AxisFeature(
            inputs={
                "face": make_ref(document.bodies["Post"].shape, face, post.id,
                                 body="Post")
            }
        )
    )
    axis = _from_context(document, feature)
    assert axis.direction == pytest.approx((0.0, 0.0, 1.0))
    assert axis.origin[0] == pytest.approx(12.0)


def test_a_point_can_be_placed_at_a_coordinate():
    document = Document("T")
    feature = document.add_feature(
        PointFeature(inputs={"x": 3, "y": -4, "z": 5})
    )
    point = _from_context(document, feature)
    assert point.position == pytest.approx((3.0, -4.0, 5.0))
    assert "3.00, -4.00, 5.00" == feature.message


def test_an_axis_with_nothing_selected_says_what_it_needs():
    document = Document("T")
    document.add_feature(AxisFeature(inputs={}))
    report = Rebuilder(document).rebuild()
    assert not report.ok
    assert "round face or a straight edge" in report.summary()


# ----------------------------------------------------------------------
def test_a_sketch_plane_can_be_taken_from_a_face(plate):
    """This is what makes "sketch on that face" work."""
    document, _box = plate
    face = top_face(document)
    plane = plane_from_face(face, "Top")

    assert plane.origin == pytest.approx((20.0, 15.0, 20.0))
    assert plane.normal == pytest.approx((0.0, 0.0, 1.0))

    sketch_plane = plane.as_sketch_plane()
    # The sketch origin lands on the face, and its axes lie in it.
    assert sketch_plane.to_3d(0, 0) == pytest.approx((20.0, 15.0, 20.0))
    assert sketch_plane.to_3d(5, 0)[2] == pytest.approx(20.0)
    assert sketch_plane.to_3d(0, 5)[2] == pytest.approx(20.0)


def test_sketching_on_a_curved_face_is_refused_clearly():
    from simplecad.core.errors import CadError

    document = Document("T")
    document.add_feature(
        CylinderFeature(inputs={"radius": 5, "height": 10}, outputs=["Post"])
    )
    built(document)
    from simplecad.kernel.detect import cylindrical_faces

    face = cylindrical_faces(document.bodies["Post"].shape)[0][0]
    with pytest.raises(CadError) as caught:
        plane_from_face(face)
    assert "flat faces" in str(caught.value)


def test_a_sketch_on_a_face_extrudes_from_it(plate):
    """The whole point: draw on a face, and the solid grows out of it."""
    from simplecad.kernel.occ import bounding_box
    from simplecad.kernel.sketch_features import ExtrudeFeature, SketchFeature
    from simplecad.sketch.sketch import Sketch

    document, _box = plate
    plane = plane_from_face(top_face(document)).as_sketch_plane()

    sketch = Sketch("OnFace", plane)
    sketch.add_rectangle(-5, -5, 5, 5)
    feature = document.add_feature(SketchFeature(inputs={"sketch": sketch}))
    document.add_feature(
        ExtrudeFeature(
            inputs={"sketch": feature.name, "distance": 8}, outputs=["Boss"]
        )
    )
    built(document)

    low, high = bounding_box(document.bodies["Boss"].shape)
    assert low[2] == pytest.approx(20.0, abs=1e-6), "starts on the face"
    assert high[2] == pytest.approx(28.0, abs=1e-6), "and grows away from it"
