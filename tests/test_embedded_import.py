"""Individual imports remain editable without their source files."""

from types import SimpleNamespace

import pytest

from simplecad.core.document import BodyRef, Document
from simplecad.core.history import History
from simplecad.core.model_clipboard import paste_fragment
from simplecad.core.project import inspect_bodies, load, save
from simplecad.core.rebuild import Rebuilder
from simplecad.core.geometry_service import serialise_shape
from simplecad.core.naming import make_ref, sub_shapes
from simplecad.kernel.detect import analyse_plane
from simplecad.kernel.face_texture import FaceTextureFeature
from simplecad.kernel.importing import EmbeddedImportFeature, ImportFeature
from simplecad.kernel.occ import bounding_box
from simplecad.kernel.operations import MoveFeature
from simplecad.kernel.primitives import BoxFeature


def test_embedded_solid_survives_source_removal_and_undo_redo(tmp_path):
    document = Document()
    history = History(document)
    builder = Rebuilder(document)
    source = Document()
    source.add_feature(BoxFeature(
        inputs={"width": 11, "depth": 12, "height": 13}, outputs=["Source"],
    ))
    assert Rebuilder(source).rebuild().ok
    history.record("Import")
    sha = document.add_asset(serialise_shape(source.body("Source").shape))
    imported = document.add_feature(EmbeddedImportFeature(
        inputs={"asset": sha}, outputs=["Imported"],
    ))
    assert builder.rebuild().ok
    assert bounding_box(document.body("Imported").shape)[1] == pytest.approx((11, 12, 13))

    builder.invalidate(history.undo())
    assert builder.rebuild().ok
    assert document.body("Imported") is None
    undone, _ = load(save(document, str(tmp_path / "undone.scad3")))
    restored_history = History(undone)
    restored_builder = Rebuilder(undone)
    restored_builder.invalidate(restored_history.redo())
    assert restored_builder.rebuild().ok
    assert bounding_box(undone.body("Imported").shape)[1] == pytest.approx((11, 12, 13))
    builder.invalidate(history.redo())
    assert builder.rebuild().ok
    assert document.feature(imported.id) is not None
    assert sha in document.assets

    path = save(document, str(tmp_path / "self_contained.scad3"))
    reopened, cached = load(path)
    assert cached
    reopened.body("Imported").shape = None
    assert Rebuilder(reopened).rebuild(force=True).ok
    assert bounding_box(reopened.body("Imported").shape)[1] == pytest.approx((11, 12, 13))


def test_single_native_object_clones_only_its_editable_history(tmp_path):
    from simplecad.ui.main_window import MainWindow

    source = Document()
    source.parameters.set("length", "10")
    source.add_feature(BoxFeature(
        inputs={"width": "length", "depth": 8, "height": 6}, outputs=["Wanted"],
    ))
    source.add_feature(MoveFeature(
        inputs={"body": BodyRef("Wanted"), "dx": 5}, outputs=["Wanted"],
    ))
    assert Rebuilder(source).rebuild().ok
    moved = source.body("Wanted").shape
    upper = max((face for face in sub_shapes(moved, "face")
                 if (plane := analyse_plane(face)) and plane.normal[2] > .99),
                key=lambda face: analyse_plane(face).area)
    source.add_feature(FaceTextureFeature(inputs={
        "body": BodyRef("Wanted"),
        "face": make_ref(moved, upper, source.producer_of("Wanted").id, body="Wanted"),
        "pattern": "horizontal_lines", "mode": "raised", "size": "2",
        "spacing": "2", "depth": "0.2", "edge_clearance": "0.4",
    }, outputs=["Wanted"]))
    source.add_feature(BoxFeature(
        inputs={"width": 100, "depth": 100, "height": 100}, outputs=["Other"],
    ))
    assert Rebuilder(source).rebuild().ok
    source.body("Wanted").color = "#123456"
    source.body("Wanted").metadata["tag"] = "keep"
    source_path = save(source, str(tmp_path / "source.scad3"))
    wanted_id = next(ident for ident, name in inspect_bodies(source_path) if name == "Wanted")

    destination = Document()
    errors = []

    def paste(fragment, _label, assets=None):
        for blob in (assets or {}).values():
            destination.add_asset(blob)
        _features, names = paste_fragment(destination, fragment)
        assert Rebuilder(destination).rebuild().ok
        return names

    shell = SimpleNamespace(_paste_model_fragment=paste, set_hint=lambda _text: None,
                            _report_import_failure=lambda _path, error: errors.append(error))
    created = MainWindow._import_native_project(shell, source_path, wanted_id)
    assert not errors
    assert created == ["Wanted"]
    assert "Other" not in destination.bodies
    assert destination.body("Wanted").color == "#123456"
    assert destination.body("Wanted").metadata["tag"] == "keep"
    assert {f.type_name for f in destination.features} == {"box", "move", "face_texture"}

    # The copied parameter and feature are ordinary, independent recipe data.
    copied_box = next(f for f in destination.features if f.type_name == "box")
    copied_parameter = next(iter(destination.parameters)).name
    assert copied_box.inputs["width"] == copied_parameter
    destination.parameters.set(copied_parameter, "20")
    assert Rebuilder(destination).rebuild().ok
    low, high = bounding_box(destination.body("Wanted").shape)
    assert high[0] - low[0] == pytest.approx(20)


def test_native_import_materializes_external_leaf(tmp_path):
    from simplecad.ui.main_window import MainWindow

    seed = Document()
    seed.add_feature(BoxFeature(inputs={"width": 7, "depth": 8, "height": 9}, outputs=["Seed"]))
    assert Rebuilder(seed).rebuild().ok
    blob = serialise_shape(seed.body("Seed").shape)
    from simplecad.core.geometry_service import deserialise_shape
    from simplecad.kernel.io_formats import ImportedBody
    from simplecad.kernel import importing

    external = tmp_path / "external.step"
    external.write_text("fixture")
    source = Document()
    source.add_feature(ImportFeature(inputs={"path": str(external), "index": 0}, outputs=["Part"]))
    original_reader = importing.read_bodies
    importing.read_bodies = lambda _path: [ImportedBody("Part", deserialise_shape(blob))]
    try:
        assert Rebuilder(source).rebuild().ok
        path = save(source, str(tmp_path / "source.scad3"))
        target = Document()

        def paste(fragment, _label, assets=None):
            for value in (assets or {}).values():
                target.add_asset(value)
            _features, names = paste_fragment(target, fragment)
            assert Rebuilder(target).rebuild().ok
            return names

        errors = []
        shell = SimpleNamespace(_paste_model_fragment=paste, set_hint=lambda _text: None,
                                _report_import_failure=lambda _path, error: errors.append(error))
        assert MainWindow._import_native_project(shell, path, source.body("Part").id) == ["Part"]
        assert not errors
        assert any(f.type_name == "embedded_import" for f in target.features)
        external.unlink()
        reopened, _ = load(save(target, str(tmp_path / "target.scad3")))
        assert Rebuilder(reopened).rebuild(force=True).ok
        assert bounding_box(reopened.body("Part").shape)[1] == pytest.approx((7, 8, 9))
    finally:
        importing.read_bodies = original_reader
