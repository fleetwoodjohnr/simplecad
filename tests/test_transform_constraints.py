"""Dragging one axis changes that axis only -- from every camera angle.

The reported bug: "when I move an object in one direction it sometimes moves
diagonally". The causes were a snap lock left over from an earlier click that
the next drag applied to its untouched axes, a part-drag plane chosen at a
grazing angle, and edge-on rings. These tests drive ``TransformGizmo`` through
a stub orthographic camera (``tests/gizmo_stub.py``) at many orientations, and
the Move panel's snapping filter directly.
"""

from __future__ import annotations

import itertools
import math
from types import SimpleNamespace

import pytest

from tests.gizmo_stub import gizmo_on


def _directions():
    """26 view directions: the six faces, twelve edges and eight corners of a cube,
    plus a few just off an axis -- where an axis is nearly end-on."""
    found = []
    for v in itertools.product((-1, 0, 1), repeat=3):
        if v != (0, 0, 0):
            found.append(tuple(float(x) for x in v))
    found += [(0.02, 0.01, -1.0), (1.0, 0.03, -0.02), (0.01, -1.0, 0.04)]
    return found


DIRECTIONS = _directions()
SCREEN_MOVES = ((60, 0), (0, -60), (45, 35), (-30, 50))


@pytest.mark.parametrize("direction", DIRECTIONS)
@pytest.mark.parametrize("axis", (0, 1, 2))
def test_an_axis_drag_changes_that_axis_only(direction, axis):
    for dx, dy in SCREEN_MOVES:
        gizmo = gizmo_on(direction, "translation", axis=axis, pivot=(12.0, -7.0, 3.0))
        assert gizmo.press(400, 300)
        gizmo.drag(400 + dx, 300 + dy)
        components = gizmo.release()
        if components is None:
            continue                  # the ray never met the axis: nothing moved
        moved = components[:3]
        for other in range(3):
            if other != axis:
                assert moved[other] == 0.0, (
                    f"dragging axis {axis} from {direction} moved axis {other} by "
                    f"{moved[other]!r}"
                )
        assert components[3:] == (0.0, 0.0, 0.0)


@pytest.mark.parametrize("direction", [(0.0, 0.0, -1.0), (1.0, 1.0, -1.0), (1.0, 0.0, 0.0)])
def test_an_axis_drag_tracks_the_cursor_along_the_axis(direction):
    """Move the cursor to where +10 mm on X projects: the part moves 10 mm."""
    gizmo = gizmo_on(direction, "translation", axis=0)
    view = gizmo.viewport.view
    start = view.screen_of((0.0, 0.0, 0.0))
    end = view.screen_of((10.0, 0.0, 0.0))
    if math.hypot(end[0] - start[0], end[1] - start[1]) < 1.0:
        pytest.skip("X points at the camera")
    assert gizmo.press(round(start[0]), round(start[1]))
    gizmo.drag(end[0], end[1])
    dx, dy, dz, *_ = gizmo.release()
    assert dx == pytest.approx(10.0, abs=0.05)
    assert (dy, dz) == (0.0, 0.0)


def test_an_axis_pointing_at_the_camera_moves_by_screen_motion_not_jumps():
    """Looking straight down Z, the Z arrow has no direction on screen.

    The closest approach of the cursor ray to it is undefined there; a pixel
    of motion used to become an enormous move. Dragging up the screen moves it
    a pixel's worth per pixel instead.
    """
    gizmo = gizmo_on((0.001, 0.0, -1.0), "translation", axis=2)
    assert gizmo.press(400, 300)
    gizmo.drag(400, 250)
    dx, dy, dz, *_ = gizmo.release()
    assert (dx, dy) == (0.0, 0.0)
    assert 10.0 < abs(dz) < 100.0


def test_a_snapping_filter_cannot_add_motion_off_the_dragged_axis():
    """The constraint is applied again after the filter: a filter adding Y
    (as a stale magnet lock once did) is overruled."""
    gizmo = gizmo_on((1.0, 1.0, -1.0), "translation", axis=2)
    gizmo.translation_filter = lambda values, bypass, fine: (1.6, -0.8, values[2])
    assert gizmo.press(400, 300)
    gizmo.drag(400, 260)
    dx, dy, dz, *_ = gizmo.release()
    assert (dx, dy) == (0.0, 0.0)
    assert dz != 0.0


@pytest.mark.parametrize("direction", [(0.3, 0.4, -1.0), (1.0, 0.2, -0.3), (-0.5, 1.0, -0.6)])
def test_dragging_the_part_slides_it_in_one_plane(direction):
    gizmo = gizmo_on(direction, "translation", on_part=True)
    assert gizmo.press(400, 300)
    normal = gizmo.constraint[1]
    gizmo.drag(450, 340)
    moved = gizmo.release()[:3]
    assert moved[normal] == 0.0


def test_the_part_drag_plane_is_never_seen_at_a_grazing_angle():
    """At 20 degrees above the horizon the ground plane amplifies motion ~3x;
    the vertical plane facing the camera is chosen instead."""
    from simplecad.ui.viewport.gizmo import facing_axis

    elevation = math.radians(20.0)
    eye = (math.cos(elevation), 0.0, -math.sin(elevation))
    assert facing_axis(eye) == 0
    iso = (1.0 / math.sqrt(3.0),) * 2 + (-1.0 / math.sqrt(3.0),)
    assert facing_axis(iso) == 2, "a normal 3/4 view still slides parts on the ground"


def test_x_y_z_keys_lock_a_part_drag_to_one_axis_mid_gesture():
    gizmo = gizmo_on((1.0, 1.0, -1.0), "translation", on_part=True)
    assert gizmo.press(400, 300)
    gizmo.drag(440, 320)
    assert gizmo.set_constraint("axis", 2)
    gizmo.drag(440, 260)
    dx, dy, dz, *_ = gizmo.release()
    assert (dx, dy) == (0.0, 0.0), "Z only, once Z is pressed"
    assert dz > 0.0


def test_shift_key_locks_an_axis_drag_to_a_plane():
    gizmo = gizmo_on((1.0, 1.0, -1.0), "translation", axis=0)
    assert gizmo.press(400, 300)
    assert gizmo.set_constraint("plane", 2)
    gizmo.drag(450, 330)
    moved = gizmo.release()[:3]
    assert moved[2] == 0.0
    assert moved[0] != 0.0 and moved[1] != 0.0


def test_a_local_frame_drag_stays_on_the_local_axis():
    from simplecad.ui.viewport.gizmo import to_frame

    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    frame = ((c, s, 0.0), (-s, c, 0.0), (0.0, 0.0, 1.0))
    gizmo = gizmo_on((0.0, 0.0, -1.0), "translation", axis=0, frame=frame)
    assert gizmo.press(400, 300)
    gizmo.drag(470, 260)
    moved = gizmo.release()[:3]
    local = to_frame(moved, frame)
    assert local[1] == pytest.approx(0.0, abs=1e-9)
    assert local[2] == pytest.approx(0.0, abs=1e-9)
    assert abs(local[0]) > 1.0


# -- rotation ---------------------------------------------------------------
def _sweep(gizmo, centre, radius, degrees, steps=24):
    """Drag the cursor around *centre* on screen by *degrees* (counter-clockwise)."""
    for step in range(1, steps + 1):
        angle = math.radians(degrees * step / steps)
        gizmo.drag(centre[0] + radius * math.cos(angle), centre[1] - radius * math.sin(angle))


def test_a_ring_drag_turns_about_its_own_axis_only():
    gizmo = gizmo_on((0.0, 0.0, -1.0), "rotation", axis=2)
    assert gizmo.press(500, 300)          # on the ring, 100 px right of the pivot
    _sweep(gizmo, (400, 300), 100, 60)
    *_, rx, ry, rz = gizmo.release()
    assert (rx, ry) == (0.0, 0.0)
    assert rz == pytest.approx(60.0, abs=0.5)


def test_an_edge_on_ring_still_turns_and_only_about_its_axis():
    gizmo = gizmo_on((0.0, 0.0, -1.0), "rotation", axis=0)
    assert gizmo.press(400, 300)
    gizmo.drag(400, 260)
    *_, rx, ry, rz = gizmo.release()
    assert (ry, rz) == (0.0, 0.0)
    assert rx != 0.0


def test_turning_the_part_ignores_the_cursor_near_the_pivot():
    """The angle around a point right under the cursor swings wildly; a drag
    that starts on the pivot turns nothing until it leaves the dead zone."""
    gizmo = gizmo_on((0.0, 0.0, -1.0), "rotation", on_part=True)
    assert gizmo.press(402, 301)
    gizmo.drag(405, 298)
    assert gizmo._last is None
    gizmo.drag(500, 300)
    _sweep(gizmo, (400, 300), 100, 45)
    *_, rx, ry, rz = gizmo.release()
    assert rz == pytest.approx(45.0, abs=1.0)


def test_screen_frame_axes_matches_the_arrow_key_rule_in_world_axes():
    from simplecad.ui.main_window import _world_axes
    from simplecad.ui.viewport.gizmo import screen_frame_axes

    s2, s3, s6 = math.sqrt(2), math.sqrt(3), math.sqrt(6)
    vectors = ((1 / s2, -1 / s2, 0), (-1 / s6, -1 / s6, 2 / s6), (-1 / s3, -1 / s3, -1 / s3))
    world = _world_axes(*vectors)
    framed = screen_frame_axes(*vectors)
    for axis, (index, sign) in zip(world, framed):
        assert axis[index] == sign


# -- the Move panel's snapping filter ----------------------------------------
def _filter_host(locks=None):
    from simplecad.ui.tools.modeling import _TransformPanel
    from simplecad.ui.viewport.gizmo import WORLD_FRAME

    viewport = SimpleNamespace(mm_per_pixel=lambda _p: 0.1, gizmo=None)
    host = SimpleNamespace(
        _translation_locks=dict(locks or {}),
        _translation_geometry=(
            ((0.0, 5.0, 10.0),) * 3,
            ([(30.0, "Other: near edge")], [(30.0, "Other: near edge")], [(30.0, "Other: near edge")]),
            (5.0, 5.0, 5.0),
            (5.0, 5.0, 5.0),
        ),
        _move_step=1.0,
        window_=SimpleNamespace(stage=SimpleNamespace(viewport=viewport)),
        _frame=lambda: WORLD_FRAME,
        _absolute=lambda: False,
        _base_delta_components=lambda: (0.0, 0.0, 0.0),
        _show_snap_target=lambda _label: None,
    )
    return host, _TransformPanel._snap_translation


def test_a_stale_magnet_lock_cannot_move_an_axis_the_drag_is_not_moving():
    """Regression: a click near another body left X/Y locks behind, and the next
    Z-arrow drag applied them -- the 'diagonal' move."""
    host, snap = _filter_host({0: (1.6, "Other: near edge to near edge"),
                               1: (-0.8, "Other: far edge to near edge")})
    out = snap(host, (0.0, 0.0, 7.3))
    assert out[0] == 0.0 and out[1] == 0.0
    assert out[2] == pytest.approx(7.0)
    assert host._translation_locks == {}


def test_the_grid_snaps_the_reference_point_not_the_offset():
    """A part centred at x = 5.3 dragged about 2 mm lands its centre on 7, not 7.3."""
    host, snap = _filter_host()
    host._translation_geometry = host._translation_geometry[:3] + ((5.3, 5.0, 5.0),)
    out = snap(host, (1.9, 0.0, 0.0))
    assert out[0] == pytest.approx(1.7)
    assert out[1:] == (0.0, 0.0)


def test_magnets_still_catch_on_the_dragged_axis():
    host, snap = _filter_host()
    # source near edge 0 -> target 30 is a 30 mm move; 29.95 is within capture.
    out = snap(host, (29.95, 0.0, 0.0))
    assert out == (30.0, 0.0, 0.0)


def test_rotate_keeps_one_pivot_for_the_whole_session():
    """Re-centring after each turn meant +45 then -45 did not come back."""
    from simplecad.ui.tools.modeling import _TransformPanel

    centres = iter([(1.0, 2.0, 3.0), (9.0, 9.0, 9.0)])
    host = SimpleNamespace(_session_pivot=None, _picked_axis=None,
                           _pivot_index=lambda: 0,
                           _shared_pivot=lambda _names: next(centres))
    first = _TransformPanel._pivot_point(host, ["A"])
    second = _TransformPanel._pivot_point(host, ["A"])
    assert first == second == (1.0, 2.0, 3.0)


# -- the Move feature -------------------------------------------------------
def _box_doc():
    from simplecad.core.document import Document
    from simplecad.core.rebuild import Rebuilder
    from simplecad.kernel.primitives import BoxFeature

    document = Document()
    document.add_feature(BoxFeature(inputs={"width": 40, "depth": 20, "height": 10},
                                    outputs=["Block"]))
    return document, Rebuilder(document)


def test_a_turn_about_an_arbitrary_axis_is_exact():
    from simplecad.core.document import BodyRef
    from simplecad.kernel.occ import bounding_box
    from simplecad.kernel.operations import MoveFeature

    document, rebuilder = _box_doc()
    rebuilder.rebuild()
    before = bounding_box(document.bodies["Block"].shape)
    document.add_feature(MoveFeature(inputs={
        "body": BodyRef("Block"), "axis": [0.0, 0.0, 1.0], "angle": "90 deg",
        "pivot": [0.0, 0.0, 0.0],
    }, outputs=["Block"]))
    rebuilder.rebuild()
    after = bounding_box(document.bodies["Block"].shape)
    width = before[1][0] - before[0][0]
    depth = before[1][1] - before[0][1]
    assert after[1][0] - after[0][0] == pytest.approx(depth, abs=1e-6)
    assert after[1][1] - after[0][1] == pytest.approx(width, abs=1e-6)


def test_plus_45_then_minus_45_about_one_pivot_returns_exactly():
    from simplecad.core.document import BodyRef
    from simplecad.kernel.occ import bounding_box
    from simplecad.kernel.operations import MoveFeature

    document, rebuilder = _box_doc()
    rebuilder.rebuild()
    before = bounding_box(document.bodies["Block"].shape)
    for angle in ("45 deg", "-45 deg"):
        document.add_feature(MoveFeature(inputs={
            "body": BodyRef("Block"), "axis": [0.3, 0.4, 0.866], "angle": angle,
            "pivot": [7.0, -3.0, 2.0],
        }, outputs=["Block"]))
    rebuilder.rebuild()
    after = bounding_box(document.bodies["Block"].shape)
    for a, b in zip(before, after):
        assert a == pytest.approx(b, abs=1e-6)


def test_accumulated_rotation_reads_every_turn_and_reset_undoes_it():
    from OCP.gp import gp_Vec

    from simplecad.core.document import BodyRef
    from simplecad.kernel.occ import bounding_box
    from simplecad.kernel.operations import MoveFeature, accumulated_rotation

    document, rebuilder = _box_doc()
    rebuilder.rebuild()
    before = bounding_box(document.bodies["Block"].shape)
    size = [before[1][i] - before[0][i] for i in range(3)]
    document.add_feature(MoveFeature(inputs={
        "body": BodyRef("Block"), "rx": "30 deg", "rz": "20 deg", "order": "xyz",
        "pivot": [0, 0, 0],
    }, outputs=["Block"]))
    document.add_feature(MoveFeature(inputs={
        "body": BodyRef("Block"), "axis": [0, 1, 0], "angle": "15 deg", "pivot": [5, 5, 5],
    }, outputs=["Block"]))
    rebuilder.rebuild()
    turn = accumulated_rotation(document.features, "Block", document.parameters)
    axis = gp_Vec()
    angle = turn.GetRotation().GetVectorAndAngle(axis)
    angle = angle[0] if isinstance(angle, tuple) else angle
    low, high = bounding_box(document.bodies["Block"].shape)
    document.add_feature(MoveFeature(inputs={
        "body": BodyRef("Block"), "axis": [axis.X(), axis.Y(), axis.Z()],
        "angle": f"{-math.degrees(angle):.12g} deg",
        "pivot": [(low[i] + high[i]) / 2 for i in range(3)],
    }, outputs=["Block"]))
    rebuilder.rebuild()
    low, high = bounding_box(document.bodies["Block"].shape)
    assert [high[i] - low[i] for i in range(3)] == pytest.approx(size, abs=1e-5)


def test_old_moves_without_an_axis_build_exactly_as_before():
    from simplecad.kernel.operations import move_transform

    old = move_transform((1, 2, 3), (10, 20, 30), (4, 5, 6), "xyz")
    new = move_transform((1, 2, 3), (10, 20, 30), (4, 5, 6), "xyz", None, 0.0)
    for row in range(1, 4):
        for column in range(1, 5):
            assert old.Value(row, column) == new.Value(row, column)


# -- a drag released while the model is still rebuilding -----------------------
from tests.test_texture_editing import model  # noqa: E402,F401 - the shared window fixture


def test_a_drag_released_during_a_rebuild_is_applied_on_its_own(model, monkeypatch):  # noqa: F811
    """Regression: the refused commit stayed in the fields and the next drag
    added to it, so a Z drag also applied the Y drag before it -- diagonally."""
    import simplecad.ui.geometry_client as client
    from simplecad.kernel.occ import bounding_box

    busy = {"value": False}
    monkeypatch.setattr(client.GeometryClient, "busy", property(lambda self: busy["value"]))
    window = model.window
    model.pick("A")
    window.activate_tool("move")
    panel = next(w for w, _a in window.stage.overlays if getattr(w, "title", "") == "Move")
    low0 = bounding_box(window.document.body("A").shape)[0]
    busy["value"] = True
    panel._on_gizmo_started()
    panel._on_gizmo_commit(0.0, 7.0, 0.0, 0.0, 0.0, 0.0)      # a Y drag, refused for now
    assert panel._field_values() == (0.0, 0.0, 0.0), "nothing left over for the next drag"
    panel._on_gizmo_started()
    panel._on_gizmo_commit(0.0, 0.0, 5.0, 0.0, 0.0, 0.0)      # then a Z drag
    assert len(panel._queued) == 2
    busy["value"] = False
    panel.on_model_rebuilt()
    low1 = bounding_box(window.document.body("A").shape)[0]
    assert low1[1] - low0[1] == pytest.approx(7.0)
    assert low1[2] - low0[2] == pytest.approx(5.0)
    assert low1[0] == pytest.approx(low0[0])
    assert not panel._queued
