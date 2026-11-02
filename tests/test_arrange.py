"""Arrange: rows with equal or fixed gaps, and patterns laid out on a face."""

from __future__ import annotations

import pytest

from simplecad.core.document import BodyRef, Document
from simplecad.core.naming import sub_shapes
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.align import interference_volume, reference_frame
from simplecad.kernel.arrange import (
    ArrangeFeature, arrange_on_face, arrange_shapes, projected_bounds,
)
from simplecad.kernel.detect import analyse_cylinder, analyse_plane
from simplecad.kernel.measure import minimum_distance
from simplecad.kernel.occ import bounding_box, transformed, volume
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


# ----------------------------------------------------------------------
# Patterns on a face
# ----------------------------------------------------------------------
PATTERNS = ("grid", "linear", "radial", "staggered", "packed", "custom")


def cut(shape, *tools):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

    from simplecad.kernel.occ import boolean_builder, built_shape

    for tool in tools:
        shape = built_shape(boolean_builder(BRepAlgoAPI_Cut, shape, tool), "cut")
    return shape


def turned(shape, axis, degrees, pivot=(0.0, 0.0, 0.0)):
    import math

    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

    transform = gp_Trsf()
    transform.SetRotation(gp_Ax1(gp_Pnt(*pivot), gp_Dir(*axis)), math.radians(degrees))
    return transformed(shape, transform)


def ell(size=20.0, arm=6.0, height=8.0, at=(0.0, 0.0, 0.0)):
    x, y, z = at
    return cut(box(size, size, height, at=at), box(size, size, height + 2, at=(x + arm, y + arm, z - 1)))


def star(size=20.0, height=6.0, at=(0.0, 0.0, 0.0)):
    import math

    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Pnt, gp_Vec

    polygon = BRepBuilderAPI_MakePolygon()
    for i in range(10):
        radius = size / 2 if i % 2 == 0 else size / 5
        angle = math.pi / 2 + i * math.pi / 5
        polygon.Add(gp_Pnt(at[0] + radius * math.cos(angle), at[1] + radius * math.sin(angle), at[2]))
    polygon.Close()
    face = BRepBuilderAPI_MakeFace(polygon.Wire()).Face()
    return BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, height)).Shape()


def scattered(shapes):
    """Name the shapes and move them well away from wherever they are going."""
    return [(f"P{i}", transformed(shape, _shift((300 + 40 * i, 200, 50))))
            for i, shape in enumerate(shapes)]


def _shift(vector):
    from simplecad.kernel.align import translation

    return translation(vector)


def assert_placed(target, items, result, *, gap=0.0, touching=True):
    """Seated on the target, nowhere inside it, apart by the gap, unchanged in size."""
    assert result.fits, result.problems
    for name, original in items:
        shape = result.shapes[name]
        assert volume(shape) == pytest.approx(volume(original), rel=1e-6)
        assert interference_volume(target, shape) == pytest.approx(0.0, abs=1e-4)
        if touching:
            assert minimum_distance(target, shape) == pytest.approx(0.0, abs=1e-4)
    names = list(result.shapes)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            assert interference_volume(result.shapes[a], result.shapes[b]) == pytest.approx(0.0, abs=1e-4)
            assert minimum_distance(result.shapes[a], result.shapes[b]) >= gap - 0.02


def within(face, shape, margin=0.0):
    frame = reference_frame(face)
    for axis, (low, high) in ((frame.x_axis, frame.x_bounds), (frame.y_axis, frame.y_bounds)):
        start, end = projected_bounds(shape, axis)
        middle = sum(frame.center[i] * axis[i] for i in range(3))
        if start < middle + low + margin - 1e-3 or end > middle + high - margin + 1e-3:
            return False
    return True


@pytest.mark.parametrize("pattern", PATTERNS)
def test_every_pattern_seats_identical_boxes_inside_a_plate(pattern):
    plate = box(120, 80, 5)
    top = planar_face(plate, (0.0, 0.0, 1.0))
    items = scattered([box(12, 12, 10)] * 6)
    result = arrange_on_face(items, top, {"pattern": pattern, "gap": 3, "margin": 2,
                                          "rows": 2, "columns": 3})
    assert_placed(plate, items, result, gap=3)
    assert all(within(top, shape, margin=2) for shape in result.shapes.values())


def test_mixed_sizes_and_irregular_shapes_share_a_plate():
    plate = box(90, 70, 4)
    top = planar_face(plate, (0.0, 0.0, 1.0))
    items = scattered([box(30, 20, 8), box(10, 10, 15), ell(), star(), cylinder(6, 12), box(18, 8, 5)])
    for pattern in ("packed", "grid"):
        result = arrange_on_face(items, top, {"pattern": pattern, "gap": 2, "margin": 1,
                                              "auto_rotate": True})
        assert_placed(plate, items, result, gap=2)
        assert all(within(top, shape, margin=1) for shape in result.shapes.values())


def test_a_footprint_is_the_outline_not_the_bounding_box():
    from simplecad.kernel.arrange import _FlatTarget, _seat

    top = planar_face(box(100, 100, 5), (0.0, 0.0, 1.0))
    seat = _seat("L", ell(size=20, arm=6), _FlatTarget(top), None, True)
    assert seat.footprint.area == pytest.approx(20 * 20 - 14 * 14, rel=1e-3)
    assert seat.height == pytest.approx(8.0)


def test_an_angled_face_seats_every_object_flat_on_it():
    from simplecad.core.naming import sub_shapes

    plate = turned(box(100, 70, 5), (1.0, 1.0, 0.3), 37.0)
    face = max(sub_shapes(plate, "face"), key=lambda f: (analyse_plane(f).area, analyse_plane(f).normal[2]))
    items = scattered([box(12, 8, 10), cylinder(5, 9), box(10, 10, 4), ell(size=14, arm=4)])
    for pattern in ("grid", "packed", "radial"):
        result = arrange_on_face(items, face, {"pattern": pattern, "gap": 2, "margin": 2})
        assert_placed(plate, items, result, gap=2)
        assert all(within(face, shape, margin=2) for shape in result.shapes.values())


def test_a_wedge_slope_takes_a_row():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeWedge

    from simplecad.core.naming import sub_shapes

    wedge = BRepPrimAPI_MakeWedge(80.0, 30.0, 60.0, 0.0).Shape()
    slope = next(f for f in sub_shapes(wedge, "face")
                 if (info := analyse_plane(f)) is not None and 0.1 < abs(info.normal[1]) < 0.99)
    items = scattered([box(10, 10, 6)] * 4)
    result = arrange_on_face(items, slope, {"pattern": "linear", "gap": 4, "margin": 3,
                                            "spacing": "fixed"})
    assert_placed(wedge, items, result, gap=4)


def test_holes_and_their_margin_are_kept_clear():
    pins = [cylinder(8, 20, at=(30, 30, -5)), cylinder(8, 20, at=(70, 30, -5))]
    plate = cut(box(100, 60, 5), *pins)
    top = planar_face(plate, (0.0, 0.0, 1.0))
    items = scattered([box(8, 8, 6)] * 10)
    for pattern in ("grid", "packed", "staggered"):
        result = arrange_on_face(items, top, {"pattern": pattern, "gap": 2, "margin": 3})
        assert_placed(plate, items, result, gap=2)
        for shape in result.shapes.values():
            for pin in pins:
                assert minimum_distance(pin, shape) >= 3.0 - 0.02


def test_round_and_l_shaped_faces():
    disk = cylinder(40, 6)
    l_plate = ell(size=90, arm=35, height=5)
    items = scattered([box(10, 10, 8)] * 6)
    for body in (disk, l_plate):
        top = planar_face(body, (0.0, 0.0, 1.0))
        for pattern in ("grid", "packed"):
            result = arrange_on_face(items, top, {"pattern": pattern, "gap": 2, "margin": 2})
            assert_placed(body, items, result, gap=2)


def test_objects_wrap_round_a_cylinder_and_line_a_bore():
    from simplecad.core.naming import sub_shapes

    tube = cylinder(20, 80)
    side = next(f for f in sub_shapes(tube, "face") if analyse_cylinder(f) is not None)
    items = scattered([box(10, 10, 10)] * 6)
    result = arrange_on_face(items, side, {"pattern": "grid", "gap": 2, "margin": 1})
    assert_placed(tube, items, result, gap=2)

    pipe = cut(cylinder(60, 80), cylinder(50, 80))
    bore = next(f for f in sub_shapes(pipe, "face")
                if (info := analyse_cylinder(f)) is not None and info.internal)
    result = arrange_on_face(items, bore, {"pattern": "linear", "gap": 2, "margin": 1})
    # A flat base touches a bore at its corners, so it is seated, not floating.
    assert_placed(pipe, items, result, gap=2)


def test_fixed_spacing_is_exact_between_outlines():
    plate = box(200, 40, 5)
    top = planar_face(plate, (0.0, 0.0, 1.0))
    items = scattered([box(10, 10, 5), box(25, 10, 5), box(6, 10, 5), box(14, 10, 5)])
    result = arrange_on_face(items, top, {"pattern": "linear", "spacing": "fixed", "gap": 4.5})
    assert_placed(plate, items, result, gap=4.5)
    extents = sorted(projected_bounds(s, (1.0, 0.0, 0.0)) for s in result.shapes.values())
    assert [extents[i + 1][0] - extents[i][1] for i in range(3)] == pytest.approx([4.5] * 3, abs=1e-5)


def test_automatic_spacing_spreads_to_the_margins():
    plate = box(100, 30, 5)
    top = planar_face(plate, (0.0, 0.0, 1.0))
    items = scattered([box(10, 10, 5)] * 4)
    result = arrange_on_face(items, top, {"pattern": "linear", "spacing": "auto", "gap": 1,
                                          "margin": 5})
    extents = sorted(projected_bounds(s, (1.0, 0.0, 0.0)) for s in result.shapes.values())
    assert extents[0][0] == pytest.approx(5.0, abs=1e-5)
    assert extents[-1][1] == pytest.approx(95.0, abs=1e-5)
    assert result.layout.gap == pytest.approx((90 - 40) / 3, abs=1e-5)


def test_what_cannot_fit_is_reported_with_alternatives():
    plate = box(50, 50, 5)
    top = planar_face(plate, (0.0, 0.0, 1.0))
    items = scattered([box(10, 10, 5)] * 16)
    options = {"pattern": "grid", "spacing": "fixed", "gap": 5, "margin": 1}
    result = arrange_on_face(items, top, options, suggest=True)
    assert not result.fits
    assert "collide" in result.summary or "outside" in result.summary
    assert result.suggestions
    for _label, patch in result.suggestions:
        assert arrange_on_face(items, top, {**options, **patch}).fits


def test_a_manual_boundary_keeps_its_margin_only_from_picked_edges():
    from simplecad.core.naming import sub_shapes

    plate = box(100, 60, 5)
    top = planar_face(plate, (0.0, 0.0, 1.0))
    left = next(e for e in sub_shapes(top, "edge")
                if bounding_box(e)[1][0] < 1e-6)
    items = scattered([box(10, 10, 5)] * 3)
    options = {"pattern": "linear", "spacing": "fixed", "gap": 2, "align": "left",
               "boundary": "manual", "margin": 10}
    flush = arrange_on_face(items, top, options)
    assert min(projected_bounds(s, (1, 0, 0))[0] for s in flush.shapes.values()) == pytest.approx(0.0, abs=1e-5)
    kept = arrange_on_face(items, top, options, edges=[left])
    assert min(projected_bounds(s, (1, 0, 0))[0] for s in kept.shapes.values()) >= 10.0 - 1e-3
    inset = arrange_on_face(items, top, {**options, "inset_left": 20, "inset_bottom": 30})
    assert min(projected_bounds(s, (1, 0, 0))[0] for s in inset.shapes.values()) == pytest.approx(20.0, abs=1e-5)
    assert min(projected_bounds(s, (0, 1, 0))[0] for s in inset.shapes.values()) >= 30.0 - 1e-5


def test_other_objects_on_the_face_are_kept_clear():
    plate = box(100, 100, 5)
    top = planar_face(plate, (0.0, 0.0, 1.0))
    standing = box(30, 30, 20, at=(35, 35, 5))
    items = scattered([box(10, 10, 6)] * 8)
    result = arrange_on_face(items, top, {"pattern": "grid", "gap": 3, "margin": 2},
                             others=[("Post", standing)])
    assert_placed(plate, items, result, gap=3)
    assert all(minimum_distance(standing, s) >= 3.0 - 0.02 for s in result.shapes.values())
    ignored = arrange_on_face(items, top, {"pattern": "grid", "gap": 3, "margin": 2,
                                           "avoid_bodies": False}, others=[("Post", standing)])
    assert any(interference_volume(standing, s) > 1 for s in ignored.shapes.values())


def test_a_picked_contact_face_is_the_one_stood_on():
    plate = box(100, 100, 5)
    top = planar_face(plate, (0.0, 0.0, 1.0))
    tall = box(10, 20, 30, at=(200, 0, 0))
    end = planar_face(tall, (-1.0, 0.0, 0.0))            # the 20 x 30 side
    result = arrange_on_face([("Tall", tall)], top, {"pattern": "grid"}, contacts={"Tall": end})
    low, high = bounding_box(result.shapes["Tall"])
    assert high[2] - low[2] == pytest.approx(10.0, abs=1e-5)
    default = arrange_on_face([("Tall", tall)], top, {"pattern": "grid"})
    low, high = bounding_box(default.shapes["Tall"])
    assert high[2] - low[2] == pytest.approx(10.0, abs=1e-5)  # largest face already
    small = planar_face(tall, (0.0, 0.0, -1.0))           # the 10 x 20 end
    upright = arrange_on_face([("Tall", tall)], top, {"pattern": "grid"}, contacts={"Tall": small})
    low, high = bounding_box(upright.shapes["Tall"])
    assert high[2] - low[2] == pytest.approx(30.0, abs=1e-5)


def test_keeping_orientation_turns_nothing():
    plate = box(100, 100, 5)
    top = planar_face(plate, (0.0, 0.0, 1.0))
    tilted = turned(box(10, 20, 30, at=(200, 0, 0)), (1.0, 0.0, 0.0), 20.0, pivot=(200, 0, 0))
    result = arrange_on_face([("Tilted", tilted)], top, {"pattern": "grid", "orient": False})
    before, after = bounding_box(tilted), bounding_box(result.shapes["Tilted"])
    assert [after[1][i] - after[0][i] for i in range(3)] == pytest.approx(
        [before[1][i] - before[0][i] for i in range(3)], abs=1e-5)
    assert after[0][2] == pytest.approx(5.0, abs=1e-5)


def test_a_hundred_objects_arrange_quickly():
    import time

    plate = box(300, 300, 5)
    top = planar_face(plate, (0.0, 0.0, 1.0))
    items = scattered([box(12, 12, 6), cylinder(6, 8)] * 50)
    started = time.perf_counter()
    result = arrange_on_face(items, top, {"pattern": "grid", "gap": 3, "margin": 2})
    first = time.perf_counter() - started
    started = time.perf_counter()
    arrange_on_face(items, top, {"pattern": "packed", "gap": 3, "margin": 2})
    again = time.perf_counter() - started
    assert result.fits
    assert first < 15.0, f"first layout took {first:.1f} s"
    assert again < 5.0, f"a re-layout with cached outlines took {again:.1f} s"


# ----------------------------------------------------------------------
# The feature: rebuild, undo/redo, editing, saving, upstream changes
# ----------------------------------------------------------------------
def _project():
    from simplecad.core.naming import make_ref

    document = Document("Tray")
    document.add_feature(BoxFeature(inputs={"width": 100, "depth": 60, "height": 5},
                                    outputs=["Plate"]))
    for index in range(5):
        document.add_feature(BoxFeature(
            inputs={"width": 10, "depth": "part", "height": 8, "x": 150 + 20 * index, "z": 20},
            outputs=[f"Part{index}"]))
    document.parameters.set("part", "10")
    builder = Rebuilder(document)
    assert builder.rebuild().ok
    plate = document.body("Plate").shape
    ref = make_ref(plate, planar_face(plate, (0.0, 0.0, 1.0)),
                   document.producer_of("Plate").id, kind="face", body="Plate")
    return document, builder, ref


def _pattern(ref, **extra):
    names = [f"Part{i}" for i in range(5)]
    inputs = {"bodies": [BodyRef(n) for n in names], "target_face": ref, "pattern": "grid",
              "spacing": "auto", "gap": "3", "margin": "2", "frame_mode": "reference", **extra}
    return ArrangeFeature(inputs=inputs, outputs=names)


def _positions(document):
    return {name: bounding_box(document.body(name).shape)[0] for name in
            (f"Part{i}" for i in range(5))}


def _assert_same_positions(actual, expected):
    # pytest >= 9.2 refuses approx() on a dict of tuples; compare each corner.
    assert actual.keys() == expected.keys()
    for name, low in expected.items():
        assert actual[name] == pytest.approx(low)


def test_the_feature_rebuilds_and_survives_save_and_reopen(tmp_path):
    from simplecad.core.project import load, save

    document, builder, ref = _project()
    document.add_feature(_pattern(ref, pattern="staggered"))
    report = builder.rebuild()
    assert report.ok, report.summary()
    placed = _positions(document)
    assert all(low[2] == pytest.approx(5.0, abs=1e-5) for low in placed.values())
    assert set(document.bodies) == {"Plate", *placed}
    reopened, _cached = load(save(document, str(tmp_path / "tray.scad3")))
    assert Rebuilder(reopened).rebuild().ok
    again = {name: bounding_box(reopened.body(name).shape)[0] for name in placed}
    for name in placed:
        assert again[name] == pytest.approx(placed[name], abs=1e-6)


def test_undo_redo_and_editing_are_single_steps():
    from simplecad.core.history import History

    document, builder, ref = _project()
    history = History(document)
    before = _positions(document)

    history.record("Arrange")
    feature = document.add_feature(_pattern(ref))
    assert builder.rebuild().ok
    grid = _positions(document)
    assert grid != before

    builder.invalidate(history.undo())
    assert builder.rebuild().ok
    _assert_same_positions(_positions(document), before)
    builder.invalidate(history.redo())
    assert builder.rebuild().ok
    _assert_same_positions(_positions(document), grid)

    # Edit: the same feature, new settings, one undoable step.
    feature = next(f for f in document.features if f.type_name == "arrange")
    history.record("Edit arrangement")
    edited = _pattern(ref, pattern="linear")
    edited.id, edited.name = feature.id, feature.name
    document.features[document.index_of(feature.id)] = edited
    builder.invalidate({feature.id})
    assert builder.rebuild().ok
    row = _positions(document)
    assert len({round(low[1], 6) for low in row.values()}) == 1
    builder.invalidate(history.undo())
    assert builder.rebuild().ok
    _assert_same_positions(_positions(document), grid)


def test_an_upstream_change_that_no_longer_fits_still_builds_and_says_so():
    document, builder, ref = _project()
    document.add_feature(_pattern(ref, spacing="fixed", gap="3"))
    report = builder.rebuild()
    assert report.ok and not any("no longer fits" in w for w in report.warnings)
    document.parameters.set("part", "70")          # longer than the plate is deep
    builder.invalidate({f.id for f in document.features})
    report = builder.rebuild()
    assert report.ok, report.summary()
    assert any("no longer fits" in w for w in report.warnings)


def test_the_preview_shows_problems_and_offers_alternatives():
    from simplecad.core.geometry_service import build_preview_result

    document, builder, ref = _project()
    document.parameters.set("part", "30")
    builder.invalidate({f.id for f in document.features})
    assert builder.rebuild().ok
    document._rebuilder = builder
    # Five 10 mm parts in one row with 15 mm gaps need 110 mm of a 96 mm face.
    state = _pattern(ref, spacing="fixed", gap="15", rows=1, columns=5, pattern="custom").to_dict()
    result = build_preview_result(document, state)
    assert result["error"] is None
    notes = result["annotations"]
    assert notes["fits"] is False and notes["problems"]
    assert notes["suggestions"]
    assert {"result", "ok", "problem", "boundary"} <= set(result["parts"]) | {"ok"}
    assert result["parts"]["problem"] is not None


def test_patterns_need_a_target_face():
    from simplecad.core.errors import CadError

    document, builder, _ref = _project()
    document.add_feature(ArrangeFeature(
        inputs={"bodies": [BodyRef("Part0"), BodyRef("Part1")], "pattern": "grid"},
        outputs=["Part0", "Part1"]))
    report = builder.rebuild()
    assert not report.ok
    assert "face" in report.summary().lower()
    with pytest.raises(CadError, match="flat or cylindrical"):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere

        from simplecad.core.naming import sub_shapes

        ball = sub_shapes(BRepPrimAPI_MakeSphere(10.0).Shape(), "face")[0]
        arrange_on_face([("A", box(5))], ball, {"pattern": "grid"})


def test_a_row_on_a_face_warns_when_it_runs_past_the_edge():
    document = Document("Row")
    document.add_feature(BoxFeature(inputs={"width": 40, "depth": 40, "height": 5}, outputs=["Plate"]))
    for index in range(3):
        document.add_feature(BoxFeature(inputs={"width": 20, "depth": 10, "height": 5,
                                                "x": 100 + 30 * index, "z": 20},
                                        outputs=[f"R{index}"]))
    builder = Rebuilder(document)
    assert builder.rebuild().ok
    from simplecad.core.naming import make_ref

    plate = document.body("Plate").shape
    ref = make_ref(plate, planar_face(plate, (0, 0, 1)), document.producer_of("Plate").id,
                   kind="face", body="Plate")
    document.add_feature(ArrangeFeature(inputs={
        "bodies": [BodyRef(f"R{i}") for i in range(3)], "target_face": ref,
        "mode": "fixed_gap", "gap": "5", "axis": "x", "frame_mode": "reference"},
        outputs=[f"R{i}" for i in range(3)]))
    report = builder.rebuild()
    assert report.ok
    assert any("past the edge" in w for w in report.warnings)


def test_arrange_and_edit_are_offered_for_the_right_selections():
    from types import SimpleNamespace

    from simplecad.ui.selection import Picked, _arrangeable, arrangement_for

    document, builder, ref = _project()

    def model(*picks):
        return SimpleNamespace(
            document=document, picks=list(picks),
            bodies=list(dict.fromkeys(p.body for p in picks)),
            faces=lambda: [p for p in picks if p.kind == "face"])

    a, b = (Picked(body=f"Part{i}", kind="body", shape=document.body(f"Part{i}").shape)
            for i in range(2))
    plate = document.body("Plate").shape
    top = planar_face(plate, (0.0, 0.0, 1.0))
    face = Picked(body="Plate", kind="face", shape=top, info=analyse_plane(top))
    edge = Picked(body="Plate", kind="edge", shape=sub_shapes(top, "edge")[0])
    assert _arrangeable(model(a, b))
    assert _arrangeable(model(a, face))               # one object and where it goes
    assert not _arrangeable(model(a))
    assert not _arrangeable(model(a, b, edge))
    assert arrangement_for(model(a)) is None
    document.add_feature(_pattern(ref))
    assert builder.rebuild().ok
    assert arrangement_for(model(a)).type_name == "arrange"
