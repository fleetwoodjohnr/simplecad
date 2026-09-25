"""Placed textures are reachable from picks, and removal rebuilds their users."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from simplecad.core.document import BodyRef
from simplecad.core.history import History
from simplecad.core.naming import make_ref, sub_shapes
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.detect import analyse_plane
from simplecad.kernel.face_texture import FaceTextureFeature
from simplecad.kernel.occ import bounding_box, is_valid, volume
from simplecad.kernel.operations import MoveFeature
from simplecad.kernel.primitives import BoxFeature


def _face(shape, sign=1):
    return max(
        (face for face in sub_shapes(shape, "face")
         if (info := analyse_plane(face)) and info.normal[2] * sign > .99),
        key=lambda face: analyse_plane(face).area,
    )


@pytest.fixture
def model(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    from simplecad.ui.geometry_client import GeometryClient
    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(GeometryClient, "start", lambda self: False)
    monkeypatch.setattr(MainWindow, "_start_autosave", lambda self: None)
    window = MainWindow(Mode.DARK)
    window.geometry.available = True
    chosen = []
    reports = []

    def entries():
        result = []
        for name, kind in chosen:
            body = window.document.body(name)
            if body is None:
                continue
            shape = body.shape
            if kind in ("face", "edge", "vertex"):
                shape = sub_shapes(shape, kind)[0]
            result.append(dict(presentation=window._presentations[name], kind=kind, shape=shape))
        return result

    def pick(*names, kind="solid"):
        chosen[:] = [(name, kind) for name in names]
        window._on_selection()

    def rebuild(*_args, **_kwargs):
        window.rebuilder.invalidate(window._stale)
        window._stale.clear()
        report = window.rebuilder.rebuild()
        reports.append(report)
        for name in window.document.bodies:
            window._presentations.setdefault(name, object())
        window.browser.refresh()
        window._on_selection()

    monkeypatch.setattr(window.stage.viewport, "selected_entries", entries)
    monkeypatch.setattr(window.stage.viewport, "clear_selection", lambda: pick())
    monkeypatch.setattr(window, "rebuild", rebuild)
    for name, x in (("A", 0), ("B", 30)):
        window.document.add_feature(BoxFeature(
            inputs={"width": 15, "depth": 15, "height": 3, "x": x}, outputs=[name],
        ))
    rebuild()
    yield SimpleNamespace(window=window, pick=pick, reports=reports)
    window.close_tool_panels()
    window.geometry.stop()
    window.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)


def _texture(model, *, name="A", sign=1, mode="raised", pattern="horizontal_lines"):
    window = model.window
    shape = window.document.body(name).shape
    feature = window.document.add_feature(FaceTextureFeature(inputs={
        "body": BodyRef(name),
        "face": make_ref(shape, _face(shape, sign), window.document.producer_of(name).id, body=name),
        "pattern": pattern, "mode": mode, "size": "5", "spacing": "5",
        "depth": "0.4", "edge_clearance": "1",
    }, outputs=[name]))
    window.rebuild()
    assert model.reports[-1].ok, model.reports[-1].summary()
    return feature


def _panel(window):
    from simplecad.ui.tools.face_texture import FaceTexturePanel

    return next(widget for widget, _ in window.stage.overlays if isinstance(widget, FaceTexturePanel))


def _preview(panel):
    # Exercise the real preview result without a worker or a GL context.
    for _ in range(2):
        panel._preview.timer.stop()
        assert panel._preview._state is not None
        panel._preview_answered({"token": 1, **panel._preview._in_process()})
    assert panel.confirm.isEnabled(), panel.status.text()


@pytest.mark.parametrize("kind", ["solid", "face", "edge", "vertex"])
def test_texture_actions_follow_the_body_not_the_surface_type(model, kind):
    feature = _texture(model)
    window = model.window
    model.pick("A", kind=kind)
    assert window.context_bar.keys()[:2] == ["edit_face_texture", "remove_face_texture"]
    window.run_action("edit_face_texture")
    assert _panel(window)._editing.id == feature.id
    window.cancel_tool()
    for names in [("B",), ("A", "B"), ()]:
        model.pick(*names)
        assert "edit_face_texture" not in window.context_bar.keys()
    feature.suppressed = True
    model.pick("A")
    assert "remove_face_texture" not in window.context_bar.keys()


def test_editor_loads_settings_changes_pattern_and_cancels_without_mutation(model):
    window = model.window
    feature = _texture(model)
    model.pick("A")
    before = window.document.to_dict()
    window.run_action("edit_face_texture")
    panel = _panel(window)
    assert panel.title_label.text() == "Edit texture"
    assert panel.confirm.text() == "Save changes"
    assert panel.fields["depth"].expression() == "0.4"
    assert panel.fields["spacing"].expression() == "5"
    panel.pattern.setCurrentIndex(next(i for i in range(panel.pattern.count())
                                      if panel.pattern.itemData(i)[1] == "vertical_lines"))
    panel.fields["depth"].setText("0.8")
    _preview(panel)
    assert window.document.to_dict() == before
    window.cancel_tool()
    assert window.document.to_dict() == before
    assert not window.history.can_undo
    model.pick("A")
    window.run_action("edit_face_texture")
    panel = _panel(window)
    panel.pattern.setCurrentIndex(next(i for i in range(panel.pattern.count())
                                      if panel.pattern.itemData(i)[1] == "vertical_lines"))
    panel.fields["depth"].setText("0.8")
    _preview(panel)
    panel.commit()
    assert window.document.feature(feature.id).inputs["pattern"] == "vertical_lines"
    assert len(window.document.features) == len(before["features"])
    assert model.reports[-1].ok
    edited_volume = volume(window.document.body("A").shape)
    window.undo()
    assert window.document.feature(feature.id).inputs["pattern"] == "horizontal_lines"
    window.redo()
    assert volume(window.document.body("A").shape) == pytest.approx(edited_volume)


@pytest.mark.parametrize("action", ["edit_face_texture", "remove_face_texture"])
def test_multiple_textures_choose_by_feature_id_and_cancel(model, action):
    window = model.window
    first = _texture(model)
    second = _texture(model, sign=-1, pattern="vertical_lines")
    model.pick("A")
    before = window.document.to_dict()
    QTimer.singleShot(0, lambda: QApplication.activePopupWidget().close())
    window.run_action(action)
    assert window.document.to_dict() == before

    offered = []

    def choose():
        menu = QApplication.activePopupWidget()
        offered.extend((item.data(), item.text()) for item in menu.actions())
        menu.setActiveAction(menu.actions()[1])
        QTest.keyClick(menu, Qt.Key_Return)

    QTimer.singleShot(0, choose)
    window.run_action(action)
    assert [item[0] for item in offered] == [first.id, second.id]
    assert first.name in offered[0][1] and "Vertical" in offered[1][1]
    assert window.document.feature(first.id) is first
    if action == "edit_face_texture":
        assert _panel(window)._editing.id == second.id
        assert _panel(window).pattern.currentData()[1] == "vertical_lines"
    else:
        assert window.document.feature(second.id) is None
        assert model.reports[-1].ok
        assert volume(window.document.body("A").shape) > 675


@pytest.mark.parametrize("mode", ["raised", "recessed", "through"])
@pytest.mark.parametrize("reopen", [False, True])
def test_remove_rebuilds_later_move_preserves_body_and_undo_redo(model, tmp_path, mode, reopen):
    window = model.window
    feature = _texture(model, mode=mode, pattern="vent" if mode == "through" else "horizontal_lines")
    move = window.document.add_feature(MoveFeature(
        inputs={"body": BodyRef("A"), "dx": 10}, outputs=["A"],
    ))
    window.rebuild()
    body = window.document.body("A")
    body.color = "#123456"
    body.metadata["custom"] = "preserved"
    if reopen:
        from simplecad.core.project import load, save

        document, cached = load(save(window.document, str(tmp_path / "textured.scad3")))
        assert cached
        window.document = document
        window.selection.document = document
        window.browser.document = document
        window.rebuilder = Rebuilder(document)
        assert window.rebuilder.adopt_baseline()
        window.history = History(document)
        body = document.body("A")
    textured_volume = volume(body.shape)
    other = window.document.body("B").shape
    model.pick("A")
    window.run_action("remove_face_texture")
    assert model.reports[-1].ok
    assert move.id in model.reports[-1].rebuilt
    assert window.document.feature(feature.id) is None
    assert window.document.body("A") is body
    assert body.color == "#123456" and body.metadata["custom"] == "preserved"
    assert volume(body.shape) == pytest.approx(675)
    assert bounding_box(body.shape)[0][0] == pytest.approx(10, abs=1e-6)
    assert is_valid(body.shape)
    assert bounding_box(window.document.body("B").shape) == bounding_box(other)
    assert volume(window.document.body("B").shape) == pytest.approx(volume(other))
    if not reopen:
        assert window.document.body("B").shape.IsSame(other)
    assert window.history.undo_label == "Remove Texture from A"
    window.undo()
    assert model.reports[-1].ok
    assert volume(body.shape) == pytest.approx(textured_volume)
    window.redo()
    assert model.reports[-1].ok
    assert move.id in model.reports[-1].rebuilt
    assert volume(body.shape) == pytest.approx(675)


def test_undo_closes_texture_editor(model):
    window = model.window
    _texture(model)
    model.pick("A")
    window.run_action("edit_face_texture")
    panel = _panel(window)
    _preview(panel)
    panel.commit()
    model.pick("A")
    window.run_action("edit_face_texture")
    panel = _panel(window)
    window.undo()
    assert panel._preview._closed
    assert not any(getattr(widget, "is_tool_panel", False) for widget, _ in window.stage.overlays)


def test_stale_editor_cannot_overwrite_a_replaced_feature(model):
    from simplecad.core.document import Feature

    window = model.window
    feature = _texture(model)
    model.pick("A")
    window.run_action("edit_face_texture")
    panel = _panel(window)
    _preview(panel)
    replacement = Feature.from_dict(feature.to_dict())
    window.document.features[window.document.index_of(feature.id)] = replacement
    before = window.document.to_dict()
    panel.commit()
    assert window.document.to_dict() == before
    assert not window.history.can_undo
    assert not panel.confirm.isEnabled()
    assert "changed or was removed" in panel.status.text()


def test_failed_downstream_rebuild_keeps_last_valid_solid(model):
    window = model.window
    _texture(model)
    move = window.document.add_feature(MoveFeature(
        inputs={"body": BodyRef("A"), "dx": 10}, outputs=["A"],
    ))
    window.rebuild()
    before = window.document.body("A").shape
    move.inputs["dx"] = "missing_dimension"
    model.pick("A")
    window.run_action("remove_face_texture")
    assert not model.reports[-1].ok
    assert move.id in model.reports[-1].failures
    assert window.document.body("A").shape.IsSame(before)


def test_cylindrical_texture_is_editable_from_a_pick_and_removable(model):
    from simplecad.kernel.detect import analyse_cylinder
    from simplecad.kernel.primitives import TubeFeature

    window = model.window
    tube = window.document.add_feature(TubeFeature(
        inputs={"outer_radius": 7, "inner_radius": 5, "height": 8}, outputs=["Tube"],
    ))
    window.rebuild()
    shape = window.document.body("Tube").shape
    base_volume = volume(shape)
    face = next(face for face in sub_shapes(shape, "face")
                if (info := analyse_cylinder(face)) and not info.internal)
    feature = window.document.add_feature(FaceTextureFeature(inputs={
        "body": BodyRef("Tube"), "face": make_ref(shape, face, tube.id, body="Tube"),
        "pattern": "horizontal_lines", "size": 5, "spacing": 5,
        "depth": .3, "edge_clearance": 1,
    }, outputs=["Tube"]))
    window.rebuild()
    assert model.reports[-1].ok
    model.pick("Tube", kind="face")
    window.run_action("edit_face_texture")
    panel = _panel(window)
    assert panel._editing.id == feature.id
    _preview(panel)
    window.run_action("remove_face_texture")
    assert panel._preview._closed
    assert model.reports[-1].ok
    assert volume(window.document.body("Tube").shape) == pytest.approx(base_volume)
