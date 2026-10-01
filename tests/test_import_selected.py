"""Import chosen objects -- with their groups -- from another project."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from simplecad.core.document import BodyRef, Document, Group
from simplecad.core.geometry_service import serialise_shape
from simplecad.core.model_clipboard import copy_fragment, paste_fragment
from simplecad.core.project import inspect_tree, save
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.importing import EmbeddedImportFeature
from simplecad.kernel.occ import bounding_box
from simplecad.kernel.operations import MoveFeature
from simplecad.kernel.primitives import BoxFeature, CylinderFeature


def source_project(tmp_path):
    source = Document("Source")
    source.parameters.set("post", "12")
    source.add_feature(BoxFeature(inputs={"width": 10, "depth": 10, "height": 4}, outputs=["Base"]))
    source.add_feature(CylinderFeature(inputs={"radius": 2, "height": "post", "x": 5, "y": 5},
                                       outputs=["Post"]))
    source.add_feature(BoxFeature(inputs={"width": 3, "depth": 3, "height": 3, "x": 40},
                                  outputs=["Loose"]))
    seed = Document()
    seed.add_feature(BoxFeature(inputs={"width": 6, "depth": 6, "height": 6}, outputs=["Seed"]))
    Rebuilder(seed).rebuild()
    asset = source.add_asset(serialise_shape(seed.body("Seed").shape))
    source.add_feature(EmbeddedImportFeature(inputs={"asset": asset, "dx": 80}, outputs=["Mesh"]))
    source.add_feature(MoveFeature(inputs={"body": BodyRef("Mesh"), "dy": 5}, outputs=["Mesh"]))
    assert Rebuilder(source).rebuild().ok
    source.groups["Stand"] = Group("Stand", ["Base", "Post"])
    source.groups["Kit"] = Group("Kit", ["Stand", "Mesh"])
    source.body("Post").color = "#aa3300"
    return save(source, str(tmp_path / "source.scad3"))


def test_the_tree_lists_groups_with_their_members_and_no_geometry(tmp_path):
    path = source_project(tmp_path)
    tree = inspect_tree(path)
    assert [(n["kind"], n["name"]) for n in tree] == [("group", "Kit"), ("body", "Loose")]
    kit = tree[0]
    assert [(n["kind"], n["name"]) for n in kit["children"]] == [("group", "Stand"), ("body", "Mesh")]
    assert [n["name"] for n in kit["children"][0]["children"]] == ["Base", "Post"]


def _import(path, items):
    from simplecad.ui.main_window import MainWindow

    destination = Document("Destination")
    destination.parameters.set("unrelated", "1")
    errors = []

    def paste(fragment, _label, assets=None, **options):
        for blob in (assets or {}).values():
            destination.add_asset(blob)
        _features, names = paste_fragment(destination, fragment, **options)
        assert Rebuilder(destination).rebuild().ok
        return names

    shell = SimpleNamespace(_paste_model_fragment=paste, set_hint=lambda _text: None,
                            _report_import_failure=lambda _path, error: errors.append(error))
    created = MainWindow._import_native_project(shell, path, items=items)
    assert not errors, errors
    return destination, created


def test_a_group_arrives_as_a_group_with_its_editable_members(tmp_path):
    path = source_project(tmp_path)
    destination, created = _import(path, ["Stand"])
    assert created == ["Stand"]
    assert destination.groups["Stand"].members == ["Base", "Post"]
    assert set(destination.bodies) == {"Base", "Post"}
    assert destination.body("Post").color == "#aa3300"
    # The parameter keeps its name -- nothing here used it already.
    assert "post" in {p.name for p in destination.parameters}
    destination.parameters.set("post", "30")
    assert Rebuilder(destination).rebuild().ok
    assert bounding_box(destination.body("Post").shape)[1][2] == pytest.approx(30)


def test_several_objects_and_an_embedded_mesh_import_together(tmp_path):
    path = source_project(tmp_path)
    tree = inspect_tree(path)
    loose = tree[1]["key"]
    destination, created = _import(path, ["Kit", loose])
    assert created == ["Kit", "Loose"]
    assert set(destination.expand(["Kit"])) == {"Base", "Post", "Mesh"}
    assert destination.groups["Kit"].members == ["Stand", "Mesh"]
    # The imported mesh brought its embedded source along; it stays movable.
    mesh = destination.body("Mesh").shape
    assert mesh is not None
    low, high = bounding_box(mesh)
    assert high[0] - low[0] == pytest.approx(6)


def test_clipboard_fragments_carry_embedded_import_sources():
    source = Document()
    seed = Document()
    seed.add_feature(BoxFeature(inputs={"width": 6, "depth": 6, "height": 6}, outputs=["Seed"]))
    Rebuilder(seed).rebuild()
    asset = source.add_asset(serialise_shape(seed.body("Seed").shape))
    source.add_feature(EmbeddedImportFeature(inputs={"asset": asset}, outputs=["Mesh"]))
    assert Rebuilder(source).rebuild().ok
    fragment = copy_fragment(source, ["Mesh"])
    assert asset in fragment.assets
    other = Document()
    paste_fragment(other, type(fragment).from_dict(fragment.to_dict()))
    assert Rebuilder(other).rebuild().ok
    assert other.body("Mesh").shape is not None


def test_the_picker_takes_whole_groups_or_single_members(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from simplecad.ui.dialogs.import_picker import ImportPicker

    app = QApplication.instance() or QApplication([])
    tree = inspect_tree(source_project(tmp_path))
    picker = ImportPicker(None, "Import", tree)
    assert not picker.import_button.isEnabled()
    picker.check("Stand")
    assert picker.selected_keys() == ["Stand"]
    loose = tree[1]["key"]
    picker.check(loose)
    assert picker.selected_keys() == ["Stand", loose]
    picker._check_all(True)
    assert picker.selected_keys() == ["Kit", loose]
    chosen = []
    picker.chosen.connect(chosen.append)
    picker._accept()
    assert chosen == [["Kit", loose]]
    picker.deleteLater()
    del app
