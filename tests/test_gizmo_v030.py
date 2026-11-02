"""v0.3.0 Move behaviour: the body drags on the ground, plane squares, cancel.

Driven through the stub cameras in ``tests/gizmo_stub.py``, orthographic and
perspective, so the geometry of every gesture is exact and headless.
"""

from __future__ import annotations

import math

import pytest

from tests.gizmo_stub import PerspectiveStubView, StubViewport, gizmo_on

#: Cameras looking down onto the ground from ordinary working angles.
WORKING_VIEWS = [(0.0, 0.0, -1.0), (1.0, 1.0, -1.0), (0.3, 0.4, -1.0), (1.0, 0.2, -0.6),
                 (-0.5, 1.0, -0.6), (0.0, -1.0, -0.4)]


def _perspective(gizmo, direction):
    view = PerspectiveStubView(direction, target=gizmo._pivot)
    gizmo.viewport.view = view
    return view


@pytest.mark.parametrize("perspective", [False, True])
@pytest.mark.parametrize("direction", WORKING_VIEWS)
def test_dragging_the_part_never_changes_its_height(direction, perspective):
    for dx, dy in ((60, 0), (0, -60), (45, 35), (-30, 50)):
        gizmo = gizmo_on(direction, "translation", on_part=True, pivot=(5.0, -3.0, 8.0))
        if perspective:
            _perspective(gizmo, direction)
        assert gizmo.press(400, 300)
        assert gizmo.constraint == ("ground", 2)
        gizmo.drag(400 + dx, 300 + dy)
        moved = gizmo.release()
        assert moved is not None
        assert moved[2] == 0.0, f"a body drag from {direction} changed Z by {moved[2]!r}"
        assert moved[3:] == (0.0, 0.0, 0.0)
        assert math.hypot(moved[0], moved[1]) > 1.0


@pytest.mark.parametrize("direction", [(0.0, 0.0, -1.0), (1.0, 1.0, -1.0), (0.4, -1.0, -0.7)])
def test_the_grabbed_point_stays_under_the_cursor_in_perspective(direction):
    gizmo = gizmo_on(direction, "translation", on_part=True, pivot=(0.0, 0.0, 10.0))
    view = _perspective(gizmo, direction)
    start, end = (410, 320), (470, 260)
    assert gizmo.press(*start)
    eye, ray = view.ConvertWithProj(*start)[:3], view.ConvertWithProj(*start)[3:]
    t = (10.0 - eye[2]) / ray[2]
    grabbed = tuple(eye[i] + ray[i] * t for i in range(3))
    gizmo.drag(*end)
    dx, dy, dz, *_ = gizmo.release()
    after = view.Convert(grabbed[0] + dx, grabbed[1] + dy, grabbed[2] + dz)
    assert after == pytest.approx(end, abs=0.01)


def test_a_low_camera_drags_the_part_by_screen_motion_not_a_runaway_hit():
    """Five degrees above the horizon a ray-plane hit amplifies motion ~11x."""
    elevation = math.radians(5.0)
    direction = (0.0, math.cos(elevation), -math.sin(elevation))
    gizmo = gizmo_on(direction, "translation", on_part=True)
    assert gizmo.press(400, 300)
    gizmo.drag(450, 300)                     # 50 px right
    dx, dy, dz, *_ = gizmo.release()
    assert dz == 0.0
    assert dx == pytest.approx(50.0, abs=1e-6), "screen right is +X looking along +Y"
    assert dy == pytest.approx(0.0, abs=1e-9)
    gizmo = gizmo_on(direction, "translation", on_part=True)
    assert gizmo.press(400, 300)
    gizmo.drag(400, 260)                     # 40 px up the screen: away from the viewer
    dx, dy, dz, *_ = gizmo.release()
    assert (dx, dz) == (0.0, 0.0)
    assert dy == pytest.approx(40.0, abs=1e-6)


def _plane_gizmo(direction):
    gizmo = gizmo_on(direction, "translation", axis=0)
    gizmo.show_planes = True
    gizmo._handle_under = lambda *_args: False          # no arrow under the cursor
    gizmo._nearby_handle = lambda *_args, **_kwargs: None
    return gizmo


@pytest.mark.parametrize("normal", [0, 1, 2])
def test_each_plane_square_moves_only_its_two_axes(normal):
    gizmo = _plane_gizmo((1.0, 1.2, -0.9))
    squares = dict(gizmo.plane_handles())
    assert normal in squares
    corners = squares[normal]
    cx = sum(x for x, _ in corners) / 4.0
    cy = sum(y for _, y in corners) / 4.0
    assert gizmo.plane_handle_at(cx, cy) == normal
    assert gizmo.press(round(cx), round(cy))
    assert gizmo.constraint == ("plane", normal)
    gizmo.drag(round(cx) + 40, round(cy) + 25)
    moved = gizmo.release()[:3]
    assert moved[normal] == 0.0
    assert any(moved[i] != 0.0 for i in range(3) if i != normal)


def test_a_plane_seen_edge_on_is_neither_drawn_nor_lockable():
    gizmo = _plane_gizmo((1.0, 0.0, 0.0))     # looking along X: the XY plane is a line
    normals = {normal for normal, _corners in gizmo.plane_handles()}
    assert 2 not in normals and 1 not in normals
    assert 0 in normals
    gizmo = gizmo_on((1.0, 0.0, 0.0), "translation", axis=1)
    assert gizmo.press(400, 300)
    assert not gizmo.set_constraint("plane", 2)
    assert gizmo.constraint == ("axis", 1), "a refused lock leaves the drag as it was"


def test_cancel_puts_the_part_back_and_keeps_the_gizmo():
    gizmo = gizmo_on((1.0, 1.0, -1.0), "translation", axis=0)
    cancelled = []
    gizmo.cancelled.connect(lambda: cancelled.append(True))
    assert gizmo.press(400, 300)
    gizmo.drag(460, 300)
    assert gizmo.cancel_drag()
    assert gizmo._manipulator.stopped_with is False, "the preview is rolled back"
    assert not gizmo.dragging
    assert gizmo.active, "still attached for the next drag"
    assert cancelled == [True]
    assert gizmo.release() is None, "nothing left to commit"
    assert not gizmo.cancel_drag()


def test_snapping_toggle_and_shift_are_opposites():
    seen = []

    def watch(values, bypass, fine):
        seen.append(bypass)
        return values

    from PySide6.QtCore import Qt

    for enabled, shift, bypass in ((True, False, False), (True, True, True),
                                   (False, False, True), (False, True, False)):
        gizmo = gizmo_on((0.0, 0.0, -1.0), "translation", axis=0)
        gizmo.translation_filter = watch
        gizmo.snap_enabled = enabled
        assert gizmo.press(400, 300)
        gizmo.drag(430, 300, Qt.ShiftModifier if shift else Qt.NoModifier)
        gizmo.release()
        assert seen[-1] is bypass, (enabled, shift)


def test_the_angle_wedge_reports_the_turn():
    gizmo = gizmo_on((0.0, 0.0, -1.0), "rotation", axis=2)
    assert gizmo.press(460, 300)
    gizmo.drag(400, 240)
    wedge = gizmo.wedge()
    assert wedge is not None
    _centre, _start, sweep, angle = wedge
    assert angle == pytest.approx(90.0, abs=1.0)
    assert abs(sweep) == pytest.approx(abs(angle))
    gizmo.release()
    assert gizmo.wedge() is None


def test_snap_reach_is_ten_pixels_however_far_out():
    from simplecad.ui.tools.modeling import SNAP_REACH_PX

    assert SNAP_REACH_PX == 10.0


# -- through the window ------------------------------------------------------
from tests.test_texture_editing import model  # noqa: E402,F401 - the shared window fixture


def test_a_multi_body_drag_moves_every_body_by_the_same_amount_in_one_step(model):  # noqa: F811
    from simplecad.kernel.occ import bounding_box

    window = model.window
    model.pick("A", "B")
    window.activate_tool("move")
    panel = next(w for w, _a in window.stage.overlays if getattr(w, "title", "") == "Move")
    before = {n: bounding_box(window.document.body(n).shape)[0] for n in ("A", "B")}
    panel._on_gizmo_started()
    panel._on_gizmo_commit(4.0, 6.0, 0.0, 0.0, 0.0, 0.0)
    for name in ("A", "B"):
        low = bounding_box(window.document.body(name).shape)[0]
        assert (low[0] - before[name][0], low[1] - before[name][1]) == pytest.approx((4.0, 6.0))
        assert low[2] == pytest.approx(before[name][2])
    assert window.history.undo_label.startswith("Move")
    window.undo()
    for name in ("A", "B"):
        assert bounding_box(window.document.body(name).shape)[0] == pytest.approx(before[name])


def test_esc_ends_a_pivot_pick_before_closing_the_tool(model):  # noqa: F811
    window = model.window
    model.pick("A")
    window.activate_tool("rotate")
    panel = next(w for w, _a in window.stage.overlays if getattr(w, "title", "") == "Rotate")
    panel._picking_pivot = True
    panel._end_pivot_pick = lambda: setattr(panel, "_picking_pivot", False)
    window.cancel_tool()
    assert not panel._picking_pivot
    assert any(w is panel for w, _a in window.stage.overlays), "the tool stays open"


def test_stub_viewport_still_constructs():
    """Guard the stub itself: the perspective view projects its own rays back."""
    view = PerspectiveStubView((1.0, 1.0, -1.0))
    viewport = StubViewport(view)
    eye_ray = view.ConvertWithProj(420, 280)
    point = tuple(eye_ray[i] + eye_ray[3 + i] * 500.0 for i in range(3))
    assert view.Convert(*point) == pytest.approx((420, 280), abs=1e-6)
    assert viewport.mm_per_pixel(None) == 1.0


def test_the_overlay_paints_squares_and_the_wedge(qapp):
    """Paint the overlay offscreen with a known state: no errors, real pixels."""
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QWidget

    from simplecad.ui.panels.gizmo_overlay import GizmoOverlay
    from simplecad.ui.theme import DARK

    class FakeViewport(QObject):
        painted = Signal()
        gizmo = None

        def devicePixelRatioF(self):  # noqa: N802
            return 1.0

    stage = QWidget()
    stage.resize(300, 200)
    overlay = GizmoOverlay(DARK, FakeViewport(), stage)
    overlay.resize(300, 200)
    square = [(150.0, 100.0), (190.0, 100.0), (190.0, 60.0), (150.0, 60.0)]
    overlay._drawn = (((2, tuple(square)),), 2, None, ((100.0, 120.0), 0.0, 45.0, 45.0))
    image = overlay.grab().toImage()
    assert image.pixelColor(170, 80).alpha() > 0, "the XY square is drawn"
    assert image.pixelColor(130, 105).alpha() > 0, "the wedge is drawn"
    stage.deleteLater()
