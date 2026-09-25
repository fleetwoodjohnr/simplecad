"""Finished edges keep a selectable, editable source feature."""

import pytest

from simplecad.core.document import BodyRef, Document
from simplecad.core.edge_edit import inspect_edge_finish
from simplecad.core.naming import fingerprint, make_ref, sub_shapes
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.occ import volume
from simplecad.kernel.operations import ChamferFeature, EdgeFinishFeature, FilletFeature, MoveFeature, edge_finish_specs
from simplecad.kernel.primitives import BoxFeature
from tests.test_texture_editing import model  # noqa: F401 - shared Qt fixture


@pytest.mark.parametrize("kind,setting", [(FilletFeature, "radius"), (ChamferFeature, "distance")])
def test_picked_finish_finds_its_source_edge_and_can_change_type(kind, setting):
    document = Document()
    box = document.add_feature(BoxFeature(
        inputs={"width": 30, "depth": 30, "height": 30}, outputs=["Box"],
    ))
    builder = Rebuilder(document)
    assert builder.rebuild().ok
    original = document.body("Box").shape
    edge = make_ref(original, sub_shapes(original, "edge")[0], box.id, body="Box")
    finish = document.add_feature(kind(
        inputs={"body": BodyRef("Box"), "edges": [edge], setting: 2}, outputs=["Box"],
    ))
    assert builder.rebuild().ok
    shown = document.body("Box").shape

    editable = EdgeFinishFeature(
        feature_id=finish.id, inputs={"body": BodyRef("Box"),
                                   "edge_specs": edge_finish_specs(finish)},
        outputs=["Box"],
    )
    editable.bindings = dict(finish.bindings)
    editable.output_ids = dict(finish.output_ids)
    _shape, faces = editable.build_with_generated(builder.context_for(editable, edit_id=finish.id))
    picked = make_ref(shown, faces[0][0], finish.id, body="Box")
    found = inspect_edge_finish(document, "Box", picked.to_dict())
    assert (found["feature_id"], found["edge_index"]) == (finish.id, 0)

    changed = EdgeFinishFeature(
        feature_id=finish.id, inputs={"body": BodyRef("Box"), "edge_specs": [{
            "edge": edge, "type": "chamfer" if setting == "radius" else "fillet",
            "distance": 3, "radius": 3,
        }]}, outputs=["Box"],
    )
    changed.bindings = dict(finish.bindings)
    changed.output_ids = dict(finish.output_ids)
    document.features[document.index_of(finish.id)] = changed
    assert builder.rebuild().ok
    assert volume(document.body("Box").shape) != pytest.approx(volume(shown))


def test_two_finished_edges_have_separate_settings_and_removal_restores_one():
    document = Document()
    box = document.add_feature(BoxFeature(
        inputs={"width": 30, "depth": 30, "height": 30}, outputs=["Box"],
    ))
    builder = Rebuilder(document)
    assert builder.rebuild().ok
    original = document.body("Box").shape
    edges = sub_shapes(original, "edge")
    refs = [make_ref(original, edges[index], box.id, body="Box") for index in (0, 6)]
    finish = document.add_feature(EdgeFinishFeature(
        inputs={"body": BodyRef("Box"), "edge_specs": [
            {"edge": refs[0], "type": "fillet", "radius": 2},
            {"edge": refs[1], "type": "fillet", "radius": 4},
        ]}, outputs=["Box"],
    ))
    assert builder.rebuild().ok
    fully_finished = volume(document.body("Box").shape)
    finish.inputs["edge_specs"] = [finish.inputs["edge_specs"][1]]
    assert builder.rebuild().ok
    one_finished = volume(document.body("Box").shape)
    assert fully_finished < one_finished < volume(original)


def test_finished_face_remains_editable_after_a_later_move():
    document = Document()
    box = document.add_feature(BoxFeature(
        inputs={"width": 20, "depth": 20, "height": 20}, outputs=["Box"],
    ))
    builder = Rebuilder(document)
    assert builder.rebuild().ok
    original = document.body("Box").shape
    edge = make_ref(original, sub_shapes(original, "edge")[0], box.id, body="Box")
    finish = document.add_feature(FilletFeature(
        inputs={"body": BodyRef("Box"), "edges": [edge], "radius": 2}, outputs=["Box"],
    ))
    document.add_feature(MoveFeature(
        inputs={"body": BodyRef("Box"), "dx": 40, "rz": 30}, outputs=["Box"],
    ))
    assert builder.rebuild().ok
    before_move = builder._get(finish.id)["Box"]
    moved = document.body("Box").shape
    blend = next(face for face in sub_shapes(before_move, "face")
                 if any(face.IsPartner(other) for other in sub_shapes(moved, "face"))
                 and fingerprint(face, "face").geometry == "cylinder")
    picked = next(face for face in sub_shapes(moved, "face") if face.IsPartner(blend))
    reference = make_ref(moved, picked, finish.id, body="Box")
    assert inspect_edge_finish(document, "Box", reference.to_dict())["feature_id"] == finish.id
    document.remove_feature(finish.id)
    assert builder.rebuild().ok
    assert volume(document.body("Box").shape) == pytest.approx(volume(original))


def test_edge_editor_changes_and_removes_a_picked_finish(model, monkeypatch):
    """The panel's preview and commit path works from a viewport face pick."""
    from simplecad.ui.tools.edge_edit import EdgeEditPanel

    window = model.window
    initial = window.document.body("A").shape
    source = window.document.producer_of("A")
    edge = make_ref(initial, sub_shapes(initial, "edge")[0], source.id, body="A")
    finish = window.document.add_feature(FilletFeature(
        inputs={"body": BodyRef("A"), "edges": [edge], "radius": 1}, outputs=["A"],
    ))
    window.rebuild()
    assert model.reports[-1].ok
    panel_feature = EdgeFinishFeature(
        feature_id=finish.id, inputs={"body": BodyRef("A"),
                                   "edge_specs": edge_finish_specs(finish)}, outputs=["A"],
    )
    panel_feature.bindings = dict(finish.bindings)
    panel_feature.output_ids = dict(finish.output_ids)
    _shape, faces = panel_feature.build_with_generated(
        window.rebuilder.context_for(panel_feature, edit_id=finish.id)
    )
    finished_face = faces[0][0]
    monkeypatch.setattr(window.stage.viewport, "selected_entries", lambda: [dict(
        presentation=window._presentations["A"], kind="face", shape=finished_face,
    )])
    window._on_selection()
    window.run_action("edit_edge_finish")
    panel = next(widget for widget, _ in window.stage.overlays if isinstance(widget, EdgeEditPanel))
    panel._preview.timer.stop()
    panel._answered(panel._preview._in_process())
    assert panel._source.id == finish.id
    panel.kind.setCurrentIndex(1)
    panel.fields["distance"].setText("2")
    panel._preview.timer.stop()
    panel._answered(panel._preview._in_process())
    assert panel.confirm.isEnabled(), panel.status.text()
    panel.commit()
    assert window.document.feature(finish.id).type_name == "edge_finish"
    assert window.document.feature(finish.id).inputs["edge_specs"][0]["type"] == "chamfer"

    # Timeline editing reaches the same feature even after the selected face
    # has changed identity, then removes the sole finish in one undo step.
    window._edge_edit_feature_id = finish.id
    window.run_action("edit_edge_finish")
    panel = next(widget for widget, _ in window.stage.overlays if isinstance(widget, EdgeEditPanel))
    panel._preview.timer.stop()
    panel._answered(panel._preview._in_process())
    panel.remove_edge()
    panel._preview.timer.stop()
    panel._answered(panel._preview._in_process())
    assert window.document.feature(finish.id) is None
