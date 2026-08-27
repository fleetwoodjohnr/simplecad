"""Document graph, rebuild ordering, and failure containment."""

from __future__ import annotations

import json

import pytest

from simplecad.core.document import BodyRef, Document, Feature, FeatureState, register
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.occ import volume
from simplecad.kernel.primitives import BoxFeature, CylinderFeature


@pytest.fixture
def doc():
    document = Document("Test")
    document.parameters.set("width", "60")
    document.parameters.set("wall", "2 mm")
    return document


def test_primitive_builds_with_expected_volume(doc):
    doc.add_feature(BoxFeature(inputs={"width": 60, "depth": 40, "height": 20}))
    report = Rebuilder(doc).rebuild()
    assert report.ok, report.summary()
    assert volume(doc.bodies["Box"].shape) == pytest.approx(48000.0)


def test_expressions_feed_geometry(doc):
    doc.add_feature(BoxFeature(inputs={"width": "width", "depth": "width/2", "height": 10}))
    Rebuilder(doc).rebuild()
    assert volume(doc.bodies["Box"].shape) == pytest.approx(60 * 30 * 10)


def test_parameter_edit_rebuilds_only_dependents(doc):
    doc.add_feature(BoxFeature(inputs={"width": "width", "depth": 40, "height": 20}))
    doc.add_feature(CylinderFeature(inputs={"radius": 5, "height": 10, "x": 200}))
    builder = Rebuilder(doc)
    builder.rebuild()

    doc.parameters.set("width", "90")
    builder.invalidate_parameter("width")
    report = builder.rebuild()

    names = {doc.feature(f).name for f in report.rebuilt}
    assert names == {"Box"}, "the cylinder does not use `width` and must stay cached"
    assert volume(doc.bodies["Box"].shape) == pytest.approx(90 * 40 * 20)


def test_unique_names_do_not_collide(doc):
    for _ in range(3):
        doc.add_feature(BoxFeature(inputs={"width": 10, "depth": 10, "height": 10}))
    assert [f.name for f in doc.features] == ["Box", "Box2", "Box3"]


def test_failed_feature_keeps_the_rest_of_the_model(doc):
    doc.add_feature(BoxFeature(inputs={"width": 60, "depth": 40, "height": 20}))
    doc.add_feature(CylinderFeature(inputs={"radius": -5, "height": 10}))
    report = Rebuilder(doc).rebuild()

    assert not report.ok
    assert "Box" in doc.bodies, "a good body must survive a bad sibling"
    assert doc.features[1].state is FeatureState.FAILED
    assert "greater than zero" in doc.features[1].message


def test_failure_message_is_human_readable(doc):
    doc.add_feature(CylinderFeature(inputs={"radius": 0, "height": 10}))
    report = Rebuilder(doc).rebuild()
    message = report.summary()
    assert "StdFail" not in message and "BRep" not in message
    assert "radius" in message


def test_suppressed_feature_is_skipped(doc):
    box = doc.add_feature(BoxFeature(inputs={"width": 10, "depth": 10, "height": 10}))
    box.suppressed = True
    report = Rebuilder(doc).rebuild()
    assert box.state is FeatureState.SUPPRESSED
    assert "Box" not in doc.bodies


def test_previous_geometry_is_retained_when_a_rebuild_fails(doc):
    box = doc.add_feature(BoxFeature(inputs={"width": 60, "depth": 40, "height": 20}))
    builder = Rebuilder(doc)
    builder.rebuild()
    good = volume(doc.bodies["Box"].shape)

    box.inputs["width"] = -1
    builder.invalidate({box.id})
    report = builder.rebuild()

    assert not report.ok
    assert volume(doc.bodies["Box"].shape) == pytest.approx(good)


def test_reorder_is_refused_when_it_would_break_a_dependency(doc):
    base = doc.add_feature(BoxFeature(inputs={"width": 40, "depth": 40, "height": 10}))
    follower = CylinderFeature(inputs={"radius": 5, "height": 10, "base": BodyRef("Box")})
    doc.add_feature(follower)
    base.outputs = ["Box"]

    assert doc.can_reorder(follower.id, 1) is True
    assert doc.can_reorder(follower.id, 0) is False
    with pytest.raises(Exception):
        doc.reorder(follower.id, 0)


def test_dependents_are_found_transitively(doc):
    first = doc.add_feature(BoxFeature(inputs={"width": 10, "depth": 10, "height": 10}))
    first.outputs = ["Box"]
    second = doc.add_feature(CylinderFeature(inputs={"radius": 2, "height": 2, "base": BodyRef("Box")}))
    second.outputs = ["Cylinder"]
    third = doc.add_feature(CylinderFeature(inputs={"radius": 2, "height": 2, "base": BodyRef("Cylinder")}))

    assert doc.dependents_of({first.id}) == {first.id, second.id, third.id}


def test_document_round_trips_through_json(doc):
    doc.add_feature(BoxFeature(inputs={"width": "width", "depth": 40, "height": 20}))
    doc.add_feature(CylinderFeature(inputs={"radius": 5, "height": 10, "base": BodyRef("Box")}))
    Rebuilder(doc).rebuild()

    restored = Document.from_dict(json.loads(json.dumps(doc.to_dict())))
    assert restored.title == doc.title
    assert [f.name for f in restored.features] == [f.name for f in doc.features]
    assert isinstance(restored.features[1].inputs["base"], BodyRef)
    assert restored.parameters.values["width"] == pytest.approx(60.0)

    Rebuilder(restored).rebuild()
    assert volume(restored.bodies["Box"].shape) == pytest.approx(48000.0)


def test_rebuild_reports_progress_per_feature(doc):
    """The UI needs to say what it is building, and repaint between features."""
    doc.add_feature(BoxFeature(inputs={"width": 10, "depth": 10, "height": 10}))
    doc.add_feature(CylinderFeature(inputs={"radius": 3, "height": 8, "x": 50}))

    seen = []
    Rebuilder(doc).rebuild(on_feature=lambda f, i, n: seen.append((f.name, i, n)))

    assert seen == [("Box", 0, 2), ("Cylinder", 1, 2)]
