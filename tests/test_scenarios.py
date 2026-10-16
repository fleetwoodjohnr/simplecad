"""The seven end-to-end scenarios from the overhaul brief, as written."""

from __future__ import annotations

import random

import pytest

from simplecad.core.document import BodyRef, Document
from simplecad.core.history import RETAINED_GEOMETRY_STEPS, History
from simplecad.core.model_clipboard import copy_fragment, paste_fragment
from simplecad.core.naming import make_ref, sub_shapes
from simplecad.core.project import load, save
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.face_texture import FaceTextureFeature
from simplecad.kernel.occ import bounding_box, is_valid, volume
from simplecad.kernel.operations import (
    AlignFeature, BooleanFeature, FilletFeature, MoveFeature,
)
from simplecad.kernel.primitives import BoxFeature, CylinderFeature
from simplecad.kernel.split import SplitFeature
from tests.test_operation_matrix import _face, _ref


class Model:
    def __init__(self):
        self.d = Document("Scenario")
        self.history = History(self.d, limit=2000)
        self.rebuilder = Rebuilder(self.d)

    def do(self, label, change):
        self.history.record(label)
        result = change()
        self.build()
        return result

    def build(self):
        report = self.rebuilder.rebuild()
        assert report.ok, report.summary()

    def undo(self):
        self.rebuilder.invalidate(self.history.undo())
        self.build()

    def redo(self):
        self.rebuilder.invalidate(self.history.redo())
        self.build()

    def add(self, feature):
        return self.d.add_feature(feature)

    def shape(self, name):
        return self.d.body(name).shape

    def duplicate(self, item, offset=(40, 0, 0)):
        return self.do("Duplicate", lambda: paste_fragment(
            self.d, copy_fragment(self.d, [item]), offset)[1][0])

    def move(self, name, **delta):
        self.do("Move", lambda: self.add(MoveFeature(
            inputs={"body": BodyRef(name), "dx": 0, "dy": 0, "dz": 0, **delta}, outputs=[name])))

    def turn(self, name, key, angle):
        low, high = bounding_box(self.shape(name))
        pivot = [(a + b) / 2 for a, b in zip(low, high)]
        self.do("Rotate", lambda: self.add(MoveFeature(inputs={
            "body": BodyRef(name), "dx": 0, "dy": 0, "dz": 0, key: angle,
            "pivot": pivot, "order": "xyz"}, outputs=[name])))

    def stack(self, name, target):
        bottom = _face(self.shape(name), -1)
        top = _face(self.shape(target), 1)
        self.do("Stack", lambda: self.add(AlignFeature(inputs={
            "body": BodyRef(name), "moving_face": _ref(self.d, name, bottom),
            "target_face": _ref(self.d, target, top), "operation": "stack",
            "offset": "0", "flip": False}, outputs=[name])))
        assert bounding_box(self.shape(name))[0][2] == pytest.approx(
            bounding_box(self.shape(target))[1][2], abs=1e-4)

    def split(self, name, a, b, normal=(1, 0, 0)):
        low, high = bounding_box(self.shape(name))
        origin = [(lo + hi) / 2 for lo, hi in zip(low, high)]
        self.do("Split", lambda: self.add(SplitFeature(inputs={
            "body": BodyRef(name), "normal": list(normal), "origin": origin, "position": 0.0,
            "names": [a, b]}, outputs=[a, b])))


def test_1_cube_slice_duplicate_rotate_align():
    m = Model()
    m.do("Box", lambda: m.add(BoxFeature(inputs={"width": 20, "depth": 20, "height": 20}, outputs=["Cube"])))
    m.do("Base", lambda: m.add(BoxFeature(inputs={"width": 50, "depth": 50, "height": 5, "x": 60},
                                          outputs=["Other"])))
    m.split("Cube", "Cube A", "Cube B")
    copy = m.duplicate("Cube A")
    m.turn(copy, "ry", 90)
    m.stack(copy, "Other")
    assert volume(m.shape(copy)) == pytest.approx(volume(m.shape("Cube A")))
    assert bounding_box(m.shape("Cube A"))[0] == pytest.approx((0, 0, 0), abs=1e-6)


def test_2_cylinder_halves_move_independently_and_stack():
    m = Model()
    m.do("Cylinder", lambda: m.add(CylinderFeature(inputs={"radius": 10, "height": 30}, outputs=["Cyl"])))
    m.do("Box", lambda: m.add(BoxFeature(inputs={"width": 80, "depth": 80, "height": 6, "x": -40,
                                                 "y": -40, "z": -30}, outputs=["Table"])))
    m.split("Cyl", "Left", "Right")
    copy = m.duplicate("Left")
    m.move("Left", dx=-15)
    m.move(copy, dy=25)
    lefts = bounding_box(m.shape("Left")), bounding_box(m.shape(copy))
    assert lefts[0][0][0] == pytest.approx(-25, abs=1e-6)
    assert lefts[1][0][1] == pytest.approx(15, abs=1e-6)
    m.stack("Left", "Table")
    m.stack(copy, "Table")
    assert bounding_box(m.shape("Right"))[0][0] == pytest.approx(0, abs=1e-6)


def test_3_fillet_move_edit_duplicate_reopen(tmp_path):
    m = Model()
    m.do("Box", lambda: m.add(BoxFeature(inputs={"width": 20, "depth": 20, "height": 10}, outputs=["Part"])))
    edge = max(sub_shapes(m.shape("Part"), "edge"),
               key=lambda e: sum(bounding_box(e)[1]) + sum(bounding_box(e)[0]))
    fillet = m.do("Fillet", lambda: m.add(FilletFeature(inputs={
        "body": BodyRef("Part"), "edges": [_ref(m.d, "Part", edge, "edge")], "radius": 2},
        outputs=["Part"])))
    m.move("Part", dx=10)
    small = volume(m.shape("Part"))
    m.do("Edit fillet", lambda: fillet.inputs.__setitem__("radius", 4))
    assert volume(m.shape("Part")) < small
    copy = m.duplicate("Part")
    path = save(m.d, str(tmp_path / "s3.scad3"))
    reopened, _ = load(path)
    assert Rebuilder(reopened).rebuild().ok
    for name in ("Part", copy):
        assert volume(reopened.body(name).shape) == pytest.approx(volume(m.shape(name)))


def _texture(m, name, **settings):
    top = _face(m.shape(name), 1)
    return m.add(FaceTextureFeature(inputs={
        "body": BodyRef(name), "face": _ref(m.d, name, top), "pattern": "grid",
        "mode": "raised", "size": "3", "spacing": "3", "depth": "0.4", "edge_clearance": "1",
        **settings}, outputs=[name]))


def test_4_boolean_then_rotate_duplicate_texture_undo():
    m = Model()
    m.do("Box", lambda: m.add(BoxFeature(inputs={"width": 30, "depth": 30, "height": 8}, outputs=["Part"])))
    m.do("Pin", lambda: m.add(CylinderFeature(inputs={"radius": 4, "height": 20, "x": 15, "y": 15,
                                                      "z": -5}, outputs=["Pin"])))
    m.do("Subtract", lambda: m.add(BooleanFeature(inputs={
        "body": BodyRef("Part"), "tools": [BodyRef("Pin")], "operation": "cut"}, outputs=["Part"])))
    m.turn("Part", "rz", 45)
    m.turn("Part", "rz", -45)
    copy = m.duplicate("Part")
    plain = volume(m.shape(copy))
    m.do("Texture", lambda: _texture(m, copy))
    assert volume(m.shape(copy)) > plain and is_valid(m.shape(copy))
    m.undo()
    assert volume(m.shape(copy)) == pytest.approx(plain)
    assert volume(m.shape("Part")) == pytest.approx(plain)


def test_5_copy_several_delete_one_undo_independent():
    m = Model()
    for index, name in enumerate(("A", "B", "C")):
        m.do("Box", lambda n=name, i=index: m.add(BoxFeature(inputs={
            "width": 10, "depth": 10, "height": 10, "x": i * 15}, outputs=[n])))
    copies = m.do("Paste", lambda: paste_fragment(m.d, copy_fragment(m.d, ["A", "B", "C"]), (0, 40, 0))[1])
    assert len(copies) == 3

    def delete(name):
        for feature in [f for f in m.d.features if name in f.outputs]:
            m.d.remove_feature(feature.id, splice=False)
        m.d.bodies.pop(name, None)
    m.do("Delete", lambda: delete(copies[1]))
    assert m.d.body(copies[1]) is None and m.d.body("B") is not None
    m.undo()
    assert m.d.body(copies[1]).shape is not None
    m.move(copies[1], dz=5)
    assert bounding_box(m.shape("B"))[0][2] == pytest.approx(0, abs=1e-6)
    assert bounding_box(m.shape(copies[1]))[0][2] == pytest.approx(5, abs=1e-6)


def test_6_texture_edited_repeatedly_then_undone_stays_stable():
    m = Model()
    m.do("Box", lambda: m.add(BoxFeature(inputs={"width": 30, "depth": 30, "height": 8}, outputs=["Part"])))
    m.move("Part", dx=3)
    texture = m.do("Texture", lambda: _texture(m, "Part"))
    first = volume(m.shape("Part"))
    for size in ("2", "4", "2.5", "3.5", "3"):
        m.do("Edit texture", lambda s=size: texture.inputs.__setitem__("size", s))
        assert is_valid(m.shape("Part"))
    assert volume(m.shape("Part")) == pytest.approx(first, rel=1e-6)
    for _ in range(5):
        m.undo()
    assert volume(m.shape("Part")) == pytest.approx(first, rel=1e-6)


def test_7_hundreds_of_operations_keep_history_whole():
    m = Model()
    m.do("Box", lambda: m.add(BoxFeature(inputs={"width": 10, "depth": 10, "height": 10}, outputs=["Box0"])))
    def boxes():
        return {n: tuple(v for corner in bounding_box(b.shape) for v in corner)
                for n, b in m.d.bodies.items() if b.shape is not None}

    initial = boxes()
    rng = random.Random(3)
    names = ["Box0"]
    for step in range(300):
        choice = step % 6
        if choice == 0 and len(names) < 12:
            name = f"Box{len(names)}"
            m.do("Box", lambda n=name, s=step: m.add(BoxFeature(inputs={
                "width": 6, "depth": 6, "height": 6, "x": s * 2.0, "y": 30}, outputs=[n])))
            names.append(name)
        elif choice in (1, 2):
            m.move(rng.choice(names), dx=rng.choice((-1, 1)) * 0.5)
        elif choice == 3:
            m.turn(rng.choice(names), rng.choice(("rx", "ry", "rz")), 90)
        elif choice == 4 and len(names) < 12:
            names.append(m.duplicate(rng.choice(names), (0, 25, 0)))
        else:
            m.move(rng.choice(names), dz=0.25)
    final = boxes()
    steps = len(m.history._undo)
    assert steps >= 290
    for _ in range(steps):
        m.undo()
    undone = boxes()
    assert set(undone) == set(initial)
    for name in initial:
        assert undone[name] == pytest.approx(initial[name], abs=1e-6)
    for _ in range(steps):
        m.redo()
    redone = boxes()
    assert set(redone) == set(final)
    for name in final:
        assert redone[name] == pytest.approx(final[name], abs=1e-6)
    # What stays pinned is bounded by recent history, not session length.
    assert len(m.history.cache_keys()) <= RETAINED_GEOMETRY_STEPS * 4 + len(m.d.features)
