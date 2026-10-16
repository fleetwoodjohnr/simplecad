"""Every supported operation works on an object whatever was done to it before.

Subjects are objects with different histories -- a primitive, a split half, a
boolean result, a filleted, hollowed, textured or imported part, a group --
and every operation is applied to each. A case passes only if the rebuild
succeeds, the result is a valid solid of the expected size, the original and
any copy stay independent, and the result survives save and reopen.
"""

from __future__ import annotations

import math

import pytest

from simplecad.core.document import BodyRef, Document, Group
from simplecad.core.history import History
from simplecad.core.model_clipboard import copy_fragment, paste_fragment
from simplecad.core.naming import make_ref, sub_shapes
from simplecad.core.project import load, save
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.detect import analyse_plane
from simplecad.kernel.face_texture import FaceTextureFeature
from simplecad.kernel.occ import bounding_box, is_valid, volume
from simplecad.kernel.operations import (
    AlignFeature, BooleanFeature, FilletFeature, MoveFeature, ShellFeature,
)
from simplecad.kernel.primitives import BoxFeature, CylinderFeature
from simplecad.kernel.split import SplitFeature


def _face(shape, z_sign):
    faces = [f for f in sub_shapes(shape, "face")
             if (info := analyse_plane(f)) is not None and info.normal[2] * z_sign > 0.99]
    return max(faces, key=lambda f: analyse_plane(f).area) if faces else None


def _ok(document):
    report = Rebuilder(document).rebuild()
    assert report.ok, report.summary()
    return report


def _ref(document, name, shape_part, kind="face"):
    return make_ref(document.body(name).shape, shape_part, document.producer_of(name).id,
                    kind=kind, body=name)


# -- subjects: (document, the item operated on, the bodies it stands for) ------
def _box(_tmp):
    d = Document("M")
    d.add_feature(BoxFeature(inputs={"width": 20, "depth": 14, "height": 8}, outputs=["Part"]))
    return d, "Part"


def _cylinder(_tmp):
    d = Document("M")
    d.add_feature(CylinderFeature(inputs={"radius": 8, "height": 16}, outputs=["Part"]))
    return d, "Part"


def _split_half(_tmp):
    d = Document("M")
    d.add_feature(CylinderFeature(inputs={"radius": 8, "height": 16}, outputs=["Cyl"]))
    d.add_feature(SplitFeature(inputs={
        "body": BodyRef("Cyl"), "normal": [1, 0, 0], "origin": [0, 0, 0], "position": 0.0,
        "names": ["Part", "Other"]}, outputs=["Part", "Other"]))
    return d, "Part"


def _boolean(_tmp):
    d = Document("M")
    d.add_feature(BoxFeature(inputs={"width": 20, "depth": 20, "height": 8}, outputs=["Part"]))
    d.add_feature(CylinderFeature(inputs={"radius": 4, "height": 20, "x": 10, "y": 10, "z": -5},
                                  outputs=["Pin"]))
    d.add_feature(BooleanFeature(inputs={"body": BodyRef("Part"), "tools": [BodyRef("Pin")],
                                         "operation": "cut"}, outputs=["Part"]))
    return d, "Part"


def _filleted(_tmp):
    d, name = _box(_tmp)
    _ok(d)
    shape = d.body(name).shape
    edge = max(sub_shapes(shape, "edge"), key=lambda e: bounding_box(e)[1][2] + bounding_box(e)[1][0])
    d.add_feature(FilletFeature(inputs={"body": BodyRef(name), "edges": [_ref(d, name, edge, "edge")],
                                        "radius": 1.5}, outputs=[name]))
    return d, name


def _hollowed(_tmp):
    d, name = _box(_tmp)
    _ok(d)
    top = _face(d.body(name).shape, 1)
    d.add_feature(ShellFeature(inputs={"body": BodyRef(name), "thickness": 1.5,
                                       "faces": [_ref(d, name, top)]}, outputs=[name]))
    return d, name


def _textured(_tmp):
    d, name = _box(_tmp)
    _ok(d)
    top = _face(d.body(name).shape, 1)
    d.add_feature(FaceTextureFeature(inputs={
        "body": BodyRef(name), "face": _ref(d, name, top), "pattern": "horizontal_lines",
        "mode": "raised", "size": "3", "spacing": "3", "depth": "0.4", "edge_clearance": "1",
    }, outputs=[name]))
    return d, name


def _imported(tmp):
    from simplecad.kernel.importing import ImportFeature
    from simplecad.kernel.io_formats import export_shapes

    source = Document("Source")
    source.add_feature(BoxFeature(inputs={"width": 12, "depth": 10, "height": 6}, outputs=["Src"]))
    _ok(source)
    path = str(tmp / "part.stl")
    export_shapes([source.body("Src").shape], path)
    d = Document("M")
    d.add_feature(ImportFeature(inputs={"path": path, "index": 0}, outputs=["Part"]))
    return d, "Part"


def _group(_tmp):
    d = Document("M")
    d.add_feature(BoxFeature(inputs={"width": 10, "depth": 10, "height": 6}, outputs=["Left"]))
    d.add_feature(BoxFeature(inputs={"width": 10, "depth": 10, "height": 6, "x": 15}, outputs=["Right"]))
    d.groups["Pair"] = Group(name="Pair", members=["Left", "Right"])
    return d, "Pair"


SUBJECTS = {
    "box": _box, "cylinder": _cylinder, "split_half": _split_half, "boolean": _boolean,
    "filleted": _filleted, "hollowed": _hollowed, "textured": _textured,
    "imported_stl": _imported, "group": _group,
}


def _bodies(document, item):
    return document.expand([item])


def _state(document, item):
    shapes = [document.body(n).shape for n in _bodies(document, item)]
    boxes = [bounding_box(s) for s in shapes]
    low = tuple(min(b[0][i] for b in boxes) for i in range(3))
    high = tuple(max(b[1][i] for b in boxes) for i in range(3))
    return (sum(volume(s) for s in shapes), *low, *high)


def _low(state):
    return state[1:4]


def _high(state):
    return state[4:7]


def _valid(document, item):
    for name in _bodies(document, item):
        shape = document.body(name).shape
        assert shape is not None and is_valid(shape), name


# -- operations -------------------------------------------------------------
def _move(d, item, before):
    for name in _bodies(d, item):
        d.add_feature(MoveFeature(inputs={"body": BodyRef(name), "dx": 7, "dy": -3, "dz": 2},
                                  outputs=[name]))
    _ok(d)
    after = _state(d, item)
    assert after[0] == pytest.approx(before[0], rel=1e-6)
    assert _low(after) == pytest.approx(tuple(a + b for a, b in zip(_low(before), (7, -3, 2))), abs=1e-5)


def _rotate(key):
    def run(d, item, before):
        centre = [(a + b) / 2 for a, b in zip(_low(before), _high(before))]
        for _ in range(3):  # 3 x 120 degrees comes back round
            for name in _bodies(d, item):
                d.add_feature(MoveFeature(inputs={"body": BodyRef(name), "dx": 0, "dy": 0, "dz": 0,
                                                  key: 120, "pivot": centre, "order": "xyz"},
                                          outputs=[name]))
            _ok(d)
        assert _state(d, item) == pytest.approx(before, rel=1e-6, abs=1e-4)
    return run


def _duplicate(d, item, before):
    _clones, items = paste_fragment(d, copy_fragment(d, [item]), (50, 0, 0))
    _ok(d)
    copy = items[0]
    assert copy != item
    assert _state(d, item) == pytest.approx(before, rel=1e-6, abs=1e-5)
    copied = _state(d, copy)
    assert copied[0] == pytest.approx(before[0], rel=1e-6)
    assert copied[1] == pytest.approx(before[1] + 50, abs=1e-5)
    # Change the copy; the original must not move.
    for name in _bodies(d, copy):
        d.add_feature(MoveFeature(inputs={"body": BodyRef(name), "dx": 0, "dy": 9, "dz": 0},
                                  outputs=[name]))
    _ok(d)
    assert _state(d, item) == pytest.approx(before, rel=1e-6, abs=1e-5)


def _stack(d, item, before):
    d.add_feature(BoxFeature(inputs={"width": 60, "depth": 60, "height": 4, "x": -20, "y": -20,
                                     "z": -40}, outputs=["Base"]))
    _ok(d)
    target = _face(d.body("Base").shape, 1)
    for name in _bodies(d, item):
        bottom = _face(d.body(name).shape, -1)
        if bottom is None:
            pytest.skip("no downward flat face to stack by")
        d.add_feature(AlignFeature(inputs={
            "body": BodyRef(name), "moving_face": _ref(d, name, bottom),
            "target_face": _ref(d, "Base", target), "operation": "stack",
            "offset": "0", "flip": False}, outputs=[name]))
    _ok(d)
    for name in _bodies(d, item):
        assert bounding_box(d.body(name).shape)[0][2] == pytest.approx(-36.0, abs=1e-4)


def _boolean_cut(d, item, before):
    name = _bodies(d, item)[0]
    original = volume(d.body(name).shape)
    low, high = bounding_box(d.body(name).shape)
    # A quarter of the way in: the boolean subject already has a hole at its centre.
    spot = [a + (b - a) * 0.25 for a, b in zip(low, high)]
    d.add_feature(BoxFeature(inputs={"width": 1.5, "depth": 1.5, "height": 100,
                                     "x": spot[0] - 0.75, "y": spot[1] - 0.75, "z": -50},
                             outputs=["Cutter"]))
    d.add_feature(BooleanFeature(inputs={"body": BodyRef(name), "tools": [BodyRef("Cutter")],
                                         "operation": "cut"}, outputs=[name]))
    _ok(d)
    assert volume(d.body(name).shape) < original


def _fillet(d, item, before):
    name = _bodies(d, item)[0]
    shape = d.body(name).shape
    straight = [e for e in sub_shapes(shape, "edge")
                if math.dist(*bounding_box(e)) > 5]
    if not straight:
        pytest.skip("no edge long enough to round")
    edge = max(straight, key=lambda e: math.dist(*bounding_box(e)))
    d.add_feature(FilletFeature(inputs={"body": BodyRef(name), "edges": [_ref(d, name, edge, "edge")],
                                        "radius": 0.5}, outputs=[name]))
    _ok(d)
    assert volume(d.body(name).shape) < before[0] + 1e-6


def _delete_undo(d, item, before):
    history = History(d, limit=50)
    rebuilder = Rebuilder(d)
    history.record("Delete")
    for name in _bodies(d, item):
        for feature in [f for f in d.features if name in f.outputs]:
            remaining = [o for o in feature.outputs if o != name]
            if remaining and set(remaining) - set(feature.dropped):
                feature.dropped = sorted(set(feature.dropped) | {name})
            else:
                d.remove_feature(feature.id, splice=False)
        d.bodies.pop(name, None)
    assert rebuilder.rebuild().ok
    assert all(d.body(n) is None for n in _bodies(d, item))
    rebuilder.invalidate(history.undo())
    assert rebuilder.rebuild().ok
    assert _state(d, item) == pytest.approx(before, rel=1e-6, abs=1e-5)


def _colour(d, item, before):
    for name in _bodies(d, item):
        d.body(name).color = "#2266aa"
    assert d.export_colours()[item] == "#2266aa"


def _save_reopen(d, item, before, tmp):
    for name in _bodies(d, item):
        d.body(name).color = "#2266aa"
    path = save(d, str(tmp / "matrix.scad3"))
    reopened, _cached = load(path)
    assert Rebuilder(reopened).rebuild().ok
    assert _state(reopened, item) == pytest.approx(before, rel=1e-6, abs=1e-5)
    assert all(reopened.body(n).color == "#2266aa" for n in _bodies(reopened, item))
    # And it keeps working after reopening.
    _move(reopened, item, before)


OPERATIONS = {
    "move": _move, "rotate_x": _rotate("rx"), "rotate_y": _rotate("ry"), "rotate_z": _rotate("rz"),
    "duplicate": _duplicate, "stack": _stack, "boolean_cut": _boolean_cut, "fillet": _fillet,
    "delete_undo": _delete_undo, "colour": _colour, "save_reopen": _save_reopen,
}


@pytest.mark.parametrize("operation", list(OPERATIONS))
@pytest.mark.parametrize("subject", list(SUBJECTS))
def test_operation_works_on_any_history(subject, operation, tmp_path):
    document, item = SUBJECTS[subject](tmp_path)
    _ok(document)
    _valid(document, item)
    before = _state(document, item)
    run = OPERATIONS[operation]
    if operation == "save_reopen":
        run(document, item, before, tmp_path)
    else:
        run(document, item, before)
    _valid(document, item)
