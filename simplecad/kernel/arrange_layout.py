"""Where several footprints go inside a region: the 2-D half of Arrange.

Everything here is plain shapely in face coordinates -- millimetres along the
target face's U and V -- so a pattern can be planned, checked and re-planned
for a suggestion in milliseconds, without the geometry kernel. ``arrange.py``
turns a face into a region and bodies into footprints, and turns the
placements returned here back into transforms.

A footprint is an item's outline seen from above the face, drawn around the
item's reference point at the origin. A placement ``(u, v, angle)`` turns the
footprint by ``angle`` degrees about that point and moves the point to (u, v).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy
import shapely
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box

PATTERNS = ("grid", "linear", "radial", "staggered", "packed", "custom")

#: Anything closer than the gap by more than this is a collision, so rounding
#: never turns an exact fit red.
_SLACK = 1e-4


@dataclass(frozen=True)
class Settings:
    pattern: str = "grid"
    spacing: str = "auto"          # "auto" spreads out; "fixed" holds ``gap`` exactly
    gap: float = 5.0               # fixed: the clear gap; auto: the least allowed
    gap_y: float | None = None     # custom: a separate gap between rows
    direction: float = 0.0         # degrees from +U; the whole pattern turns with it
    align: str = "center"          # one of the nine anchor presets
    rows: int = 0                  # 0 = choose
    columns: int = 0
    fill: str = "rows"             # fill "rows" or "columns" first
    distribute: str = "between"    # gaps "between" items, or "evenly" including edges
    rotation: float = 0.0          # every item turned by this many degrees
    auto_rotate: bool = False
    radius: float = 0.0            # radial: 0 = choose
    start_angle: float = 0.0
    face_center: bool = False
    contain: bool = True           # whole footprint inside, or only its centre
    period: float | None = None    # circumference of a full cylinder


@dataclass
class Layout:
    placements: list[tuple[float, float, float]]
    #: index -> ("outside" | "overlap" | "close", the other item or None)
    problems: dict[int, tuple[str, int | None]] = field(default_factory=dict)
    gap: float = 0.0
    rows: int = 0
    columns: int = 0
    radius: float = 0.0

    @property
    def fits(self) -> bool:
        return not self.problems


def placed(footprint, placement):
    u, v, angle = placement
    return affinity.translate(_turned(footprint, angle), u, v)


def _turned(shape, angle):
    return affinity.rotate(shape, angle, origin=(0.0, 0.0)) if angle else shape


def check(items, placements, region, *, gap, contain=True, wrap=None, inside=None):
    """Which placed items leave the region or come closer than *gap* to another."""
    shapes = [placed(fp, p) for fp, p in zip(items, placements)]
    if inside is None:
        inside = region.buffer(_SLACK)
        shapely.prepare(inside)
    problems = {}
    for index, shape in enumerate(shapes):
        if not inside.covers(shape if contain else Point(placements[index][:2])):
            problems[index] = ("outside", None)
    others, owner = list(shapes), list(range(len(shapes)))
    if wrap is not None:
        # Round a full cylinder, an item just past the seam meets the first one.
        for sign in (-1.0, 1.0):
            others += [affinity.translate(s, sign * wrap[0], sign * wrap[1]) for s in shapes]
            owner += list(range(len(shapes)))
    if len(shapes) < 2:
        return problems
    tree = shapely.STRtree(others)
    left, right = tree.query(shapes, predicate="dwithin", distance=max(gap, 0.0))
    for i, k in zip(left.tolist(), right.tolist()):
        j = owner[k]
        if j == i:
            continue
        overlap = shapes[i].intersection(others[k]).area > 1e-6
        if not overlap and (gap <= _SLACK or shapes[i].distance(others[k]) >= gap - _SLACK):
            continue
        if overlap or i not in problems:
            problems[i] = ("overlap" if overlap else "close", j)
    return problems


class _Job:
    """One planning pass, in the pattern's own frame (turned by ``direction``)."""

    def __init__(self, region, bounds, settings: Settings, items):
        self.settings = settings
        self.region = region
        self.bounds = bounds
        self.items = items
        self.inside = region.buffer(_SLACK)
        shapely.prepare(self.inside)
        self.wrap = None
        if settings.period:
            angle = math.radians(-settings.direction)
            self.wrap = (settings.period * math.cos(angle), settings.period * math.sin(angle))
        self.extents = [fp.bounds for fp in items]

    def finish(self, layout: Layout) -> Layout:
        layout.problems = check(
            self.items, layout.placements, self.region, gap=self.gap_floor(),
            contain=self.settings.contain, wrap=self.wrap, inside=self.inside,
        )
        return layout

    def gap_floor(self) -> float:
        s = self.settings
        return min(s.gap, s.gap_y if s.gap_y is not None else s.gap)


def _anchor(align: str) -> tuple[int, int]:
    return (
        -1 if "left" in align else 1 if "right" in align else 0,
        1 if "top" in align else -1 if "bottom" in align else 0,
    )


def _start(low, high, size, side) -> float:
    if side < 0:
        return low
    if side > 0:
        return high - size
    return (low + high - size) / 2.0


def _span(region, y, height, low, high):
    """The stretch along X where a band of *height* centred on *y* lies in the region."""
    found = None
    for level in (y - height / 2.0 + 1e-6, y, y + height / 2.0 - 1e-6):
        piece = LineString([(low - 1.0, level), (high + 1.0, level)]).intersection(region)
        pieces = [g for g in getattr(piece, "geoms", [piece]) if g.length > 0]
        if not pieces:
            return (low, high)
        best = max(pieces, key=lambda g: g.length)
        a, b = best.bounds[0], best.bounds[2]
        found = (a, b) if found is None else (max(found[0], a), min(found[1], b))
    return found if found[1] > found[0] else (low, high)


# ----------------------------------------------------------------------
# Patterns. Each returns placements in the pattern frame.
# ----------------------------------------------------------------------
def _linear(job: _Job) -> Layout:
    s, ext = job.settings, job.extents
    n = len(ext)
    widths = [e[2] - e[0] for e in ext]
    height = max(e[3] - e[1] for e in ext)
    lo_x, lo_y, hi_x, hi_y = job.bounds
    side_x, side_y = _anchor(s.align)
    middle = _start(lo_y, hi_y, height, side_y) + height / 2.0
    x0, x1 = _span(job.region, middle, height, lo_x, hi_x)
    total = sum(widths)
    gap, start = s.gap, None
    if s.spacing == "auto":
        if job.wrap is not None:
            slots, x0, x1 = n, lo_x, lo_x + s.period
        else:
            slots = n + 1 if s.distribute == "evenly" else n - 1
        if slots > 0 and (x1 - x0 - total) / slots >= s.gap:
            gap = (x1 - x0 - total) / slots
            start = x0 + (gap if s.distribute == "evenly" and job.wrap is None else 0.0)
    if start is None:
        start = _start(x0, x1, total + gap * (n - 1), side_x)
    placements, cursor = [], start
    for (bx0, by0, _bx1, by1), width in zip(ext, widths):
        if side_y > 0:
            v = middle + height / 2.0 - by1
        elif side_y < 0:
            v = middle - height / 2.0 - by0
        else:
            v = middle - (by0 + by1) / 2.0
        placements.append((cursor - bx0, v, 0.0))
        cursor += width + gap
    return job.finish(Layout(placements, gap=gap, rows=1, columns=n))


def _grid_shapes(n: int, rows: int, columns: int) -> list[tuple[int, int]]:
    if rows and columns:
        return [(max(rows, math.ceil(n / columns)), columns)]
    if rows:
        return [(rows, math.ceil(n / rows))]
    if columns:
        return [(math.ceil(n / columns), columns)]
    return [(math.ceil(n / c), c) for c in range(1, n + 1)
            if c == 1 or math.ceil(n / c) * (c - 1) < n]


def _grid_layout(job: _Job, area, shape, staggered: bool, row_step=1.0) -> Layout:
    s, ext = job.settings, job.extents
    n, (rows, columns) = len(ext), shape
    cells = [(i // columns, i % columns) if s.fill != "columns" else (i % rows, i // rows)
             for i in range(n)]
    widths = [e[2] - e[0] for e in ext]
    heights = [e[3] - e[1] for e in ext]
    if staggered:
        col_w, row_h = [max(widths)] * columns, [max(heights)] * rows
    else:
        col_w, row_h = [0.0] * columns, [0.0] * rows
        for (row, col), w, h in zip(cells, widths, heights):
            col_w[col], row_h[row] = max(col_w[col], w), max(row_h[row], h)
    lo_x, lo_y, hi_x, hi_y = area
    side_x, side_y = _anchor(s.align)
    shifted = staggered and rows > 1
    edges = 2 if s.distribute == "evenly" else 0

    # Columns: clear gaps between cell edges, plus half a pitch for a stagger.
    # Round a full cylinder the last column is next to the first, so a turn
    # holds as many gaps as columns and a stagger adds no width.
    wrapped = job.wrap is not None and abs(job.wrap[1]) < 1e-9
    gap_x = s.gap
    if wrapped:
        fixed_x, slots_x = sum(col_w), columns
    else:
        fixed_x = sum(col_w) + (col_w[0] / 2.0 if shifted else 0.0)
        slots_x = columns - 1 + edges + (0.5 if shifted else 0.0)
    spread_x = s.spacing == "auto" and slots_x > 0 and (hi_x - lo_x - fixed_x) / slots_x >= s.gap
    if spread_x:
        gap_x = (hi_x - lo_x - fixed_x) / slots_x
    width = fixed_x + gap_x * (columns - 1 + (0.5 if shifted and not wrapped else 0.0))
    if spread_x:
        x_start = lo_x + (gap_x / 2.0 if wrapped else gap_x if edges else 0.0)
    else:
        x_start = _start(lo_x, hi_x, width, side_x)

    # Rows, read from the top down.
    gap_y = s.gap_y if s.gap_y is not None else s.gap
    slots_y = rows - 1 + edges
    spread_y = s.spacing == "auto" and slots_y > 0 and (hi_y - lo_y - sum(row_h)) / slots_y >= gap_y
    if spread_y:
        gap_y = (hi_y - lo_y - sum(row_h)) / slots_y
    pitches = [row_h[r] + gap_y for r in range(rows)]
    if staggered and not spread_y:
        pitches = [p * row_step for p in pitches]
    height = sum(pitches[:-1]) + row_h[-1]
    y_top = hi_y - (gap_y if edges else 0.0) if spread_y else _start(lo_y, hi_y, height, side_y) + height

    lefts = [x_start + sum(col_w[:c]) + gap_x * c for c in range(columns)]
    tops = [y_top - sum(pitches[:r]) for r in range(rows)]
    placements = []
    for (row, col), (bx0, by0, bx1, by1) in zip(cells, ext):
        left = lefts[col] + ((col_w[0] + gap_x) / 2.0 if shifted and row % 2 else 0.0)
        u = left + col_w[col] / 2.0 - (bx0 + bx1) / 2.0
        v = tops[row] - row_h[row] / 2.0 - (by0 + by1) / 2.0
        placements.append((u, v, 0.0))
    gaps = ([gap_x] if columns > 1 else []) + ([gap_y] if rows > 1 else [])
    return job.finish(Layout(placements, gap=min(gaps, default=0.0), rows=rows, columns=columns))


def _grid(job: _Job, staggered: bool = False) -> Layout:
    s, ext = job.settings, job.extents
    n = len(ext)
    shapes = _grid_shapes(n, s.rows, s.columns)
    widths = [e[2] - e[0] for e in ext]
    heights = [e[3] - e[1] for e in ext]

    def slack(shape, area):
        # A cheap estimate from the widest and tallest item, only to rank the
        # shapes; each layout then measures its own cells.
        rows, columns = shape
        need_x = max(widths) * (columns + (0.5 if staggered and rows > 1 else 0.0)) \
            + s.gap * (columns - 1)
        need_y = max(heights) * rows + (s.gap_y if s.gap_y is not None else s.gap) * (rows - 1)
        return min(area[2] - area[0] - need_x, area[3] - area[1] - need_y)

    # A rectangle takes the grid as it is. Anything else -- a disk, an L, a
    # plate with holes -- also tries the largest clear rectangle inside it and
    # ever smaller boxes about its middle; automatic spacing keeps whichever
    # fitting layout leaves the widest gaps.
    if job.region.area >= box(*job.bounds).area * 0.999:
        areas = [job.bounds]
    else:
        areas = [job.bounds, _largest_rectangle(job)] + [
            _scaled(job.bounds, 1.0 - 0.05 * k) for k in range(1, 13)]
    steps = (math.sqrt(3) / 2.0, 0.9, 1.0) if staggered and s.spacing != "auto" else (1.0,)
    best = first = None
    for area in areas:
        if area is None:
            continue
        for shape in sorted(shapes, key=lambda shape: -slack(shape, area))[:4]:
            for step in steps:
                layout = _grid_layout(job, area, shape, staggered, step)
                if layout.fits:
                    if s.spacing != "auto":
                        return layout
                    if best is None or layout.gap > best.gap + 1e-9:
                        best = layout
                    break
                first = first or layout
    return best or first


def _largest_rectangle(job: _Job, cells: int = 64):
    """The biggest axis-aligned rectangle of whole raster cells inside the region."""
    lo_x, lo_y, hi_x, hi_y = job.bounds
    step = max(hi_x - lo_x, hi_y - lo_y) / cells
    if step <= 0:
        return None
    nx, ny = max(1, int((hi_x - lo_x) / step)), max(1, int((hi_y - lo_y) / step))
    xs, ys = numpy.meshgrid(lo_x + numpy.arange(nx) * step, lo_y + numpy.arange(ny) * step)
    free = shapely.covers(job.inside, shapely.box(xs, ys, xs + step, ys + step))
    heights = numpy.zeros(nx, dtype=int)
    best = (0, 0, 0, 0, 0)            # area, left, right (exclusive), top row, height
    for row in range(ny):
        heights = numpy.where(free[row], heights + 1, 0)
        stack = []
        for col in range(nx + 1):
            here = heights[col] if col < nx else 0
            start = col
            while stack and stack[-1][1] >= here:
                start, tall = stack.pop()
                if tall * (col - start) > best[0]:
                    best = (tall * (col - start), start, col, row, tall)
            stack.append((start, here))
    area, left, right, top, tall = best
    if not area:
        return None
    return (lo_x + left * step, lo_y + (top - tall + 1) * step,
            lo_x + right * step, lo_y + (top + 1) * step)


def _scaled(bounds, scale):
    lo_x, lo_y, hi_x, hi_y = bounds
    cx, cy = (lo_x + hi_x) / 2.0, (lo_y + hi_y) / 2.0
    hx, hy = (hi_x - lo_x) * scale / 2.0, (hi_y - lo_y) * scale / 2.0
    return (cx - hx, cy - hy, cx + hx, cy + hy)


def _outline(region) -> Polygon:
    parts = list(getattr(region, "geoms", [region]))
    return Polygon(max(parts, key=lambda p: p.area).exterior)


def _reach(fp) -> float:
    return max(math.hypot(x, y) for x, y in fp.exterior.coords)


def _radial(job: _Job) -> Layout:
    s, items = job.settings, job.items
    n = len(items)
    outline = _outline(job.region)
    centre = outline.centroid
    cx, cy = centre.x, centre.y
    reach = [_reach(fp) for fp in items]
    start = math.radians(s.start_angle)

    def facing(theta):
        return math.degrees(theta) - 90.0 if s.face_center else 0.0

    def tangential(index, theta):
        # How wide the item is across the ring at *theta*.
        if not s.face_center:
            return 2.0 * reach[index]
        b = items[index].bounds
        return b[2] - b[0]

    def at(radius, angles):
        return job.finish(Layout(
            [(cx + radius * math.cos(a), cy + radius * math.sin(a), facing(a)) for a in angles],
            gap=s.gap, radius=radius,
        ))

    if n == 1:
        return at(s.radius, [start])
    if s.spacing == "auto":
        angles = [start + 2.0 * math.pi * i / n for i in range(n)]
        if s.radius > 0:
            return at(s.radius, angles)
        smallest = (2.0 * max(reach) + s.gap) / (2.0 * math.sin(math.pi / n))
        largest = max(outline.exterior.distance(Point(cx, cy)) - max(reach), smallest)
        first = None
        for k in range(17):
            layout = at(largest - (largest - smallest) * k / 16.0, angles)
            if layout.fits:
                layout.gap = _ring_gap(layout, items)
                return layout
            first = first or layout
        return first

    # Fixed: the clear gap between neighbours, measured where they come closest.
    widths = [tangential(i, 0.0) for i in range(n)]

    def clear(layout):
        return not any(kind != "outside" for kind, _ in layout.problems.values())

    if not s.radius:
        # A closed ring: angles shared out by width, radius as small as the gap allows.
        shares = [(widths[i] + widths[(i + 1) % n]) / 2.0 + s.gap for i in range(n)]
        angles = [start]
        for share in shares[:-1]:
            angles.append(angles[-1] + 2.0 * math.pi * share / sum(shares))
        low, high = 0.0, sum(shares) / (2.0 * math.pi)
        for _ in range(40):
            if clear(at(high, angles)):
                break
            low, high = high, high * 1.5
        for _ in range(24):
            middle = (low + high) / 2.0
            low, high = (low, middle) if clear(at(middle, angles)) else (middle, high)
        return at(high, angles)

    # A given radius: an arc centred on the start angle, opened until clear.
    def arc(widen):
        steps = [2.0 * math.asin(min(1.0, widen * ((widths[i] + widths[i + 1]) / 2.0 + s.gap)
                                     / (2.0 * s.radius)))
                 for i in range(n - 1)]
        angles = [start - sum(steps) / 2.0]
        for step in steps:
            angles.append(angles[-1] + step)
        return at(s.radius, angles), sum(steps)

    layout, _sweep = arc(1.0)
    if clear(layout):
        return layout
    low, high = 1.0, 1.25
    while True:
        layout, sweep = arc(high)
        if clear(layout) or sweep >= 2.0 * math.pi or high > 64:
            break
        low, high = high, high * 1.25
    if not clear(layout):
        return layout
    for _ in range(20):
        middle = (low + high) / 2.0
        low, high = (low, middle) if clear(arc(middle)[0]) else (middle, high)
    return arc(high)[0]


def _ring_gap(layout: Layout, items) -> float:
    shapes = [placed(fp, p) for fp, p in zip(items, layout.placements)]
    return min(shapes[i].distance(shapes[(i + 1) % len(shapes)]) for i in range(len(shapes)))


def _packed(job: _Job) -> Layout:
    """Bottom-left fill, biggest first."""
    # ponytail: greedy bottom-left with 90-degree turns, not true nesting;
    # upgrade to no-fit-polygon nesting if packing density matters.
    s, items = job.settings, job.items
    gap = s.gap
    lo_x, lo_y, hi_x, hi_y = job.bounds
    turns = (0.0, 90.0, 180.0, 270.0) if s.auto_rotate else (0.0,)
    order = sorted(range(len(items)), key=lambda i: -items[i].area)
    placements: list = [None] * len(items)
    shapes: list = []
    overflow_x = hi_x + gap

    def first_free(fp, spots, tree, others):
        """The lowest, then leftmost, spot where *fp* fits: ``(key, u, v)``."""
        b = fp.bounds
        spots = numpy.unique(numpy.round(numpy.asarray(spots, dtype=float), 9), axis=0)
        if s.contain:
            spots = spots[(spots[:, 0] + b[0] >= lo_x - _SLACK) & (spots[:, 1] + b[1] >= lo_y - _SLACK)
                          & (spots[:, 0] + b[2] <= hi_x + _SLACK) & (spots[:, 1] + b[3] <= hi_y + _SLACK)]
        if not len(spots):
            return None
        spots = spots[numpy.lexsort((spots[:, 0], spots[:, 1]))]
        ring = numpy.asarray(fp.exterior.coords)
        candidates = shapely.polygons(ring[None, :, :] + spots[:, None, :])
        ok = shapely.covers(job.inside, candidates if s.contain else shapely.points(spots))
        if tree is not None:
            if gap > _SLACK:
                left, right = tree.query(candidates, predicate="dwithin", distance=gap)
                bad = shapely.distance(candidates[left], others[right]) < gap - _SLACK
            else:
                left, right = tree.query(candidates, predicate="intersects")
                bad = shapely.area(shapely.intersection(candidates[left], others[right])) > 1e-6
            ok[left[bad]] = False
        hits = numpy.nonzero(ok)[0]
        if not hits.size:
            return None
        u, v = spots[hits[0]]
        return ((v + b[1], u + b[0]), float(u), float(v))

    for index in order:
        others = numpy.array(shapes, dtype=object)
        tree = shapely.STRtree(others) if shapes else None
        near = shapely.bounds(others).reshape(-1, 4) if shapes else numpy.empty((0, 4))
        options = []
        for turn in turns:
            # Packing fills an outline's holes; nesting inside a tube is not attempted.
            fp = Polygon(_turned(items[index], turn).exterior)
            b = fp.bounds
            spots = numpy.vstack([
                [[lo_x - b[0], lo_y - b[1]]],
                numpy.column_stack([near[:, 2] + gap - b[0], near[:, 1] - b[1]]),
                numpy.column_stack([near[:, 0] - b[0], near[:, 3] + gap - b[1]]),
                numpy.column_stack([numpy.full(len(near), lo_x - b[0]), near[:, 3] + gap - b[1]]),
                numpy.column_stack([near[:, 2] + gap - b[0], numpy.full(len(near), lo_y - b[1])]),
            ])
            options.append((turn, fp, spots))
        best = None
        for attempt in range(2):
            for turn, fp, spots in options:
                hit = first_free(fp, spots, tree, others)
                if hit is not None and (best is None or hit[0] < best[0]):
                    best = (hit[0], hit[1], hit[2], turn, fp)
            if best is not None:
                break
            # Nothing at the corners: scan the region on a coarse raster.
            smallest = min(min(o[1].bounds[2] - o[1].bounds[0], o[1].bounds[3] - o[1].bounds[1])
                           for o in options)
            step = max(gap, smallest / 3.0, math.hypot(hi_x - lo_x, hi_y - lo_y) / 90.0)
            xs, ys = numpy.meshgrid(numpy.arange(lo_x, hi_x + step, step),
                                    numpy.arange(lo_y, hi_y + step, step))
            raster = numpy.column_stack([xs.ravel(), ys.ravel()])
            options = [(turn, fp, raster - numpy.array(fp.bounds[:2])) for turn, fp, _ in options]
        if best is None:
            # No room: park it beside the region, where the check marks it.
            fp = items[index]
            best = (None, overflow_x - fp.bounds[0], lo_y - fp.bounds[1], 0.0, fp)
            overflow_x += fp.bounds[2] - fp.bounds[0] + gap
        _key, u, v, turn, fp = best
        placements[index] = (u, v, turn)
        shapes.append(affinity.translate(fp, u, v))

    layout = job.finish(Layout(placements, gap=gap))
    if layout.fits:
        # Slide the cluster to the chosen alignment when it still fits there.
        low_x, low_y, high_x, high_y = shapely.total_bounds(numpy.array(shapes, dtype=object))
        side_x, side_y = _anchor(s.align)
        dx = float(_start(lo_x, hi_x, high_x - low_x, side_x) - low_x)
        dy = float(_start(lo_y, hi_y, high_y - low_y, side_y) - low_y)
        moved = job.finish(Layout([(u + dx, v + dy, a) for u, v, a in placements], gap=gap))
        if moved.fits:
            return moved
    return layout


# ----------------------------------------------------------------------
# Entry points
# ----------------------------------------------------------------------
def _square_angle(fp) -> float:
    """The turn that lays an item's long side along U (0 for round items)."""
    rect = shapely.oriented_envelope(fp)
    if rect.geom_type != "Polygon" or rect.area <= 0 or fp.area / rect.area < 0.9:
        return 0.0
    (x0, y0), (x1, y1), (x2, y2) = list(rect.exterior.coords)[:3]
    first, second = (x1 - x0, y1 - y0), (x2 - x1, y2 - y1)
    long = first if math.hypot(*first) >= math.hypot(*second) else second
    angle = -math.degrees(math.atan2(long[1], long[0]))
    fold = 45.0 if abs(math.hypot(*first) - math.hypot(*second)) < 0.05 * math.hypot(*long) else 90.0
    angle = (angle + fold) % (2 * fold) - fold
    return 0.0 if abs(angle) < 1e-6 else angle


def _plan_once(items, region, s: Settings, bounds, turns) -> Layout:
    direction = s.direction
    frame_region = _turned(region, -direction)
    frame_bounds = (_turned(box(*bounds), -direction).bounds if bounds is not None
                    else frame_region.bounds)
    turned = [_turned(fp, turn) for fp, turn in zip(items, turns)]
    job = _Job(frame_region, frame_bounds, s, turned)
    pattern = s.pattern
    if pattern == "linear":
        layout = _linear(job)
    elif pattern == "radial":
        layout = _radial(job)
    elif pattern == "packed":
        layout = _packed(job)
    else:
        layout = _grid(job, staggered=pattern == "staggered")
    back = math.radians(direction)
    cos_b, sin_b = math.cos(back), math.sin(back)
    layout.placements = [
        (u * cos_b - v * sin_b, u * sin_b + v * cos_b, turn + extra + direction)
        for (u, v, extra), turn in zip(layout.placements, turns)
    ]
    return layout


def plan(items, region, settings: Settings, bounds=None) -> Layout:
    """Lay *items* out in *region* following *settings*.

    *bounds* is the extent the pattern spreads across, when that is not the
    region's own -- round a full cylinder the region is widened past the seam
    so items may straddle it, but the pattern still spans one turn.
    """
    if settings.pattern not in PATTERNS:
        raise ValueError(f"Unknown arrangement pattern {settings.pattern!r}")
    items = list(items)
    if not items:
        return Layout([])
    if region.is_empty:
        return Layout([(0.0, 0.0, 0.0)] * len(items),
                      {i: ("outside", None) for i in range(len(items))})
    turns = [settings.rotation] * len(items)
    if settings.auto_rotate:
        turns = [settings.rotation + _square_angle(fp) for fp in items]
    variants = [turns]
    if settings.auto_rotate and settings.pattern not in ("packed", "radial"):
        variants.append([turn + 90.0 for turn in turns])
    best = None
    for variant in variants:
        layout = _plan_once(items, region, settings, bounds, variant)
        if layout.fits:
            return layout
        if best is None or len(layout.problems) < len(best.problems):
            best = layout
    return best


def alternatives(items, region, settings: Settings, bounds=None, limit: int = 3):
    """Changed settings that would fit, as ``(label, patch)``, best first."""
    s = settings
    found = []

    def offer(label, patch):
        if len(found) < limit and plan(items, region, replace(s, **patch), bounds).fits:
            found.append((label, patch))

    if s.spacing == "fixed":
        offer("Use automatic spacing", {"spacing": "auto"})
    if s.gap > 0 and s.pattern != "packed" and len(found) < limit:
        if plan(items, region, replace(s, gap=0.0, gap_y=None if s.gap_y is None else 0.0),
                bounds).fits:
            low, high = 0.0, s.gap
            for _ in range(8):
                middle = (low + high) / 2.0
                patch = {"gap": middle} if s.gap_y is None else {"gap": middle, "gap_y": middle}
                if plan(items, region, replace(s, **patch), bounds).fits:
                    low = middle
                else:
                    high = middle
            low = math.floor(low * 100.0) / 100.0
            patch = {"gap": low} if s.gap_y is None else {"gap": low, "gap_y": low}
            word = "spacing" if s.spacing == "fixed" else "minimum spacing"
            found.append((f"Use {low:g} mm {word}", patch))
    if s.rows or s.columns:
        offer("Choose rows and columns automatically", {"rows": 0, "columns": 0})
    if not s.auto_rotate:
        offer("Allow rotation", {"auto_rotate": True})
    for pattern, label in (("packed", "Try Packed"), ("staggered", "Try Staggered"),
                           ("grid", "Try Grid")):
        if s.pattern != pattern:
            offer(label, {"pattern": pattern})
    if s.pattern == "packed" and not s.auto_rotate:
        offer("Pack with rotation", {"auto_rotate": True})
    return found


__all__ = ["Layout", "PATTERNS", "Settings", "alternatives", "check", "placed", "plan"]
