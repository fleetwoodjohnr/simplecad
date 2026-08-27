"""Regressions for bugs that shipped and were caught by driving the real UI.

Each of these was invisible to the test suite when it happened, because the
behaviour only appears when input is actually delivered. The GUI scripts caught
them; these tests keep them caught without needing a window, so a revert turns
the fast suite red.
"""

from __future__ import annotations

import math

import pytest

from simplecad.sketch.sketch import Sketch, SketchPlane
from simplecad.sketch.solver import solve


class StubViewport:
    """Enough viewport for the sketch canvas to run headless.

    ``SketchCanvas.refresh`` returns early when there is no AIS context, so a
    context of None is all that is needed to exercise the geometry and solver
    paths without a GL surface.
    """

    context = None

    def batch(self):
        from contextlib import nullcontext

        return nullcontext()

    def refresh(self) -> None:
        pass


def canvas_with_rectangle(width=40.0, height=25.0):
    from simplecad.ui.viewport.sketch_canvas import SketchCanvas
    from simplecad.ui.theme import DARK

    sketch = Sketch("Test", SketchPlane.named("XY"))
    sketch.add_rectangle(0, 0, width, height)
    canvas = SketchCanvas(sketch, StubViewport(), DARK)
    canvas.solve()
    return canvas


def bottom_edge(canvas):
    sketch = canvas.sketch
    return next(
        entity for entity in sketch.entities.values()
        if entity.kind == "line"
        and abs(sketch.points[entity.start].y) < 1e-9
        and abs(sketch.points[entity.end].y) < 1e-9
    )


def length_of(canvas, line) -> float:
    a = canvas.sketch.points[line.start]
    b = canvas.sketch.points[line.end]
    return math.dist((a.x, a.y), (b.x, b.y))


# ----------------------------------------------------------------------
# Bug: editing a dimension added a second constraint instead of changing it,
# so every edit conflicted with the value already there and was rolled back.
# ----------------------------------------------------------------------
def test_editing_a_dimension_changes_it_rather_than_adding_another():
    canvas = canvas_with_rectangle(40.0, 25.0)
    line = bottom_edge(canvas)

    canvas.add_dimension(line, 40.0)
    after_first = len(canvas.sketch.constraints)

    result = canvas.add_dimension(line, 62.0)

    assert result is not None, "the edit must be accepted"
    assert len(canvas.sketch.constraints) == after_first, (
        "editing must not add a second dimension"
    )
    assert length_of(canvas, line) == pytest.approx(62.0, abs=1e-6)


def test_a_dimension_can_be_edited_repeatedly():
    canvas = canvas_with_rectangle(40.0, 25.0)
    line = bottom_edge(canvas)
    for value in (40.0, 55.0, 30.0, 47.5):
        assert canvas.add_dimension(line, value) is not None
        assert length_of(canvas, line) == pytest.approx(value, abs=1e-6)
    distances = [c for c in canvas.sketch.constraints if c.kind == "distance"]
    assert len(distances) == 1, "one line carries one length, however often it is set"


def test_dimensioning_reduces_the_degrees_of_freedom():
    canvas = canvas_with_rectangle(40.0, 25.0)
    before = canvas.last_result.dof
    canvas.add_dimension(bottom_edge(canvas), 40.0)
    assert canvas.last_result.dof == before - 1


def test_a_circle_is_dimensioned_by_diameter():
    canvas = canvas_with_rectangle()
    centre = canvas.sketch.add_point(20, 12)
    circle = canvas.sketch.add_circle(centre, 5.0)
    canvas.solve()

    kind, value = canvas.measurement(circle)
    assert kind == "diameter"
    assert value == pytest.approx(10.0)

    canvas.add_dimension(circle, 17.0)
    assert canvas.sketch.entities[circle.id].radius == pytest.approx(8.5, abs=1e-6)


def test_an_impossible_dimension_is_refused_and_rolled_back():
    """A value the sketch cannot satisfy must not be left in place."""
    canvas = canvas_with_rectangle(40.0, 25.0)
    sketch = canvas.sketch
    line = bottom_edge(canvas)

    canvas.add_dimension(line, 40.0)
    # Pin both ends, so the length can no longer change.
    sketch.constrain("fix", [line.start])
    sketch.constrain("fix", [line.end])
    canvas.solve()
    before = length_of(canvas, line)
    count = len(sketch.constraints)

    assert canvas.add_dimension(line, 90.0) is None
    assert len(sketch.constraints) == count, "the rejected value must not linger"
    assert length_of(canvas, line) == pytest.approx(before, abs=1e-6)
    assert canvas.last_result.ok, "the sketch must still solve afterwards"


def test_picking_finds_the_nearest_entity_and_ignores_distant_ones():
    canvas = canvas_with_rectangle(40.0, 25.0)
    assert canvas.pick_entity(20.0, 0.2) is bottom_edge(canvas)
    assert canvas.pick_entity(20.0, 12.0) is None, "the middle is empty"


# ----------------------------------------------------------------------
# Bug: the gizmo re-read its transform at the *start* position on release,
# which is zero by definition, so every drag previewed and then committed
# nothing.
# ----------------------------------------------------------------------
class StubManipulator:
    """Reports a translation proportional to how far the mouse moved."""

    def __init__(self) -> None:
        self.stopped_with = None
        self.deactivated = False
        self._origin = (0, 0)

    def HasActiveMode(self):  # noqa: N802 - mirrors the OCCT name
        return True

    def StartTransform(self, x, y, _view):  # noqa: N802
        self._origin = (x, y)

    def Transform(self, x, y, _view):  # noqa: N802
        from OCP.gp import gp_Trsf, gp_Vec

        transform = gp_Trsf()
        transform.SetTranslation(
            gp_Vec(float(x - self._origin[0]), float(y - self._origin[1]), 0.0)
        )
        return transform

    def StopTransform(self, apply):  # noqa: N802
        self.stopped_with = apply

    def DeactivateCurrentMode(self):  # noqa: N802
        self.deactivated = True


class StubContext:
    """Detection is OCCT's job; here it only has to not get in the way."""

    def MoveTo(self, _x, _y, _view, _redraw):  # noqa: N802 - mirrors OCCT
        pass


def gizmo_with_stub():
    from simplecad.ui.viewport.gizmo import TransformGizmo

    class Viewport:
        context = StubContext()
        view = None

        def refresh(self):
            pass

    gizmo = TransformGizmo(Viewport())
    gizmo._manipulator = StubManipulator()
    return gizmo


def test_a_drag_commits_what_was_dragged_not_zero():
    gizmo = gizmo_with_stub()
    assert gizmo.press(100, 100) is True

    gizmo.drag(140, 100)
    gizmo.drag(175, 100)          # ends 75 px right of where it started

    components = gizmo.release()
    assert components is not None
    dx, dy, dz, *_rotation = components
    assert dx == pytest.approx(75.0), "the committed move must be the dragged move"
    assert (dy, dz) == pytest.approx((0.0, 0.0))


def test_the_preview_is_rolled_back_so_the_move_is_not_applied_twice():
    """The feature applies the move; the manipulator must not also keep it."""
    gizmo = gizmo_with_stub()
    gizmo.press(0, 0)
    gizmo.drag(30, 0)
    gizmo.release()
    assert gizmo._manipulator.stopped_with is False
    assert gizmo._manipulator.deactivated is True


def test_releasing_without_dragging_reports_nothing():
    gizmo = gizmo_with_stub()
    gizmo.press(50, 50)
    assert gizmo.release() is None, "a click that never moved is not a transform"


def test_a_release_without_a_press_is_harmless():
    assert gizmo_with_stub().release() is None


def test_drag_state_is_cleared_between_gestures():
    gizmo = gizmo_with_stub()
    gizmo.press(0, 0)
    gizmo.drag(20, 0)
    assert gizmo.release()[0] == pytest.approx(20.0)
    assert gizmo.dragging is False

    gizmo.press(0, 0)
    gizmo.drag(5, 0)
    assert gizmo.release()[0] == pytest.approx(5.0), (
        "the previous drag must not leak into this one"
    )


@pytest.mark.parametrize(
    "axis,degrees",
    [((0, 0, 1), 90.0), ((1, 0, 0), 45.0), ((0, 1, 0), -30.0)],
)
def test_rotation_decomposes_to_the_axis_it_was_made_about(axis, degrees):
    """A pure rotation must come back as a single Euler angle, not zeros."""
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

    from simplecad.ui.viewport.gizmo import _decompose

    transform = gp_Trsf()
    transform.SetRotation(
        gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(*axis)), math.radians(degrees)
    )
    dx, dy, dz, rx, ry, rz = _decompose(transform)

    assert (dx, dy, dz) == pytest.approx((0.0, 0.0, 0.0))
    got = {(1, 0, 0): rx, (0, 1, 0): ry, (0, 0, 1): rz}[axis]
    assert got == pytest.approx(degrees, abs=1e-6)
    others = [
        value for other, value in
        (((1, 0, 0), rx), ((0, 1, 0), ry), ((0, 0, 1), rz))
        if other != axis
    ]
    assert others == pytest.approx([0.0, 0.0], abs=1e-6), (
        "a rotation about one axis must not leak into the others"
    )


# ----------------------------------------------------------------------
# Reaching the tools at all
# ----------------------------------------------------------------------
def test_the_tool_registry_answers_before_anything_has_imported_it():
    """``run_action`` asks whether a key is a tool before deciding what to do.

    Registration is a side effect of importing the tool modules, so asking
    ``TOOLS`` directly answered "no" for every tool in the application until
    something had already opened one. The contextual bar and command search
    both route through that question -- so on a fresh window, selecting an edge
    and pressing Fillet reported "Fillet is not available yet" and nothing
    happened. Run in a subprocess because "nothing has imported it yet" cannot
    be arranged twice in one process.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c",
         "from simplecad.ui.tools.registry import is_tool, TOOLS;"
         "assert not TOOLS, 'nothing should be registered yet';"
         "assert is_tool('fillet');"
         "assert is_tool('subtract');"
         "assert not is_tool('delete');"
         "print('ok')"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


# ----------------------------------------------------------------------
# What the contextual bar offers
# ----------------------------------------------------------------------
class StubSelection:
    """Enough of SelectionModel for the action predicates to run."""

    def __init__(self, document, bodies):
        self.document = document
        self.bodies = list(bodies)
        self.items = list(bodies)
        self.groups = []
        self.picks = []
        self.only_bodies = True
        self.count = len(bodies)

    def edges(self):
        return []

    def faces(self):
        return []

    def planar_faces(self):
        return []

    def round_faces(self):
        return []


def test_two_bodies_offer_the_booleans_and_subtract_leads():
    """The kernel had booleans from the start and nothing could reach them."""
    from simplecad.core.document import Document
    from simplecad.ui.selection import available_actions

    actions = [
        key for key, _l, _i in available_actions(StubSelection(Document("T"), ["A", "B"]))
    ]
    assert actions[0] == "subtract"
    assert {"subtract", "join", "intersect"} <= set(actions)


def test_one_body_is_not_offered_a_boolean():
    """A boolean needs something to act with; offering it for one body lies."""
    from simplecad.core.document import Document
    from simplecad.ui.selection import available_actions

    actions = [
        key for key, _l, _i in available_actions(StubSelection(Document("T"), ["A"]))
    ]
    assert not {"subtract", "join", "intersect"} & set(actions)


# ----------------------------------------------------------------------
# Filleting a corner
# ----------------------------------------------------------------------
def test_a_corner_expands_to_three_edges_not_six():
    """``MapShapesAndAncestors`` lists an edge once per face that uses it.

    Undeduplicated, filleting a box corner added each of its three edges to the
    builder twice and stored six references in the feature.
    """
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.TopAbs import TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    from simplecad.kernel.edge_frame import edges_at_vertex

    box = BRepPrimAPI_MakeBox(10.0, 10.0, 10.0).Shape()
    explorer = TopExp_Explorer(box, TopAbs_VERTEX)
    corner = TopoDS.Vertex_s(explorer.Current())
    assert len(edges_at_vertex(box, corner)) == 3


# ----------------------------------------------------------------------
# Snapping, which runs on every mouse-move
# ----------------------------------------------------------------------
def test_snapping_gives_up_on_shapes_too_complex_to_enumerate():
    """Face centres cost a surface integration each.

    Unbounded, this ran for tens of milliseconds per mouse-move and the window
    stopped repainting -- which is what "no dot appears and it freezes" was.
    """
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    from simplecad.kernel import snapping

    box = BRepPrimAPI_MakeBox(10.0, 10.0, 10.0).Shape()
    assert snapping.snap_points(box)                     # six faces: fine
    limit = snapping.MAX_FACES_FOR_SNAPS
    try:
        snapping.MAX_FACES_FOR_SNAPS = 2
        assert snapping.snap_points(box) == []
    finally:
        snapping.MAX_FACES_FOR_SNAPS = limit


def test_the_ancestor_map_is_reused_between_calls():
    """Rebuilding it per mouse-move is a full traversal of the body."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    from simplecad.kernel import snapping

    box = BRepPrimAPI_MakeBox(10.0, 10.0, 10.0).Shape()
    explorer = TopExp_Explorer(box, TopAbs_FACE)
    face = TopoDS.Face_s(explorer.Current())

    snapping.clear_caches()
    first = snapping.snap_points(face, parent=box)
    assert snapping._ANCESTORS, "the map was not kept"
    again = snapping.snap_points(face, parent=box)
    assert [s.position for s in again] == [s.position for s in first]
    snapping.clear_caches()
    assert not snapping._ANCESTORS
