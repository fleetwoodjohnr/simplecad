"""Sweep, Loft, Draft, Mirror and the patterns."""

from __future__ import annotations

import math

import pytest

from simplecad.core.document import BodyRef, Document
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel import advanced, sketch_features  # noqa: F401 - registers
from simplecad.kernel.advanced import (
    CircularPatternFeature, DraftFeature, LoftFeature, MirrorFeature,
    PathPatternFeature, RectangularPatternFeature, SweepFeature,
)
from simplecad.kernel.occ import bounding_box, is_valid, volume
from simplecad.kernel.primitives import BoxFeature, CylinderFeature
from simplecad.kernel.sketch_features import SketchFeature
from simplecad.sketch.sketch import Sketch, SketchPlane


def build(document):
    report = Rebuilder(document).rebuild()
    assert report.ok, report.summary()
    return document


def circle_sketch(name, radius, plane="XY", at=(0.0, 0.0, 0.0)):
    sketch = Sketch(name, SketchPlane(origin=at, **_axes(plane)))
    centre = sketch.add_point(0, 0)
    circle = sketch.add_circle(centre, radius)
    sketch.constrain("fix", [centre.id])
    sketch.constrain("radius", [circle.id], radius)
    return sketch


def _axes(plane: str) -> dict:
    base = SketchPlane.named(plane)
    return {"x_axis": base.x_axis, "normal": base.normal}


# ----------------------------------------------------------------------
def test_sweeping_a_circle_along_a_line_makes_a_rod():
    document = Document("T")
    profile = document.add_feature(
        SketchFeature(inputs={"sketch": circle_sketch("Profile", 5.0)})
    )
    path = Sketch("Path", SketchPlane.named("XZ"))
    start, end = path.add_point(0, 0), path.add_point(0, 40)
    path.add_line(start, end)
    path_feature = document.add_feature(SketchFeature(inputs={"sketch": path}))

    document.add_feature(
        SweepFeature(
            inputs={"profile": profile.name, "path": path_feature.name},
            outputs=["Rod"],
        )
    )
    build(document)
    shape = document.bodies["Rod"].shape
    assert is_valid(shape)
    assert volume(shape) == pytest.approx(math.pi * 25 * 40, rel=0.02)


def test_lofting_between_two_circles_makes_a_cone_frustum():
    document = Document("T")
    big = document.add_feature(
        SketchFeature(inputs={"sketch": circle_sketch("Big", 10.0)})
    )
    small = document.add_feature(
        SketchFeature(
            inputs={"sketch": circle_sketch("Small", 4.0, at=(0.0, 0.0, 20.0))}
        )
    )
    document.add_feature(
        LoftFeature(
            inputs={"sketches": [big.name, small.name]}, outputs=["Frustum"]
        )
    )
    build(document)
    shape = document.bodies["Frustum"].shape
    assert is_valid(shape)
    # Frustum volume: pi*h/3 * (R^2 + Rr + r^2)
    expected = math.pi * 20 / 3 * (100 + 40 + 16)
    assert volume(shape) == pytest.approx(expected, rel=0.02)


def test_a_loft_needs_at_least_two_profiles():
    from simplecad.core.errors import CadError

    document = Document("T")
    only = document.add_feature(
        SketchFeature(inputs={"sketch": circle_sketch("Only", 5.0)})
    )
    document.add_feature(LoftFeature(inputs={"sketches": [only.name]}))
    report = Rebuilder(document).rebuild()
    assert not report.ok
    assert "at least two" in report.summary()


def test_draft_tapers_a_face_and_reduces_the_top():
    from simplecad.core.naming import fingerprint, make_ref, sub_shapes

    document = Document("T")
    box = document.add_feature(
        BoxFeature(inputs={"width": 40, "depth": 40, "height": 20}, outputs=["Block"])
    )
    build(document)
    shape = document.bodies["Block"].shape
    side = next(
        f for f in sub_shapes(shape, "face")
        if (p := fingerprint(f, "face")).direction
        and p.direction == pytest.approx((1.0, 0.0, 0.0))
    )
    before = volume(shape)

    document.add_feature(
        DraftFeature(
            inputs={
                "body": BodyRef("Block"),
                "faces": [make_ref(shape, side, box.id, body="Block")],
                "angle": 10,
                "pull_direction": (0.0, 0.0, 1.0),
                "neutral_plane": (0.0, 0.0, 0.0),
            },
            outputs=["Block"],
        )
    )
    build(document)
    after = document.bodies["Block"].shape
    assert is_valid(after)
    assert volume(after) != pytest.approx(before, rel=1e-6), "draft must change the shape"


# ----------------------------------------------------------------------
def test_mirror_doubles_a_body_across_a_plane():
    document = Document("T")
    document.add_feature(
        BoxFeature(inputs={"width": 10, "depth": 10, "height": 10, "x": 5},
                   outputs=["Block"])
    )
    build(document)
    document.add_feature(
        MirrorFeature(
            inputs={"body": BodyRef("Block"), "normal": (1.0, 0.0, 0.0),
                    "origin": (0.0, 0.0, 0.0)},
            outputs=["Block"],
        )
    )
    build(document)
    shape = document.bodies["Block"].shape
    assert is_valid(shape)
    assert volume(shape) == pytest.approx(2000.0, rel=1e-6)
    low, high = bounding_box(shape)
    assert low[0] == pytest.approx(-15.0, abs=1e-6)
    assert high[0] == pytest.approx(15.0, abs=1e-6)


def test_a_rectangular_pattern_makes_a_grid():
    document = Document("T")
    document.add_feature(
        BoxFeature(inputs={"width": 5, "depth": 5, "height": 5}, outputs=["Pip"])
    )
    build(document)
    document.add_feature(
        RectangularPatternFeature(
            inputs={"body": BodyRef("Pip"), "count_x": 3, "count_y": 2,
                    "spacing_x": 20, "spacing_y": 15},
            outputs=["Pip"],
        )
    )
    build(document)
    shape = document.bodies["Pip"].shape
    assert is_valid(shape)
    assert volume(shape) == pytest.approx(6 * 125, rel=1e-6)
    low, high = bounding_box(shape)
    assert high[0] == pytest.approx(45.0, abs=1e-6)     # 2 * 20 + 5
    assert high[1] == pytest.approx(20.0, abs=1e-6)     # 1 * 15 + 5


def test_a_full_circular_pattern_does_not_double_up_the_first_copy():
    """360 degrees over N copies steps by 360/N, not 360/(N-1)."""
    document = Document("T")
    document.add_feature(
        CylinderFeature(inputs={"radius": 2, "height": 5, "x": 20}, outputs=["Boss"])
    )
    build(document)
    single = volume(document.bodies["Boss"].shape)

    document.add_feature(
        CircularPatternFeature(
            inputs={"body": BodyRef("Boss"), "count": 6, "total_angle": 360,
                    "axis": (0.0, 0.0, 1.0), "center": (0.0, 0.0, 0.0)},
            outputs=["Boss"],
        )
    )
    build(document)
    shape = document.bodies["Boss"].shape
    assert is_valid(shape)
    assert volume(shape) == pytest.approx(6 * single, rel=1e-4), (
        "six distinct copies, none overlapping"
    )


def test_a_partial_circular_pattern_reaches_the_stated_angle():
    document = Document("T")
    document.add_feature(
        CylinderFeature(inputs={"radius": 2, "height": 5, "x": 20}, outputs=["Boss"])
    )
    build(document)
    document.add_feature(
        CircularPatternFeature(
            inputs={"body": BodyRef("Boss"), "count": 3, "total_angle": 90,
                    "axis": (0.0, 0.0, 1.0), "center": (0.0, 0.0, 0.0)},
            outputs=["Boss"],
        )
    )
    build(document)
    low, high = bounding_box(document.bodies["Boss"].shape)
    # The last copy sits at 90 degrees, so the pattern spans +X and +Y equally.
    assert high[0] == pytest.approx(22.0, abs=0.1)
    assert high[1] == pytest.approx(22.0, abs=0.1)


def test_a_pattern_along_a_path_follows_it():
    document = Document("T")
    document.add_feature(
        BoxFeature(inputs={"width": 4, "depth": 4, "height": 4}, outputs=["Pip"])
    )
    path = Sketch("Path")
    start, end = path.add_point(0, 0), path.add_point(60, 0)
    path.add_line(start, end)
    path_feature = document.add_feature(SketchFeature(inputs={"sketch": path}))
    build(document)

    document.add_feature(
        PathPatternFeature(
            inputs={"body": BodyRef("Pip"), "path": path_feature.name, "count": 4},
            outputs=["Pip"],
        )
    )
    build(document)
    shape = document.bodies["Pip"].shape
    assert is_valid(shape)
    assert volume(shape) == pytest.approx(4 * 64, rel=1e-6)
    assert bounding_box(shape)[1][0] == pytest.approx(64.0, abs=1e-6)


def test_an_absurd_pattern_count_is_refused_kindly():
    document = Document("T")
    document.add_feature(
        BoxFeature(inputs={"width": 2, "depth": 2, "height": 2}, outputs=["Pip"])
    )
    build(document)
    document.add_feature(
        RectangularPatternFeature(
            inputs={"body": BodyRef("Pip"), "count_x": 50, "count_y": 50,
                    "spacing_x": 5, "spacing_y": 5},
            outputs=["Pip"],
        )
    )
    report = Rebuilder(document).rebuild()
    assert not report.ok
    assert "400 copies" in report.summary()


def test_patterns_are_parametric():
    """Change the count, get a different number of copies."""
    document = Document("T")
    document.parameters.set("holes", "4")
    document.add_feature(
        CylinderFeature(inputs={"radius": 2, "height": 5, "x": 20}, outputs=["Boss"])
    )
    build(document)
    single = volume(document.bodies["Boss"].shape)
    pattern = document.add_feature(
        CircularPatternFeature(
            inputs={"body": BodyRef("Boss"), "count": "holes", "total_angle": 360,
                    "axis": (0.0, 0.0, 1.0), "center": (0.0, 0.0, 0.0)},
            outputs=["Boss"],
        )
    )
    builder = Rebuilder(document)
    assert builder.rebuild().ok
    assert volume(document.bodies["Boss"].shape) == pytest.approx(4 * single, rel=1e-4)

    document.parameters.set("holes", "8")
    builder.invalidate_parameter("holes")
    assert builder.rebuild().ok
    assert volume(document.bodies["Boss"].shape) == pytest.approx(8 * single, rel=1e-4)
