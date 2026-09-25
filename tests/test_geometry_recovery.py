"""Regression cases for plate rollback and independent copy-time state."""

import pytest

from simplecad.core.document import BodyRef, Document
from simplecad.core.errors import CadError
from simplecad.core.history import History
from simplecad.core.model_clipboard import ModelFragment, copy_fragment, paste_fragment
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.occ import bounding_box, volume
from simplecad.kernel.operations import MoveFeature
from simplecad.kernel.primitives import BoxFeature


def box(document, name, width=10):
    return document.add_feature(BoxFeature(
        inputs={"width": width, "depth": 10, "height": 10}, outputs=[name],
    ))


def test_failed_replay_during_paste_keeps_finished_unrelated_body(monkeypatch):
    document = Document()
    box(document, "A")
    move = document.add_feature(MoveFeature(
        inputs={"body": BodyRef("A"), "dx": 50}, outputs=["A"],
    ))
    later = document.add_feature(MoveFeature(
        inputs={"body": BodyRef("A"), "dz": 12}, outputs=["A"],
    ))
    box(document, "B", 5)
    assert Rebuilder(document).rebuild().ok
    before = bounding_box(document.body("A").shape)
    fragment = copy_fragment(document, ["B"])

    def fail(_context):
        raise CadError("Simulated failed replay")

    monkeypatch.setattr(move, "execute", fail)
    monkeypatch.setattr(later, "execute", lambda _ctx: pytest.fail("Dependent ran after failure"))
    paste_fragment(document, fragment, (20, 0, 0))
    report = Rebuilder(document).rebuild(force=True)
    assert not report.ok
    assert later.id in report.skipped
    assert bounding_box(document.body("A").shape) == before
    assert volume(document.body("B2").shape) == pytest.approx(500)


def test_copy_freezes_parameter_closure_and_keeps_steps_editable(tmp_path):
    document = Document()
    document.parameters.set("unit_size", "5")
    document.parameters.set("width", "unit_size * 2")
    box(document, "A", "width")
    assert Rebuilder(document).rebuild().ok
    document.body("A").color = "#d08040"
    fragment = ModelFragment.from_dict(copy_fragment(document, ["A"]).to_dict())
    document.parameters.set("unit_size", "15")
    assert Rebuilder(document).rebuild(force=True).ok
    before = bounding_box(document.body("A").shape)
    history = History(document)
    history.record("Paste")
    clones, roots = paste_fragment(document, fragment, (50, 0, 0))
    assert roots == ["A2"]
    rebuilder = Rebuilder(document)
    assert rebuilder.rebuild().ok
    assert volume(document.body("A2").shape) == pytest.approx(1000)
    assert bounding_box(document.body("A").shape) == before
    assert document.body("A2").color == "#d08040"
    assert history.undo() is not None
    assert rebuilder.rebuild(force=True).ok
    assert "A2" not in document.bodies
    assert bounding_box(document.body("A").shape) == before
    assert history.redo() is not None
    assert rebuilder.rebuild(force=True).ok
    assert volume(document.body("A2").shape) == pytest.approx(1000)
    copied_box = document.feature(clones[0].id)
    copied_box.inputs["width"] = "20"
    rebuilder.invalidate({copied_box.id})
    assert rebuilder.rebuild().ok
    assert volume(document.body("A2").shape) == pytest.approx(2000)
    assert bounding_box(document.body("A").shape) == before
    from simplecad.core.project import load, save

    restored, cached = load(save(document, str(tmp_path / "copied.scad3")))
    assert cached
    assert Rebuilder(restored).rebuild(force=True).ok
    assert volume(restored.body("A").shape) == pytest.approx(3000)
    assert volume(restored.body("A2").shape) == pytest.approx(2000)


def test_paste_checkpoint_does_not_replay_copied_history(monkeypatch):
    document = Document()
    box(document, "A")
    assert Rebuilder(document).rebuild().ok
    clones, _ = paste_fragment(document, copy_fragment(document, ["A"]), (30, 0, 0))
    monkeypatch.setattr(clones[0], "execute", lambda _ctx: pytest.fail("Copied history replayed"))
    report = Rebuilder(document).rebuild()
    assert report.ok and clones[0].id in report.sealed
    assert bounding_box(document.body("A2").shape)[0][0] == pytest.approx(30, abs=1e-6)


def test_v1_clipboard_remains_readable():
    document = Document()
    box(document, "A")
    assert Rebuilder(document).rebuild().ok
    payload = copy_fragment(document, ["A"]).to_dict()
    payload["version"] = 1
    for key in ("parameters", "shapes", "appearance"):
        payload.pop(key)
    paste_fragment(document, ModelFragment.from_dict(payload))
    assert Rebuilder(document).rebuild().ok
    assert "A2" in document.bodies
