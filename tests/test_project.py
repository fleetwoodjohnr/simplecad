"""Saving and reopening a project.

The promise being tested is that a saved file stays *editable*: reopening gives
back the feature graph, the expressions and the references, so a parameter can
still be changed afterwards and the model rebuilds.
"""

from __future__ import annotations

import zipfile

import pytest

from simplecad.core.document import BodyRef, Document
from simplecad.core.errors import CadError
from simplecad.core.naming import fingerprint, make_ref, sub_shapes
from simplecad.core.project import EXTENSION, load, read_thumbnail, save
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel import operations, primitives  # noqa: F401 - registers types
from simplecad.kernel.occ import bounding_box, volume
from simplecad.kernel.operations import AlignFeature, FilletFeature
from simplecad.kernel.primitives import BoxFeature


def upward_face(shape):
    faces = [(f, fingerprint(f, "face")) for f in sub_shapes(shape, "face")]
    up = [(f, p) for f, p in faces
          if p.geometry == "plane" and p.direction and p.direction[2] > 0.99]
    return max(up, key=lambda item: item[1].center[2])[0]


def downward_face(shape):
    faces = [(f, fingerprint(f, "face")) for f in sub_shapes(shape, "face")]
    down = [(f, p) for f, p in faces
            if p.geometry == "plane" and p.direction and p.direction[2] < -0.99]
    return min(down, key=lambda item: item[1].center[2])[0]


@pytest.fixture
def stacked(tmp_path):
    """Two parts, one stacked on the other, driven by a parameter."""
    document = Document("Bracket")
    document.parameters.set("wall", "3 mm")
    document.parameters.set("height", "12")
    base = document.add_feature(
        BoxFeature(inputs={"width": 60, "depth": 40, "height": "height"}, outputs=["Base"])
    )
    lid = document.add_feature(
        BoxFeature(inputs={"width": 60, "depth": 40, "height": "wall", "x": 150},
                   outputs=["Lid"])
    )
    Rebuilder(document).rebuild()
    document.add_feature(
        AlignFeature(
            inputs={
                "body": BodyRef("Lid"),
                "moving_face": make_ref(
                    document.bodies["Lid"].shape,
                    downward_face(document.bodies["Lid"].shape), lid.id, body="Lid",
                ),
                "target_face": make_ref(
                    document.bodies["Base"].shape,
                    upward_face(document.bodies["Base"].shape), base.id, body="Base",
                ),
                "operation": "stack",
            },
            outputs=["Lid"],
        )
    )
    assert Rebuilder(document).rebuild().ok
    return document


def test_extension_is_added_if_missing(stacked, tmp_path):
    path = save(stacked, str(tmp_path / "part"))
    assert path.endswith(EXTENSION)


def test_archive_holds_the_recipe_and_a_geometry_cache(stacked, tmp_path):
    path = save(stacked, str(tmp_path / "part.scad3"))
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    assert "model.json" in names
    assert "bodies/Base.brep" in names and "bodies/Lid.brep" in names


def test_reopening_restores_geometry_without_rebuilding(stacked, tmp_path):
    path = save(stacked, str(tmp_path / "part.scad3"))
    reopened, cached = load(path)
    assert cached is True
    assert set(reopened.bodies) == {"Base", "Lid"}
    assert volume(reopened.bodies["Base"].shape) == pytest.approx(60 * 40 * 12)
    assert bounding_box(reopened.bodies["Lid"].shape)[0][2] == pytest.approx(12.0, abs=1e-6)


def test_a_reopened_project_is_still_parametric(stacked, tmp_path):
    """The whole point of a native format: change a parameter after reopening."""
    path = save(stacked, str(tmp_path / "part.scad3"))
    reopened, _cached = load(path)

    reopened.parameters.set("height", "30")
    builder = Rebuilder(reopened)
    builder.invalidate()
    report = builder.rebuild()

    assert report.ok, report.summary()
    assert bounding_box(reopened.bodies["Base"].shape)[1][2] == pytest.approx(30.0)
    # And the stack relationship survived the round trip.
    assert bounding_box(reopened.bodies["Lid"].shape)[0][2] == pytest.approx(30.0, abs=1e-6)


def test_references_survive_the_round_trip(stacked, tmp_path):
    """A fillet's edge references must still resolve after reopening."""
    shape = stacked.bodies["Base"].shape
    base_feature = stacked.features[0]
    vertical = [
        e for e in sub_shapes(shape, "edge")
        if (p := fingerprint(e, "edge")).geometry == "line"
        and p.direction and abs(p.direction[2]) > 0.99
    ]
    stacked.add_feature(
        FilletFeature(
            inputs={
                "body": BodyRef("Base"),
                "edges": [make_ref(shape, e, base_feature.id, body="Base")
                          for e in vertical],
                "radius": 4,
            },
            outputs=["Base"],
        )
    )
    assert Rebuilder(stacked).rebuild().ok
    filleted = volume(stacked.bodies["Base"].shape)

    path = save(stacked, str(tmp_path / "part.scad3"))
    reopened, _cached = load(path)
    builder = Rebuilder(reopened)
    builder.invalidate()
    assert builder.rebuild().ok
    assert volume(reopened.bodies["Base"].shape) == pytest.approx(filleted, rel=1e-9)


def test_the_model_rebuilds_when_the_cache_is_absent(stacked, tmp_path):
    """The cache is an optimisation; the recipe is the file."""
    import shutil

    path = save(stacked, str(tmp_path / "part.scad3"))
    stripped = str(tmp_path / "nocache.scad3")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(stripped, "w") as target:
        target.writestr("model.json", source.read("model.json"))

    reopened, cached = load(stripped)
    assert cached is False
    assert Rebuilder(reopened).rebuild().ok
    assert volume(reopened.bodies["Base"].shape) == pytest.approx(60 * 40 * 12)


def test_thumbnail_round_trips(stacked, tmp_path):
    path = save(stacked, str(tmp_path / "part.scad3"), thumbnail=b"\x89PNG\r\n fake")
    assert read_thumbnail(path) == b"\x89PNG\r\n fake"


def test_opening_something_that_is_not_a_project_says_so(tmp_path):
    junk = tmp_path / "notaproject.scad3"
    junk.write_text("this is not a zip")
    with pytest.raises(CadError) as caught:
        load(str(junk))
    assert "not a SimpleCAD project" in str(caught.value)
    assert "Import" in caught.value.suggestion


def test_a_file_from_a_newer_version_is_refused_clearly(stacked, tmp_path):
    import json

    path = save(stacked, str(tmp_path / "part.scad3"))
    future = str(tmp_path / "future.scad3")
    with zipfile.ZipFile(path) as source:
        payload = json.loads(source.read("model.json"))
    payload["format"] = 99
    with zipfile.ZipFile(future, "w") as target:
        target.writestr("model.json", json.dumps(payload))

    with pytest.raises(CadError) as caught:
        load(future)
    assert "newer SimpleCAD" in str(caught.value)


# ----------------------------------------------------------------------
# Sketches in projects
# ----------------------------------------------------------------------
def sketched(width=40.0, height=25.0, depth=10.0):
    """A document whose body comes from a constrained sketch."""
    from simplecad.kernel.sketch_features import ExtrudeFeature, SketchFeature
    from simplecad.sketch.sketch import Sketch

    document = Document("Sketched")
    sketch = Sketch("Profile")
    lines = sketch.add_rectangle(0, 0, width, height)
    sketch.constrain("fix", [sketch.points[lines[0].start].id])
    sketch.constrain("distance", [lines[0].start, lines[0].end], width)
    sketch.constrain("distance", [lines[1].start, lines[1].end], height)

    feature = document.add_feature(SketchFeature(inputs={"sketch": sketch}))
    document.add_feature(
        ExtrudeFeature(
            inputs={"sketch": feature.name, "distance": depth}, outputs=["Solid"]
        )
    )
    assert Rebuilder(document).rebuild().ok
    return document


def test_a_document_containing_a_sketch_serialises():
    """History.record() calls to_dict() on every edit, so this path is hot."""
    import json

    document = sketched()
    text = json.dumps(document.to_dict())      # must not raise on a Sketch object
    assert '"kind": "line"' in text
    assert "distance" in text


def test_a_sketched_project_reopens_and_is_still_parametric(tmp_path):
    path = save(sketched(40, 25, 10), str(tmp_path / "sketched.scad3"))
    reopened, cached = load(path)
    assert cached
    assert volume(reopened.bodies["Solid"].shape) == pytest.approx(40 * 25 * 10)

    builder = Rebuilder(reopened)
    builder.invalidate()
    assert builder.rebuild().ok
    assert volume(reopened.bodies["Solid"].shape) == pytest.approx(40 * 25 * 10)


def test_adding_geometry_to_a_reopened_sketch_does_not_overwrite_it(tmp_path):
    """Sketch ids come from a process-global counter that restarts at 1.

    A reopened sketch brings its old ids with it, so without reserving them the
    next point issued reuses an existing id and silently replaces that corner --
    taking its constraints with it.
    """
    import itertools

    from simplecad.sketch import entities
    from simplecad.sketch.sketch import Sketch

    path = save(sketched(), str(tmp_path / "sketched.scad3"))

    # Simulate a fresh session. The counter is module-global and starts at 1 in
    # every new process; without this the test runs with a counter already
    # advanced past every id in the file and cannot see the collision at all.
    entities._COUNTER = itertools.count(1)

    reopened, _cached = load(path)

    feature = next(f for f in reopened.features if f.type_name == "sketch")
    sketch = feature.sketch()
    assert isinstance(sketch, Sketch)
    before = len(sketch.points)
    existing = set(sketch.points)

    added = sketch.add_point(99.0, 99.0)

    assert added.id not in existing, f"reused an existing id: {added.id}"
    assert len(sketch.points) == before + 1
    assert all(point_id in sketch.points for point_id in existing), (
        "an existing point was overwritten"
    )


def test_a_reopened_sketch_can_be_re_dimensioned(tmp_path):
    path = save(sketched(40, 25, 10), str(tmp_path / "sketched.scad3"))
    reopened, _cached = load(path)

    feature = next(f for f in reopened.features if f.type_name == "sketch")
    sketch = feature.sketch()
    width_constraint = next(
        c for c in sketch.constraints if c.kind == "distance" and c.value == 40.0
    )
    width_constraint.value = 60.0

    builder = Rebuilder(reopened)
    builder.invalidate()
    assert builder.rebuild().ok
    assert volume(reopened.bodies["Solid"].shape) == pytest.approx(60 * 25 * 10)
