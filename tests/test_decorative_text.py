"""Decorative silhouette primitives and face-attached solid text."""

from __future__ import annotations

import json

import pytest

from simplecad.core.document import BodyRef, Document
from simplecad.core.naming import fingerprint, make_ref, sub_shapes
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.decorative import (
    CrescentFeature, CrossFeature, HeartFeature, LightningFeature, StarFeature,
)
from simplecad.kernel.occ import bounding_box, is_valid, volume
from simplecad.kernel.primitives import BoxFeature
from simplecad.kernel.text import TextFeature, _rendered_text


DECORATIVE = (
    StarFeature, HeartFeature, CrossFeature, CrescentFeature, LightningFeature,
)


def top_face(shape):
    candidates = [
        (face, fingerprint(face, "face")) for face in sub_shapes(shape, "face")
    ]
    return max(
        (
            (face, mark) for face, mark in candidates
            if mark.geometry == "plane" and mark.direction
            and mark.direction[2] > 0.99
        ),
        key=lambda item: item[1].center[2],
    )[0]


def plate_with_text(mode="raised", text="CAD 8", **overrides):
    document = Document("Marked plate")
    box = document.add_feature(
        BoxFeature(
            inputs={"width": 60, "depth": 30, "height": "plate_height"},
            outputs=["Plate"],
        )
    )
    document.parameters.set("plate_height", "10")
    builder = Rebuilder(document)
    assert builder.rebuild().ok
    shape = document.bodies["Plate"].shape
    inputs = {
        "body": BodyRef("Plate"),
        "face": make_ref(shape, top_face(shape), box.id, body="Plate"),
        "text": text,
        "font_family": "sans-serif",
        "mode": mode,
        "text_height": 8,
        "depth": 1,
        "offset_x": 0,
        "offset_y": 0,
        "rotation": 0,
    }
    inputs.update(overrides)
    feature = document.add_feature(TextFeature(inputs=inputs, outputs=["Plate"]))
    report = builder.rebuild()
    return document, builder, feature, report


@pytest.mark.parametrize("feature_class", DECORATIVE)
def test_decorative_shape_is_a_valid_sized_solid(feature_class):
    document = Document(feature_class.label)
    document.add_feature(
        feature_class(inputs={"size": 30, "height": 5}, outputs=[feature_class.label])
    )
    report = Rebuilder(document).rebuild()
    assert report.ok, report.summary()
    shape = document.bodies[feature_class.label].shape
    assert is_valid(shape)
    low, high = bounding_box(shape)
    assert max(high[0] - low[0], high[1] - low[1]) == pytest.approx(30, abs=1e-5)
    assert high[2] - low[2] == pytest.approx(5, abs=1e-5)
    assert volume(shape) > 0


@pytest.mark.parametrize(
    "feature",
    [
        StarFeature(inputs={"size": 30, "height": 5, "inner_ratio": 1.0}),
        CrossFeature(inputs={"size": 30, "height": 5, "arm_width": 30}),
        CrescentFeature(inputs={"size": 30, "height": 5, "thickness": 0}),
    ],
)
def test_invalid_decorative_proportions_are_explained(feature):
    document = Document()
    document.add_feature(feature)
    report = Rebuilder(document).rebuild()
    assert not report.ok
    assert any(word in report.summary().lower() for word in ("inset", "width", "thickness"))


def test_raised_text_adds_material_above_the_face():
    document, _builder, _feature, report = plate_with_text("raised")
    assert report.ok, report.summary()
    shape = document.bodies["Plate"].shape
    assert is_valid(shape)
    assert volume(shape) > 60 * 30 * 10
    assert bounding_box(shape)[1][2] == pytest.approx(11, abs=1e-5)


def test_engraved_text_removes_material_without_changing_the_outer_height():
    document, _builder, _feature, report = plate_with_text("engraved")
    assert report.ok, report.summary()
    shape = document.bodies["Plate"].shape
    assert is_valid(shape)
    assert volume(shape) < 60 * 30 * 10
    assert bounding_box(shape)[1][2] == pytest.approx(10, abs=1e-5)


def test_engraving_cannot_break_through_the_body():
    document, _builder, _feature, report = plate_with_text(
        "engraved", text="I", depth=10
    )
    assert not report.ok
    assert "deeper than the material" in report.summary().lower()
    assert volume(document.bodies["Plate"].shape) == pytest.approx(60 * 30 * 10)


def test_text_on_a_vertical_face_stays_upright_and_extrudes_outward():
    document = Document("Vertical label")
    box = document.add_feature(
        BoxFeature(inputs={"width": 60, "depth": 30, "height": 20}, outputs=["Plate"])
    )
    builder = Rebuilder(document)
    assert builder.rebuild().ok
    shape = document.bodies["Plate"].shape
    front = next(
        face for face in sub_shapes(shape, "face")
        if (mark := fingerprint(face, "face")).geometry == "plane"
        and mark.direction and mark.direction[1] < -0.99
    )
    document.add_feature(TextFeature(inputs={
        "body": BodyRef("Plate"),
        "face": make_ref(shape, front, box.id, body="Plate"),
        "text": "UP",
        "font_family": "sans-serif",
        "mode": "raised",
        "text_height": 8,
        "depth": 1,
    }, outputs=["Plate"]))
    report = builder.rebuild()
    assert report.ok, report.summary()
    labelled = document.bodies["Plate"].shape
    assert is_valid(labelled)
    low, high = bounding_box(labelled)
    assert low[1] == pytest.approx(-1, abs=1e-5)
    assert low[2] == pytest.approx(0, abs=1e-5)
    assert high[2] == pytest.approx(20, abs=1e-5)


@pytest.mark.parametrize("family", ["sans-serif", "serif", "monospace"])
def test_each_generic_font_family_can_render_solid_glyphs(family):
    concrete, glyphs = _rendered_text("A8i", family, 8)
    assert concrete
    assert len(glyphs) == 3
    assert all(is_valid(glyph) for glyph, _advance in glyphs)


@pytest.mark.parametrize("text", ["", "   ", "two\nlines"])
def test_empty_or_multiline_text_is_refused(text):
    _document, _builder, _feature, report = plate_with_text(text=text)
    assert not report.ok
    assert "text" in report.summary().lower() or "single line" in report.summary().lower()


def test_detached_raised_text_is_refused_and_keeps_the_plate():
    document, _builder, _feature, report = plate_with_text(offset_x=100)
    assert not report.ok
    assert "detached" in report.summary().lower() or "overlap" in report.summary().lower()
    assert volume(document.bodies["Plate"].shape) == pytest.approx(60 * 30 * 10)


def test_text_literals_do_not_become_parameter_dependencies():
    feature = TextFeature(inputs={
        "text": "width family",
        "font_family": "sans-serif",
        "mode": "raised",
        "text_height": "label_height",
        "depth": "engrave_depth / 2",
    })
    assert feature.parameter_names() == {"label_height", "engrave_depth"}


def test_text_round_trips_and_still_follows_an_edited_face():
    document, _builder, feature, report = plate_with_text("raised", text="A8")
    assert report.ok, report.summary()
    restored = Document.from_dict(json.loads(json.dumps(document.to_dict())))
    restored.parameters.set("plate_height", "14")
    builder = Rebuilder(restored)
    builder.invalidate()
    report = builder.rebuild()
    assert report.ok, report.summary()
    assert isinstance(restored.feature(feature.id).inputs["body"], BodyRef)
    assert bounding_box(restored.bodies["Plate"].shape)[1][2] == pytest.approx(
        15, abs=1e-5
    )


def test_one_planar_face_offers_text_and_search_lists_every_new_command():
    from simplecad.kernel.detect import PlaneInfo
    from simplecad.ui.panels.command_search import catalogue
    from simplecad.ui.selection import Picked, SelectionModel, available_actions

    model = SelectionModel(Document(), None, {})
    model.picks = [Picked(
        body="Plate", kind="face", shape=object(),
        info=PlaneInfo((0, 0, 0), (0, 0, 1), 100),
    )]
    actions = {key for key, _label, _icon in available_actions(model)}
    assert "text" in actions

    commands = {command.key for command in catalogue()}
    assert {
        "text", "shape_star", "shape_heart", "shape_cross",
        "shape_crescent", "shape_lightning",
    } <= commands
