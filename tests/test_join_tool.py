"""Join must collect picks made after opening and apply the previewed result."""

from types import SimpleNamespace

import pytest
from OCP.AIS import (
    AIS_SelectionScheme_Add, AIS_SelectionScheme_Replace,
    AIS_SelectionScheme_XOR,
)
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from simplecad.kernel.occ import volume
from simplecad.kernel.primitives import BoxFeature
from simplecad.ui.viewport.occt_view import SelectionMode, _Nav


def _solid_count(shape):
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    solids = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_SOLID, solids)
    return solids.Extent()


@pytest.mark.parametrize("gap,bridged", [(0.0, False), (0.1, True), (0.2, True)])
def test_join_box_and_vent_produces_one_connected_solid(gap, bridged):
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN
    from OCP.gp import gp_Pnt

    from simplecad.core.document import BodyRef, Document
    from simplecad.core.geometry_service import build_preview_result
    from simplecad.core.rebuild import Rebuilder
    from simplecad.kernel.occ import is_valid
    from simplecad.kernel.operations import BooleanFeature
    from simplecad.kernel.vent import VentPlateFeature

    document = Document("Sponge holder")
    document.add_feature(BoxFeature(
        inputs={"width": 40, "depth": 40, "height": 4}, outputs=["Box"],
    ))
    document.add_feature(VentPlateFeature(inputs={
        "width": 20, "depth": 20, "thickness": 2,
        "across_flats": 5, "wall": 1.2, "margin": 2,
        "x": 20, "y": 20, "z": 4 + gap,
    }, outputs=["Vent"]))
    rebuilder = Rebuilder(document)
    assert rebuilder.rebuild().ok
    feature = BooleanFeature(inputs={
        "body": BodyRef("Box"), "tools": [BodyRef("Vent")],
        "operation": "join", "keep_tool": False,
    }, outputs=["Box"])

    preview = build_preview_result(document, feature.to_dict())
    assert preview["error"] is None
    assert _solid_count(preview["shape"]) == 1
    assert bool(preview["warnings"]) is bridged
    if bridged:
        assert "did not originally touch" in preview["warnings"][0]
    document.add_feature(feature)
    assert rebuilder.rebuild().ok
    assert list(document.bodies) == ["Box"]
    assert _solid_count(document.body("Box").shape) == 1
    assert is_valid(document.body("Box").shape)
    if bridged:
        # The repair adds material through the gap, not just a tree wrapper.
        assert BRepClass3d_SolidClassifier(
            document.body("Box").shape, gp_Pnt(11, 20, 4.05), 1e-7
        ).State() == TopAbs_IN


def test_join_rejects_gap_over_limit_without_consuming_vent():
    from simplecad.core.document import BodyRef, Document
    from simplecad.core.geometry_service import build_preview_result
    from simplecad.core.rebuild import Rebuilder
    from simplecad.kernel.operations import BooleanFeature
    from simplecad.kernel.vent import VentPlateFeature

    document = Document("Sponge holder")
    document.add_feature(BoxFeature(
        inputs={"width": 40, "depth": 40, "height": 4}, outputs=["Box"],
    ))
    document.add_feature(VentPlateFeature(inputs={
        "width": 20, "depth": 20, "thickness": 2,
        "across_flats": 5, "wall": 1.2, "margin": 2,
        "x": 20, "y": 20, "z": 4.3,
    }, outputs=["Vent"]))
    rebuilder = Rebuilder(document)
    assert rebuilder.rebuild().ok
    feature = BooleanFeature(inputs={
        "body": BodyRef("Box"), "tools": [BodyRef("Vent")],
        "operation": "join", "keep_tool": False,
    }, outputs=["Box"])

    preview = build_preview_result(document, feature.to_dict())
    assert preview["shape"] is None
    assert "do not make solid contact within 0.2 mm" in preview["error"]
    assert set(document.bodies) == {"Box", "Vent"}


def test_join_bridges_a_chain_of_three_nearby_bodies():
    from simplecad.core.document import BodyRef, Document
    from simplecad.core.rebuild import Rebuilder
    from simplecad.kernel.operations import BooleanFeature

    document = Document("Three parts")
    for name, x in (("A", 0), ("B", 10.1), ("C", 20.2)):
        document.add_feature(BoxFeature(
            inputs={"width": 10, "depth": 10, "height": 10, "x": x},
            outputs=[name],
        ))
    rebuilder = Rebuilder(document)
    assert rebuilder.rebuild().ok
    document.add_feature(BooleanFeature(inputs={
        "body": BodyRef("A"), "tools": [BodyRef("B"), BodyRef("C")],
        "operation": "join",
    }, outputs=["A"]))

    report = rebuilder.rebuild()
    assert report.ok
    assert list(document.bodies) == ["A"]
    assert _solid_count(document.body("A").shape) == 1
    assert "bridged" in report.warnings[0]


def test_join_many_bodies_previews_commits_and_reopens_as_one_solid(tmp_path):
    from simplecad.core.document import BodyRef, Document
    from simplecad.core.geometry_service import build_preview_result
    from simplecad.core.project import load, save
    from simplecad.core.rebuild import Rebuilder
    from simplecad.kernel.occ import is_valid
    from simplecad.kernel.operations import BooleanFeature, MoveFeature

    document = Document("Many parts")
    names = [f"Part {index}" for index in range(18)]
    for index, name in enumerate(names):
        document.add_feature(BoxFeature(
            inputs={"width": 10, "depth": 10, "height": 10,
                    "x": index * 10},
            outputs=[name],
        ))
    rebuilder = Rebuilder(document)
    assert rebuilder.rebuild().ok
    feature = BooleanFeature(inputs={
        "body": BodyRef(names[0]),
        "tools": [BodyRef(name) for name in names[1:]],
        "operation": "join", "keep_tool": False,
    }, outputs=[names[0]])

    preview = build_preview_result(document, feature.to_dict())
    assert preview["error"] is None
    assert _solid_count(preview["shape"]) == 1
    assert is_valid(preview["shape"])
    document.add_feature(feature)
    assert rebuilder.rebuild().ok
    assert list(document.bodies) == names[:1]
    joined = document.body(names[0]).shape
    assert _solid_count(joined) == 1
    assert volume(joined) == pytest.approx(18_000)
    restored, cached = load(save(document, str(tmp_path / "joined.scad3")))
    assert cached
    assert list(restored.bodies) == names[:1]
    assert _solid_count(restored.body(names[0]).shape) == 1
    assert volume(restored.body(names[0]).shape) == pytest.approx(18_000)
    restored.add_feature(MoveFeature(
        inputs={"body": BodyRef(names[0]), "dx": 5}, outputs=[names[0]],
    ))
    assert Rebuilder(restored).rebuild().ok
    assert _solid_count(restored.body(names[0]).shape) == 1


def test_join_distance_check_skips_many_far_apart_solids(monkeypatch):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP import BRepExtrema
    from OCP.gp import gp_Pnt
    from simplecad.kernel.operations import _join_bridge_gap

    solids = [
        BRepPrimAPI_MakeBox(gp_Pnt(index * 100, 0, 0), 10, 10, 10).Shape()
        for index in range(40)
    ]
    monkeypatch.setattr(BRepExtrema, "BRepExtrema_DistShapeShape",
                        lambda: pytest.fail("distant solids should not be measured"))
    assert _join_bridge_gap(solids) is None


def test_join_welds_solids_that_already_touch_exactly():
    """Flush faces are contact, not a gap.

    The bridging search only considered pairs more than 1e-7 apart, so two
    parts sitting exactly on each other -- the ordinary way a vent plate meets
    the floor it fills -- were told they "do not make solid contact within
    0.2 mm" while they were flush.
    """
    from simplecad.core.document import BodyRef, Document
    from simplecad.core.rebuild import Rebuilder
    from simplecad.kernel.operations import _join_bridge_gap, _join_solids
    from simplecad.kernel.operations import BooleanFeature

    document = Document("Flush")
    document.add_feature(BoxFeature(
        inputs={"width": 10, "depth": 10, "height": 10}, outputs=["A"],
    ))
    document.add_feature(BoxFeature(
        inputs={"width": 10, "depth": 10, "height": 10, "x": 10}, outputs=["B"],
    ))
    rebuilder = Rebuilder(document)
    assert rebuilder.rebuild().ok
    # The measured need for a pair that touches is zero, and zero is an answer.
    touching = [document.body("A").shape, document.body("B").shape]
    assert _join_bridge_gap(touching) == 0.0

    document.add_feature(BooleanFeature(inputs={
        "body": BodyRef("A"), "tools": [BodyRef("B")], "operation": "join",
    }, outputs=["A"]))
    report = rebuilder.rebuild()

    assert report.ok
    assert list(document.bodies) == ["A"]
    assert _solid_count(document.body("A").shape) == 1
    assert report.warnings == [], "nothing was bridged, so nothing to warn about"


def test_join_bridges_a_chain_that_mixes_contact_and_a_gap():
    """One exact contact plus one near miss is still one chain.

    Touching pairs were left out of the connectivity test entirely, so a run of
    three parts where two were flush and the third was 0.1 mm away counted as
    two separate clusters and the whole Join was refused.
    """
    from simplecad.kernel.operations import _join_bridge_gap

    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    solids = [
        BRepPrimAPI_MakeBox(gp_Pnt(x, 0, 0), 10.0, 10.0, 10.0).Shape()
        for x in (0.0, 10.0, 20.1)
    ]

    assert _join_bridge_gap(solids) == pytest.approx(0.1)
    assert _join_bridge_gap(solids[:1] + solids[2:]) is None


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
    # This fixture tests panel behavior with deterministic local geometry.
    # Production no longer falls back to running OCCT on the GUI thread.
    monkeypatch.setattr(window, "rebuild", window._rebuild_in_process)
    viewport = window.stage.viewport
    viewport.set_selection_modes((
        SelectionMode.BODY, SelectionMode.FACE,
        SelectionMode.EDGE, SelectionMode.VERTEX,
    ))
    for name, x in (("A", 0), ("B", 5), ("C", 9)):
        window.document.add_feature(BoxFeature(
            inputs={"width": 10, "depth": 10, "height": 10, "x": x},
            outputs=[name],
        ))
    assert window.rebuilder.rebuild().ok
    window._presentations.update({name: object() for name in ("A", "B", "C")})
    chosen = []

    def entries():
        return [
            {
                "presentation": window._presentations[name], "kind": "solid",
                "shape": window.document.body(name).shape,
            }
            for name in chosen if window.document.body(name) is not None
        ]

    def pick(*names):
        chosen[:] = names
        viewport.selection_changed.emit()

    monkeypatch.setattr(viewport, "selected_entries", entries)
    monkeypatch.setattr(viewport, "clear_selection", lambda: pick())
    yield SimpleNamespace(window=window, viewport=viewport, pick=pick)
    window.close_tool_panels()
    window.geometry.stop()
    window.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)


def _panel(window):
    panel = next(
        widget for widget, _anchor in window.stage.overlays
        if getattr(widget, "is_tool_panel", False)
    )
    panel._preview_controller.allow_in_process = True
    return panel


def _wait_for_preview(panel):
    for _ in range(40):
        if panel.confirm.isEnabled():
            return
        QTest.qWait(25)
    assert panel.confirm.isEnabled(), panel.status.text()


def test_join_opened_first_collects_bodies_and_commits_valid_preview(model):
    window, viewport = model.window, model.viewport
    normal_modes = viewport.selection_modes
    window.run_action("join")
    panel = _panel(window)
    assert viewport.selection_modes == (SelectionMode.BODY,)
    assert viewport._additive_selection
    assert not panel.confirm.isEnabled()

    model.pick("A")
    assert panel.target == "A" and panel.tools == []
    model.pick("A", "B")
    assert panel.target == "A" and panel.tools == ["B"]
    assert not panel.confirm.isEnabled()
    panel.commit()
    assert len(window.document.features) == 3

    _wait_for_preview(panel)
    panel.commit()
    assert len(window.document.features) == 4
    assert set(window.document.bodies) == {"A", "C"}
    assert volume(window.document.body("A").shape) == pytest.approx(1500)
    assert viewport.selection_modes == normal_modes
    assert not viewport._additive_selection


def test_selected_first_swap_and_later_picks_keep_the_chosen_target(model):
    window, viewport = model.window, model.viewport
    normal_modes = viewport.selection_modes
    model.pick("A", "B")
    window.run_action("join")
    panel = _panel(window)
    assert panel.target == "A" and panel.tools == ["B"]
    _wait_for_preview(panel)
    panel._swap()
    assert panel.target == "B" and not panel.confirm.isEnabled()
    model.pick("A", "B", "C")
    assert panel.target == "B" and panel.tools == ["A", "C"]
    model.pick("B", "C")
    assert panel.target == "B" and panel.tools == ["C"]
    _wait_for_preview(panel)
    panel.commit()
    assert set(window.document.bodies) == {"A", "B"}
    assert volume(window.document.body("B").shape) == pytest.approx(1400)
    assert not viewport._additive_selection
    assert viewport.selection_modes == normal_modes


def test_preview_error_does_not_commit_or_close_join(model, monkeypatch):
    from simplecad.core import geometry_service

    window = model.window
    monkeypatch.setattr(geometry_service, "build_preview_result", lambda *_args: {
        "shape": None, "error": "Join geometry failed",
    })
    window.run_action("join")
    panel = _panel(window)
    model.pick("A", "B")
    QTest.qWait(150)
    assert not panel.confirm.isEnabled()
    assert "Join geometry failed" in panel.status.text()
    panel.commit()
    assert len(window.document.features) == 3
    assert _panel(window) is panel


@pytest.mark.parametrize("position,can_apply", [(10.1, True), (10.3, False)])
def test_join_preview_reports_gap_and_only_enables_connected_result(model, position, can_apply):
    window = model.window
    window.document.features[1].inputs["x"] = position
    assert window.rebuilder.rebuild(force=True).ok
    window.run_action("join")
    panel = _panel(window)
    model.pick("A", "B")
    for _ in range(40):
        if ("did not originally touch" if can_apply else "do not make solid contact") in panel.status.text():
            break
        QTest.qWait(25)
    assert panel.confirm.isEnabled() is can_apply
    assert ("did not originally touch" if can_apply else "do not make solid contact") in panel.status.text()
    if can_apply:
        panel.commit()
        assert _solid_count(window.document.body("A").shape) == 1
        assert "did not originally touch" in window.stage.hint.full_text()
    else:
        panel.commit()
        assert set(window.document.bodies) == {"A", "B", "C"}


def test_combine_click_policy_adds_then_restores_normal_selection(model, monkeypatch):
    viewport, window = model.viewport, model.window
    window.run_action("join")
    seen = []
    context = SimpleNamespace(
        MoveTo=lambda *_args: None,
        SelectDetected=lambda scheme: seen.append(scheme),
    )
    monkeypatch.setattr(viewport, "_context", context)
    monkeypatch.setattr(viewport, "_view", object())
    monkeypatch.setattr(viewport, "_handle_view_cube_click", lambda: False)
    viewport._nav = _Nav.NONE
    viewport._press_pos = QPoint(20, 20)
    viewport._dragged = False

    def click(modifiers):
        event = SimpleNamespace(
            button=lambda: Qt.LeftButton,
            modifiers=lambda: modifiers,
            position=lambda: SimpleNamespace(toPoint=lambda: QPoint(20, 20)),
        )
        viewport.mouseReleaseEvent(event)
        return seen[-1]

    assert click(Qt.NoModifier) == AIS_SelectionScheme_Add
    assert click(Qt.ControlModifier) == AIS_SelectionScheme_XOR
    monkeypatch.setattr(viewport, "_context", None)
    window.cancel_tool()
    monkeypatch.setattr(viewport, "_context", context)
    assert click(Qt.NoModifier) == AIS_SelectionScheme_Replace
    monkeypatch.setattr(viewport, "_context", None)
