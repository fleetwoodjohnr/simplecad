"""The workflow the product spec names, end to end.

    Create Box -> Pull Face -> Create Second Part -> Stack Faces ->
    Add Hole -> Create Automatic Thread Pair -> Fillet -> Export

This is the acceptance criterion for the whole first milestone set, so it is
written as one continuous story rather than isolated units: each step builds on
the geometry the previous one produced, exactly as it would in the app.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from simplecad.core.document import BodyRef, Document
from simplecad.core.naming import fingerprint, make_ref, sub_shapes
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel import operations, primitives  # noqa: F401 - registers features
from simplecad.kernel.io_formats import export_shapes
from simplecad.kernel.occ import bounding_box, is_valid, volume
from simplecad.kernel.operations import (
    AlignFeature, FilletFeature, HoleFeature, PushPullFeature,
    ThreadedConnectionFeature,
)
from simplecad.kernel.primitives import BoxFeature, CylinderFeature


def face_where(shape, predicate):
    """The one face satisfying *predicate*, described by its fingerprint."""
    matches = [f for f in sub_shapes(shape, "face") if predicate(fingerprint(f, "face"))]
    assert len(matches) == 1, f"expected exactly one face, found {len(matches)}"
    return matches[0]


def top_face(shape):
    """The highest upward-facing planar face."""
    candidates = [
        (f, fingerprint(f, "face")) for f in sub_shapes(shape, "face")
    ]
    upward = [
        (f, p) for f, p in candidates
        if p.geometry == "plane" and p.direction and p.direction[2] > 0.99
    ]
    assert upward, "no upward face"
    return max(upward, key=lambda item: item[1].center[2])[0]


def bottom_face(shape):
    candidates = [(f, fingerprint(f, "face")) for f in sub_shapes(shape, "face")]
    downward = [
        (f, p) for f, p in candidates
        if p.geometry == "plane" and p.direction and p.direction[2] < -0.99
    ]
    assert downward, "no downward face"
    return min(downward, key=lambda item: item[1].center[2])[0]


@pytest.fixture
def doc():
    document = Document("Acceptance")
    document.parameters.set("wall", "3 mm")
    return document


def test_the_full_modelling_workflow(doc, tmp_path):
    builder = Rebuilder(doc)

    # 1. Create Box ----------------------------------------------------
    base = doc.add_feature(
        BoxFeature(inputs={"width": 60, "depth": 40, "height": 12}, outputs=["Base"])
    )
    assert builder.rebuild().ok
    assert volume(doc.bodies["Base"].shape) == pytest.approx(60 * 40 * 12)

    # 2. Pull Face -- raise the top by 8 mm ----------------------------
    face = top_face(doc.bodies["Base"].shape)
    pull = doc.add_feature(
        PushPullFeature(
            inputs={
                "body": BodyRef("Base"),
                "face": make_ref(doc.bodies["Base"].shape, face, base.id, body="Base"),
                "distance": 8,
            },
            outputs=["Base"],
        )
    )
    report = builder.rebuild()
    assert report.ok, report.summary()
    assert volume(doc.bodies["Base"].shape) == pytest.approx(60 * 40 * 20)
    low, high = bounding_box(doc.bodies["Base"].shape)
    assert high[2] == pytest.approx(20.0)

    # 3. Create Second Part -------------------------------------------
    lid = doc.add_feature(
        BoxFeature(
            inputs={"width": 60, "depth": 40, "height": "wall", "x": 150},
            outputs=["Lid"],
        )
    )
    assert builder.rebuild().ok
    assert bounding_box(doc.bodies["Lid"].shape)[0][0] == pytest.approx(150.0)

    # 4. Stack Faces -- lid onto base, no manual maths -----------------
    doc.add_feature(
        AlignFeature(
            inputs={
                "body": BodyRef("Lid"),
                "moving_face": make_ref(
                    doc.bodies["Lid"].shape, bottom_face(doc.bodies["Lid"].shape),
                    lid.id, body="Lid",
                ),
                "target_face": make_ref(
                    doc.bodies["Base"].shape, top_face(doc.bodies["Base"].shape),
                    pull.id, body="Base",
                ),
                "operation": "stack",
            },
            outputs=["Lid"],
        )
    )
    report = builder.rebuild()
    assert report.ok, report.summary()
    low, high = bounding_box(doc.bodies["Lid"].shape)
    assert low[2] == pytest.approx(20.0, abs=1e-6), "lid must seat on the base"
    assert high[2] == pytest.approx(23.0, abs=1e-6)
    assert (low[0] + high[0]) / 2 == pytest.approx(30.0, abs=1e-6), "and be centred"

    # 5. Add Hole -- through the lid ------------------------------------
    lid_top = top_face(doc.bodies["Lid"].shape)
    doc.add_feature(
        HoleFeature(
            inputs={
                "body": BodyRef("Lid"),
                "face": make_ref(doc.bodies["Lid"].shape, lid_top, lid.id, body="Lid"),
                "diameter": 12,
                "position": (30.0, 20.0, 23.0),
                "depth_mode": "through",
            },
            outputs=["Lid"],
        )
    )
    report = builder.rebuild()
    assert report.ok, report.summary()
    assert is_valid(doc.bodies["Lid"].shape)

    from simplecad.kernel.detect import cylindrical_faces

    bores = cylindrical_faces(doc.bodies["Lid"].shape)
    assert len(bores) == 1
    assert bores[0][1].internal is True
    assert bores[0][1].diameter == pytest.approx(12.0)

    # 6. Fillet the base's vertical edges --------------------------------
    shape = doc.bodies["Base"].shape
    vertical = [
        f for f in sub_shapes(shape, "edge")
        if (p := fingerprint(f, "edge")).geometry == "line"
        and p.direction and abs(p.direction[2]) > 0.99
    ]
    assert len(vertical) == 4
    doc.add_feature(
        FilletFeature(
            inputs={
                "body": BodyRef("Base"),
                "edges": [
                    make_ref(shape, e, pull.id, body="Base") for e in vertical
                ],
                "radius": 4,
            },
            outputs=["Base"],
        )
    )
    report = builder.rebuild()
    assert report.ok, report.summary()
    filleted = volume(doc.bodies["Base"].shape)
    assert filleted < 60 * 40 * 20, "a fillet must remove material"
    assert is_valid(doc.bodies["Base"].shape)

    # 7. Export ----------------------------------------------------------
    shapes = [b.shape for b in doc.visible_bodies()]
    for extension in (".step", ".stl", ".3mf", ".obj"):
        out = str(tmp_path / f"part{extension}")
        export_shapes(shapes, out)
        assert os.path.getsize(out) > 200, extension


def test_editing_an_early_parameter_updates_everything_downstream(doc):
    """The parametric promise: change the base, and the stack follows."""
    builder = Rebuilder(doc)
    base = doc.add_feature(
        BoxFeature(
            inputs={"width": 60, "depth": 40, "height": "height"}, outputs=["Base"]
        )
    )
    doc.parameters.set("height", "12")
    lid = doc.add_feature(
        BoxFeature(inputs={"width": 60, "depth": 40, "height": 3, "x": 150}, outputs=["Lid"])
    )
    assert builder.rebuild().ok

    doc.add_feature(
        AlignFeature(
            inputs={
                "body": BodyRef("Lid"),
                "moving_face": make_ref(
                    doc.bodies["Lid"].shape, bottom_face(doc.bodies["Lid"].shape),
                    lid.id, body="Lid",
                ),
                "target_face": make_ref(
                    doc.bodies["Base"].shape, top_face(doc.bodies["Base"].shape),
                    base.id, body="Base",
                ),
                "operation": "stack",
            },
            outputs=["Lid"],
        )
    )
    assert builder.rebuild().ok
    assert bounding_box(doc.bodies["Lid"].shape)[0][2] == pytest.approx(12.0, abs=1e-6)

    # Make the base taller. The lid must ride up with it, because the align is
    # re-solved from live faces rather than baked in at commit time.
    doc.parameters.set("height", "25")
    builder.invalidate_parameter("height")
    report = builder.rebuild()
    assert report.ok, report.summary()
    assert bounding_box(doc.bodies["Base"].shape)[1][2] == pytest.approx(25.0)
    assert bounding_box(doc.bodies["Lid"].shape)[0][2] == pytest.approx(25.0, abs=1e-6), (
        "the stacked lid must follow the face it was stacked onto"
    )


@pytest.mark.slow
def test_automatic_thread_pair_between_two_parts(doc):
    """Select a post and its mating hole, get a matched printable pair."""
    from simplecad.kernel.detect import cylindrical_faces

    builder = Rebuilder(doc)
    post = doc.add_feature(
        CylinderFeature(inputs={"radius": 6, "height": 20}, outputs=["Post"])
    )
    plate = doc.add_feature(
        BoxFeature(inputs={"width": 40, "depth": 40, "height": 10, "x": 100}, outputs=["Plate"])
    )
    assert builder.rebuild().ok

    plate_top = top_face(doc.bodies["Plate"].shape)
    doc.add_feature(
        HoleFeature(
            inputs={
                "body": BodyRef("Plate"),
                "face": make_ref(doc.bodies["Plate"].shape, plate_top, plate.id, body="Plate"),
                "diameter": 12,
                "position": (120.0, 20.0, 10.0),
                "depth_mode": "through",
            },
            outputs=["Plate"],
        )
    )
    assert builder.rebuild().ok

    post_face = cylindrical_faces(doc.bodies["Post"].shape)[0][0]
    hole_face = cylindrical_faces(doc.bodies["Plate"].shape)[0][0]

    connection = doc.add_feature(
        ThreadedConnectionFeature(
            inputs={
                "body_a": BodyRef("Post"),
                "body_b": BodyRef("Plate"),
                "face_a": make_ref(doc.bodies["Post"].shape, post_face, post.id, body="Post"),
                "face_b": make_ref(doc.bodies["Plate"].shape, hole_face, plate.id, body="Plate"),
                "clearance": "normal",
                "length": 10,
            },
            outputs=["Post", "Plate"],
        )
    )
    report = builder.rebuild()
    assert report.ok, report.summary()

    # It picked the size itself, and it is the one a person would pick.
    assert connection.inputs["designation"] == "M12"
    assert "M12" in connection.message
    assert is_valid(doc.bodies["Post"].shape)
    assert is_valid(doc.bodies["Plate"].shape)

    # One node, two bodies changed.
    assert set(connection.outputs) == {"Post", "Plate"}
