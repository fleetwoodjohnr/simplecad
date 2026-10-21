"""Oval and Pill: flat outlines extruded like the other shape tiles."""

from __future__ import annotations

import math

import pytest

from simplecad.core.document import Document
from simplecad.core.naming import sub_shapes
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.occ import bounding_box, is_valid, volume
from simplecad.kernel.primitives import OvalFeature, PillFeature


def build(feature_class, width, depth, height=10):
    document = Document()
    document.add_feature(feature_class(
        inputs={"width": width, "depth": depth, "height": height}, outputs=["Part"],
    ))
    report = Rebuilder(document).rebuild()
    return report, document.bodies.get("Part")


def size(shape):
    low, high = bounding_box(shape)
    return tuple(round(high[i] - low[i], 4) for i in range(3))


@pytest.mark.parametrize("width,depth", [(40, 25), (25, 40), (20, 20)])
def test_oval_matches_its_size_and_ellipse_area(width, depth):
    report, body = build(OvalFeature, width, depth)
    assert report.ok, report.summary()
    assert is_valid(body.shape)
    assert size(body.shape) == pytest.approx((width, depth, 10), abs=1e-3)
    assert volume(body.shape) == pytest.approx(math.pi * width / 2 * depth / 2 * 10, rel=1e-4)


@pytest.mark.parametrize("width,depth", [(40, 15), (15, 40)])
def test_pill_is_a_rectangle_with_semicircular_ends(width, depth):
    report, body = build(PillFeature, width, depth)
    assert report.ok, report.summary()
    assert is_valid(body.shape)
    assert size(body.shape) == pytest.approx((width, depth, 10), abs=1e-3)
    radius, run = min(width, depth) / 2, abs(width - depth)
    assert volume(body.shape) == pytest.approx((run * 2 * radius + math.pi * radius ** 2) * 10, rel=1e-4)
    # Two caps, two flat sides, two half-round ends.
    assert len(sub_shapes(body.shape, "face")) == 6


def test_round_pill_is_explained():
    report, _body = build(PillFeature, 20, 20)
    assert not report.ok
    assert "longer" in report.summary().lower()
