"""Real Qt key events and real feature rebuilds, with display and IPC replaced.

The GUI smoke script covers OCCT picking. Here rebuild completion is controlled
so keystrokes arriving during slow geometry can be tested deterministically.
"""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from simplecad.core.naming import fingerprint, sub_shapes
from simplecad.kernel.occ import bounding_box
from simplecad.kernel.primitives import BoxFeature
from simplecad.core.units import Dimension


@pytest.fixture
def model(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    from simplecad.ui.geometry_client import GeometryClient
    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode
    from simplecad.ui.viewport import camera

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(GeometryClient, "start", lambda self: False)
    monkeypatch.setattr(MainWindow, "_start_autosave", lambda self: None)
    monkeypatch.setattr(camera, "read_state", lambda view: (
        (0, 0, 10), (0, 0, 0), (0, 1, 0), 20,
    ))
    window = MainWindow(Mode.DARK)
    viewport = window.stage.viewport
    entries = []

    def clear():
        entries.clear()
        viewport.selection_changed.emit()

    def select_presentations(presentations):
        entries.clear()
        for presentation in presentations:
            name = next(n for n, p in window._presentations.items() if p is presentation)
            entries.append(dict(presentation=presentation, kind="solid",
                                shape=window.document.body(name).shape))
        viewport.selection_changed.emit()

    def select_face(presentation, face):
        entries[:] = [dict(presentation=presentation, kind="face", shape=face)]
        viewport.selection_changed.emit()
        return True

    def refresh():
        clear()
        window._presentations.clear()
        window._presentations.update({name: object() for name in window.document.bodies})

    def request(*args, **kwargs):
        window.geometry._busy = True
        return True

    def finish():
        report = window.rebuilder.rebuild()
        window.geometry._busy = False
        window._refresh_rebuilt_model(report.ok)
        return report

    monkeypatch.setattr(viewport, "selected_entries", lambda: list(entries))
    monkeypatch.setattr(viewport, "clear_selection", clear)
    monkeypatch.setattr(viewport, "select_presentations", select_presentations)
    monkeypatch.setattr(viewport, "select_subshape", select_face)
    monkeypatch.setattr(window, "refresh_view", refresh)
    monkeypatch.setattr(window.geometry, "request", request)
    window.geometry.available = True
    for name, x in (("A", 0), ("B", 20)):
        window.document.add_feature(BoxFeature(
            inputs={"width": 10, "depth": 10, "height": 10, "x": x}, outputs=[name],
        ))
    assert finish().ok

    def select(*names, face=False):
        if face:
            shape = window.document.body(names[0]).shape
            top = next(f for f in sub_shapes(shape, "face")
                       if fingerprint(f, "face").direction == (0.0, 0.0, 1.0))
            select_face(window._presentations[names[0]], top)
        else:
            select_presentations(window._presentations[n] for n in names)

    yield SimpleNamespace(window=window, viewport=viewport, select=select, finish=finish)
    window._cancel_nudge()
    window.geometry.stop()
    window.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)


def test_holding_an_arrow_waits_for_real_release_and_groups_repeats(model):
    window, viewport = model.window, model.viewport
    model.select("A")
    QTest.keyPress(viewport, Qt.Key_Right)
    QTest.qWait(300)  # Longer than the quiet timer, shorter than many repeat delays.
    assert len(window.document.features) == 2
    for _ in range(3):
        QApplication.sendEvent(viewport, QKeyEvent(
            QEvent.KeyRelease, Qt.Key_Right, Qt.NoModifier, "", True,
        ))
        QApplication.sendEvent(viewport, QKeyEvent(
            QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier, "", True,
        ))
    QTest.keyRelease(viewport, Qt.Key_Right)
    QTest.qWait(300)
    assert len(window.document.features) == 3
    assert model.finish().ok
    assert bounding_box(window.document.body("A").shape)[0][0] == pytest.approx(1, abs=1e-6)
    window.undo()
    assert model.finish().ok
    assert bounding_box(window.document.body("A").shape)[0][0] == pytest.approx(0, abs=1e-6)
    window.redo()
    assert model.finish().ok
    assert bounding_box(window.document.body("A").shape)[0][0] == pytest.approx(1, abs=1e-6)


@pytest.mark.parametrize("face", [False, True])
def test_arrows_during_rebuild_resume_on_the_same_geometry(model, face):
    window, viewport = model.window, model.viewport
    model.select("A", face=face)
    key = Qt.Key_Up if face else Qt.Key_Right
    QTest.keyClick(viewport, key)
    window._commit_nudge()
    assert window.geometry.busy
    QTest.keyClick(viewport, key)
    QTest.keyClick(viewport, key)
    assert len(window.document.features) == 3
    assert model.finish().ok
    assert window._nudge_state is not None
    window._commit_nudge()
    assert model.finish().ok
    low, high = bounding_box(window.document.body("A").shape)
    assert (high[2] if face else low[0]) == pytest.approx(10.75 if face else .75, abs=1e-6)
    assert window.selection.bodies == ["A"]
    # A second body nudge folds into the first Move; a face nudge is its own
    # push/pull. Either way each burst stays its own undo step.
    assert len(window.document.features) == (4 if face else 3)
    window.history._finish_pending()
    assert [a.label for a in window.history._undo][-2:] == (
        ["Nudge face", "Nudge face"] if face else ["Nudge objects", "Nudge objects"])


def test_screen_and_depth_steps_move_all_selected_bodies(model):
    window, viewport = model.window, model.viewport
    model.select("A", "B")
    QTest.keyClick(viewport, Qt.Key_Left)
    QTest.keyClick(viewport, Qt.Key_Down)
    QTest.keyClick(viewport, Qt.Key_Up, Qt.ControlModifier)
    window._commit_nudge()
    assert model.finish().ok
    for name, x in (("A", 0), ("B", 20)):
        assert bounding_box(window.document.body(name).shape)[0] == pytest.approx(
            (x - .25, -.25, -.25), abs=1e-6,
        )


@pytest.mark.parametrize("internal", [False, True])
def test_round_face_steps_change_radius_and_restore_the_same_surface(model, internal):
    from simplecad.kernel.detect import cylindrical_faces
    from simplecad.kernel.primitives import TubeFeature

    window, viewport = model.window, model.viewport
    window.document.add_feature(TubeFeature(
        inputs={"outer_radius": 5, "inner_radius": 3, "height": 10}, outputs=["Tube"],
    ))
    assert model.finish().ok
    face = next(f for f, info in cylindrical_faces(window.document.body("Tube").shape)
                if info.internal == internal)
    viewport.select_subshape(window._presentations["Tube"], face)
    for key, change in ((Qt.Key_Up, .25), (Qt.Key_Down, 0)):
        QTest.keyClick(viewport, key)
        expected_delta = "−0.25 mm" if (key == Qt.Key_Up) == internal else "+0.25 mm"
        expected_diameter = (
            (5.5 if internal else 10.5)
            if key == Qt.Key_Up else (6.0 if internal else 10.0)
        )
        assert window.stage.drag_readout.delta_label.text() == expected_delta
        assert window.stage.drag_readout.size_label.text() == (
            f"Diameter {expected_diameter:.2f} mm"
        )
        window._commit_nudge()
        assert window.stage.drag_readout.isHidden()
        assert model.finish().ok
        radii = {info.internal: info.radius
                 for _, info in cylindrical_faces(window.document.body("Tube").shape)}
        assert radii[internal] == pytest.approx(3 - change if internal else 5 + change)
        assert radii[not internal] == pytest.approx(5 if internal else 3)
        assert window.selection.round_faces()[0].info.internal == internal


def test_a_held_queued_arrow_still_waits_for_release_after_rebuild(model):
    window, viewport = model.window, model.viewport
    model.select("A")
    QTest.keyClick(viewport, Qt.Key_Right)
    window._commit_nudge()
    QTest.keyPress(viewport, Qt.Key_Right)
    assert model.finish().ok
    QTest.qWait(300)
    assert len(window.document.features) == 3
    QTest.keyRelease(viewport, Qt.Key_Right)
    QTest.qWait(300)
    assert model.finish().ok
    assert bounding_box(window.document.body("A").shape)[0][0] == pytest.approx(.5, abs=1e-6)


def test_unavailable_worker_keeps_geometry_and_never_rebuilds_on_ui(model, monkeypatch):
    window, viewport = model.window, model.viewport
    window.geometry.available = False
    model.select("A", face=True)
    before = bounding_box(window.document.body("A").shape)
    monkeypatch.setattr(window.rebuilder, "rebuild", lambda *a, **k: pytest.fail("OCCT ran on the UI thread"))
    QTest.keyClick(viewport, Qt.Key_Up)
    window._commit_nudge()
    assert bounding_box(window.document.body("A").shape) == before
    assert window._nudge_state is None


@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("cancel", ["escape", "selection", "tool", "failure", "document"])
def test_pending_arrows_cannot_outlive_their_target(model, monkeypatch, queued, cancel):
    from simplecad.core.document import Document
    from simplecad.ui.tools import registry

    window, viewport = model.window, model.viewport
    model.select("A")
    QTest.keyClick(viewport, Qt.Key_Right)
    if queued:
        window._commit_nudge()
        QTest.keyClick(viewport, Qt.Key_Right)
    if cancel == "escape":
        QTest.keyClick(viewport, Qt.Key_Escape)
    elif cancel == "selection":
        model.select("B")
    elif cancel == "tool":
        monkeypatch.setattr(registry, "activate", lambda *args: None)
        window.activate_tool("move")
    elif cancel == "failure":
        window._on_geometry_failed("Test failure")
    else:
        window.set_document(Document("New"))
    assert window._nudge_state is None
    assert window._nudge_restore is None
    assert not window._nudge_queue
    assert not window._nudge_timer.isActive()
    before = len(window.document.features)
    assert model.finish().ok
    window._commit_nudge()
    assert len(window.document.features) == before
    if cancel == "selection":
        assert bounding_box(window.document.body("B").shape)[0][0] == pytest.approx(20, abs=1e-6)


def test_editors_and_unsupported_face_keys_keep_their_normal_behavior(model):
    window, viewport = model.window, model.viewport
    model.select("A", face=True)
    for key, modifiers in ((Qt.Key_Left, Qt.NoModifier), (Qt.Key_Up, Qt.ControlModifier)):
        QTest.keyClick(viewport, key, modifiers)
        assert window._nudge_state is None
    editor = QLineEdit("123", window)
    editor.setCursorPosition(2)
    QTest.keyClick(editor, Qt.Key_Left)
    assert editor.cursorPosition() == 1
    assert window._nudge_state is None


def test_active_move_tool_receives_exact_arrows_and_can_undo_redo(model):
    window = model.window
    model.select("A")
    window.activate_tool("move")
    panel = next(w for w, _a in window.stage.overlays
                 if getattr(w, "is_tool_panel", False))
    for _ in range(4):
        QTest.keyClick(panel, Qt.Key_Right)
    assert panel.value("dx") == pytest.approx(1.0)
    for _ in range(2):
        QTest.keyClick(panel, Qt.Key_Left)
    assert panel.value("dx") == pytest.approx(.5)
    QTest.mouseClick(panel.confirm, Qt.LeftButton)
    assert model.finish().ok
    assert bounding_box(window.document.body("A").shape)[0][0] == pytest.approx(.5)
    window.undo()
    assert model.finish().ok
    assert bounding_box(window.document.body("A").shape)[0][0] == pytest.approx(0, abs=1e-6)
    window.redo()
    assert model.finish().ok
    assert bounding_box(window.document.body("A").shape)[0][0] == pytest.approx(.5)


def test_active_push_pull_arrows_are_quarter_mm_and_fields_keep_arrows(model):
    window = model.window
    model.select("A", face=True)
    window.activate_tool("pushpull")
    panel = next(w for w, _a in window.stage.overlays
                 if getattr(w, "is_tool_panel", False))
    start = panel.value("distance")
    for _ in range(4):
        QTest.keyClick(panel, Qt.Key_Up)
    assert panel.value("distance") - start == pytest.approx(1.0)
    for _ in range(4):
        QTest.keyClick(panel, Qt.Key_Down)
    assert panel.value("distance") == pytest.approx(start)

    field = panel.fields["distance"]
    field.setFocus()
    field.setCursorPosition(1)
    QTest.keyClick(field, Qt.Key_Left)
    assert field.cursorPosition() == 0
    assert panel.value("distance") == pytest.approx(start)


def test_direct_push_pull_arrows_show_delta_and_resulting_total(model):
    window, viewport = model.window, model.viewport
    model.select("A", face=True)
    QTest.keyClick(viewport, Qt.Key_Up)
    assert window.stage.drag_readout.size_label.text() == "Height 10.25 mm"
    assert window.stage.drag_readout.delta_label.text() == "+0.25 mm"
    window._commit_nudge()
    assert window.stage.drag_readout.isHidden()
    assert model.finish().ok
    assert bounding_box(window.document.body("A").shape)[1][2] == pytest.approx(10.25)


@pytest.mark.parametrize(("entered", "height"), [("3", 13.0), ("-3", 7.0)])
def test_typed_push_pull_value_is_visible_and_click_commits(model, entered, height):
    window = model.window
    model.select("A", face=True)
    window.activate_tool("pushpull")
    panel = next(w for w, _a in window.stage.overlays
                 if getattr(w, "is_tool_panel", False))
    field = panel.fields["distance"]
    field.setFocus()
    field.selectAll()
    QTest.keyClicks(field, entered)
    assert field.text() == entered
    assert window.palette_.text in QApplication.instance().styleSheet()
    assert window.stage.drag_readout.size_label.text() == f"Height {height:.2f} mm"
    signed = float(entered)
    sign = "+" if signed >= 0 else "−"
    assert window.stage.drag_readout.delta_label.text() == f"{sign}{abs(signed):.2f} mm"
    QTest.mouseClick(panel.confirm, Qt.LeftButton)
    assert model.finish().ok
    low, high = bounding_box(window.document.body("A").shape)
    assert high[2] - low[2] == pytest.approx(height, abs=1e-6)


def test_push_pull_input_contrast_tracks_both_themes(model):
    window = model.window
    model.select("A", face=True)
    window.activate_tool("pushpull")
    dark_rules = QApplication.instance().styleSheet().split(
        "/* ---- Inputs ---- */", 1
    )[1].split("/* ---- Lists", 1)[0]
    assert f"background: {window.palette_.surface_sunken};" in dark_rules
    assert f"color: {window.palette_.text};" in dark_rules
    window.toggle_theme()
    light_rules = QApplication.instance().styleSheet().split(
        "/* ---- Inputs ---- */", 1
    )[1].split("/* ---- Lists", 1)[0]
    assert f"background: {window.palette_.surface_sunken};" in light_rules
    assert f"color: {window.palette_.text};" in light_rules


def test_move_magnets_capture_release_and_shift_override(model):
    window = model.window
    model.select("A")
    window.activate_tool("move")
    panel = next(w for w, _a in window.stage.overlays
                 if getattr(w, "is_tool_panel", False))
    # A spans X=0..10 and B starts at X=20, so 10 mm aligns their edges.
    assert panel._snap_translation((9.98, 0, 0)) == pytest.approx((10, 0, 0))
    # The release radius is wider than capture, preventing edge jitter.
    assert panel._snap_translation((9.94, 0, 0)) == pytest.approx((10, 0, 0))
    # Shift is an unconditional freeform override.
    assert panel._snap_translation((9.94, 0, 0), bypass=True) == pytest.approx(
        (9.94, 0, 0)
    )


def test_push_pull_opened_before_selection_reconfigures_for_round_face(model):
    from simplecad.kernel.detect import cylindrical_faces
    from simplecad.kernel.primitives import TubeFeature

    window, viewport = model.window, model.viewport
    window.document.add_feature(TubeFeature(
        inputs={"outer_radius": 5, "inner_radius": 3, "height": 10},
        outputs=["Tube"],
    ))
    assert model.finish().ok
    viewport.clear_selection()
    window.activate_tool("pushpull")
    panel = next(w for w, _a in window.stage.overlays
                 if getattr(w, "is_tool_panel", False))
    face = next(
        face for face, info in cylindrical_faces(window.document.body("Tube").shape)
        if not info.internal
    )
    viewport.select_subshape(window._presentations["Tube"], face)
    assert "diameter" in panel.fields and "distance" not in panel.fields
    start = panel.value("diameter")
    for _ in range(4):
        QTest.keyClick(panel, Qt.Key_Up)
    assert panel.value("diameter") == pytest.approx(start + 2.0)
    assert window.stage.drag_readout.size_label.text() == (
        f"Diameter {start + 2.0:.2f} mm"
    )
    assert window.stage.drag_readout.delta_label.text() == "+1.00 mm"


def test_align_stack_keeps_custom_position_during_camera_change(model, monkeypatch):
    from simplecad.kernel.align import edge_gaps_from_shape, frame_basis
    from simplecad.kernel.detect import analyse_plane
    from simplecad.kernel.occ import transformed
    from simplecad.ui.selection import Picked
    from simplecad.ui.viewport import camera

    window, viewport = model.window, model.viewport
    states = [[(0, 0, 100), (0, 0, 0), (0, 1, 0), 100]]
    monkeypatch.setattr(camera, "read_state", lambda _view: states[0])
    moving = window.document.body("B").shape
    target = window.document.body("A").shape
    bottom = next(face for face in sub_shapes(moving, "face")
                  if (info := analyse_plane(face)) and info.normal[2] < -0.99)
    top = next(face for face in sub_shapes(target, "face")
               if (info := analyse_plane(face)) and info.normal[2] > 0.99)
    window.selection.picks = [
        Picked("B", "face", bottom, window._presentations["B"], analyse_plane(bottom)),
        Picked("A", "face", top, window._presentations["A"], analyse_plane(top)),
    ]
    window.activate_tool("align_stack")
    panel = next(w for w, _anchor in window.stage.overlays
                 if getattr(w, "is_tool_panel", False))
    panel.horizontal_side.setCurrentIndex(2)
    panel.vertical_side.setCurrentIndex(2)
    panel.fields["horizontal_gap"].set_value(2.37)
    panel.fields["vertical_gap"].set_value(3.46)
    panel._edge_changed()
    pair = panel._pair()
    frame = panel._reference_frames(*pair)[1]
    before = bounding_box(transformed(moving, panel._solve(*pair).transform))
    values = (panel.value("horizontal_gap"), panel.value("vertical_gap"))

    states[0] = [(100, 80, 50), (0, 0, 0), (0, 0, 1), 100]
    viewport.view_changed.emit()
    assert (panel.value("horizontal_gap"), panel.value("vertical_gap")) == values
    assert panel._reference_frames(*pair)[1] is frame
    after_orbit = bounding_box(transformed(moving, panel._solve(*pair).transform))
    assert tuple(v for corner in after_orbit for v in corner) == pytest.approx(
        tuple(v for corner in before for v in corner)
    )

    # Every path that used to re-derive the frame from the live camera: the
    # anchor grid and back, and a swap of the pair. A placement the user has
    # already made must survive all of them, whatever the camera is doing.
    panel._anchor_changed()
    panel._edge_changed()
    assert panel._reference_frames(*pair)[1] is frame
    assert tuple(
        v for corner in bounding_box(transformed(moving, panel._solve(*pair).transform))
        for v in corner
    ) == pytest.approx(tuple(v for corner in before for v in corner))
    panel._swap_pair()
    panel._swap_pair()
    assert panel._reference_frames(*panel._pair())[1] is frame

    panel.fields["horizontal_gap"].set_value(4.37)
    panel._edge_changed()
    assert panel._reference_frames(*pair)[1] is frame
    placed = transformed(moving, panel._solve(*pair).transform)
    assert edge_gaps_from_shape(frame, placed, "right", "top") == pytest.approx((4.37, 3.46))
    expected = bounding_box(placed)
    panel.commit()
    assert model.finish().ok
    saved = window.document.features[-1].inputs["target_basis"]
    assert saved == pytest.approx(frame_basis(top, frame))
    # The camera picks which face axis reads right and up, never an angle of
    # its own, so what is written down is exact and survives a rebuild.
    assert set(saved) <= {0.0, 1.0, -1.0}
    assert tuple(v for corner in bounding_box(window.document.body("B").shape)
                 for v in corner) == pytest.approx(
        tuple(v for corner in expected for v in corner)
    )


def test_align_refreshes_faces_without_reorienting_to_new_camera(model, monkeypatch):
    from simplecad.core.geometry_service import serialise_shape, deserialise_shape
    from simplecad.kernel.detect import analyse_plane
    from simplecad.kernel.occ import transformed
    from simplecad.ui.selection import Picked
    from simplecad.ui.viewport import camera

    window = model.window
    state = [[(0, 0, 100), (0, 0, 0), (0, 1, 0), 100]]
    monkeypatch.setattr(camera, "read_state", lambda _: state[0])
    picks = []
    for name, sign in (("B", -1), ("A", 1)):
        face = next(f for f in sub_shapes(window.document.body(name).shape, "face")
                    if (info := analyse_plane(f)) and info.normal[2] * sign > .99)
        picks.append(Picked(name, "face", face, window._presentations[name], analyse_plane(face)))
    window.selection.picks = picks
    window.activate_tool("align_stack")
    panel = next(w for w, _ in window.stage.overlays if getattr(w, "is_tool_panel", False))
    panel.fields["horizontal_gap"].set_value(2.37)
    panel.fields["vertical_gap"].set_value(3.46)
    panel._edge_changed()
    pair = panel._pair()
    before = bounding_box(transformed(window.document.body("B").shape, panel._solve(*pair).transform))
    state[0] = [(100, 80, 50), (0, 0, 0), (0, 0, 1), 100]
    for name in ("A", "B"):
        body = window.document.body(name)
        body.shape = deserialise_shape(serialise_shape(body.shape))
    panel.preview()
    pair = panel._pair()
    assert pair is not None
    after = bounding_box(transformed(window.document.body("B").shape, panel._solve(*pair).transform))
    assert tuple(v for c in after for v in c) == pytest.approx(tuple(v for c in before for v in c))
    window.document.bodies.pop("A")
    panel.preview()
    assert not panel.confirm.isEnabled()
    assert "again" in panel.status.text()


def test_copy_during_rebuild_captures_history_without_stale_shape(model):
    import json
    from simplecad.core.document import BodyRef
    from simplecad.core.model_clipboard import ModelFragment
    from simplecad.kernel.operations import MoveFeature
    from simplecad.ui.main_window import MODEL_CLIPBOARD_MIME

    window = model.window
    model.select("A")
    move = window.document.add_feature(MoveFeature(
        inputs={"body": BodyRef("A"), "dx": 50}, outputs=["A"],
    ))
    window.geometry._busy = True
    window.copy_selection()
    try:
        data = bytes(QApplication.clipboard().mimeData().data(MODEL_CLIPBOARD_MIME))
        fragment = ModelFragment.from_dict(json.loads(data))
        assert fragment.shapes == {}
        assert fragment.features[-1]["id"] == move.id
        assert fragment.features[-1]["inputs"]["dx"] == 50
        window.paste_selection()
        assert model.finish().ok
        assert bounding_box(window.document.body("A").shape)[0][0] == pytest.approx(50, abs=1e-6)
        assert bounding_box(window.document.body("B").shape)[0][0] == pytest.approx(20, abs=1e-6)
        assert "A2" in window.document.bodies
    finally:
        QApplication.clipboard().clear()


def test_align_takes_the_next_click_as_its_target_and_rotates_by_quarters(model):
    from simplecad.kernel.detect import analyse_plane
    from simplecad.kernel.occ import transformed

    window, viewport = model.window, model.viewport
    moving = window.document.body("B").shape
    bottom = next(face for face in sub_shapes(moving, "face")
                  if (info := analyse_plane(face)) and info.normal[2] < -0.99)
    viewport.select_subshape(window._presentations["B"], bottom)
    window.activate_tool("align")
    panel = next(w for w, _ in window.stage.overlays if getattr(w, "is_tool_panel", False))
    assert panel._moving is not None and panel._target is None

    model.select("A", face=True)  # one click on A's top face, no role button
    pair = panel._pair()
    assert pair is not None and (pair[0].body, pair[1].body) == ("B", "A")
    low, high = bounding_box(transformed(moving, panel._solve(*pair).transform))
    assert low[2] == pytest.approx(10, abs=1e-6)
    assert ((low[0] + high[0]) / 2, (low[1] + high[1]) / 2) == pytest.approx((5, 5), abs=1e-6)

    panel.rotate_quarter.click()
    panel.rotate_quarter.click()
    assert panel.value("rotation") == pytest.approx(180)
    panel.commit()
    report = model.finish()
    assert report.ok, report.summary()
    align = window.document.features[-1]
    assert align.type_name == "align"
    assert window.document.parameters.evaluate(str(align.inputs["rotation"]), Dimension.ANGLE) == 180
    assert bounding_box(window.document.body("B").shape)[0][2] == pytest.approx(10, abs=1e-6)


def test_ctrl_d_duplicates_the_selection(model):
    window = model.window
    shortcuts = {shortcut.key().toString(): shortcut for shortcut in window._shortcuts}
    model.select("A")
    shortcuts["Ctrl+D"].activated.emit()
    assert model.finish().ok
    assert "A2" in window.document.bodies
    assert window.document.body("A2").shape is not None


def test_the_tree_follows_the_view_so_a_row_can_be_picked_again(model, monkeypatch):
    window, viewport = model.window, model.viewport
    tree = window.browser.bodies_tree
    # The browser selects through select_shape; route it to the stubbed one.
    monkeypatch.setattr(viewport, "select_shape",
                        lambda presentation, replace=True: viewport.select_presentations([presentation]))

    def lit():
        return [item.text(0) for item in tree.selectedItems()]

    model.select("A")
    assert lit() == ["A"]
    model.select("A", face=True)
    assert lit() == []          # a face is not the body: clicking the row must still pick it
    model.select("A")
    viewport.clear_selection()  # Esc, an empty click, a finished tool
    assert lit() == []
    tree.findItems("A", Qt.MatchExactly)[0].setSelected(True)   # a click on the row
    assert window.selection.only_bodies and window.selection.bodies == ["A"]
    assert lit() == ["A"]


def test_delete_reaches_the_model_from_the_tree(model):
    window = model.window
    tree = window.browser.bodies_tree
    on_tree = [shortcut for shortcut in window._shortcuts
               if shortcut.parent() is tree and shortcut.key().toString() == "Del"]
    assert len(on_tree) == 1 and on_tree[0].context() == Qt.WidgetShortcut
    model.select("A")
    on_tree[0].activated.emit()
    assert model.finish().ok
    assert "A" not in window.document.bodies and "B" in window.document.bodies
