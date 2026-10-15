from __future__ import annotations

import pytest

from simplecad.core.document import BodyRef, Document
from simplecad.core.history import History
from simplecad.core.model_clipboard import (
    ModelFragment, copy_fragment, paste_fragment,
)
from simplecad.core.rebuild import Rebuilder
from simplecad.core.naming import make_ref, sub_shapes
from simplecad.core.project import load, save
from simplecad.kernel.occ import bounding_box, volume
from simplecad.kernel.operations import BooleanFeature, ChamferFeature, FilletFeature, MoveFeature
from simplecad.kernel.primitives import BoxFeature, CylinderFeature
from simplecad.kernel.split import SplitFeature


def _box_history() -> tuple[Document, Rebuilder]:
    document = Document("Clipboard")
    document.add_feature(BoxFeature(
        inputs={"width": "10", "depth": "12", "height": "14"},
        outputs=["Box"],
    ))
    document.add_feature(MoveFeature(
        inputs={"body": BodyRef("Box"), "dx": 5}, outputs=["Box"],
    ))
    rebuilder = Rebuilder(document)
    assert rebuilder.rebuild(force=True).ok
    return document, rebuilder


def test_fragment_round_trips_through_json_data():
    document, _rebuilder = _box_history()
    fragment = copy_fragment(document, ["Box"])

    restored = ModelFragment.from_dict(fragment.to_dict())

    assert restored.items == ["Box"]
    assert restored.root_bodies == ["Box"]
    assert len(restored.features) == 2


def test_paste_clones_the_whole_history_with_new_identities_and_offset():
    document, _rebuilder = _box_history()
    source_bounds = bounding_box(document.body("Box").shape)
    fragment = copy_fragment(document, ["Box"], bounds=source_bounds)
    original_ids = {feature.id for feature in document.features}

    clones, items = paste_fragment(document, fragment, (20.0, 0.0, 0.0))
    report = Rebuilder(document).rebuild(force=True)

    assert report.ok, report.summary()
    assert items == ["Box2"]
    assert original_ids.isdisjoint(feature.id for feature in clones)
    copied_bounds = bounding_box(document.body("Box2").shape)
    assert copied_bounds[0][0] - source_bounds[0][0] == pytest.approx(20.0)

    copied_box = next(
        feature for feature in clones
        if feature.type_name == "box" and "Box2" in feature.outputs
    )
    copied_box.inputs["width"] = "25"
    assert Rebuilder(document).rebuild(force=True).ok
    assert bounding_box(document.body("Box").shape)[1][0] - source_bounds[0][0] == pytest.approx(10.0)
    copied_width = (
        bounding_box(document.body("Box2").shape)[1][0]
        - bounding_box(document.body("Box2").shape)[0][0]
    )
    assert copied_width == pytest.approx(25.0)

    _more, second_items = paste_fragment(document, fragment, (40.0, 0.0, 0.0))
    assert Rebuilder(document).rebuild(force=True).ok
    assert second_items == ["Box3"]


def test_paste_preserves_nested_group_structure():
    document = Document("Groups")
    for name, x in (("A", 0), ("B", 20)):
        document.add_feature(BoxFeature(
            inputs={"width": 5, "depth": 5, "height": 5, "x": x},
            outputs=[name],
        ))
    assert Rebuilder(document).rebuild(force=True).ok
    inner = document.add_group(["A", "B"], "Pair")
    outer = document.add_group([inner.name], "Assembly")

    fragment = copy_fragment(document, [outer.name])
    _features, items = paste_fragment(document, fragment, (40, 0, 0))
    assert Rebuilder(document).rebuild(force=True).ok

    assert items == ["Assembly2"]
    assert document.expand(items) == ["A2", "B2"]
    assert document.group("Assembly2").members == ["Pair2"]


def test_pasting_cylinder_keeps_later_history_and_body_appearance():
    document = Document("Paste history")
    document.add_feature(CylinderFeature(
        inputs={"radius": 6, "height": 12}, outputs=["Cylinder"],
    ))
    document.add_feature(MoveFeature(
        inputs={"body": BodyRef("Cylinder"), "dx": 15}, outputs=["Cylinder"],
    ))
    document.add_feature(BoxFeature(
        inputs={"width": 9, "depth": 8, "height": 7}, outputs=["LaterBox"],
    ))
    document.add_feature(MoveFeature(
        inputs={"body": BodyRef("LaterBox"), "dy": 25}, outputs=["LaterBox"],
    ))
    assert Rebuilder(document).rebuild(force=True).ok
    document.body("LaterBox").color = "#dd8844"
    fragment = copy_fragment(document, ["Cylinder"])
    original_features = [feature.to_dict() for feature in document.features]
    original_bounds = bounding_box(document.body("LaterBox").shape)
    history = History(document)

    history.record("Paste")
    _clones, roots = paste_fragment(document, fragment, (40, 0, 0))
    assert roots == ["Cylinder2"]
    assert [feature.to_dict() for feature in document.features[:4]] == original_features
    assert Rebuilder(document).rebuild(force=True).ok
    assert bounding_box(document.body("LaterBox").shape) == original_bounds
    assert document.body("LaterBox").color == "#dd8844"

    assert history.undo() is not None
    assert Rebuilder(document).rebuild(force=True).ok
    assert [feature.to_dict() for feature in document.features] == original_features
    assert "Cylinder2" not in document.bodies
    assert document.body("LaterBox").color == "#dd8844"
    assert history.redo() is not None
    assert Rebuilder(document).rebuild(force=True).ok
    assert "Cylinder2" in document.bodies
    assert bounding_box(document.body("LaterBox").shape) == original_bounds


# ----------------------------------------------------------------------
# Modified bodies: a split half, booleans, edge finishes
# ----------------------------------------------------------------------
def _split_cylinder() -> Document:
    document = Document("Split")
    document.add_feature(CylinderFeature(
        inputs={"radius": "10", "height": "30"}, outputs=["Cylinder"],
    ))
    document.add_feature(SplitFeature(
        inputs={"body": BodyRef("Cylinder"), "normal": [1, 0, 0], "origin": [0, 0, 0],
                "position": 0.0, "names": ["Cylinder A", "Cylinder B"]},
        outputs=["Cylinder A", "Cylinder B"],
    ))
    assert Rebuilder(document).rebuild(force=True).ok
    return document


def _state(document, name):
    shape = document.body(name).shape
    return volume(shape), bounding_box(shape)


def _edge_near(shape, point):
    def distance(edge):
        low, high = bounding_box(edge)
        return sum(((lo + hi) / 2 - p) ** 2 for lo, hi, p in zip(low, high, point))
    return min(sub_shapes(shape, "edge"), key=distance)


def _finish(document, kind, name, point, size):
    shape = document.body(name).shape
    ref = make_ref(shape, _edge_near(shape, point), document.producer_of(name).id,
                   kind="edge", body=name)
    feature = (FilletFeature(inputs={"body": BodyRef(name), "edges": [ref], "radius": size},
                             outputs=[name])
               if kind == "fillet" else
               ChamferFeature(inputs={"body": BodyRef(name), "edges": [ref], "distance": size},
                              outputs=[name]))
    return document.add_feature(feature)


def test_pasting_one_split_half_makes_an_independent_half():
    document = _split_cylinder()
    originals = {name: _state(document, name) for name in ("Cylinder A", "Cylinder B")}
    producer = document.producer_of("Cylinder A").id

    clones, items = paste_fragment(document, copy_fragment(document, ["Cylinder A"]), (40, 0, 0))
    report = Rebuilder(document).rebuild(force=True)

    assert report.ok, report.summary()
    assert items == ["Cylinder A2"]
    split = next(clone for clone in clones if clone.type_name == "split")
    # The unused twin is not dragged along as a hidden body.
    assert split.outputs == ["Cylinder A2"]
    assert document.producer_of("Cylinder A").id == producer
    assert {name: _state(document, name) for name in originals} == originals
    copy_volume, (low, _high) = _state(document, "Cylinder A2")
    assert copy_volume == pytest.approx(originals["Cylinder A"][0])
    assert low[0] - originals["Cylinder A"][1][0][0] == pytest.approx(40.0)

    # Cut, fillet and pull the duplicate: the original half never moves.
    _finish(document, "fillet", "Cylinder A2", (40, 10, 15), 1.0)
    document.add_feature(BoxFeature(
        inputs={"width": 4, "depth": 4, "height": 40, "x": 33, "y": -2, "z": -5},
        outputs=["Notch"],
    ))
    document.add_feature(BooleanFeature(
        inputs={"body": BodyRef("Cylinder A2"), "tools": [BodyRef("Notch")], "operation": "cut"},
        outputs=["Cylinder A2"],
    ))
    report = Rebuilder(document).rebuild(force=True)
    assert report.ok, report.summary()
    assert _state(document, "Cylinder A2")[0] < copy_volume
    assert {name: _state(document, name) for name in originals} == originals


def test_a_pasted_split_half_reuses_its_copied_geometry(monkeypatch):
    document = _split_cylinder()
    clones, _ = paste_fragment(document, copy_fragment(document, ["Cylinder A"]), (40, 0, 0))
    split = next(clone for clone in clones if clone.type_name == "split")
    monkeypatch.setattr(split, "execute", lambda _ctx: pytest.fail("Copied history replayed"))

    report = Rebuilder(document).rebuild()

    assert report.ok, report.summary()
    assert split.id in report.sealed


def test_renaming_a_split_half_survives_a_rebuild():
    document = _split_cylinder()
    document.rename_body("Cylinder A", "Half")

    assert Rebuilder(document).rebuild(force=True).ok
    assert sorted(document.bodies) == ["Cylinder B", "Half"]
    assert document.body("Half").shape is not None
    assert document.features[-1].inputs["names"] == ["Half", "Cylinder B"]


def _cut_and_finished_box() -> Document:
    document = Document("Machined")
    document.add_feature(BoxFeature(
        inputs={"width": 40, "depth": 30, "height": 20}, outputs=["Box"],
    ))
    document.add_feature(CylinderFeature(
        inputs={"radius": 5, "height": 40, "x": 20, "y": 15, "z": -10}, outputs=["Bore"],
    ))
    document.add_feature(BoxFeature(
        inputs={"width": 6, "depth": 40, "height": 5, "x": 5, "y": -5, "z": 15},
        outputs=["Slot"],
    ))
    document.add_feature(BooleanFeature(
        inputs={"body": BodyRef("Box"), "tools": [BodyRef("Bore"), BodyRef("Slot")],
                "operation": "cut"},
        outputs=["Box"],
    ))
    assert Rebuilder(document).rebuild(force=True).ok
    _finish(document, "fillet", "Box", (40, 0, 10), 2.0)
    assert Rebuilder(document).rebuild(force=True).ok
    _finish(document, "chamfer", "Box", (0, 30, 10), 1.5)
    report = Rebuilder(document).rebuild(force=True)
    assert report.ok, report.summary()
    return document


def test_duplicate_of_a_cut_filleted_and_chamfered_part_is_independent():
    document = _cut_and_finished_box()
    original = _state(document, "Box")

    clones, [copy] = paste_fragment(document, copy_fragment(document, ["Box"]), (60, 0, 0))
    assert Rebuilder(document).rebuild(force=True).ok
    assert copy != "Box"
    assert _state(document, copy)[0] == pytest.approx(original[0])

    # The duplicate carries its own editable history.
    fillet = next(clone for clone in clones if clone.type_name == "fillet")
    fillet.inputs["radius"] = 4.0
    assert Rebuilder(document).rebuild(force=True).ok
    assert _state(document, copy)[0] < original[0]
    assert _state(document, "Box") == original

    # Delete the whole source history: the duplicate does not notice.
    duplicate = _state(document, copy)
    for feature in [f for f in document.features if f not in clones]:
        document.remove_feature(feature.id)
    report = Rebuilder(document).rebuild(force=True)
    assert report.ok, report.summary()
    assert "Box" not in document.bodies
    assert _state(document, copy)[0] == pytest.approx(duplicate[0])


def test_undo_on_one_duplicate_leaves_the_other_alone():
    document = _split_cylinder()
    history = History(document)
    history.record("Paste")
    paste_fragment(document, copy_fragment(document, ["Cylinder A"]), (40, 0, 0))
    assert Rebuilder(document).rebuild(force=True).ok
    original = _state(document, "Cylinder A")
    plain = _state(document, "Cylinder A2")

    history.record("Fillet")
    _finish(document, "fillet", "Cylinder A2", (40, 10, 15), 1.0)
    assert Rebuilder(document).rebuild(force=True).ok
    filleted = _state(document, "Cylinder A2")
    assert filleted[0] < plain[0]

    history.undo()
    assert Rebuilder(document).rebuild(force=True).ok
    assert _state(document, "Cylinder A2")[0] == pytest.approx(plain[0])
    assert _state(document, "Cylinder A") == original
    history.redo()
    assert Rebuilder(document).rebuild(force=True).ok
    assert _state(document, "Cylinder A2")[0] == pytest.approx(filleted[0])
    assert _state(document, "Cylinder A") == original


def test_duplicates_stay_independent_after_save_and_reopen(tmp_path):
    document = _cut_and_finished_box()
    _clones, [copy] = paste_fragment(document, copy_fragment(document, ["Box"]), (60, 0, 0))
    assert Rebuilder(document).rebuild(force=True).ok
    path = save(document, str(tmp_path / "copies"))

    reopened, _cached = load(path)
    assert Rebuilder(reopened).rebuild(force=True).ok
    original = _state(reopened, "Box")
    assert _state(reopened, copy)[0] == pytest.approx(original[0])
    fillet = next(f for f in reopened.features
                  if f.type_name == "fillet" and f.outputs == [copy])
    fillet.inputs["radius"] = 4.0
    assert Rebuilder(reopened).rebuild(force=True).ok
    assert _state(reopened, "Box") == original
    assert _state(reopened, copy)[0] < original[0]


def test_pasting_a_clip_jointed_part_renames_its_connectors():
    from tests.test_clips import _document_and_feature

    document, rebuilder, feature = _document_and_feature([(-5, 0), (5, 0)], ["Clip", "Clip2"])
    document.add_feature(feature)
    assert rebuilder.rebuild().ok

    clones, items = paste_fragment(document, copy_fragment(document, ["Clip"]), (80, 0, 0))
    report = Rebuilder(document).rebuild(force=True)

    assert report.ok, report.summary()
    assert items == ["Clip3"]
    joint = next(clone for clone in clones if clone.type_name == "clip_joint")
    assert "Clip" not in joint.inputs["connector_names"]
    assert document.producer_of("Clip").id == feature.id
    assert document.body("Clip3").shape is not None


def test_a_paste_that_fails_part_way_is_rolled_back(monkeypatch):
    document = _split_cylinder()
    history = History(document)
    before = document.to_dict()
    fragment = copy_fragment(document, ["Cylinder A"])
    add = document.add_feature
    calls = []

    def failing(feature, index=None):
        calls.append(feature)
        if len(calls) == 2:
            raise ValueError("Simulated failure")
        return add(feature, index)

    monkeypatch.setattr(document, "add_feature", failing)
    history.record("Paste")
    with pytest.raises(ValueError):
        paste_fragment(document, fragment, (40, 0, 0))
    history.cancel()

    assert document.to_dict() == before
    assert not history.can_undo


def _chained(tmp_path, copies: int):
    """Cut, split, fillet, duplicate and stack, then save, reopen and edit."""
    from simplecad.kernel.align import interference_volume
    from simplecad.kernel.detect import analyse_plane
    from simplecad.kernel.operations import AlignFeature

    document = _cut_and_finished_box()
    document.add_feature(SplitFeature(
        inputs={"body": BodyRef("Box"), "normal": [0, 0, 1], "origin": [0, 0, 0],
                "position": 10.0, "names": ["Lower", "Upper"]},
        outputs=["Lower", "Upper"],
    ))
    assert Rebuilder(document).rebuild(force=True).ok
    _finish(document, "fillet", "Upper", (0, 15, 20), 1.0)
    document.add_feature(BoxFeature(
        inputs={"width": 200, "depth": 200, "height": 5, "x": -50, "y": 100}, outputs=["Base"],
    ))
    assert Rebuilder(document).rebuild(force=True).ok
    fragment = copy_fragment(document, ["Upper"])
    names = []
    for index in range(copies):
        _clones, [name] = paste_fragment(document, fragment, (0, 0, 40 * (index + 1)))
        names.append(name)
    report = Rebuilder(document).rebuild(force=True)
    assert report.ok, report.summary()

    base_top = next(f for f in sub_shapes(document.body("Base").shape, "face")
                    if (i := analyse_plane(f)) and i.normal[2] > 0.99)
    for index, name in enumerate(names):
        shape = document.body(name).shape
        cut = next(f for f in sub_shapes(shape, "face")
                   if (i := analyse_plane(f)) and i.normal[2] < -0.99)
        document.add_feature(AlignFeature(
            inputs={"body": BodyRef(name),
                    "moving_face": make_ref(shape, cut, document.producer_of(name).id, body=name),
                    "target_face": make_ref(document.body("Base").shape, base_top,
                                            document.producer_of("Base").id, body="Base"),
                    "operation": "stack", "moving_anchor": "center", "target_anchor": "bottom_left",
                    "u": str(20 + 45 * (index % 4)), "v": str(20 + 35 * (index // 4)),
                    "offset": "0", "flip": False, "rotation": str(90 * index)},
            outputs=[name],
        ))
    report = Rebuilder(document).rebuild(force=True)
    assert report.ok and not report.warnings, report.summary()

    reopened, _cached = load(save(document, str(tmp_path / "chain")))
    report = Rebuilder(reopened).rebuild(force=True)
    assert report.ok, report.summary()
    upper = _state(reopened, "Upper")
    for name in names:
        assert _state(reopened, name)[0] == pytest.approx(upper[0])
        assert interference_volume(reopened.body(name).shape, reopened.body("Base").shape) < 1e-6
    # Edit the first copy's own history; the source and the other copies hold.
    first_fillet = next(f for f in reopened.features
                        if f.type_name == "fillet" and f.outputs == [names[0]])
    first_fillet.inputs["radius"] = 2.0
    others = {name: _state(reopened, name) for name in ["Upper", *names[1:]]}
    assert Rebuilder(reopened).rebuild(force=True).ok
    assert _state(reopened, names[0])[0] < upper[0]
    assert {name: _state(reopened, name) for name in others} == others


def test_a_cut_split_filleted_part_survives_duplicate_stack_save_and_edit(tmp_path):
    _chained(tmp_path, copies=2)


@pytest.mark.slow
def test_many_duplicates_of_a_modified_part_stay_independent(tmp_path):
    _chained(tmp_path, copies=24)
