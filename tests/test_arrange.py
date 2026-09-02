"""Equal-gap and fixed-gap placement of independent bodies."""

from __future__ import annotations

import pytest

from simplecad.core.document import BodyRef, Document
from simplecad.core.naming import sub_shapes
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.arrange import ArrangeFeature, arrange_shapes, projected_bounds
from simplecad.kernel.detect import analyse_cylinder, analyse_plane
from simplecad.kernel.primitives import BoxFeature


def box(dx, dy=10.0, dz=10.0, at=(0.0, 0.0, 0.0)):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    return BRepPrimAPI_MakeBox(gp_Pnt(*at), dx, dy, dz).Shape()


def cylinder(radius, height, at=(0.0, 0.0, 0.0)):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    return BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(*at), gp_Dir(0, 0, 1)), radius, height
    ).Shape()


def planar_face(shape, normal):
    for face in sub_shapes(shape, "face"):
        info = analyse_plane(face)
        if info is not None and info.normal == pytest.approx(normal):
            return face
    raise AssertionError(f"No face with normal {normal}")


def gaps(result, axis=(1.0, 0.0, 0.0)):
    extents = [projected_bounds(result.shapes[name], axis) for name in result.order]
    return [extents[index + 1][0] - extents[index][1]
            for index in range(len(extents) - 1)]


def test_equal_gap_uses_clear_edges_and_keeps_the_outer_span():
    items = [
        ("A", box(10, at=(0, 0, 0))),
        ("B", box(20, at=(40, 20, 0))),
        ("C", box(10, at=(100, 40, 0))),
    ]
    result = arrange_shapes(items, mode="equal_gaps", axis="x")
    assert gaps(result) == pytest.approx([35.0, 35.0], abs=1e-5)
    assert projected_bounds(result.shapes["A"], (1, 0, 0))[0] == pytest.approx(0.0, abs=1e-5)
    assert projected_bounds(result.shapes["C"], (1, 0, 0))[1] == pytest.approx(110.0, abs=1e-5)
    y_centers = [sum(projected_bounds(shape, (0, 1, 0))) / 2
                 for shape in result.shapes.values()]
    assert y_centers == pytest.approx([25.0, 25.0, 25.0], abs=1e-5)


def test_fixed_gap_keeps_the_leading_edge_and_spreads_every_object():
    items = [
        ("A", box(10, at=(5, 0, 0))),
        ("B", box(20, at=(25, 0, 0))),
        ("C", box(8, at=(60, 0, 0))),
    ]
    result = arrange_shapes(items, mode="fixed_gap", axis="x", gap=12.5)
    assert gaps(result) == pytest.approx([12.5, 12.5], abs=1e-5)
    assert projected_bounds(result.shapes["A"], (1, 0, 0))[0] == pytest.approx(5.0, abs=1e-5)


def test_equal_gap_refuses_an_outer_span_too_small_for_the_objects():
    from simplecad.core.errors import CadError

    with pytest.raises(CadError, match="too close"):
        arrange_shapes(
            [("A", box(20)), ("B", box(20, at=(5, 0, 0))),
             ("C", box(20, at=(10, 0, 0)))],
            mode="equal_gaps",
        )


def test_a_target_face_seats_and_orients_cylinders_on_a_vertical_wall():
    target = box(40, 60, 50)
    face = planar_face(target, (1.0, 0.0, 0.0))
    result = arrange_shapes(
        [("A", cylinder(4, 15, at=(80, 0, 0))),
         ("B", cylinder(4, 15, at=(80, 25, 0))),
         ("C", cylinder(4, 15, at=(80, 50, 0)))],
        mode="fixed_gap",
        axis="y",
        gap=6,
        target_face=face,
        orient=True,
    )
    for shape in result.shapes.values():
        round_face = next(f for f in sub_shapes(shape, "face")
                          if analyse_cylinder(f) is not None)
        direction = analyse_cylinder(round_face).direction
        assert abs(direction[0]) == pytest.approx(1.0, abs=1e-6)
        assert projected_bounds(shape, (1, 0, 0))[0] == pytest.approx(40.0, abs=1e-5)


def test_arrange_feature_keeps_bodies_separate_and_serializable():
    document = Document("Row")
    for index, x in enumerate((0, 40, 100)):
        name = chr(ord("A") + index)
        document.add_feature(
            BoxFeature(
                inputs={"width": 10, "depth": 10, "height": 10, "x": x},
                outputs=[name],
            )
        )
    builder = Rebuilder(document)
    assert builder.rebuild().ok
    feature = document.add_feature(
        ArrangeFeature(
            inputs={
                "bodies": [BodyRef("A"), BodyRef("B"), BodyRef("C")],
                "mode": "fixed_gap",
                "axis": "x",
                "gap": "12",
            },
            outputs=["A", "B", "C"],
        )
    )
    assert builder.rebuild().ok
    assert set(document.bodies) == {"A", "B", "C"}
    assert feature.to_dict()["type"] == "arrange"
    result_gaps = [
        projected_bounds(document.bodies[name].shape, (1, 0, 0))
        for name in ("A", "B", "C")
    ]
    assert result_gaps[1][0] - result_gaps[0][1] == pytest.approx(12.0, abs=1e-5)
    assert result_gaps[2][0] - result_gaps[1][1] == pytest.approx(12.0, abs=1e-5)
