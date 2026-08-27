"""Drawing on a sketch plane with the mouse.

Click to place geometry, watch it follow the cursor, click again to finish. The
sketch is live throughout -- the solver runs on every change, so the constraint
state in the status bar is always the truth about what has just been drawn.

Two things do most of the work of making this feel like drawing rather than
data entry:

* **Snapping.** The cursor snaps to existing sketch points first, then to the
  grid. Snapping to points is what makes shapes actually connect, which is what
  the solver needs to see a closed profile.
* **Inferred constraints.** A line drawn nearly horizontal gets a Horizontal
  constraint, not a horizontal-ish pair of coordinates. That is the difference
  between a sketch that survives being edited and one that drifts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ...sketch.solver import solve
from ...sketch.sketch import Sketch

#: Snap radius in millimetres, for latching onto existing points.
POINT_SNAP = 2.5
#: How close to horizontal or vertical before the constraint is inferred.
ALIGN_TOLERANCE = 4.0        # degrees
#: Default grid spacing.
GRID = 1.0


@dataclass
class DrawState:
    """What the current tool is part-way through."""

    tool: str = "line"
    points: list[tuple[float, float]] = field(default_factory=list)
    cursor: tuple[float, float] = (0.0, 0.0)
    snapped_to: str | None = None
    inferred: str | None = None

    @property
    def active(self) -> bool:
        return bool(self.points)


class SketchCanvas:
    """Turns clicks on a sketch plane into constrained sketch geometry."""

    #: Tools and how many clicks each needs. Polyline and line chain.
    TOOLS = {
        "line": 2, "rectangle": 2, "center_rectangle": 2, "circle": 2,
        "polygon": 2, "slot": 3, "arc": 3, "point": 1, "polyline": 0,
        "ellipse": 3, "spline": 0,
    }

    def __init__(self, sketch: Sketch, viewport, palette) -> None:
        self.sketch = sketch
        self.viewport = viewport
        self.palette = palette
        self.state = DrawState()
        self.grid = GRID
        self.polygon_sides = 6
        self._presentations: list = []
        self._preview = None
        self.last_result = None

    # -- geometry helpers --------------------------------------------------
    def snap(self, u: float, v: float) -> tuple[float, float]:
        """Latch onto an existing point, else onto the grid."""
        best = None
        best_distance = POINT_SNAP
        for point in self.sketch.points.values():
            distance = math.hypot(point.x - u, point.y - v)
            if distance < best_distance:
                best, best_distance = point, distance
        if best is not None:
            self.state.snapped_to = best.id
            return (best.x, best.y)
        self.state.snapped_to = None
        if self.grid > 0:
            return (round(u / self.grid) * self.grid, round(v / self.grid) * self.grid)
        return (u, v)

    def _point_at(self, u: float, v: float):
        """Reuse an existing point when one is at these coordinates."""
        for point in self.sketch.points.values():
            if math.hypot(point.x - u, point.y - v) < 1e-9:
                return point
        return self.sketch.add_point(u, v)

    def _infer_alignment(self, line, start, end) -> str | None:
        """Infer the constraint a line was clearly *meant* to have.

        Horizontal and vertical first, then relationships to what is already
        drawn: parallel, perpendicular, and equal length. Inferring these while
        drawing is what makes a hand-drawn sketch survive being edited -- a line
        that is merely 89.6 degrees to another drifts the moment anything moves,
        where a perpendicular constraint holds.

        Only one is inferred per line: stacking guesses is how a sketch ends up
        over-constrained by things the user never asked for.
        """
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        if length < 1e-9:
            return None

        angle = abs(math.degrees(math.atan2(dy, dx))) % 180.0
        if angle < ALIGN_TOLERANCE or angle > 180.0 - ALIGN_TOLERANCE:
            return self._try_constrain("horizontal", [line.id], "horizontal")
        if abs(angle - 90.0) < ALIGN_TOLERANCE:
            return self._try_constrain("vertical", [line.id], "vertical")

        for other in self._other_lines(line):
            relation = self._relation_to(line, other, angle, length)
            if relation is not None:
                return relation
        return None

    def _other_lines(self, line):
        return [
            entity for entity in self.sketch.entities.values()
            if entity.kind == "line" and entity.id != line.id
        ]

    def _relation_to(self, line, other, angle: float, length: float):
        start = self.sketch.points[other.start]
        end = self.sketch.points[other.end]
        other_dx, other_dy = end.x - start.x, end.y - start.y
        other_length = math.hypot(other_dx, other_dy)
        if other_length < 1e-9:
            return None
        other_angle = abs(math.degrees(math.atan2(other_dy, other_dx))) % 180.0

        difference = abs(angle - other_angle)
        difference = min(difference, 180.0 - difference)
        if difference < ALIGN_TOLERANCE:
            return self._try_constrain(
                "parallel", [line.id, other.id], "parallel"
            )
        if abs(difference - 90.0) < ALIGN_TOLERANCE:
            return self._try_constrain(
                "perpendicular", [line.id, other.id], "perpendicular"
            )
        if abs(length - other_length) < max(length, other_length) * 0.02:
            return self._try_constrain("equal", [line.id, other.id], "equal length")
        return None

    def _try_constrain(self, kind: str, refs, label: str):
        """Add an inferred constraint, backing out if it breaks the sketch.

        An inference the user did not ask for must never leave them with a
        sketch that will not solve.
        """
        constraint = self.sketch.constrain(kind, refs)
        result = solve(self.sketch)
        if not result.ok:
            self.sketch.remove_constraint(constraint.id)
            solve(self.sketch)
            return None
        return label

    # -- interaction -------------------------------------------------------
    # -- dimensioning ------------------------------------------------------
    def pick_entity(self, u: float, v: float, radius: float = 4.0):
        """The sketch entity nearest to a point, or None."""
        best = None
        best_distance = radius
        for entity in self.sketch.entities.values():
            distance = self._distance_to(entity, (u, v))
            if distance is not None and distance < best_distance:
                best, best_distance = entity, distance
        return best

    def _distance_to(self, entity, point) -> float | None:
        if entity.kind == "line":
            start = self.sketch.points[entity.start]
            end = self.sketch.points[entity.end]
            return _distance_to_segment_clamped(
                point, (start.x, start.y), (end.x, end.y)
            )
        centre = self.sketch.points[entity.center]
        return abs(math.dist(point, (centre.x, centre.y)) - entity.radius)

    def measurement(self, entity) -> tuple[str, float]:
        """What dimensioning this entity would mean, and its value now."""
        if entity.kind == "line":
            start = self.sketch.points[entity.start]
            end = self.sketch.points[entity.end]
            return ("distance", math.dist((start.x, start.y), (end.x, end.y)))
        return ("diameter", entity.radius * 2.0)

    def existing_dimension(self, entity):
        """The dimensional constraint already on an entity, if there is one."""
        for constraint in self.sketch.constraints:
            if constraint.kind == "distance" and entity.kind == "line":
                if set(constraint.refs[:2]) == {entity.start, entity.end}:
                    return constraint
            elif constraint.kind in ("radius", "diameter"):
                if constraint.refs and constraint.refs[0] == entity.id:
                    return constraint
        return None

    def add_dimension(self, entity, value: float):
        """Set an entity's dimension, and re-solve.

        An entity that is already dimensioned has its value *changed* rather
        than a second constraint added -- two different lengths on one line
        cannot both hold, so adding would turn every edit into a conflict.

        Rolls back if the sketch cannot satisfy the new value: a dimension that
        breaks the sketch is refused where it is typed, not left for the user to
        hunt down later.
        """
        kind, _current = self.measurement(entity)
        before = self.sketch.to_vector()

        existing = self.existing_dimension(entity)
        if existing is not None:
            previous, existing.value = existing.value, value
            result = self.solve()
            if not result.ok:
                existing.value = previous
                self.sketch.from_vector(before)
                self.solve()
                self.refresh()
                return None
            self.refresh()
            return existing

        if kind == "distance":
            constraint = self.sketch.constrain(
                "distance", [entity.start, entity.end], value
            )
        else:
            constraint = self.sketch.constrain("diameter", [entity.id], value)

        result = self.solve()
        if not result.ok:
            self.sketch.remove_constraint(constraint.id)
            self.sketch.from_vector(before)
            self.solve()
            self.refresh()
            return None
        self.refresh()
        return constraint

    def dimensions(self) -> list[tuple[str, object, float]]:
        """Dimensional constraints, as (kind, entity, value)."""
        out = []
        for constraint in self.sketch.constraints:
            if constraint.kind == "distance":
                line = next(
                    (e for e in self.sketch.entities.values()
                     if e.kind == "line"
                     and {e.start, e.end} == set(constraint.refs[:2])),
                    None,
                )
                if line is not None:
                    out.append(("distance", line, constraint.value))
            elif constraint.kind in ("radius", "diameter"):
                entity = self.sketch.entities.get(constraint.refs[0])
                if entity is not None:
                    out.append((constraint.kind, entity, constraint.value))
        return out

    def set_tool(self, tool: str) -> None:
        self.state = DrawState(tool=tool)
        self.refresh()

    def cancel(self) -> None:
        """Abandon whatever is part-drawn, keeping what is already committed."""
        self.state = DrawState(tool=self.state.tool)
        self.refresh()

    def move(self, u: float, v: float) -> None:
        self.state.cursor = self.snap(u, v)
        self.refresh()

    def click(self, u: float, v: float) -> bool:
        """Register a click. Returns True when a shape was completed."""
        position = self.snap(u, v)
        state = self.state
        state.points.append(position)

        needed = self.TOOLS.get(state.tool, 2)
        if state.tool == "spline":
            # A spline runs until the user says stop, so it accumulates points
            # and redraws through all of them each click.
            self.refresh()
            return False
        if state.tool == "polyline":
            # A polyline commits a segment per click and keeps going.
            if len(state.points) >= 2:
                self._commit_line(state.points[-2], state.points[-1])
                state.points = [state.points[-1]]
                self.refresh()
                return True
            self.refresh()
            return False

        if len(state.points) < needed:
            self.refresh()
            return False

        points = state.points
        state.points = []
        self._commit(state.tool, points)
        self.refresh()
        return True

    # -- committing --------------------------------------------------------
    def _commit(self, tool: str, points) -> None:
        builders = {
            "line": self._commit_line_pair,
            "polyline": self._commit_line_pair,
            "rectangle": self._commit_rectangle,
            "center_rectangle": self._commit_center_rectangle,
            "circle": self._commit_circle,
            "polygon": self._commit_polygon,
            "slot": self._commit_slot,
            "arc": self._commit_arc,
            "point": self._commit_point,
            "ellipse": self._commit_ellipse,
            "spline": self._commit_spline,
        }
        builder = builders.get(tool)
        if builder is not None:
            builder(points)
        self.solve()

    def _commit_line_pair(self, points) -> None:
        self._commit_line(points[0], points[1])

    def _commit_line(self, start, end) -> None:
        if math.dist(start, end) < 1e-9:
            return
        line = self.sketch.add_line(self._point_at(*start), self._point_at(*end))
        self.state.inferred = self._infer_alignment(line, start, end)

    def _commit_point(self, points) -> None:
        self._point_at(*points[0])

    def _commit_rectangle(self, points) -> None:
        (x1, y1), (x2, y2) = points
        if abs(x2 - x1) < 1e-9 or abs(y2 - y1) < 1e-9:
            return
        self.sketch.add_rectangle(x1, y1, x2, y2)

    def _commit_center_rectangle(self, points) -> None:
        (cx, cy), (px, py) = points
        half_x, half_y = abs(px - cx), abs(py - cy)
        if half_x < 1e-9 or half_y < 1e-9:
            return
        self.sketch.add_rectangle(cx - half_x, cy - half_y, cx + half_x, cy + half_y)

    def _commit_circle(self, points) -> None:
        (cx, cy), edge = points
        radius = math.dist((cx, cy), edge)
        if radius < 1e-9:
            return
        centre = self._point_at(cx, cy)
        circle = self.sketch.add_circle(centre, radius)
        self.sketch.constrain("radius", [circle.id], radius)

    def _commit_polygon(self, points) -> None:
        (cx, cy), edge = points
        radius = math.dist((cx, cy), edge)
        if radius < 1e-9:
            return
        self.sketch.add_polygon(cx, cy, radius, self.polygon_sides)

    def _commit_slot(self, points) -> None:
        start, end, edge = points
        width = _distance_to_segment(edge, start, end)
        if width < 1e-6 or math.dist(start, end) < 1e-9:
            return
        from ..tools.sketching import SketchPanel

        SketchPanel._build_slot_between(self.sketch, start, end, width)

    def _commit_ellipse(self, points) -> None:
        centre, major_point, minor_point = points
        major = math.dist(centre, major_point)
        minor = _distance_to_segment(minor_point, centre, major_point)
        if major < 1e-9 or minor < 1e-9:
            return
        rotation = math.atan2(
            major_point[1] - centre[1], major_point[0] - centre[0]
        )
        anchor = self._point_at(*centre)
        self.sketch.add_ellipse(anchor, major, minor, rotation)

    def _commit_spline(self, points) -> None:
        if len(points) < 2:
            return
        self.sketch.add_spline([self._point_at(*p) for p in points])

    def finish_open_shape(self) -> bool:
        """End a shape that has no fixed click count -- spline or polyline."""
        state = self.state
        if state.tool == "spline" and len(state.points) >= 2:
            points, state.points = state.points, []
            self._commit("spline", points)
            self.refresh()
            return True
        state.points = []
        self.refresh()
        return False

    def _commit_arc(self, points) -> None:
        centre, start, end = points
        radius = math.dist(centre, start)
        if radius < 1e-9:
            return
        anchor = self._point_at(*centre)
        start_angle = math.atan2(start[1] - centre[1], start[0] - centre[0])
        end_angle = math.atan2(end[1] - centre[1], end[0] - centre[0])
        if end_angle <= start_angle:
            end_angle += 2 * math.pi
        arc = self.sketch.add_arc(anchor, radius, start_angle, end_angle)
        self.sketch.constrain("radius", [arc.id], radius)

    # -- solving and drawing ----------------------------------------------
    def solve(self):
        self.last_result = solve(self.sketch)
        return self.last_result

    def undo_last(self) -> None:
        """Remove the most recently added entity, and any points it orphaned."""
        if not self.sketch.entities:
            return
        last = list(self.sketch.entities)[-1]
        entity = self.sketch.entities.pop(last)
        self.sketch.constraints = [
            c for c in self.sketch.constraints if last not in c.refs
        ]
        used = set()
        for other in self.sketch.entities.values():
            if other.kind == "line":
                used.update({other.start, other.end})
            else:
                used.add(other.center)
        for point_id in list(self.sketch.points):
            if point_id not in used:
                del self.sketch.points[point_id]
                self.sketch.fixed_targets.pop(point_id, None)
        self.sketch.constraints = [
            c for c in self.sketch.constraints
            if all(r in self.sketch.points or r in self.sketch.entities for r in c.refs)
        ]
        self.solve()
        self.refresh()

    def refresh(self) -> None:
        """Redraw the sketch and whatever is being drawn right now."""
        from OCP.AIS import AIS_Shape
        from OCP.Quantity import Quantity_Color, Quantity_TOC_sRGB

        from ..theme import rgb
        from ...sketch.to_occ import entity_edges

        context = self.viewport.context
        if context is None:
            return

        with self.viewport.batch():
            for presentation in self._presentations:
                context.Remove(presentation, False)
            self._presentations = []

            colour = Quantity_Color(*rgb(self.palette.accent), Quantity_TOC_sRGB)
            construction = Quantity_Color(
                *rgb(self.palette.text_faint), Quantity_TOC_sRGB
            )
            for entity in self.sketch.entities.values():
                for edge in entity_edges(self.sketch, entity):
                    presentation = AIS_Shape(edge)
                    presentation.SetColor(
                        construction if entity.construction else colour
                    )
                    presentation.SetWidth(2.2)
                    context.Display(presentation, 0, -1, False)
                    self._presentations.append(presentation)

            for edge in self._preview_edges():
                presentation = AIS_Shape(edge)
                presentation.SetColor(
                    Quantity_Color(*rgb(self.palette.ghost), Quantity_TOC_sRGB)
                )
                presentation.SetWidth(1.6)
                context.Display(presentation, 0, -1, False)
                self._presentations.append(presentation)

    def dimension_anchors(self) -> list[dict]:
        """Where each dimension should be labelled, in 3D.

        Dimensions are drawn as Qt labels over the viewport rather than as OCCT
        annotations: this OCP build does not wrap ``PrsDim``, and labels that are
        real widgets can be clicked and edited, which is what
        ``D -> click -> type -> Enter`` needs.
        """
        plane = self.sketch.plane
        anchors = []
        for kind, entity, value in self.dimensions():
            if kind == "distance":
                start_point = self.sketch.points[entity.start]
                end_point = self.sketch.points[entity.end]
                middle = (
                    (start_point.x + end_point.x) / 2.0,
                    (start_point.y + end_point.y) / 2.0,
                )
                # Offset off the line so the label does not sit on top of it.
                dx = end_point.x - start_point.x
                dy = end_point.y - start_point.y
                length = math.hypot(dx, dy) or 1.0
                offset = (-dy / length * 4.0, dx / length * 4.0)
                position = (middle[0] + offset[0], middle[1] + offset[1])
                text = f"{value:.2f}"
            else:
                centre = self.sketch.points[entity.center]
                position = (centre.x, centre.y + entity.radius + 4.0)
                text = f"⌀{value:.2f}" if kind == "diameter" else f"R{value:.2f}"
            anchors.append({
                "kind": kind,
                "entity": entity,
                "value": value,
                "text": text,
                "point": plane.to_3d(*position),
            })
        return anchors

    def _preview_edges(self) -> list:
        """Rubber-band geometry between the last click and the cursor."""
        state = self.state
        if not state.points:
            return []
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
        from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt

        plane = self.sketch.plane

        def at(u, v):
            return gp_Pnt(*plane.to_3d(u, v))

        start = state.points[0]
        cursor = state.cursor
        try:
            if state.tool in ("line", "polyline"):
                return [BRepBuilderAPI_MakeEdge(at(*start), at(*cursor)).Edge()]
            if state.tool in ("rectangle", "center_rectangle"):
                if state.tool == "center_rectangle":
                    hx, hy = abs(cursor[0] - start[0]), abs(cursor[1] - start[1])
                    x1, y1, x2, y2 = (start[0] - hx, start[1] - hy,
                                      start[0] + hx, start[1] + hy)
                else:
                    x1, y1, x2, y2 = start[0], start[1], cursor[0], cursor[1]
                corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
                return [
                    BRepBuilderAPI_MakeEdge(
                        at(*corners[i]), at(*corners[(i + 1) % 4])
                    ).Edge()
                    for i in range(4)
                ]
            if state.tool == "spline":
                run = state.points + [cursor]
                return [
                    BRepBuilderAPI_MakeEdge(at(*run[i]), at(*run[i + 1])).Edge()
                    for i in range(len(run) - 1)
                ]
            if state.tool == "ellipse" and len(state.points) >= 2:
                major_point = state.points[1]
                major = math.dist(start, major_point)
                minor = _distance_to_segment(cursor, start, major_point)
                if major < 1e-6 or minor < 1e-6:
                    return []
                from OCP.gp import gp_Elips

                rotation = math.atan2(
                    major_point[1] - start[1], major_point[0] - start[0]
                )
                from ...sketch.to_occ import _in_plane_direction

                axis = gp_Ax2(
                    at(*start), gp_Dir(*plane.normal),
                    gp_Dir(*_in_plane_direction(plane, rotation)),
                )
                return [
                    BRepBuilderAPI_MakeEdge(
                        gp_Elips(axis, max(major, minor), min(major, minor))
                    ).Edge()
                ]
            if state.tool in ("circle", "polygon", "arc", "ellipse"):
                radius = math.dist(start, cursor)
                if radius < 1e-6:
                    return []
                axis = gp_Ax2(at(*start), gp_Dir(*plane.normal), gp_Dir(*plane.x_axis))
                return [BRepBuilderAPI_MakeEdge(gp_Circ(axis, radius)).Edge()]
            if state.tool == "slot" and len(state.points) >= 2:
                return [
                    BRepBuilderAPI_MakeEdge(
                        at(*state.points[0]), at(*state.points[1])
                    ).Edge()
                ]
        except Exception:  # noqa: BLE001 - a preview is never worth failing over
            return []
        return []

    def clear_display(self) -> None:
        context = self.viewport.context
        if context is None:
            return
        with self.viewport.batch():
            for presentation in self._presentations:
                context.Remove(presentation, False)
        self._presentations = []


def _distance_to_segment_clamped(point, start, end) -> float:
    """Distance to a segment, not to the infinite line through it."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_squared = dx * dx + dy * dy
    if length_squared < 1e-18:
        return math.dist(point, start)
    t = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_squared
    t = max(0.0, min(1.0, t))
    return math.dist(point, (start[0] + t * dx, start[1] + t * dy))


def _distance_to_segment(point, start, end) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return math.dist(point, start)
    cross = abs(dx * (point[1] - start[1]) - dy * (point[0] - start[0]))
    return cross / length
