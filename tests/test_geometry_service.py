"""The out-of-process geometry service.

Covers the wire protocol and the child's incremental behaviour without needing
a GUI. The responsiveness claim itself is measured by
``scripts/check_responsive.py``, which needs a real window.
"""

from __future__ import annotations

import multiprocessing

import pytest

from simplecad.core import geometry_service as service
from simplecad.core.document import Document
from simplecad.kernel.occ import volume
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


def ask(pipe, document, stale=(), force=False, timeout=120):
    pipe.send({
        "kind": service.REBUILD,
        "document": document.to_dict(),
        "stale": list(stale),
        "force": force,
    })
    assert pipe.poll(timeout), "no reply from the geometry process"
    return pipe.recv()


def test_the_child_rebuilds_and_returns_geometry(child):
    reply = ask(child, box_document(40))
    assert reply["kind"] == service.RESULT
    assert reply["report"]["ok"]
    assert "Box" in reply["bodies"]

    shape = service.deserialise_shape(reply["bodies"]["Box"])
    assert volume(shape) == pytest.approx(40 * 30 * 10)


def test_unchanged_bodies_are_not_resent(child):
    """Only geometry that actually changed crosses the pipe."""
    document = box_document(40)
    first = ask(child, document)
    assert "Box" in first["bodies"]

    second = ask(child, document)          # identical request
    assert second["report"]["ok"]
    assert second["bodies"] == {}, "an unchanged body must not be resent"


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
    assert feature.outputs == [], "nothing has named it yet"

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
    assert thread.inputs.get("designation") == "M8", (
        "the size the child chose must reach the parent"
    )
