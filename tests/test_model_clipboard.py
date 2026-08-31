from __future__ import annotations

import pytest

from simplecad.core.document import BodyRef, Document
from simplecad.core.model_clipboard import (
    ModelFragment, copy_fragment, paste_fragment,
)
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.occ import bounding_box
from simplecad.kernel.operations import MoveFeature
from simplecad.kernel.primitives import BoxFeature


def _box_history() -> tuple[Document, Rebuilder]:
    document = Document("Clipboard")
    document.add_feature(BoxFeature(
        inputs={"width": "10", "depth": "12", "height": "14"},
        outputs=["Box"],
    ))
    document.add_feature(MoveFeature(
        inputs={"body": BodyRef("Box"), "dx": 5}, outputs=["Box"],
    ))
    rebuilder = Rebuilder(document)
    assert rebuilder.rebuild(force=True).ok
    return document, rebuilder


def test_fragment_round_trips_through_json_data():
    document, _rebuilder = _box_history()
    fragment = copy_fragment(document, ["Box"])

    restored = ModelFragment.from_dict(fragment.to_dict())

    assert restored.items == ["Box"]
    assert restored.root_bodies == ["Box"]
    assert len(restored.features) == 2


def test_paste_clones_the_whole_history_with_new_identities_and_offset():
    document, _rebuilder = _box_history()
    source_bounds = bounding_box(document.body("Box").shape)
    fragment = copy_fragment(document, ["Box"], bounds=source_bounds)
    original_ids = {feature.id for feature in document.features}

    clones, items = paste_fragment(document, fragment, (20.0, 0.0, 0.0))
    report = Rebuilder(document).rebuild(force=True)

    assert report.ok, report.summary()
    assert items == ["Box2"]
    assert original_ids.isdisjoint(feature.id for feature in clones)
    copied_bounds = bounding_box(document.body("Box2").shape)
    assert copied_bounds[0][0] - source_bounds[0][0] == pytest.approx(20.0)

    copied_box = next(
        feature for feature in clones
        if feature.type_name == "box" and "Box2" in feature.outputs
    )
    copied_box.inputs["width"] = "25"
    assert Rebuilder(document).rebuild(force=True).ok
    assert bounding_box(document.body("Box").shape)[1][0] - source_bounds[0][0] == pytest.approx(10.0)
    copied_width = (
        bounding_box(document.body("Box2").shape)[1][0]
        - bounding_box(document.body("Box2").shape)[0][0]
    )
    assert copied_width == pytest.approx(25.0)

    _more, second_items = paste_fragment(document, fragment, (40.0, 0.0, 0.0))
    assert Rebuilder(document).rebuild(force=True).ok
    assert second_items == ["Box3"]


def test_paste_preserves_nested_group_structure():
    document = Document("Groups")
    for name, x in (("A", 0), ("B", 20)):
        document.add_feature(BoxFeature(
            inputs={"width": 5, "depth": 5, "height": 5, "x": x},
            outputs=[name],
        ))
    assert Rebuilder(document).rebuild(force=True).ok
    inner = document.add_group(["A", "B"], "Pair")
    outer = document.add_group([inner.name], "Assembly")

    fragment = copy_fragment(document, [outer.name])
    _features, items = paste_fragment(document, fragment, (40, 0, 0))
    assert Rebuilder(document).rebuild(force=True).ok

    assert items == ["Assembly2"]
    assert document.expand(items) == ["A2", "B2"]
    assert document.group("Assembly2").members == ["Pair2"]
