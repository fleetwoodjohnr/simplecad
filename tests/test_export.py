"""Export: what leaves SimpleCAD, and how many things a slicer sees.

The rule these tests hold is a single sentence: **a group is one object.**
Grouping two bodies is a statement that they belong together, and a group that
arrives in the slicer as two separate objects to place, orient and print has
lost the only thing the user was saying by grouping them.
"""

from __future__ import annotations

import os
import zipfile
from xml.etree import ElementTree

import pytest

from simplecad.core.document import Body, Document
from simplecad.kernel.io_formats import as_items, export_shapes, flatten
from simplecad.ui.main_window import resolved_export_path

MODEL_NS = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"


def box(dx, dy, dz, at=(0.0, 0.0, 0.0)):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    return BRepPrimAPI_MakeBox(gp_Pnt(*at), dx, dy, dz).Shape()


def read_model(path: str):
    with zipfile.ZipFile(path) as archive:
        return ElementTree.fromstring(archive.read("3D/3dmodel.model"))


def objects(model):
    return model.findall(f"./{MODEL_NS}resources/{MODEL_NS}object")


def build_items(model):
    return model.findall(f"./{MODEL_NS}build/{MODEL_NS}item")


def triangles(obj):
    return obj.findall(f"./{MODEL_NS}mesh/{MODEL_NS}triangles/{MODEL_NS}triangle")


@pytest.fixture
def document():
    doc = Document("Assembly")
    doc.bodies["Left"] = Body(name="Left", shape=box(10, 10, 10))
    doc.bodies["Right"] = Body(name="Right", shape=box(10, 10, 10, at=(20, 0, 0)))
    doc.bodies["Loose"] = Body(name="Loose", shape=box(5, 5, 5, at=(0, 30, 0)))
    return doc


# ----------------------------------------------------------------------
# What the document offers the writers
# ----------------------------------------------------------------------
class TestExportItems:
    def test_ungrouped_bodies_are_one_object_each(self, document):
        assert [name for name, _shapes in document.export_items()] == [
            "Left", "Right", "Loose",
        ]

    def test_a_group_is_a_single_item_holding_its_members(self, document):
        document.add_group(["Left", "Right"], "Bracket")
        items = dict(document.export_items())
        assert list(items) == ["Bracket", "Loose"]
        assert len(items["Bracket"]) == 2

    def test_a_nested_group_is_still_one_item(self, document):
        document.add_group(["Left", "Right"], "Inner")
        document.add_group(["Inner", "Loose"], "Outer")
        items = document.export_items()
        assert [name for name, _ in items] == ["Outer"]
        assert len(items[0][1]) == 3

    def test_hidden_members_are_left_out(self, document):
        document.add_group(["Left", "Right"], "Bracket")
        document.bodies["Right"].visible = False
        assert len(dict(document.export_items())["Bracket"]) == 1

    def test_a_group_of_nothing_visible_is_not_exported(self, document):
        document.add_group(["Left", "Right"], "Bracket")
        for name in ("Left", "Right"):
            document.bodies[name].visible = False
        assert [name for name, _ in document.export_items()] == ["Loose"]


class TestNormalising:
    """The writers still take a plain list of shapes, as they always did."""

    def test_a_flat_list_becomes_one_item_per_shape(self):
        items = as_items([box(1, 1, 1), box(2, 2, 2)])
        assert len(items) == 2
        assert all(len(shapes) == 1 for _name, shapes in items)

    def test_items_pass_through_and_flatten_back(self):
        first, second = box(1, 1, 1), box(2, 2, 2)
        items = as_items([("Pair", [first, second])])
        assert [name for name, _ in items] == ["Pair"]
        assert len(flatten(items)) == 2

    def test_nothing_at_all_is_refused(self, tmp_path):
        from simplecad.core.errors import CadError

        with pytest.raises(CadError):
            export_shapes([], str(tmp_path / "empty.3mf"))


# ----------------------------------------------------------------------
# 3MF, which is the format that reaches the slicer
# ----------------------------------------------------------------------
class TestThreeMF:
    def test_loose_bodies_are_separate_objects(self, document, tmp_path):
        path = str(tmp_path / "loose.3mf")
        export_shapes(document.export_items(), path)
        model = read_model(path)
        assert len(objects(model)) == 3
        assert len(build_items(model)) == 3

    def test_a_group_is_one_object_with_one_build_item(self, document, tmp_path):
        """The bug report, as an assertion.

        Two grouped bodies had been arriving as two objects, because the writer
        was handed a flat list of shapes and had no way to know any two of them
        belonged together.
        """
        document.add_group(["Left", "Right"], "Bracket")
        path = str(tmp_path / "grouped.3mf")
        export_shapes(document.export_items(), path)

        model = read_model(path)
        assert len(objects(model)) == 2          # the group, and the loose body
        assert len(build_items(model)) == 2
        names = {obj.get("name") for obj in objects(model)}
        assert names == {"Bracket", "Loose"}

    def test_the_grouped_object_keeps_every_triangle(self, document, tmp_path):
        """One object, but not one body: nothing is fused and nothing is lost."""
        apart = str(tmp_path / "apart.3mf")
        export_shapes(document.export_items(), apart)
        separate = sum(len(triangles(o)) for o in objects(read_model(apart)))

        document.add_group(["Left", "Right"], "Bracket")
        together = str(tmp_path / "together.3mf")
        export_shapes(document.export_items(), together)
        model = read_model(together)
        merged = sum(len(triangles(o)) for o in objects(model))

        assert merged == separate
        bracket = next(o for o in objects(model) if o.get("name") == "Bracket")
        # Both boxes are in the one mesh, and the second one's indices were
        # offset rather than overwriting the first one's.
        vertices = bracket.findall(
            f"./{MODEL_NS}mesh/{MODEL_NS}vertices/{MODEL_NS}vertex"
        )
        assert len(vertices) == 16                      # two boxes, eight each
        assert max(
            int(t.get("v3")) for t in triangles(bracket)
        ) == len(vertices) - 1

    def test_the_group_lands_where_its_members_are(self, document, tmp_path):
        """Merging must not move anything: a 3MF vertex is a world coordinate."""
        document.add_group(["Left", "Right"], "Bracket")
        path = str(tmp_path / "placed.3mf")
        export_shapes(document.export_items(), path)
        bracket = next(
            o for o in objects(read_model(path)) if o.get("name") == "Bracket"
        )
        xs = [
            float(v.get("x"))
            for v in bracket.findall(
                f"./{MODEL_NS}mesh/{MODEL_NS}vertices/{MODEL_NS}vertex"
            )
        ]
        assert min(xs) == pytest.approx(0.0)
        assert max(xs) == pytest.approx(30.0)


# ----------------------------------------------------------------------
# The other writers still work, grouped or not
# ----------------------------------------------------------------------
@pytest.mark.parametrize("suffix", [".stl", ".obj", ".step"])
def test_every_format_still_writes_a_grouped_document(document, tmp_path, suffix):
    document.add_group(["Left", "Right"], "Bracket")
    path = str(tmp_path / f"grouped{suffix}")
    export_shapes(document.export_items(), path)
    assert os.path.getsize(path) > 0


def test_a_grouped_stl_holds_every_body(document, tmp_path):
    """STL has no notion of an object, so grouping must not lose geometry.

    Nothing to assert about object counts here -- the format cannot express one
    -- so what is checked is that both boxes are still in the file, which is the
    thing a merge could plausibly have broken.
    """
    from simplecad.kernel.io_formats import import_stl
    from simplecad.kernel.occ import bounding_box

    document.add_group(["Left", "Right"], "Bracket")
    path = str(tmp_path / "grouped.stl")
    export_shapes(document.export_items(), path)

    boxes = [bounding_box(body.shape) for body in import_stl(path)]
    assert boxes
    assert min(low[0] for low, _high in boxes) == pytest.approx(0.0, abs=1e-3)
    assert max(high[0] for _low, high in boxes) == pytest.approx(30.0, abs=1e-3)


@pytest.mark.parametrize(
    "selected_filter,suffix",
    [
        ("3MF for printing (*.3mf)", ".3mf"),
        ("STEP (*.step)", ".step"),
        ("STL (*.stl)", ".stl"),
        ("OBJ (*.obj)", ".obj"),
    ],
)
def test_export_filter_supplies_the_missing_extension(selected_filter, suffix):
    assert resolved_export_path("bracket", selected_filter) == f"bracket{suffix}"


def test_an_explicit_supported_export_extension_wins_over_the_filter():
    assert resolved_export_path("bracket.stl", "3MF for printing (*.3mf)") == "bracket.stl"


def test_unknown_filename_suffix_is_kept_and_the_export_suffix_is_appended():
    assert resolved_export_path("bracket.final", "STL (*.stl)") == "bracket.final.stl"
