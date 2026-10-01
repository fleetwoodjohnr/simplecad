"""Cheap moves: folded history and placement-only worker replies."""

from __future__ import annotations

import multiprocessing

import pytest

from simplecad.core import geometry_service as service
from simplecad.core.document import BodyRef, Document
from simplecad.core.history import History
from simplecad.core.naming import make_ref, sub_shapes
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.occ import bounding_box
from simplecad.kernel.operations import FilletFeature, MoveFeature, MoveManyFeature
from simplecad.kernel.primitives import BoxFeature
from simplecad.ui.geometry_client import apply_result


def moved_box(dx="5 mm"):
    document = Document("T")
    document.add_feature(BoxFeature(inputs={"width": 10, "depth": 10, "height": 10},
                                    outputs=["Box"]))
    document.add_feature(MoveFeature(inputs={"body": BodyRef("Box"), "dx": dx, "dy": 0, "dz": 0},
                                     outputs=["Box"]))
    return document


def low_x(document, name="Box"):
    return bounding_box(document.bodies[name].shape)[0][0]


def test_a_second_move_folds_into_the_first():
    document = moved_box()
    builder = Rebuilder(document)
    assert builder.rebuild().ok
    count = len(document.features)

    folded = document.fold_translation(["Box"], (3.0, -2.0, 0.0))

    assert folded is document.features[-1]
    assert len(document.features) == count
    assert builder.rebuild().ok
    assert low_x(document) == pytest.approx(8.0, abs=1e-6)
    low = bounding_box(document.bodies["Box"].shape)[0]
    assert low[1] == pytest.approx(-2.0, abs=1e-6)


def test_folding_is_one_undo_step_each():
    document = moved_box()
    history = History(document)
    builder = Rebuilder(document)
    builder.rebuild()
    for _ in range(3):
        history.record("Move")
        assert document.fold_translation(["Box"], (1.0, 0.0, 0.0))
    builder.rebuild()
    assert low_x(document) == pytest.approx(8.0, abs=1e-6)
    for expected in (7.0, 6.0, 5.0):
        builder.invalidate(history.undo())
        builder.rebuild()
        assert low_x(document) == pytest.approx(expected, abs=1e-6)
    assert len(document.features) == 2


def test_a_move_something_depends_on_is_not_folded():
    document = moved_box()
    builder = Rebuilder(document)
    builder.rebuild()
    shape = document.bodies["Box"].shape
    move = document.features[-1]
    edge = sub_shapes(shape, "edge")[0]
    document.add_feature(FilletFeature(inputs={
        "body": BodyRef("Box"), "radius": 1,
        "edges": [make_ref(shape, edge, move.id, kind="edge", body="Box")],
    }, outputs=["Box"]))
    builder.rebuild()
    assert document.fold_translation(["Box"], (1.0, 0.0, 0.0)) is None


def test_parametric_and_rotating_moves_are_not_folded():
    document = moved_box(dx="offset")
    document.parameters.set("offset", "5")
    Rebuilder(document).rebuild()
    assert document.fold_translation(["Box"], (1.0, 0.0, 0.0)) is None

    rotated = moved_box()
    rotated.features[-1].inputs["rz"] = "90"
    Rebuilder(rotated).rebuild()
    assert rotated.fold_translation(["Box"], (1.0, 0.0, 0.0)) is None


def test_group_nudges_fold_into_the_same_move_many():
    document = Document("T")
    for name, x in (("A", 0), ("B", 20)):
        document.add_feature(BoxFeature(inputs={"width": 5, "depth": 5, "height": 5, "x": x},
                                        outputs=[name]))
    document.add_feature(MoveManyFeature(inputs={
        "bodies": [BodyRef("A"), BodyRef("B")], "dx": 1, "dy": 0, "dz": 0,
    }, outputs=["A", "B"]))
    builder = Rebuilder(document)
    builder.rebuild()
    assert document.fold_translation(["B", "A"], (0.0, 0.0, 2.0)) is not None
    assert document.fold_translation(["A"], (0.0, 0.0, 2.0)) is None
    builder.rebuild()
    assert bounding_box(document.bodies["A"].shape)[0][2] == pytest.approx(2.0, abs=1e-6)


@pytest.fixture
def child():
    context = multiprocessing.get_context("spawn")
    parent_end, child_end = context.Pipe(duplex=True)
    process = context.Process(target=service.child_main, args=(child_end,), daemon=True)
    process.start()
    child_end.close()
    assert parent_end.poll(60)
    assert parent_end.recv()["kind"] == service.READY
    yield parent_end
    try:
        parent_end.send({"kind": service.SHUTDOWN})
    except (OSError, BrokenPipeError):
        pass
    process.join(timeout=5)


def ask(pipe, document, revision):
    pipe.send({"kind": service.REBUILD, "document": document.to_dict(), "revision": revision})
    while True:
        assert pipe.poll(120)
        reply = pipe.recv()
        if reply["kind"] != service.PROGRESS:
            return reply


def test_a_moved_body_crosses_the_pipe_as_a_placement(child):
    document = moved_box()
    apply_result(document, ask(child, document, 1))
    assert low_x(document) == pytest.approx(5.0, abs=1e-6)
    before = document.bodies["Box"].shape

    document.fold_translation(["Box"], (7.0, 0.0, 0.0))
    reply = ask(child, document, 2)

    assert reply["bodies"] == {}, "no B-Rep is resent for a rigid move"
    assert set(reply["located"]) == {"Box"}
    assert apply_result(document, reply) == ["Box"]
    assert low_x(document) == pytest.approx(12.0, abs=1e-6)
    assert document.bodies["Box"].shape.IsPartner(before)


def test_a_moved_result_is_cached_as_a_placement_and_survives_reopening(tmp_path):
    from simplecad.core.project import load, save

    document = moved_box()
    builder = Rebuilder(document)
    assert builder.rebuild().ok
    move = document.features[-1]
    signature = document._geometry_signatures[move.id]
    entry = builder.store.entries[signature]
    (encoded,) = entry["outputs"].values()
    assert encoded["kind"] == "located", "a rigid move must not serialise the shape again"

    path = str(tmp_path / "moved.scad3")
    save(document, path)
    reopened, cached = load(path)
    assert cached
    assert low_x(reopened) == pytest.approx(5.0, abs=1e-6)
    # Editing the move reuses the cached box and places it again.
    reopened.fold_translation(["Box"], (2.0, 0.0, 0.0))
    report = Rebuilder(reopened).rebuild()
    assert report.ok and low_x(reopened) == pytest.approx(7.0, abs=1e-6)


def test_undoing_a_modifier_shows_the_earlier_geometry_again(child):
    """Undo hands a body back to an earlier producer whose signature never
    changed; the window must still replace the modified shape it shows."""
    document = Document("T")
    box = document.add_feature(BoxFeature(inputs={"width": 10, "depth": 10, "height": 10},
                                          outputs=["Box"]))
    history = History(document)
    apply_result(document, ask(child, document, 1))
    plain = bounding_box(document.bodies["Box"].shape)
    shape = document.bodies["Box"].shape
    history.record("Fillet")
    document.add_feature(FilletFeature(inputs={
        "body": BodyRef("Box"), "radius": 2,
        "edges": [make_ref(shape, sub_shapes(shape, "edge")[0], box.id, kind="edge", body="Box")],
    }, outputs=["Box"]))
    apply_result(document, ask(child, document, 2))
    from simplecad.kernel.occ import volume
    filleted = volume(document.bodies["Box"].shape)
    assert filleted < 1000
    history.undo()
    apply_result(document, ask(child, document, 3))
    assert volume(document.bodies["Box"].shape) == pytest.approx(1000)
    history.redo()
    apply_result(document, ask(child, document, 4))
    assert volume(document.bodies["Box"].shape) == pytest.approx(filleted)
    assert plain


def test_a_body_the_window_no_longer_holds_is_sent_again(child):
    document = moved_box()
    first = ask(child, document, 1)
    assert "Box" in first["bodies"]
    child.send({"kind": service.REBUILD, "document": document.to_dict(), "revision": 2,
                "held": ["Box"]})
    assert child.poll(60)
    assert child.recv()["bodies"] == {}
    child.send({"kind": service.REBUILD, "document": document.to_dict(), "revision": 3,
                "held": []})
    assert child.poll(60)
    assert "Box" in child.recv()["bodies"], "a body the window dropped is resent"


def test_an_import_sent_as_a_patch_finds_its_embedded_geometry(child):
    from simplecad.core.changes import make_patch
    from simplecad.kernel.importing import EmbeddedImportFeature

    seed = moved_box()
    Rebuilder(seed).rebuild()
    blob = service.serialise_shape(seed.bodies["Box"].shape)
    document = Document("T")
    document.add_feature(BoxFeature(inputs={"width": 5, "depth": 5, "height": 5}, outputs=["Plate"]))
    before = document.to_dict()
    ask(child, document, 1)
    sha = document.add_asset(blob)
    document.add_feature(EmbeddedImportFeature(inputs={"asset": sha, "dx": 30}, outputs=["Mesh"]))
    child.send({"kind": service.REBUILD, "patch": make_patch(before, document.to_dict()),
                "base_revision": 1, "revision": 2, "assets": {sha: blob}})
    while True:
        assert child.poll(60)
        reply = child.recv()
        if reply["kind"] != service.PROGRESS:
            break
    assert reply["report"]["ok"], reply["report"]["summary"]
    assert "Mesh" in reply["bodies"]
