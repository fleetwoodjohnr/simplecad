"""The out-of-process geometry service.

Covers the wire protocol and the child's incremental behaviour without needing
a GUI. The responsiveness claim itself is measured by
``scripts/check_responsive.py``, which needs a real window.
"""

from __future__ import annotations

import multiprocessing

import pytest

from simplecad.core import geometry_service as service
from simplecad.core.document import Body, Document
from simplecad.kernel.occ import is_valid, volume
from simplecad.kernel.primitives import BoxFeature, CylinderFeature


def box_document(width=40.0):
    document = Document("T")
    document.parameters.set("width", str(width))
    document.add_feature(
        BoxFeature(inputs={"width": "width", "depth": 30, "height": 10},
                   outputs=["Box"])
    )
    return document


# ----------------------------------------------------------------------
# Shape serialisation -- how geometry crosses the process boundary
# ----------------------------------------------------------------------
def test_a_shape_survives_a_round_trip_through_bytes():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    original = BRepPrimAPI_MakeBox(30.0, 20.0, 10.0).Shape()
    blob = service.serialise_shape(original)
    assert isinstance(blob, bytes) and blob

    restored = service.deserialise_shape(blob)
    assert restored is not None
    assert volume(restored) == pytest.approx(volume(original), rel=1e-9)


def test_kept_subtract_preview_can_seed_the_committed_result():
    from simplecad.core.document import BodyRef
    from simplecad.core.rebuild import Rebuilder
    from simplecad.kernel.operations import BooleanFeature

    document = Document()
    document.add_feature(BoxFeature(
        inputs={"width": 40, "depth": 40, "height": 10}, outputs=["Block"],
    ))
    document.add_feature(CylinderFeature(
        inputs={"radius": 3, "height": 20, "x": 20, "y": 20, "z": -5},
        outputs=["Pin"],
    ))
    rebuilder = Rebuilder(document)
    assert rebuilder.rebuild().ok
    feature = BooleanFeature(
        inputs={"body": BodyRef("Block"), "tool": BodyRef("Pin"),
                "operation": "cut", "keep_tool": True},
        outputs=["Block"],
    )
    preview = service.build_preview_result(document, feature.to_dict())
    assert preview["error"] is None
    assert set(preview["_outputs"]) == {"Block", "Pin"}
    document.add_feature(feature)
    rebuilder.seed_result(feature, preview["_outputs"], preview["message"])
    assert rebuilder.rebuild().ok
    assert set(document.bodies) == {"Block", "Pin"}
    assert volume(document.bodies["Block"].shape) < 40 * 40 * 10


def test_save_snapshot_does_not_replace_live_worker_state(child, tmp_path):
    from simplecad.core.history import History
    from simplecad.core.project import load
    from simplecad.ui.geometry_client import apply_result
    document = box_document(10)
    apply_result(document, ask(child, document))
    history = History(document)
    snapshot = document.to_dict()
    history.record("Resize")
    document.parameters.set("width", "20")
    apply_result(document, ask(child, document))
    child.send({"kind": service.SAVE, "document": snapshot,
                "path": str(tmp_path / "snapshot.scad3"), "token": 27,
                "history": {}, "generation": 0})
    while True:
        assert child.poll(60)
        reply = child.recv()
        if reply["kind"] != service.PROGRESS:
            break
    assert reply["kind"] == service.SAVED and not reply["error"]
    loaded, cached = load(reply["path"])
    assert cached
    assert volume(loaded.body("Box").shape) == pytest.approx(10 * 30 * 10)
    assert loaded.body("Box").id == document.body("Box").id
    reply = ask(child, document)
    assert reply["report"]["rebuilt"] == []
    assert reply["bodies"] == {}


def test_parent_and_worker_release_deleted_results_in_long_session(child):
    from simplecad.ui.geometry_client import apply_result
    document = Document()
    for i in range(12):
        feature = document.add_feature(BoxFeature(inputs={"width": i + 1}, outputs=["A"]))
        apply_result(document, ask(child, document))
        assert len(document._geometry_store.entries) == 1
        document.remove_feature(feature.id, splice=False)
        reply = ask(child, document)
        apply_result(document, reply)
        assert not document.bodies
        assert not document._geometry_store.entries
        assert not document._geometry_store.blobs


def test_reopened_worker_reuses_unchanged_display_objects(child, tmp_path):
    from simplecad.core.project import load, save
    from simplecad.core.rebuild import Rebuilder
    from simplecad.ui.geometry_client import apply_result
    document = box_document(10)
    document.add_feature(BoxFeature(outputs=["Other"], inputs={"x": 100}))
    apply_result(document, ask(child, document))
    document, cached = load(save(document, str(tmp_path / "worker.scad3")))
    assert cached
    child.send({"kind": service.SYNC, "document": document.to_dict(),
        "cache": document._geometry_store.export(),
        "bodies": {n: service.serialise_shape(b.shape) for n, b in document.bodies.items()},
        "revision": 10})
    assert child.poll(60)
    assert child.recv()["kind"] == service.SYNCED
    document.parameters.set("width", "12")
    reply = ask(child, document)
    assert reply["report"]["rebuilt"] == [document.features[0].id]
    assert set(reply["bodies"]) == {"Box"}


def test_a_threaded_shape_survives_the_round_trip():
    """The expensive case, and the one the process exists for."""
    from simplecad.kernel.thread_specs import by_designation
    from simplecad.kernel.threads import thread_solid

    size = by_designation("M6")
    original = thread_solid(size.diameter, size.pitch, 8.0)
    restored = service.deserialise_shape(service.serialise_shape(original))
    assert restored is not None
    assert volume(restored) == pytest.approx(volume(original), rel=1e-6)


# ----------------------------------------------------------------------
# The service loop
# ----------------------------------------------------------------------
@pytest.fixture
def child():
    """Run the service in a real child process, over a pipe."""
    context = multiprocessing.get_context("spawn")
    parent_end, child_end = context.Pipe(duplex=True)
    process = context.Process(
        target=service.child_main, args=(child_end,), daemon=True
    )
    process.start()
    child_end.close()
    assert parent_end.poll(60), "the geometry process did not start"
    hello = parent_end.recv()
    assert hello["kind"] == service.READY
    yield parent_end
    try:
        parent_end.send({"kind": service.SHUTDOWN})
    except (OSError, BrokenPipeError):
        pass
    process.join(timeout=10)
    if process.is_alive():
        process.terminate()


def ask(pipe, document, stale=(), force=False, timeout=120, full=False,
        progress=None):
    pipe.send({
        "kind": service.REBUILD,
        "document": document.to_dict(),
        "stale": list(stale),
        "force": force,
        "full": full,
    })
    while True:
        assert pipe.poll(timeout), "no reply from the geometry process"
        reply = pipe.recv()
        if reply["kind"] != service.PROGRESS:
            return reply
        # Heartbeats, not answers: the child says which feature it is on so a
        # long rebuild is not mistaken for a dead one.
        if progress is not None:
            progress.append(reply)


def test_the_child_rebuilds_and_returns_geometry(child):
    reply = ask(child, box_document(40))
    assert reply["kind"] == service.RESULT
    assert reply["report"]["ok"]
    assert "Box" in reply["bodies"]

    shape = service.deserialise_shape(reply["bodies"]["Box"])
    assert volume(shape) == pytest.approx(40 * 30 * 10)


def test_child_rebuilds_an_embedded_import_without_the_source_file(child):
    from simplecad.kernel.importing import EmbeddedImportFeature
    from simplecad.core.rebuild import Rebuilder

    source = box_document(17)
    assert Rebuilder(source).rebuild().ok
    document = Document()
    sha = document.add_asset(service.serialise_shape(source.body("Box").shape))
    document.add_feature(EmbeddedImportFeature(
        inputs={"asset": sha}, outputs=["Standalone"],
    ))
    child.send({"kind": service.REBUILD, "document": document.to_dict(),
                "assets": dict(document.assets), "stale": [], "force": False})
    while True:
        assert child.poll(60)
        reply = child.recv()
        if reply["kind"] != service.PROGRESS:
            break
    assert reply["kind"] == service.RESULT
    assert reply["report"]["ok"], reply["report"]["summary"]
    shape = service.deserialise_shape(reply["bodies"]["Standalone"])
    assert volume(shape) == pytest.approx(17 * 30 * 10)


def test_unchanged_bodies_are_not_resent(child):
    """Only geometry that actually changed crosses the pipe."""
    document = box_document(40)
    first = ask(child, document)
    assert "Box" in first["bodies"]

    second = ask(child, document)          # identical request
    assert second["report"]["ok"]
    assert second["bodies"] == {}, "an unchanged body must not be resent"


def test_full_reply_resends_geometry_and_removes_stale_parent_bodies(child):
    document = box_document(40)
    ask(child, document)
    # Simulate a body still shown by the parent after an earlier reply was
    # skipped. It has no producing feature in the current history.
    document.bodies["Ghost"] = Body(name="Ghost")
    reply = ask(child, document, full=True)
    assert reply["kind"] == service.RESULT
    assert "Box" in reply["bodies"]
    assert reply["bodies"]["Ghost"] is None


def test_cached_document_sync_allows_preview_without_rebuilding(child):
    from simplecad.core.document import BodyRef
    from simplecad.kernel.operations import BooleanFeature

    document = box_document(40)
    document.add_feature(BoxFeature(
        inputs={"width": 12, "depth": 12, "height": 10, "x": 30},
        outputs=["Tool"],
    ))
    from simplecad.core.rebuild import Rebuilder

    assert Rebuilder(document).rebuild().ok
    child.send({
        "kind": service.SYNC,
        "revision": 8,
        "document": document.to_dict(),
        "bodies": {
            name: service.serialise_shape(body.shape)
            for name, body in document.bodies.items()
        },
    })
    assert child.poll(30)
    assert child.recv() == {
        "kind": service.SYNCED, "revision": 8, "sealed": len(document.features),
    }

    feature = BooleanFeature(inputs={
        "body": BodyRef("Box"), "tools": [BodyRef("Tool")],
        "operation": "join", "keep_tool": False,
    }, outputs=["Box"])
    child.send({"kind": service.PREVIEW, "feature": feature.to_dict(), "token": 9})
    assert child.poll(30)
    reply = child.recv()
    assert reply["kind"] == service.PREVIEWED
    assert reply["token"] == 9
    assert reply["error"] is None
    assert reply["shape"] is not None


def test_an_edited_parameter_produces_new_geometry(child):
    document = box_document(40)
    ask(child, document)

    document.parameters.set("width", "80")
    reply = ask(child, document, stale={document.features[0].id})
    shape = service.deserialise_shape(reply["bodies"]["Box"])
    assert volume(shape) == pytest.approx(80 * 30 * 10)


def test_the_cache_survives_between_requests(child):
    """An untouched feature must not be rebuilt when a sibling changes."""
    document = box_document(40)
    document.add_feature(
        CylinderFeature(inputs={"radius": 5, "height": 10, "x": 200},
                        outputs=["Pin"])
    )
    ask(child, document)

    document.features[0].inputs["depth"] = 60
    reply = ask(child, document, stale={document.features[0].id})
    rebuilt = set(reply["report"]["rebuilt"])
    assert document.features[0].id in rebuilt
    assert document.features[1].id not in rebuilt, "the pin should have been cached"


def test_deleting_a_feature_tells_the_parent_to_drop_the_body(child):
    document = box_document(40)
    document.add_feature(
        CylinderFeature(inputs={"radius": 5, "height": 10, "x": 200},
                        outputs=["Pin"])
    )
    ask(child, document)

    document.remove_feature(document.features[1].id)
    reply = ask(child, document)
    assert "Pin" in reply["removed"]
    assert reply["bodies"]["Pin"] is None


def test_a_failing_feature_comes_back_as_a_report_not_a_crash(child):
    document = Document("T")
    document.add_feature(
        BoxFeature(inputs={"width": -5, "depth": 10, "height": 10}, outputs=["Box"])
    )
    reply = ask(child, document)
    assert reply["kind"] == service.RESULT
    assert not reply["report"]["ok"]
    assert "greater than zero" in reply["report"]["summary"]


def test_feature_state_travels_back_to_the_parent(child):
    document = box_document(40)
    document.add_feature(
        BoxFeature(inputs={"width": 0, "depth": 10, "height": 10}, outputs=["Bad"])
    )
    reply = ask(child, document)
    states = reply["report"]["features"]
    assert states[document.features[0].id]["state"] == "ok"
    assert states[document.features[1].id]["state"] == "failed"
    assert "greater than zero" in states[document.features[1].id]["message"]


def test_the_child_keeps_serving_after_a_failure(child):
    """One bad feature must not take the geometry process down."""
    broken = Document("T")
    broken.add_feature(
        BoxFeature(inputs={"width": -1, "depth": 1, "height": 1}, outputs=["Box"])
    )
    ask(child, broken)

    reply = ask(child, box_document(25))
    assert reply["report"]["ok"]
    shape = service.deserialise_shape(reply["bodies"]["Box"])
    assert volume(shape) == pytest.approx(25 * 30 * 10)


def test_feature_outputs_travel_back_to_the_parent(child):
    """Features assign their own output names during execute.

    With the rebuild happening in another process, the parent's copy of the
    feature never learns what body it produced -- and dependency lookup,
    deletion and reordering all read `feature.outputs`.
    """
    document = Document("T")
    feature = document.add_feature(
        BoxFeature(inputs={"width": 20, "depth": 20, "height": 20})
    )
    assert feature.outputs == [feature.name]
    assert feature.output_ids, "output identity is allocated before execution"

    reply = ask(child, document)
    reported = reply["report"]["features"][feature.id]
    assert reported["outputs"], "the child must report what it produced"

    from simplecad.ui.geometry_client import apply_result  # noqa: PLC0415

    apply_result(document, reply)
    assert feature.outputs == reported["outputs"]
    assert document.producer_of(feature.outputs[0]) is feature


def test_a_choice_the_feature_made_comes_back(child):
    """A thread feature picks its own size; the parent needs to know which."""
    from simplecad.core.document import BodyRef
    from simplecad.core.naming import make_ref, sub_shapes
    from simplecad.kernel.detect import analyse_cylinder
    from simplecad.kernel.operations import ThreadFeature

    document = Document("T")
    post = document.add_feature(
        CylinderFeature(inputs={"radius": 4, "height": 20}, outputs=["Post"])
    )
    from simplecad.core.rebuild import Rebuilder

    Rebuilder(document).rebuild()
    face = next(
        f for f in sub_shapes(document.bodies["Post"].shape, "face")
        if analyse_cylinder(f) is not None
    )
    thread = document.add_feature(
        ThreadFeature(
            inputs={
                "body": BodyRef("Post"),
                "face": make_ref(document.bodies["Post"].shape, face, post.id,
                                 body="Post"),
                "length": 10,
            },
            outputs=["Post"],
        )
    )
    assert "designation" not in thread.inputs

    reply = ask(child, document, timeout=180)
    assert reply["report"]["ok"], reply["report"]["summary"]

    from simplecad.ui.geometry_client import apply_result  # noqa: PLC0415

    apply_result(document, reply)
    # P8, not M8: an 8 mm post is offered the printable coarse size first, and
    # what this is really checking is that whichever size the child settled on
    # travels back to the parent rather than being recomputed there.
    assert thread.inputs.get("designation") == "P8", (
        "the size the child chose must reach the parent"
    )


def test_solid_text_builds_inside_the_geometry_process(child):
    """Font discovery and glyph B-Reps must not depend on the Qt parent."""
    from simplecad.core.document import BodyRef
    from simplecad.core.naming import fingerprint, make_ref, sub_shapes
    from simplecad.core.rebuild import Rebuilder
    from simplecad.kernel.text import TextFeature

    document = box_document(60)
    document.features[0].inputs.update({"depth": 30, "height": 10})
    document.features[0].outputs = ["Box"]
    assert Rebuilder(document).rebuild().ok
    shape = document.bodies["Box"].shape
    top = max(
        (
            face for face in sub_shapes(shape, "face")
            if (mark := fingerprint(face, "face")).geometry == "plane"
            and mark.direction and mark.direction[2] > 0.99
        ),
        key=lambda face: fingerprint(face, "face").center[2],
    )
    document.add_feature(TextFeature(inputs={
        "body": BodyRef("Box"),
        "face": make_ref(shape, top, document.features[0].id, body="Box"),
        "text": "A8",
        "font_family": "sans-serif",
        "mode": "raised",
        "text_height": 8,
        "depth": 1,
    }, outputs=["Box"]))

    reply = ask(child, document)
    assert reply["kind"] == service.RESULT
    assert reply["report"]["ok"], reply["report"]["summary"]
    rebuilt = service.deserialise_shape(reply["bodies"]["Box"])
    assert is_valid(rebuilt)
    assert volume(rebuilt) > 60 * 30 * 10


# ----------------------------------------------------------------------
# Previews -- the fillet solver, kept out of the parent
# ----------------------------------------------------------------------
def preview(pipe, feature, token=1, timeout=120):
    pipe.send({
        "kind": service.PREVIEW,
        "feature": feature.to_dict(),
        "token": token,
    })
    assert pipe.poll(timeout), "no preview reply from the geometry process"
    return pipe.recv()


def built_box(child, width=40.0):
    """A document whose Box body holds the geometry the child just built.

    The edge reference has to be taken against the same shape the child will
    resolve it against, which is exactly what the panel does: it references the
    geometry on screen.
    """
    from simplecad.core.document import Body

    document = box_document(width)
    reply = ask(child, document)
    body = Body(name="Box")
    body.shape = service.deserialise_shape(reply["bodies"]["Box"])
    document.bodies["Box"] = body
    return document


def fillet_of(document, radius: float):
    """A fillet feature on the box's first edge, as the panel would send it."""
    from simplecad.core.document import BodyRef
    from simplecad.core.naming import make_ref, sub_shapes
    from simplecad.kernel.operations import FilletFeature

    shape = document.bodies["Box"].shape
    edge = sub_shapes(shape, "edge")[0]
    return FilletFeature(
        inputs={
            "body": BodyRef("Box"),
            "edges": [make_ref(shape, edge, "", kind="edge", body="Box")],
            "radius": float(radius),
        },
        outputs=["Box"],
    )


def test_a_preview_comes_back_as_a_shape(child):
    """The whole point: the fillet is built there, not in the window."""
    document = built_box(child)

    reply = preview(child, fillet_of(document, 2.0))
    assert reply["kind"] == service.PREVIEWED
    assert reply["token"] == 1
    assert reply["error"] is None
    shape = service.deserialise_shape(reply["shape"])
    assert shape is not None
    # A fillet takes material off the corner and nothing else.
    assert volume(shape) < 40 * 30 * 10
    assert volume(shape) > 40 * 30 * 10 * 0.95


def test_a_preview_that_cannot_be_built_answers_none_rather_than_raising(child):
    """A radius past what the edge can carry is an ordinary answer."""
    document = built_box(child)

    reply = preview(child, fillet_of(document, 500.0), token=7)
    assert reply["kind"] == service.PREVIEWED
    assert reply["token"] == 7
    assert reply["shape"] is None
    assert reply["error"]


def test_a_preview_leaves_the_child_document_alone(child):
    """A preview is a question. Asking it must not edit the model.

    Without this, dragging a fillet handle would rewrite the very body the
    preview is computed against, and each frame of the drag would compound on
    the last.
    """
    document = built_box(child)

    preview(child, fillet_of(document, 3.0))

    # Nothing changed, so an unchanged rebuild request must still send nothing
    # back -- which it only can if the preview left the cache and the bodies
    # exactly as they were.
    after = ask(child, document)
    assert after["report"]["ok"]
    assert after["bodies"] == {}, "the preview disturbed the child's document"


def test_the_child_keeps_serving_after_a_preview(child):
    document = built_box(child)
    preview(child, fillet_of(document, 500.0))          # one it cannot build
    reply = ask(child, box_document(50), force=True)
    assert reply["report"]["ok"]
    shape = service.deserialise_shape(reply["bodies"]["Box"])
    assert volume(shape) == pytest.approx(50 * 30 * 10)


def test_a_rebuild_says_which_feature_it_is_on(child):
    """A long rebuild has to prove it is alive, or the parent kills it.

    The parent's deadline used to run from the moment a request was sent, so a
    model that honestly took four minutes was killed at two, resent, killed
    again, and finally rebuilt on the GUI thread -- which looked, to the person
    using it, exactly like the application hanging.
    """
    document = box_document(40)
    for index in range(4):
        document.add_feature(BoxFeature(
            inputs={"width": 8, "depth": 8, "height": 8, "x": 20 * index + 60},
            outputs=[f"Extra{index}"],
        ))
    seen = []
    reply = ask(child, document, progress=seen)

    assert reply["kind"] == service.RESULT
    assert seen, "the child never reported progress"
    first = seen[0]
    assert first["total"] == len(document.features)
    assert first["index"] == 0
    assert first["feature"]
    assert {entry["id"] for entry in seen} <= {f.id for f in document.features}


def test_a_synced_prefix_leaves_only_the_appended_feature_to_build(child):
    """Opening a saved project must not cost a replay of its whole history."""
    from simplecad.core.document import BodyRef
    from simplecad.core.rebuild import Rebuilder
    from simplecad.kernel.operations import BooleanFeature

    document = box_document(40)
    document.add_feature(BoxFeature(
        inputs={"width": 12, "depth": 12, "height": 10, "x": 30},
        outputs=["Tool"],
    ))
    assert Rebuilder(document).rebuild().ok
    child.send({
        "kind": service.SYNC, "revision": 3, "document": document.to_dict(),
        "bodies": {
            name: service.serialise_shape(body.shape)
            for name, body in document.bodies.items()
        },
    })
    assert child.poll(30)
    assert child.recv()["sealed"] == len(document.features)

    document.add_feature(BooleanFeature(inputs={
        "body": BodyRef("Box"), "tools": [BodyRef("Tool")],
        "operation": "join", "keep_tool": False,
    }, outputs=["Box"]))
    reply = ask(child, document)

    assert reply["kind"] == service.RESULT
    report = reply["report"]
    assert report["rebuilt"] == [document.features[-1].id]
    assert len(report["sealed"]) == len(document.features) - 1


def test_boolean_commit_reuses_the_exact_preview(child):
    from simplecad.core.document import BodyRef
    from simplecad.kernel.operations import BooleanFeature

    document = box_document()
    document.add_feature(BoxFeature(inputs={"width": 20, "depth": 30, "height": 10, "x": 30}, outputs=["Tool"]))
    assert ask(child, document)["report"]["ok"]
    feature = BooleanFeature(inputs={"body": BodyRef("Box"), "tools": [BodyRef("Tool")], "operation": "cut"}, outputs=["Box"])
    answer = preview(child, feature)
    assert answer["shape"] is not None
    document.add_feature(feature)
    result = ask(child, document)
    assert result["preview_reused"]
    assert feature.id not in result["report"]["rebuilt"]
    assert volume(service.deserialise_shape(result["bodies"]["Box"])) == pytest.approx(volume(service.deserialise_shape(answer["shape"])))


def test_parameter_change_without_explicit_stale_rebuilds_in_worker(child):
    document = box_document(20)
    assert ask(child, document)["report"]["ok"]
    document.parameters.set("width", "35")
    result = ask(child, document)
    assert volume(service.deserialise_shape(result["bodies"]["Box"])) == pytest.approx(35 * 30 * 10)
