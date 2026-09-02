"""Kernel contracts used by exact arrow-key nudging."""

from __future__ import annotations

import pytest

from simplecad.core.document import BodyRef, Document
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.occ import bounding_box
from simplecad.kernel.operations import MoveManyFeature
from simplecad.kernel.primitives import BoxFeature


def test_one_move_feature_translates_every_selected_body_by_exact_quarter_steps():
    document = Document("Nudge")
    document.add_feature(BoxFeature(
        inputs={"width": 1, "depth": 1, "height": 1}, outputs=["A"]
    ))
    document.add_feature(BoxFeature(
        inputs={"width": 1, "depth": 1, "height": 1, "x": 5}, outputs=["B"]
    ))
    rebuilder = Rebuilder(document)
    assert rebuilder.rebuild().ok
    document.add_feature(MoveManyFeature(
        inputs={
            "bodies": [BodyRef("A"), BodyRef("B")],
            "dx": 0.25, "dy": -0.50, "dz": 0.75,
        },
        outputs=["A", "B"],
    ))
    assert rebuilder.rebuild().ok
    assert bounding_box(document.bodies["A"].shape)[0] == pytest.approx(
        (0.25, -0.50, 0.75), abs=1e-6
    )
    assert bounding_box(document.bodies["B"].shape)[0] == pytest.approx(
        (5.25, -0.50, 0.75), abs=1e-6
    )
