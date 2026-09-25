"""Geometry saved with a project stands in for the history that made it.

Re-executing a long feature history just to get back to what is already on
screen is not only slow. On a model whose features were authored against
geometry the replay no longer reproduces exactly -- a fillet that fitted when
it was applied and no longer does -- the honest replay *loses* the user's
model. Adopting the saved shapes says: these features, unedited, produced these
bodies. Only what comes after them has to run.
"""

from __future__ import annotations

import pytest

from simplecad.core.document import BodyRef, Document
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.occ import volume
from simplecad.kernel.operations import BooleanFeature, MoveFeature
from simplecad.kernel.primitives import BoxFeature


def stacked_document() -> Document:
    document = Document("Sealed")
    document.parameters.set("size", "10")
    document.add_feature(BoxFeature(
        inputs={"width": "size", "depth": "size", "height": "size"},
        outputs=["A"],
    ))
    document.add_feature(BoxFeature(
        inputs={"width": 10, "depth": 10, "height": 10, "x": 5}, outputs=["B"],
    ))
    document.add_feature(MoveFeature(
        inputs={"body": BodyRef("B"), "dz": 0}, outputs=["B"],
    ))
    return document


def reopened(document: Document) -> Rebuilder:
    """A rebuilder as a freshly opened project has one: cold cache, warm bodies."""
    rebuilder = Rebuilder(document)
    assert rebuilder.adopt_baseline()
    return rebuilder


def test_a_sealed_prefix_runs_only_the_feature_appended_after_it():
    document = stacked_document()
    assert Rebuilder(document).rebuild().ok
    rebuilder = reopened(document)
    assert rebuilder.baseline_size == len(document.features)

    document.add_feature(BooleanFeature(inputs={
        "body": BodyRef("A"), "tools": [BodyRef("B")], "operation": "join",
    }, outputs=["A"]))
    built = []
    report = rebuilder.rebuild(on_feature=lambda f, i, n: built.append((f.id, i, n)))

    assert report.ok
    assert report.rebuilt == [document.features[-1].id]
    assert [entry[0] for entry in built] == [document.features[-1].id]
    assert built[0][2] == 1, "progress must count the tail, not the sealed prefix"
    assert report.cache_hits >= 1
    assert list(document.bodies) == ["A"]
    assert volume(document.body("A").shape) == pytest.approx(1500)


def test_a_sealed_rebuild_matches_the_honest_one():
    document = stacked_document()
    assert Rebuilder(document).rebuild().ok
    document.add_feature(BooleanFeature(inputs={
        "body": BodyRef("A"), "tools": [BodyRef("B")], "operation": "join",
    }, outputs=["A"]))
    sealed_rebuilder = reopened(document)
    # adopt_baseline sealed the Join as well, so drop it back to the prefix by
    # invalidating the feature that was appended after the shapes were saved.
    sealed_rebuilder.invalidate({document.features[-1].id})
    assert sealed_rebuilder.rebuild().ok
    sealed = volume(document.body("A").shape)

    honest = Rebuilder(document)
    assert honest.rebuild(force=True).ok
    assert volume(document.body("A").shape) == pytest.approx(sealed)


def test_editing_a_saved_feature_rebuilds_only_its_branch():
    document = stacked_document()
    assert Rebuilder(document).rebuild().ok
    rebuilder = reopened(document)

    document.features[0].inputs["width"] = 20
    rebuilder.invalidate({document.features[0].id})
    report = rebuilder.rebuild()

    assert report.rebuilt == [document.features[0].id]
    assert document.features[-1].id in report.sealed
    assert volume(document.body("A").shape) == pytest.approx(2000)


def test_an_edited_parameter_is_not_hidden_by_the_baseline():
    document = stacked_document()
    assert Rebuilder(document).rebuild().ok
    rebuilder = reopened(document)

    document.parameters.set("size", "20")
    report = rebuilder.rebuild()

    assert report.rebuilt == [document.features[0].id]
    assert volume(document.body("A").shape) == pytest.approx(8000)


def test_a_forced_rebuild_ignores_the_saved_geometry():
    document = stacked_document()
    assert Rebuilder(document).rebuild().ok
    rebuilder = reopened(document)

    report = rebuilder.rebuild(force=True)

    assert report.sealed == []
    assert sorted(report.rebuilt) == sorted(f.id for f in document.features)
    assert rebuilder.baseline_size == 0


def test_reordering_independent_features_preserves_cached_results():
    document = stacked_document()
    assert Rebuilder(document).rebuild().ok
    rebuilder = reopened(document)

    document.features[0], document.features[1] = (
        document.features[1], document.features[0]
    )
    report = rebuilder.rebuild()

    assert report.rebuilt == []


def test_a_tail_feature_needing_a_sealed_internal_result_replays_instead():
    """Features hand each other results that never become document bodies."""
    document = stacked_document()
    assert Rebuilder(document).rebuild().ok
    rebuilder = reopened(document)

    document.add_feature(MoveFeature(
        inputs={"body": BodyRef("__sketch1"), "dz": 1}, outputs=["__sketch1"],
    ))
    report = rebuilder.rebuild()

    # Missing inputs fail honestly without touching independent saved bodies.
    assert not report.ok
    assert report.rebuilt == []
    assert document.features[-1].id in report.failures


def test_geometry_missing_from_the_table_refuses_the_baseline():
    document = stacked_document()
    assert Rebuilder(document).rebuild().ok
    document.body("B").shape = None

    assert Rebuilder(document).adopt_baseline() is False
    assert Rebuilder(document).baseline_size == 0
