"""Undo and redo."""

from __future__ import annotations

import pytest

from simplecad.core.document import Document
from simplecad.core.history import History
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.occ import volume
from simplecad.kernel.primitives import BoxFeature, CylinderFeature


@pytest.fixture
def setup():
    document = Document("T")
    document.parameters.set("width", "40")
    history = History(document)
    builder = Rebuilder(document)
    return document, history, builder


def test_nothing_to_undo_at_the_start(setup):
    _document, history, _builder = setup
    assert history.can_undo is False
    assert history.undo() is None


def test_undo_removes_the_feature_that_was_added(setup):
    document, history, builder = setup
    history.record("Box")
    document.add_feature(BoxFeature(inputs={"width": 20, "depth": 20, "height": 20}))
    builder.rebuild()
    assert "Box" in document.bodies

    stale = history.undo()
    builder.invalidate(stale)
    builder.rebuild()

    assert document.features == []
    assert "Box" not in document.bodies


def test_redo_puts_it_back(setup):
    document, history, builder = setup
    history.record("Box")
    document.add_feature(BoxFeature(inputs={"width": 20, "depth": 20, "height": 20}))
    builder.rebuild()

    builder.invalidate(history.undo())
    builder.rebuild()
    assert history.can_redo

    builder.invalidate(history.redo())
    builder.rebuild()
    assert len(document.features) == 1
    assert volume(document.bodies["Box"].shape) == pytest.approx(8000.0)


def test_undo_restores_a_parameter_and_the_geometry_that_used_it(setup):
    document, history, builder = setup
    document.add_feature(BoxFeature(inputs={"width": "width", "depth": 10, "height": 10}))
    builder.rebuild()
    assert volume(document.bodies["Box"].shape) == pytest.approx(4000.0)

    history.record("Change width")
    document.parameters.set("width", "80")
    builder.invalidate_parameter("width")
    builder.rebuild()
    assert volume(document.bodies["Box"].shape) == pytest.approx(8000.0)

    builder.invalidate(history.undo())
    builder.rebuild()
    assert document.parameters.values["width"] == pytest.approx(40.0)
    assert volume(document.bodies["Box"].shape) == pytest.approx(4000.0)


def test_undo_only_marks_what_changed_as_stale(setup):
    """Undo must not force a full rebuild -- that is seconds on a threaded model."""
    document, history, builder = setup
    document.add_feature(BoxFeature(inputs={"width": 20, "depth": 20, "height": 20}))
    builder.rebuild()

    history.record("Cylinder")
    added = document.add_feature(
        CylinderFeature(inputs={"radius": 4, "height": 10, "x": 60})
    )
    builder.rebuild()

    stale = history.undo()
    assert stale == {added.id}, "only the undone feature should need rebuilding"


def test_a_new_action_clears_the_redo_stack(setup):
    document, history, builder = setup
    history.record("Box")
    document.add_feature(BoxFeature(inputs={"width": 20, "depth": 20, "height": 20}))
    history.undo()
    assert history.can_redo

    history.record("Cylinder")
    document.add_feature(CylinderFeature(inputs={"radius": 3, "height": 5}))
    assert history.can_redo is False


def test_labels_describe_the_action(setup):
    document, history, _builder = setup
    history.record("Fillet")
    document.add_feature(BoxFeature(inputs={"width": 5, "depth": 5, "height": 5}))
    assert history.undo_label == "Fillet"
    history.undo()
    assert history.redo_label == "Fillet"


def test_the_stack_is_bounded(setup):
    document, _history, _builder = setup
    history = History(document, limit=5)
    for index in range(20):
        history.record(f"step {index}")
        document.add_feature(
            BoxFeature(inputs={"width": 5, "depth": 5, "height": 5})
        )
    assert len(history._undo) == 5
