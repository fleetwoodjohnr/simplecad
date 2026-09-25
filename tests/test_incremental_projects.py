"""Identity, history and recomputation invariants, before and after .scad3."""
import copy
import json
import zipfile

import pytest

from simplecad.core.document import BodyRef, Document
from simplecad.core.history import History
from simplecad.core.naming import make_ref, sub_shapes
from simplecad.core.project import load, save
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.detect import analyse_plane
from simplecad.kernel.face_texture import FaceTextureFeature
from simplecad.kernel.occ import bounding_box, volume
from simplecad.kernel.operations import BooleanFeature, MoveFeature
from simplecad.kernel.primitives import BoxFeature, CylinderFeature


def box(document, name, **inputs):
    return document.add_feature(BoxFeature(inputs={"width": 15, "depth": 15, "height": 3, **inputs}, outputs=[name]))


def texture_model(count=8):
    doc = Document()
    base = box(doc, "Target")
    for i in range(count):
        box(doc, f"Independent{i}", x=30 + 20 * i)
    builder = Rebuilder(doc)
    assert builder.rebuild().ok
    shape = doc.body("Target").shape
    face = next(f for f in sub_shapes(shape, "face") if (p := analyse_plane(f)) and p.normal[2] > .99)
    texture = doc.add_feature(FaceTextureFeature(inputs={
        "body": doc.ref("Target"), "face": make_ref(shape, face, base.id, body=doc.ref("Target")),
        "pattern": "horizontal_lines", "size": 5, "spacing": 5, "depth": .4, "edge_clearance": 1,
    }, outputs=["Target"]))
    move = doc.add_feature(MoveFeature(inputs={"body": doc.ref("Target"), "dx": 4}, outputs=["Target"]))
    assert builder.rebuild().ok
    return doc, builder, texture.id, move.id


def reopened(doc, tmp_path):
    document, cached = load(save(doc, str(tmp_path / "roundtrip.scad3")))
    assert cached and document._cache_complete
    return document, Rebuilder(document)


@pytest.mark.parametrize("reopen", [False, True])
@pytest.mark.parametrize("action", ["edit", "replace", "remove"])
def test_texture_action_touches_only_its_dependency_branch(tmp_path, reopen, action):
    doc, builder, tid, mid = texture_model()
    history = History(doc)
    if reopen:
        doc, builder = reopened(doc, tmp_path)
        history = History(doc)
    independent = {n: b.shape for n, b in doc.bodies.items() if n.startswith("Independent")}
    original = volume(doc.body("Target").shape)
    history.record(action)
    if action == "remove":
        doc.remove_feature(tid)
    else:
        doc.feature(tid).inputs["pattern" if action == "replace" else "depth"] = "vertical_lines" if action == "replace" else .8
    report = builder.rebuild()
    assert report.ok, report.summary()
    assert set(report.rebuilt) == ({mid} if action == "remove" else {tid, mid})
    for name, shape in independent.items():
        assert doc.body(name).shape.IsSame(shape)
    if action == "remove":
        assert volume(doc.body("Target").shape) == pytest.approx(675)
    assert history.undo() is not None
    assert builder.rebuild().ok
    assert volume(doc.body("Target").shape) == pytest.approx(original)
    assert history.redo() is not None
    assert builder.rebuild().ok
    assert all(doc.body(n).shape.IsSame(s) for n, s in independent.items())


@pytest.mark.parametrize("reopen", [False, True])
def test_texture_preview_never_executes_other_features(tmp_path, reopen, monkeypatch):
    from simplecad.core.geometry_service import build_preview_result
    doc, builder, tid, _ = texture_model()
    if reopen:
        doc, builder = reopened(doc, tmp_path)
    before = copy.deepcopy(doc.to_dict())
    monkeypatch.setattr(BoxFeature, "execute", lambda *a: pytest.fail("Preview replayed a primitive"))
    monkeypatch.setattr(MoveFeature, "execute", lambda *a: pytest.fail("Preview replayed a later modifier"))
    state = doc.feature(tid).to_dict()
    state["inputs"]["depth"] = .8
    state["__edit_feature_id"] = tid
    result = build_preview_result(doc, state)
    assert result["error"] is None
    assert doc.to_dict() == before


@pytest.mark.parametrize("reopen", [False, True])
def test_deleted_operand_never_rebinds_to_a_namesake(tmp_path, reopen):
    doc = Document()
    box(doc, "Target", height=10)
    cutter = doc.add_feature(CylinderFeature(inputs={"radius": 2, "height": 10, "x": 5, "y": 5}, outputs=["Cylinder2"]))
    builder = Rebuilder(doc)
    builder.rebuild()
    op = doc.add_feature(BooleanFeature(inputs={"body": doc.ref("Target"), "tool": doc.ref("Cylinder2"), "operation": "cut", "keep_tool": True}, outputs=["Target"]))
    assert builder.rebuild().ok
    if reopen:
        doc, builder = reopened(doc, tmp_path)
        op, cutter = doc.feature(op.id), doc.feature(cutter.id)
    expected = op.bindings["Cylinder2"]
    before = doc.body("Target").shape
    doc.remove_feature(cutter.id, splice=False)
    replacement = doc.add_feature(CylinderFeature(inputs={"radius": 4, "height": 10}, outputs=["Cylinder2"]))
    report = builder.rebuild()
    assert not report.ok and op.id in report.failures
    assert op.bindings["Cylinder2"] == expected
    assert replacement.output_ids["Cylinder2"] != expected.object_id
    assert doc.body("Target").shape.IsSame(before)


def test_late_cylinder_boolean_ids_survive_reopen_and_rename(tmp_path):
    doc = Document()
    box(doc, "Target", height=10)
    for i in range(1, 41):
        feature = doc.add_feature(CylinderFeature(inputs={"radius": 2, "height": 10, "x": 5, "y": 5}, outputs=[f"Cylinder{i}"]))
        if i != 32:
            doc.remove_feature(feature.id, splice=False)
    builder = Rebuilder(doc)
    assert builder.rebuild().ok
    op = doc.add_feature(BooleanFeature(inputs={"body": doc.ref("Target"), "tool": doc.ref("Cylinder32"), "operation": "cut", "keep_tool": True}, outputs=["Target"]))
    assert builder.rebuild().ok
    identities = {f.id: dict(f.output_ids) for f in doc.features}
    ref = op.bindings["Cylinder32"]
    expected = volume(doc.body("Target").shape)
    doc, builder = reopened(doc, tmp_path)
    assert {f.id: dict(f.output_ids) for f in doc.features} == identities
    doc.rename_body("Cylinder32", "Cutter")
    assert doc.feature(op.id).bindings["Cutter"] == ref
    assert builder.rebuild().rebuilt == []
    assert volume(doc.body("Target").shape) == pytest.approx(expected)


def test_reordering_cannot_retarget_an_operand():
    doc = Document()
    base = box(doc, "A")
    move = doc.add_feature(MoveFeature(inputs={"body": BodyRef("A"), "dx": 5}, outputs=["A"]))
    later = doc.add_feature(MoveFeature(inputs={"body": BodyRef("A"), "dz": 2}, outputs=["A"]))
    before = dict(later.bindings)
    assert not doc.can_reorder(move.id, 2)
    assert later.bindings == before
    assert doc.dependencies_of(later) == {move.id}


def test_indirect_parameters_only_recompute_their_users(tmp_path):
    doc = Document()
    doc.parameters.set("unit", "5")
    doc.parameters.set("width", "2 * unit")
    target = box(doc, "A", width="width")
    other = box(doc, "B")
    assert Rebuilder(doc).rebuild().ok
    doc, builder = reopened(doc, tmp_path)
    doc.parameters.set("unit", "10")
    report = builder.rebuild()
    assert report.rebuilt == [target.id]
    assert volume(doc.body("A").shape) == pytest.approx(20 * 15 * 3)


def test_history_is_immutable_and_both_stacks_survive_open(tmp_path):
    doc = Document()
    feature = box(doc, "A")
    builder = Rebuilder(doc)
    builder.rebuild()
    doc.metadata = {"nested": {"value": "before"}}
    doc.body("A").metadata = {"nested": [1]}
    history = History(doc)
    history.record("Metadata")
    doc.metadata["nested"]["value"] = "after"
    doc.body("A").metadata["nested"].append(2)
    history.record("Resize")
    feature.inputs["width"] = 20
    builder.rebuild()
    history.undo()
    builder.rebuild()
    doc, builder = reopened(doc, tmp_path)
    history = History(doc)
    assert history.undo_label == "Metadata" and history.redo_label == "Resize"
    history.undo()
    assert doc.metadata["nested"]["value"] == "before"
    assert doc.body("A").metadata == {"nested": [1]}
    history.redo()
    history.redo()
    assert builder.rebuild().ok
    assert volume(doc.body("A").shape) == pytest.approx(900)


def test_pending_recipe_is_never_saved_as_valid_cached_geometry(tmp_path):
    doc = Document()
    feature = box(doc, "A")
    Rebuilder(doc).rebuild()
    feature.inputs["width"] = 25  # Save before the worker has answered.
    loaded, _ = load(save(doc, str(tmp_path / "pending.scad3")))
    assert not loaded._cache_complete
    assert Rebuilder(loaded).rebuild().ok
    assert volume(loaded.body("A").shape) == pytest.approx(25 * 15 * 3)


def test_legacy_project_migrates_once_without_replaying_other_branches(tmp_path):
    from simplecad.core.geometry_service import serialise_shape
    doc, _, tid, mid = texture_model()
    payload = doc.to_dict()
    payload["format"] = 1
    for feature in payload["features"]:
        feature.pop("bindings")
        feature.pop("output_ids")
    for body in payload["bodies"]:
        body.pop("id")
    path = tmp_path / "legacy.scad3"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("model.json", json.dumps(payload))
        for name, body in doc.bodies.items():
            archive.writestr(f"bodies/{name}.brep", serialise_shape(body.shape))
    loaded, cached = load(str(path))
    assert cached
    ids = {b.name: b.id for b in loaded.bodies.values()}
    builder = Rebuilder(loaded)
    loaded.feature(tid).inputs["depth"] = .8
    report = builder.rebuild()
    assert report.ok
    assert not any(loaded.feature(fid).outputs[0].startswith("Independent") for fid in report.rebuilt)
    again, _ = load(save(loaded, str(tmp_path / "migrated.scad3")))
    assert {b.name: b.id for b in again.bodies.values()} == ids


@pytest.mark.parametrize("reopen", [False, True])
def test_isolated_edit_work_is_constant_as_project_grows(tmp_path, reopen):
    for size in (10, 100, 500):
        doc = Document()
        features = [box(doc, f"Box{i}", x=i * 20) for i in range(size)]
        builder = Rebuilder(doc)
        assert builder.rebuild().ok
        if reopen:
            doc, builder = reopened(doc, tmp_path)
        target = doc.feature(features[-1].id)
        for width in (16, 17, 18):
            target.inputs["width"] = width
            report = builder.rebuild()
            assert report.rebuilt == [target.id]
            assert len(doc._last_signature_changes) <= 1
        assert builder.rebuild().visited == 0


def test_long_session_reclaims_deleted_feature_cache():
    doc = Document()
    builder = Rebuilder(doc)
    for i in range(50):
        feature = box(doc, "A")
        builder.rebuild()
        doc.remove_feature(feature.id, splice=False)
        builder.rebuild()
    assert not doc.bodies
    assert not builder.store.entries
    assert not builder.store.blobs


def test_transaction_rolls_back_a_failed_edit():
    doc = Document()
    feature = box(doc, "A")
    history = History(doc)
    with pytest.raises(ValueError), doc.transaction("Invalid edit"):
        feature.inputs["width"] = 100
        raise ValueError("cancel")
    assert doc.feature(feature.id).inputs["width"] == 15
    assert not history.can_undo


def test_deep_feature_history_does_not_depend_on_python_recursion_limit():
    doc = Document()
    base = BoxFeature(name="Base", outputs=["A"], inputs={"width": 1, "depth": 1, "height": 1})
    doc.features = [base] + [MoveFeature(name=f"Move{i}",
        inputs={"body": BodyRef("A"), "dx": 1}, outputs=["A"]) for i in range(600)]
    builder = Rebuilder(doc)
    report = builder.rebuild()
    assert report.ok and len(report.rebuilt) == 601
    low, _ = bounding_box(doc.body("A").shape)
    assert low[0] == pytest.approx(600, abs=1e-5)
    assert builder.rebuild().visited == 0


@pytest.mark.parametrize("reopen", [False, True])
def test_repeated_booleans_do_not_replay_previous_operations(tmp_path, reopen):
    doc = Document()
    box(doc, "Target", width=100, depth=20, height=10)
    for i in range(20):
        box(doc, f"Independent{i}", x=200 + i * 20)
    builder = Rebuilder(doc)
    assert builder.rebuild().ok
    ids = []
    for i in range(8):
        cutter = doc.add_feature(CylinderFeature(inputs={"radius": 2, "height": 10,
            "x": 5 + i * 10, "y": 5}, outputs=[f"Cutter{i}"]))
        operation = doc.add_feature(BooleanFeature(inputs={"body": doc.ref("Target"),
            "tool": doc.ref(f"Cutter{i}"), "operation": "cut"}, outputs=["Target"]))
        report = builder.rebuild()
        assert report.ok and report.rebuilt == [cutter.id, operation.id]
        ids.append((operation.id, dict(operation.bindings)))
        if reopen:
            doc, builder = reopened(doc, tmp_path)
        assert all(dict(doc.feature(fid).bindings) == bindings for fid, bindings in ids)
    assert volume(doc.body("Target").shape) == pytest.approx(20000 - 8 * 40 * 3.141592653589793)


def test_noop_preserves_missing_reference_diagnostic():
    doc = Document()
    feature = doc.add_feature(MoveFeature(inputs={"body": BodyRef("Deleted"), "dx": 5}, outputs=["A"]))
    builder = Rebuilder(doc)
    assert feature.id in builder.rebuild().failures
    again = builder.rebuild()
    assert feature.id in again.failures and again.visited == 0
