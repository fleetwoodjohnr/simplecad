"""Nothing a long session accumulates may grow without bound."""

from __future__ import annotations

import json
import zipfile

from simplecad.core.changes import apply_patch, make_patch
from simplecad.core.document import BodyRef, Document
from simplecad.core.history import RETAINED_GEOMETRY_STEPS, History
from simplecad.core.model_clipboard import copy_fragment, paste_fragment
from simplecad.core.project import load, save
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.operations import MoveFeature
from simplecad.kernel.primitives import BoxFeature


def _boxes(count=3):
    document = Document("Growth")
    for index in range(count):
        document.add_feature(BoxFeature(
            inputs={"width": 10, "depth": 10, "height": 10, "x": index * 20},
            outputs=[f"Box{index}"]))
    assert Rebuilder(document).rebuild().ok
    return document


def test_retired_ids_patch_only_what_changed():
    before = {"retired_ids": [f"f{i}" for i in range(500)]}
    after = {"retired_ids": before["retired_ids"] + ["fnew"]}
    patch = make_patch(before, after)
    assert patch["sets"]["retired_ids"] == {"add": ["fnew"], "remove": []}
    assert len(json.dumps(patch)) < 200
    assert apply_patch(before, patch)["retired_ids"] == sorted(after["retired_ids"])


def test_undo_and_redo_of_deletes_restore_retired_ids():
    document = _boxes()
    history = History(document, limit=50)
    for name in ("Box0", "Box1"):
        history.record("Delete")
        producer = document.producer_of(name)
        document.remove_feature(producer.id, splice=False)
    retired = set(document._retired_ids)
    assert len(retired) == 2
    history.undo()
    history.undo()
    assert document._retired_ids == set()
    history.redo()
    history.redo()
    assert document._retired_ids == retired


def test_a_legacy_retired_ids_value_patch_still_undoes():
    """Projects saved before set patches carry the whole list as a value."""
    document = _boxes()
    history = History(document, limit=10)
    history.restore({"undo": [{"label": "Delete", "patch": {
        "values": {"retired_ids": ["fold"]}, "collections": {}}, "cache_keys": []}]})
    history.undo()
    assert document._retired_ids == {"fold"}


def test_only_recent_undo_steps_pin_cached_geometry():
    document = _boxes(1)
    history = History(document, limit=500)
    rebuilder = Rebuilder(document)
    box = document.features[0]
    for height in range(11, 11 + RETAINED_GEOMETRY_STEPS + 20):
        history.record("Height")
        box.inputs["height"] = height
        rebuilder.invalidate({box.id})
        assert rebuilder.rebuild().ok
    history._finish_pending()
    pinned = history.cache_keys()
    oldest = {key for action in history._undo[:20] for key in action.cache_keys}
    newest = {key for action in history._undo[-RETAINED_GEOMETRY_STEPS:] for key in action.cache_keys}
    assert len(oldest) == 20 and newest <= pinned
    assert not oldest & pinned
    # Still undoable: the recipe comes back even though its geometry was let go.
    for _ in range(RETAINED_GEOMETRY_STEPS + 20):
        history.undo()
    assert box.inputs["height"] == 10


def test_redoing_back_after_a_deep_undo_reuses_cached_geometry(monkeypatch):
    document = _boxes(1)
    history = History(document, limit=500)
    rebuilder = Rebuilder(document)
    box = document.features[0]
    steps = RETAINED_GEOMETRY_STEPS + 20
    for height in range(11, 11 + steps):
        history.record("Height")
        box.inputs["height"] = height
        rebuilder.invalidate({box.id})
        assert rebuilder.rebuild().ok
    for _ in range(steps):
        history.undo()
        assert rebuilder.rebuild().ok
    executed = []
    execute = BoxFeature.execute
    monkeypatch.setattr(BoxFeature, "execute", lambda self, ctx: executed.append(1) or execute(self, ctx))
    for _ in range(steps):
        history.redo()
        assert rebuilder.rebuild().ok
    assert box.inputs["height"] == 10 + steps
    assert not executed, "every state redo returns to was built this session"


def test_texture_transactions_pin_only_what_they_touched():
    document = _boxes(5)
    document._geometry_signatures = {f.id: f"sig-{f.id}" for f in document.features}
    history = History(document, limit=10)
    target = document.features[2]
    before = document.to_dict()
    target.inputs["height"] = 12
    history.record_data("Edit", before)
    assert history._undo[-1].cache_keys == (f"sig-{target.id}",)


def test_clipboard_checkpoints_are_used_once_and_dropped():
    document = _boxes(1)
    for _ in range(4):
        paste_fragment(document, copy_fragment(document, ["Box0"]), (0, 30, 0))
        assert Rebuilder(document).rebuild().ok
        assert getattr(document, "_geometry_fragments", []) == []


def test_cached_dependency_analysis_follows_nested_edits():
    document = _boxes(2)
    move = document.add_feature(MoveFeature(
        inputs={"body": BodyRef("Box0"), "dx": "1", "dy": 0, "dz": 0}, outputs=["Box0"]))
    assert move.parameter_names() == set()
    document.parameters.set("gap", "4")
    move.inputs["dx"] = "gap"
    assert move.parameter_names() == {"gap"}
    assert document.features_using_parameter("gap") == {move.id}
    move.inputs["body"] = BodyRef("Box1")
    document.prepare_graph()
    assert move.body_inputs() == ["Box1"]
    assert document.dependencies_of(move) == {document.features[1].id}


def test_saving_writes_current_results_not_the_whole_session(tmp_path):
    document = _boxes(1)
    history = History(document, limit=500)
    rebuilder = Rebuilder(document)
    box = document.features[0]
    for height in range(11, 11 + RETAINED_GEOMETRY_STEPS + 15):
        history.record("Height")
        box.inputs["height"] = height
        rebuilder.invalidate({box.id})
        assert rebuilder.rebuild().ok
    path = str(tmp_path / "growth.scad3")
    save(document, path)
    with zipfile.ZipFile(path) as archive:
        entries = json.loads(archive.read("model.json"))["cache"]["entries"]
    assert len(entries) <= RETAINED_GEOMETRY_STEPS + 2
    reopened, _cached = load(path)
    assert reopened.body("Box0").shape is not None
    assert Rebuilder(reopened).rebuild().ok
