"""Edit parameters: change a feature's dimensions after creating it."""

from __future__ import annotations

import pytest

from simplecad.core.document import BodyRef, FeatureState
from simplecad.core.naming import make_ref, sub_shapes
from simplecad.core.units import Dimension
from simplecad.kernel.detect import analyse_plane
from simplecad.kernel.occ import bounding_box
from simplecad.kernel.operations import MoveFeature, PushPullFeature, ShellFeature
from tests.test_texture_editing import model  # noqa: F401 - the shared window fixture


def _top_face(shape):
    return max((f for f in sub_shapes(shape, "face") if analyse_plane(f) is not None),
               key=lambda f: analyse_plane(f).center[2])


def _open(window, feature_id):
    window._feature_action(feature_id, "edit")
    panel = next(w for w, _a in window.stage.overlays if getattr(w, "title", "") == "Edit parameters")
    panel._preview_controller.allow_in_process = True
    return panel


def _settle(panel):
    from PySide6.QtWidgets import QApplication

    panel._preview_controller._send()
    QApplication.processEvents()


def _height(window, name="A"):
    low, high = bounding_box(window.document.body(name).shape)
    return high[2] - low[2]


def test_inputs_are_classified_by_what_they_measure():
    from simplecad.core.params import ParameterSet
    from simplecad.ui.tools.properties import editable_inputs

    move = MoveFeature(inputs={"body": BodyRef("A"), "dx": "5 mm", "rx": "30 deg",
                               "pivot": [0, 0, 0], "order": "xyz"}, outputs=["A"])
    kinds = dict(editable_inputs(move, ParameterSet()))
    assert kinds == {"dx": Dimension.LENGTH, "rx": Dimension.ANGLE}

    class Pattern:
        dimensions = {}
        inputs = {"count": 4, "spacing": 12.5, "keep": True, "style": "snap_tab",
                  "body": BodyRef("A"), "faces": []}

    kinds = dict(editable_inputs(Pattern(), ParameterSet()))
    assert kinds == {"count": Dimension.SCALAR, "spacing": Dimension.LENGTH, "keep": "bool"}


def test_editing_a_box_height_rebuilds_what_was_built_on_it(model):  # noqa: F811
    window = model.window
    document = window.document
    box = document.producer_of("A")
    shape = document.body("A").shape
    document.add_feature(PushPullFeature(inputs={
        "body": BodyRef("A"), "face": make_ref(shape, _top_face(shape), box.id, body="A"),
        "distance": "2 mm",
    }, outputs=["A"]))
    window.rebuild()
    assert _height(window) == pytest.approx(5.0)

    panel = _open(window, box.id)
    assert set(panel.fields) >= {"width", "depth", "height"}
    panel.fields["height"].setText("10 mm")
    panel.preview()
    _settle(panel)
    assert panel._preview_ok, panel.status.text()
    panel.commit()
    assert _height(window) == pytest.approx(12.0), "the pull rebuilt on the taller box"
    assert window.history.undo_label == f"Edit {box.name}"

    window.undo()
    assert _height(window) == pytest.approx(5.0)
    assert document.feature(box.id).inputs["height"] == 3


def test_an_expression_is_kept_as_an_expression(model):  # noqa: F811
    window = model.window
    document = window.document
    document.parameters.set("plate", "4 mm")
    box = document.producer_of("A")
    panel = _open(window, box.id)
    panel.fields["height"].setText("plate * 2")
    panel.preview()
    _settle(panel)
    panel.commit()
    assert document.feature(box.id).inputs["height"] == "plate * 2"
    assert _height(window) == pytest.approx(8.0)


def test_a_value_the_model_cannot_take_keeps_the_last_good_part(model):  # noqa: F811
    window = model.window
    document = window.document
    box = document.producer_of("A")
    shape = document.body("A").shape
    shell = document.add_feature(ShellFeature(inputs={
        "body": BodyRef("A"), "faces": [make_ref(shape, _top_face(shape), box.id, body="A")],
        "thickness": "1 mm",
    }, outputs=["A"]))
    window.rebuild()
    good = document.body("A").shape
    panel = _open(window, shell.id)
    panel.fields["thickness"].setText("20 mm")
    panel._preview_ok = True          # commit regardless of what the preview said
    panel.commit()
    edited = document.feature(shell.id)
    assert edited.state is FeatureState.FAILED
    assert edited.message, "the failure says why"
    assert document.body("A").shape is not None
    assert bounding_box(document.body("A").shape) == bounding_box(good)


def test_a_typo_is_refused_where_it_is_typed(model):  # noqa: F811
    window = model.window
    box = window.document.producer_of("A")
    panel = _open(window, box.id)
    panel.fields["width"].setText("12 bananas")
    assert panel._edited_feature() is None
    assert "Width" in panel.status.text()


def test_the_context_bar_offers_it_for_a_body(model):  # noqa: F811
    from simplecad.ui.selection import available_actions

    model.pick("A")
    keys = [key for key, _label, _icon in available_actions(model.window.selection)]
    assert "edit_parameters" in keys
    model.window.run_action("edit_parameters")
    panel = next(w for w, _a in model.window.stage.overlays
                 if getattr(w, "title", "") == "Edit parameters")
    assert panel._source is model.window.document.producer_of("A")
