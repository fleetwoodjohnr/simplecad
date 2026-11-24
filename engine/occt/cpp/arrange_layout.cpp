// SimpleCAD 0.4's arrange_layout.py over GEOS: where footprints go in a region. Each function keeps the name and
// order of work of its Python original, so a pattern lands where 0.4 put it.
#include "arrange_layout.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <numeric>
#include <stdexcept>

namespace simplecad::arrange {
namespace {

using texture::Point;
using texture::Ring;
using texture::pi;

/** Anything closer than the gap by more than this is a collision, so rounding never turns an exact fit red. */
constexpr double slack = 1e-4;

Geometry turned(const Layout &layout, const GEOSGeometry *shape, double angle) {
  return angle != 0.0 ? layout.shift(shape, {0.0, 0.0}, angle) : layout.own(GEOSGeom_clone_r(layout.context, shape));
}

Geometry moved(const Layout &layout, const GEOSGeometry *shape, double u, double v) {
  return layout.shift(shape, {u, v});
}

double distance(const Layout &layout, const GEOSGeometry *a, const GEOSGeometry *b) {
  double value = 0;
  GEOSDistance_r(layout.context, a, b, &value);
  return value;
}

double length(const Layout &layout, const GEOSGeometry *shape) {
  double value = 0;
  GEOSLength_r(layout.context, shape, &value);
  return value;
}

bool covers(const Layout &layout, const GEOSGeometry *outer, const GEOSGeometry *inner) {
  return GEOSCovers_r(layout.context, outer, inner) == 1;
}

Geometry box(const Layout &layout, const Bounds &b) { return layout.rectangle(b[0], b[1], b[2], b[3]); }

Ring exterior(const Layout &layout, const GEOSGeometry *polygon) {
  return layout.coordinates(GEOSGetExteriorRing_r(layout.context, polygon));
}

/** A polygon's outline alone, its holes left out (shapely's `Polygon(shape.exterior)`). */
Geometry filled(const Layout &layout, const GEOSGeometry *polygon) { return layout.polygon(exterior(layout, polygon)); }

/** Parts of a geometry as shapely's `getattr(g, "geoms", [g])` gives them. */
std::vector<const GEOSGeometry *> parts(const Layout &layout, const GEOSGeometry *shape) {
  const int type = GEOSGeomTypeId_r(layout.context, shape);
  if (type != GEOS_MULTIPOLYGON && type != GEOS_MULTILINESTRING && type != GEOS_GEOMETRYCOLLECTION && type != GEOS_MULTIPOINT) {
    return {shape};
  }
  std::vector<const GEOSGeometry *> out;
  for (int i = 0; i < GEOSGetNumGeometries_r(layout.context, shape); ++i) out.push_back(GEOSGetGeometryN_r(layout.context, shape, i));
  return out;
}

struct Job {
  const Layout &layout;
  Settings s;
  const GEOSGeometry *region;
  Bounds bounds;
  std::vector<const GEOSGeometry *> items;
  Geometry inside;
  std::optional<std::array<double, 2>> wrap;
  std::vector<Bounds> extents;

  Job(const Layout &layout, const GEOSGeometry *region, Bounds bounds, const Settings &settings,
      std::vector<const GEOSGeometry *> items)
      : layout(layout), s(settings), region(region), bounds(bounds), items(std::move(items)),
        inside(layout.buffer(region, slack)) {
    if (s.period && *s.period) {
      const double angle = (-s.direction) * (pi / 180);
      wrap = std::array<double, 2>{*s.period * std::cos(angle), *s.period * std::sin(angle)};
    }
    for (const auto *item : this->items) extents.push_back(layout.bounds(item));
  }

  double gap_floor() const { return std::min(s.gap, s.gap_y ? *s.gap_y : s.gap); }

  Plan finish(Plan plan) const {
    plan.problems = check(layout, items, plan.placements, region, gap_floor(), s.contain, wrap, inside.get());
    return plan;
  }
};

std::array<int, 2> anchor(const std::string &align) {
  const auto has = [&](const char *word) { return align.find(word) != std::string::npos; };
  return {has("left") ? -1 : has("right") ? 1 : 0, has("top") ? 1 : has("bottom") ? -1 : 0};
}

double start(double low, double high, double size, int side) {
  if (side < 0) return low;
  if (side > 0) return high - size;
  return (low + high - size) / 2.0;
}

/** The stretch along X where a band of `height` centred on `y` lies in the region. */
std::array<double, 2> span(const Layout &layout, const GEOSGeometry *region, double y, double height, double low,
                           double high) {
  std::optional<std::array<double, 2>> found;
  for (const double level : {y - height / 2.0 + 1e-6, y, y + height / 2.0 - 1e-6}) {
    const auto line = layout.line({{low - 1.0, level}, {high + 1.0, level}});
    const auto piece = layout.intersection(line.get(), region);
    const GEOSGeometry *best = nullptr;
    double best_length = 0;
    for (const auto *part : parts(layout, piece.get())) {
      const double l = length(layout, part);
      if (l > 0 && (!best || l > best_length)) best = part, best_length = l;
    }
    if (!best) return {low, high};
    const auto b = layout.bounds(best);
    found = found ? std::array<double, 2>{std::max((*found)[0], b[0]), std::min((*found)[1], b[2])}
                  : std::array<double, 2>{b[0], b[2]};
  }
  return (*found)[1] > (*found)[0] ? *found : std::array<double, 2>{low, high};
}

Plan linear(const Job &job) {
  const auto &s = job.s;
  const auto &ext = job.extents;
  const int n = static_cast<int>(ext.size());
  std::vector<double> widths;
  double height = 0;
  for (const auto &e : ext) {
    widths.push_back(e[2] - e[0]);
    height = std::max(height, e[3] - e[1]);
  }
  const auto [lo_x, lo_y, hi_x, hi_y] = job.bounds;
  const auto [side_x, side_y] = anchor(s.align);
  const double middle = start(lo_y, hi_y, height, side_y) + height / 2.0;
  auto [x0, x1] = span(job.layout, job.region, middle, height, lo_x, hi_x);
  const double total = std::accumulate(widths.begin(), widths.end(), 0.0);
  double gap = s.gap;
  std::optional<double> first;
  if (!s.fixed) {
    double slots;
    if (job.wrap) {
      slots = n;
      x0 = lo_x;
      x1 = lo_x + *s.period;
    } else {
      slots = s.evenly ? n + 1 : n - 1;
    }
    if (slots > 0 && (x1 - x0 - total) / slots >= s.gap) {
      gap = (x1 - x0 - total) / slots;
      first = x0 + (s.evenly && !job.wrap ? gap : 0.0);
    }
  }
  if (!first) first = start(x0, x1, total + gap * (n - 1), side_x);
  Plan plan;
  double cursor = *first;
  for (int i = 0; i < n; ++i) {
    const auto [bx0, by0, bx1, by1] = ext[i];
    (void)bx1;
    double v;
    if (side_y > 0) v = middle + height / 2.0 - by1;
    else if (side_y < 0) v = middle - height / 2.0 - by0;
    else v = middle - (by0 + by1) / 2.0;
    plan.placements.push_back({cursor - bx0, v, 0.0});
    cursor += widths[i] + gap;
  }
  plan.gap = gap;
  plan.rows = 1;
  plan.columns = n;
  return job.finish(std::move(plan));
}

int ceil_div(int a, int b) { return static_cast<int>(std::ceil(static_cast<double>(a) / b)); }

std::vector<std::array<int, 2>> grid_shapes(int n, int rows, int columns) {
  if (rows && columns) return {{std::max(rows, ceil_div(n, columns)), columns}};
  if (rows) return {{rows, ceil_div(n, rows)}};
  if (columns) return {{ceil_div(n, columns), columns}};
  std::vector<std::array<int, 2>> out;
  for (int c = 1; c <= n; ++c) {
    if (c == 1 || ceil_div(n, c) * (c - 1) < n) out.push_back({ceil_div(n, c), c});
  }
  return out;
}

double sum(const std::vector<double> &values, std::size_t count) {
  return std::accumulate(values.begin(), values.begin() + static_cast<std::ptrdiff_t>(count), 0.0);
}

Plan grid_layout(const Job &job, const Bounds &area, std::array<int, 2> shape, bool staggered, double row_step) {
  const auto &s = job.s;
  const auto &ext = job.extents;
  const int n = static_cast<int>(ext.size());
  const auto [rows, columns] = shape;
  std::vector<std::array<int, 2>> cells;
  for (int i = 0; i < n; ++i) {
    cells.push_back(!s.fill_columns ? std::array<int, 2>{i / columns, i % columns} : std::array<int, 2>{i % rows, i / rows});
  }
  std::vector<double> widths, heights;
  for (const auto &e : ext) {
    widths.push_back(e[2] - e[0]);
    heights.push_back(e[3] - e[1]);
  }
  std::vector<double> col_w(columns, 0.0), row_h(rows, 0.0);
  if (staggered) {
    std::fill(col_w.begin(), col_w.end(), *std::max_element(widths.begin(), widths.end()));
    std::fill(row_h.begin(), row_h.end(), *std::max_element(heights.begin(), heights.end()));
  } else {
    for (int i = 0; i < n; ++i) {
      const auto [row, col] = cells[i];
      col_w[col] = std::max(col_w[col], widths[i]);
      row_h[row] = std::max(row_h[row], heights[i]);
    }
  }
  const auto [lo_x, lo_y, hi_x, hi_y] = area;
  const auto [side_x, side_y] = anchor(s.align);
  const bool shifted = staggered && rows > 1;
  const int edges = s.evenly ? 2 : 0;

  // Columns: clear gaps between cell edges, plus half a pitch for a stagger. Round a full cylinder the last column
  // is next to the first, so a turn holds as many gaps as columns and a stagger adds no width.
  const bool wrapped = job.wrap && std::abs((*job.wrap)[1]) < 1e-9;
  double gap_x = s.gap, fixed_x, slots_x;
  if (wrapped) {
    fixed_x = sum(col_w, col_w.size());
    slots_x = columns;
  } else {
    fixed_x = sum(col_w, col_w.size()) + (shifted ? col_w[0] / 2.0 : 0.0);
    slots_x = columns - 1 + edges + (shifted ? 0.5 : 0.0);
  }
  const bool spread_x = !s.fixed && slots_x > 0 && (hi_x - lo_x - fixed_x) / slots_x >= s.gap;
  if (spread_x) gap_x = (hi_x - lo_x - fixed_x) / slots_x;
  const double width = fixed_x + gap_x * (columns - 1 + (shifted && !wrapped ? 0.5 : 0.0));
  const double x_start = spread_x ? lo_x + (wrapped ? gap_x / 2.0 : edges ? gap_x : 0.0) : start(lo_x, hi_x, width, side_x);

  // Rows, read from the top down.
  double gap_y = s.gap_y ? *s.gap_y : s.gap;
  const int slots_y = rows - 1 + edges;
  const double rows_h = sum(row_h, row_h.size());
  const bool spread_y = !s.fixed && slots_y > 0 && (hi_y - lo_y - rows_h) / slots_y >= gap_y;
  if (spread_y) gap_y = (hi_y - lo_y - rows_h) / slots_y;
  std::vector<double> pitches;
  for (int r = 0; r < rows; ++r) pitches.push_back(row_h[r] + gap_y);
  if (staggered && !spread_y) {
    for (auto &p : pitches) p *= row_step;
  }
  const double height = sum(pitches, pitches.size() - 1) + row_h.back();
  const double y_top = spread_y ? hi_y - (edges ? gap_y : 0.0) : start(lo_y, hi_y, height, side_y) + height;

  std::vector<double> lefts, tops;
  for (int c = 0; c < columns; ++c) lefts.push_back(x_start + sum(col_w, c) + gap_x * c);
  for (int r = 0; r < rows; ++r) tops.push_back(y_top - sum(pitches, r));
  Plan plan;
  for (int i = 0; i < n; ++i) {
    const auto [row, col] = cells[i];
    const auto [bx0, by0, bx1, by1] = ext[i];
    const double left = lefts[col] + (shifted && row % 2 ? (col_w[0] + gap_x) / 2.0 : 0.0);
    plan.placements.push_back({left + col_w[col] / 2.0 - (bx0 + bx1) / 2.0, tops[row] - row_h[row] / 2.0 - (by0 + by1) / 2.0, 0.0});
  }
  std::vector<double> gaps;
  if (columns > 1) gaps.push_back(gap_x);
  if (rows > 1) gaps.push_back(gap_y);
  plan.gap = gaps.empty() ? 0.0 : *std::min_element(gaps.begin(), gaps.end());
  plan.rows = rows;
  plan.columns = columns;
  return job.finish(std::move(plan));
}

Bounds scaled(const Bounds &b, double scale) {
  const double cx = (b[0] + b[2]) / 2.0, cy = (b[1] + b[3]) / 2.0;
  const double hx = (b[2] - b[0]) * scale / 2.0, hy = (b[3] - b[1]) * scale / 2.0;
  return {cx - hx, cy - hy, cx + hx, cy + hy};
}

/** The biggest axis-aligned rectangle of whole raster cells inside the region. */
std::optional<Bounds> largest_rectangle(const Job &job, int cells = 64) {
  const auto [lo_x, lo_y, hi_x, hi_y] = job.bounds;
  const double step = std::max(hi_x - lo_x, hi_y - lo_y) / cells;
  if (step <= 0) return std::nullopt;
  const int nx = std::max(1, static_cast<int>((hi_x - lo_x) / step));
  const int ny = std::max(1, static_cast<int>((hi_y - lo_y) / step));
  std::vector<int> heights(nx, 0);
  std::array<int, 5> best{0, 0, 0, 0, 0};  // area, left, right (exclusive), top row, height
  for (int row = 0; row < ny; ++row) {
    for (int col = 0; col < nx; ++col) {
      const double x = lo_x + col * step, y = lo_y + row * step;
      const auto cell = job.layout.rectangle(x, y, x + step, y + step);
      heights[col] = covers(job.layout, job.inside.get(), cell.get()) ? heights[col] + 1 : 0;
    }
    std::vector<std::array<int, 2>> stack;
    for (int col = 0; col <= nx; ++col) {
      const int here = col < nx ? heights[col] : 0;
      int first = col;
      while (!stack.empty() && stack.back()[1] >= here) {
        const auto [from, tall] = stack.back();
        stack.pop_back();
        first = from;
        if (tall * (col - from) > best[0]) best = {tall * (col - from), from, col, row, tall};
      }
      stack.push_back({first, here});
    }
  }
  const auto [area, left, right, top, tall] = best;
  if (!area) return std::nullopt;
  return Bounds{lo_x + left * step, lo_y + (top - tall + 1) * step, lo_x + right * step, lo_y + (top + 1) * step};
}

double area_of(const Layout &layout, const GEOSGeometry *shape) { return layout.area(shape); }

Plan grid(const Job &job, bool staggered) {
  const auto &s = job.s;
  const auto &ext = job.extents;
  const int n = static_cast<int>(ext.size());
  auto shapes = grid_shapes(n, s.rows, s.columns);
  double widest = 0, tallest = 0;
  for (const auto &e : ext) {
    widest = std::max(widest, e[2] - e[0]);
    tallest = std::max(tallest, e[3] - e[1]);
  }
  // A cheap estimate from the widest and tallest item, only to rank the shapes.
  const auto slack_of = [&](const std::array<int, 2> &shape, const Bounds &area) {
    const auto [rows, columns] = shape;
    const double need_x = widest * (columns + (staggered && rows > 1 ? 0.5 : 0.0)) + s.gap * (columns - 1);
    const double need_y = tallest * rows + (s.gap_y ? *s.gap_y : s.gap) * (rows - 1);
    return std::min(area[2] - area[0] - need_x, area[3] - area[1] - need_y);
  };
  // A rectangle takes the grid as it is; anything else also tries the largest clear rectangle inside it and ever
  // smaller boxes about its middle. Automatic spacing keeps whichever fitting layout leaves the widest gaps.
  std::vector<std::optional<Bounds>> areas{job.bounds};
  if (!(area_of(job.layout, job.region) >= area_of(job.layout, box(job.layout, job.bounds).get()) * 0.999)) {
    areas.push_back(largest_rectangle(job));
    for (int k = 1; k <= 12; ++k) areas.push_back(scaled(job.bounds, 1.0 - 0.05 * k));
  }
  const std::vector<double> steps = staggered && s.fixed ? std::vector<double>{std::sqrt(3.0) / 2.0, 0.9, 1.0}
                                                         : std::vector<double>{1.0};
  std::optional<Plan> best, first;
  for (const auto &area : areas) {
    if (!area) continue;
    auto ranked = shapes;
    std::stable_sort(ranked.begin(), ranked.end(), [&](const auto &a, const auto &b) {
      return -slack_of(a, *area) < -slack_of(b, *area);
    });
    if (ranked.size() > 4) ranked.resize(4);
    for (const auto &shape : ranked) {
      for (const double step : steps) {
        auto layout = grid_layout(job, *area, shape, staggered, step);
        if (layout.fits()) {
          if (s.fixed) return layout;
          if (!best || layout.gap > best->gap + 1e-9) best = layout;
          break;
        }
        if (!first) first = layout;
      }
    }
  }
  return best ? *best : *first;
}

Geometry outline(const Layout &layout, const GEOSGeometry *region) {
  const GEOSGeometry *largest = nullptr;
  double largest_area = -1;
  for (const auto *part : parts(layout, region)) {
    const double a = layout.area(part);
    if (a > largest_area) largest = part, largest_area = a;
  }
  return filled(layout, largest);
}

double reach(const Layout &layout, const GEOSGeometry *footprint) {
  double far = 0;
  for (const auto &[x, y] : exterior(layout, footprint)) far = std::max(far, std::hypot(x, y));
  return far;
}

double ring_gap(const Layout &layout, const Plan &plan, const std::vector<const GEOSGeometry *> &items) {
  std::vector<Geometry> shapes;
  for (std::size_t i = 0; i < items.size(); ++i) shapes.push_back(placed(layout, items[i], plan.placements[i]));
  double gap = 1e300;
  for (std::size_t i = 0; i < shapes.size(); ++i) {
    gap = std::min(gap, distance(layout, shapes[i].get(), shapes[(i + 1) % shapes.size()].get()));
  }
  return gap;
}

Plan radial(const Job &job) {
  const auto &s = job.s;
  const auto &layout = job.layout;
  const int n = static_cast<int>(job.items.size());
  const auto ring = outline(layout, job.region);
  const auto centre = layout.own(GEOSGetCentroid_r(layout.context, ring.get()));
  double cx = 0, cy = 0;
  GEOSGeomGetX_r(layout.context, centre.get(), &cx);
  GEOSGeomGetY_r(layout.context, centre.get(), &cy);
  std::vector<double> reaches;
  for (const auto *item : job.items) reaches.push_back(reach(layout, item));
  const double first_angle = s.start_angle * (pi / 180);
  const auto facing = [&](double theta) { return s.face_center ? theta * (180 / pi) - 90.0 : 0.0; };
  const auto tangential = [&](int index) {
    if (!s.face_center) return 2.0 * reaches[index];
    return job.extents[index][2] - job.extents[index][0];
  };
  const auto at = [&](double radius, const std::vector<double> &angles) {
    Plan plan;
    for (const double a : angles) plan.placements.push_back({cx + radius * std::cos(a), cy + radius * std::sin(a), facing(a)});
    plan.gap = s.gap;
    plan.radius = radius;
    return job.finish(std::move(plan));
  };
  if (n == 1) return at(s.radius, {first_angle});
  const double max_reach = *std::max_element(reaches.begin(), reaches.end());
  if (!s.fixed) {
    std::vector<double> angles;
    for (int i = 0; i < n; ++i) angles.push_back(first_angle + 2.0 * pi * i / n);
    if (s.radius > 0) return at(s.radius, angles);
    const double smallest = (2.0 * max_reach + s.gap) / (2.0 * std::sin(pi / n));
    const auto point = layout.own(GEOSGeom_createPointFromXY_r(layout.context, cx, cy));
    const auto boundary = layout.own(GEOSGeom_clone_r(layout.context, GEOSGetExteriorRing_r(layout.context, ring.get())));
    const double largest = std::max(distance(layout, boundary.get(), point.get()) - max_reach, smallest);
    std::optional<Plan> first;
    for (int k = 0; k < 17; ++k) {
      auto plan = at(largest - (largest - smallest) * k / 16.0, angles);
      if (plan.fits()) {
        plan.gap = ring_gap(layout, plan, job.items);
        return plan;
      }
      if (!first) first = plan;
    }
    return *first;
  }

  // Fixed: the clear gap between neighbours, measured where they come closest.
  std::vector<double> widths;
  for (int i = 0; i < n; ++i) widths.push_back(tangential(i));
  const auto clear = [](const Plan &plan) {
    return std::none_of(plan.problems.begin(), plan.problems.end(), [](const auto &p) { return p.second.kind != "outside"; });
  };
  if (!s.radius) {
    // A closed ring: angles shared out by width, radius as small as the gap allows.
    std::vector<double> shares;
    for (int i = 0; i < n; ++i) shares.push_back((widths[i] + widths[(i + 1) % n]) / 2.0 + s.gap);
    const double total = std::accumulate(shares.begin(), shares.end(), 0.0);
    std::vector<double> angles{first_angle};
    for (int i = 0; i + 1 < n; ++i) angles.push_back(angles.back() + 2.0 * pi * shares[i] / total);
    double low = 0.0, high = total / (2.0 * pi);
    for (int i = 0; i < 40; ++i) {
      if (clear(at(high, angles))) break;
      low = high;
      high = high * 1.5;
    }
    for (int i = 0; i < 24; ++i) {
      const double middle = (low + high) / 2.0;
      if (clear(at(middle, angles))) high = middle;
      else low = middle;
    }
    return at(high, angles);
  }

  // A given radius: an arc centred on the start angle, opened until clear.
  const auto arc = [&](double widen) {
    std::vector<double> steps;
    for (int i = 0; i + 1 < n; ++i) {
      steps.push_back(2.0 * std::asin(std::min(1.0, widen * ((widths[i] + widths[i + 1]) / 2.0 + s.gap) / (2.0 * s.radius))));
    }
    const double sweep = std::accumulate(steps.begin(), steps.end(), 0.0);
    std::vector<double> angles{first_angle - sweep / 2.0};
    for (const double step : steps) angles.push_back(angles.back() + step);
    return std::pair{at(s.radius, angles), sweep};
  };
  auto plan = arc(1.0).first;
  if (clear(plan)) return plan;
  double low = 1.0, high = 1.25;
  while (true) {
    auto [next, sweep] = arc(high);
    plan = std::move(next);
    if (clear(plan) || sweep >= 2.0 * pi || high > 64) break;
    low = high;
    high = high * 1.25;
  }
  if (!clear(plan)) return plan;
  for (int i = 0; i < 20; ++i) {
    const double middle = (low + high) / 2.0;
    if (clear(arc(middle).first)) high = middle;
    else low = middle;
  }
  return arc(high).first;
}


/** Bottom-left fill, biggest first. */
// ponytail: greedy bottom-left with 90-degree turns, as 0.4; no-fit-polygon nesting if density matters.
Plan packed(const Job &job) {
  const auto &s = job.s;
  const auto &layout = job.layout;
  const double gap = s.gap;
  const auto [lo_x, lo_y, hi_x, hi_y] = job.bounds;
  const std::vector<double> turns = s.auto_rotate ? std::vector<double>{0.0, 90.0, 180.0, 270.0} : std::vector<double>{0.0};
  const int n = static_cast<int>(job.items.size());
  std::vector<int> order(n);
  std::iota(order.begin(), order.end(), 0);
  std::vector<double> areas;
  for (const auto *item : job.items) areas.push_back(layout.area(item));
  std::stable_sort(order.begin(), order.end(), [&](int a, int b) { return -areas[a] < -areas[b]; });
  std::vector<std::array<double, 3>> placements(n);
  std::vector<Geometry> shapes;
  double overflow_x = hi_x + gap;
  struct Hit { double key_v, key_u, u, v; };
  using Spots = std::vector<std::array<double, 2>>;

  // The lowest, then leftmost, spot where `fp` fits.
  const auto first_free = [&](const GEOSGeometry *fp, Spots spots) -> std::optional<Hit> {
    const auto b = layout.bounds(fp);
    for (auto &spot : spots) {
      for (auto &c : spot) c = std::nearbyint(c * 1e9) / 1e9;
    }
    std::sort(spots.begin(), spots.end());
    spots.erase(std::unique(spots.begin(), spots.end()), spots.end());
    if (s.contain) {
      spots.erase(std::remove_if(spots.begin(), spots.end(), [&](const auto &p) {
        return !(p[0] + b[0] >= lo_x - slack && p[1] + b[1] >= lo_y - slack && p[0] + b[2] <= hi_x + slack &&
                 p[1] + b[3] <= hi_y + slack);
      }), spots.end());
    }
    if (spots.empty()) return std::nullopt;
    std::stable_sort(spots.begin(), spots.end(), [](const auto &a, const auto &c) {
      return a[1] != c[1] ? a[1] < c[1] : a[0] < c[0];
    });
    for (const auto &[u, v] : spots) {
      const auto candidate = moved(layout, fp, u, v);
      bool ok;
      if (s.contain) {
        ok = covers(layout, job.inside.get(), candidate.get());
      } else {
        const auto centre = layout.own(GEOSGeom_createPointFromXY_r(layout.context, u, v));
        ok = covers(layout, job.inside.get(), centre.get());
      }
      for (std::size_t k = 0; ok && k < shapes.size(); ++k) {
        if (gap > slack) {
          const double apart = distance(layout, candidate.get(), shapes[k].get());
          if (apart <= gap && apart < gap - slack) ok = false;
        } else if (GEOSIntersects_r(layout.context, candidate.get(), shapes[k].get()) == 1) {
          const auto shared = layout.intersection(candidate.get(), shapes[k].get());
          if (layout.area(shared.get()) > 1e-6) ok = false;
        }
      }
      if (ok) return Hit{v + b[1], u + b[0], u, v};
    }
    return std::nullopt;
  };

  for (const int index : order) {
    std::vector<Bounds> near;
    for (const auto &shape : shapes) near.push_back(layout.bounds(shape.get()));
    struct Option { double turn; Geometry fp; Spots spots; };
    std::vector<Option> options;
    for (const double turn : turns) {
      // Packing fills an outline's holes; nesting inside a tube is not attempted.
      const auto spun = turned(layout, job.items[index], turn);
      auto fp = filled(layout, spun.get());
      const auto b = layout.bounds(fp.get());
      Spots spots{{lo_x - b[0], lo_y - b[1]}};
      for (const auto &o : near) spots.push_back({o[2] + gap - b[0], o[1] - b[1]});
      for (const auto &o : near) spots.push_back({o[0] - b[0], o[3] + gap - b[1]});
      for (const auto &o : near) spots.push_back({lo_x - b[0], o[3] + gap - b[1]});
      for (const auto &o : near) spots.push_back({o[2] + gap - b[0], lo_y - b[1]});
      options.push_back({turn, std::move(fp), std::move(spots)});
    }
    struct Best { std::optional<std::pair<double, double>> key; double u, v, turn; const GEOSGeometry *fp; };
    std::optional<Best> best;
    for (int attempt = 0; attempt < 2; ++attempt) {
      for (const auto &option : options) {
        const auto hit = first_free(option.fp.get(), option.spots);
        if (hit && (!best || std::pair{hit->key_v, hit->key_u} < *best->key)) {
          best = Best{std::pair{hit->key_v, hit->key_u}, hit->u, hit->v, option.turn, option.fp.get()};
        }
      }
      if (best) break;
      // Nothing at the corners: scan the region on a coarse raster.
      double smallest = 1e300;
      for (const auto &option : options) {
        const auto b = layout.bounds(option.fp.get());
        smallest = std::min(smallest, std::min(b[2] - b[0], b[3] - b[1]));
      }
      const double step = std::max({gap, smallest / 3.0, std::hypot(hi_x - lo_x, hi_y - lo_y) / 90.0});
      Spots raster;
      // numpy.arange(lo, hi + step, step), rows of y then x as meshgrid ravels them.
      const int count_x = static_cast<int>(std::ceil((hi_x + step - lo_x) / step));
      const int count_y = static_cast<int>(std::ceil((hi_y + step - lo_y) / step));
      for (int j = 0; j < count_y; ++j) {
        for (int i = 0; i < count_x; ++i) raster.push_back({lo_x + i * step, lo_y + j * step});
      }
      for (auto &option : options) {
        const auto b = layout.bounds(option.fp.get());
        option.spots = raster;
        for (auto &p : option.spots) p = {p[0] - b[0], p[1] - b[1]};
      }
    }
    if (!best) {
      // No room: park it beside the region, where the check marks it.
      const auto b = layout.bounds(job.items[index]);
      best = Best{std::nullopt, overflow_x - b[0], lo_y - b[1], 0.0, job.items[index]};
      overflow_x += b[2] - b[0] + gap;
    }
    placements[index] = {best->u, best->v, best->turn};
    shapes.push_back(moved(layout, best->fp, best->u, best->v));
  }

  Plan plan;
  plan.placements = placements;
  plan.gap = gap;
  plan = job.finish(std::move(plan));
  if (plan.fits() && !shapes.empty()) {
    // Slide the cluster to the chosen alignment when it still fits there.
    Bounds total = layout.bounds(shapes[0].get());
    for (const auto &shape : shapes) {
      const auto b = layout.bounds(shape.get());
      total = {std::min(total[0], b[0]), std::min(total[1], b[1]), std::max(total[2], b[2]), std::max(total[3], b[3])};
    }
    const auto [side_x, side_y] = anchor(s.align);
    const double dx = start(lo_x, hi_x, total[2] - total[0], side_x) - total[0];
    const double dy = start(lo_y, hi_y, total[3] - total[1], side_y) - total[1];
    Plan slid;
    for (const auto &[u, v, a] : placements) slid.placements.push_back({u + dx, v + dy, a});
    slid.gap = gap;
    slid = job.finish(std::move(slid));
    if (slid.fits()) return slid;
  }
  return plan;
}

/** The turn that lays an item's long side along U (0 for round items). */
double square_angle(const Layout &layout, const GEOSGeometry *fp) {
  const auto rect = layout.own(GEOSMinimumRotatedRectangle_r(layout.context, fp));
  if (GEOSGeomTypeId_r(layout.context, rect.get()) != GEOS_POLYGON) return 0.0;
  const double rect_area = layout.area(rect.get());
  if (rect_area <= 0 || layout.area(fp) / rect_area < 0.9) return 0.0;
  const auto corners = exterior(layout, rect.get());
  const std::array<double, 2> first{corners[1][0] - corners[0][0], corners[1][1] - corners[0][1]};
  const std::array<double, 2> second{corners[2][0] - corners[1][0], corners[2][1] - corners[1][1]};
  const auto &long_side = std::hypot(first[0], first[1]) >= std::hypot(second[0], second[1]) ? first : second;
  double angle = -std::atan2(long_side[1], long_side[0]) * (180 / pi);
  const double fold = std::abs(std::hypot(first[0], first[1]) - std::hypot(second[0], second[1])) <
                              0.05 * std::hypot(long_side[0], long_side[1]) ? 45.0 : 90.0;
  // Python's % takes the sign of the divisor.
  const double wrapped = std::fmod(angle + fold, 2 * fold);
  angle = (wrapped < 0 ? wrapped + 2 * fold : wrapped) - fold;
  return std::abs(angle) < 1e-6 ? 0.0 : angle;
}

Plan plan_once(const Layout &layout, const std::vector<const GEOSGeometry *> &items, const GEOSGeometry *region,
               const Settings &s, std::optional<Bounds> bounds, const std::vector<double> &turns) {
  const double direction = s.direction;
  const auto frame_region = turned(layout, region, -direction);
  Bounds frame_bounds;
  if (bounds) {
    const auto rect = box(layout, *bounds);
    frame_bounds = layout.bounds(turned(layout, rect.get(), -direction).get());
  } else {
    frame_bounds = layout.bounds(frame_region.get());
  }
  std::vector<Geometry> spun;
  std::vector<const GEOSGeometry *> views;
  for (std::size_t i = 0; i < items.size(); ++i) {
    spun.push_back(turned(layout, items[i], turns[i]));
    views.push_back(spun.back().get());
  }
  const Job job(layout, frame_region.get(), frame_bounds, s, views);
  Plan plan;
  if (s.pattern == "linear") plan = linear(job);
  else if (s.pattern == "radial") plan = radial(job);
  else if (s.pattern == "packed") plan = packed(job);
  else plan = grid(job, s.pattern == "staggered");
  const double back = direction * (pi / 180);
  const double c = std::cos(back), sn = std::sin(back);
  for (std::size_t i = 0; i < plan.placements.size(); ++i) {
    const auto [u, v, extra] = plan.placements[i];
    plan.placements[i] = {u * c - v * sn, u * sn + v * c, turns[i] + extra + direction};
  }
  return plan;
}

}  // namespace

Geometry placed(const Layout &layout, const GEOSGeometry *footprint, const std::array<double, 3> &placement) {
  const auto turn = turned(layout, footprint, placement[2]);
  return moved(layout, turn.get(), placement[0], placement[1]);
}

std::map<int, Problem> check(const Layout &layout, const std::vector<const GEOSGeometry *> &items,
                             const std::vector<std::array<double, 3>> &placements, const GEOSGeometry *region,
                             double gap, bool contain, std::optional<std::array<double, 2>> wrap,
                             const GEOSGeometry *inside) {
  std::vector<Geometry> shapes;
  for (std::size_t i = 0; i < items.size(); ++i) shapes.push_back(placed(layout, items[i], placements[i]));
  Geometry own_inside;
  if (!inside) {
    own_inside = layout.buffer(region, slack);
    inside = own_inside.get();
  }
  std::map<int, Problem> problems;
  for (std::size_t i = 0; i < shapes.size(); ++i) {
    bool in;
    if (contain) {
      in = covers(layout, inside, shapes[i].get());
    } else {
      const auto centre = layout.own(GEOSGeom_createPointFromXY_r(layout.context, placements[i][0], placements[i][1]));
      in = covers(layout, inside, centre.get());
    }
    if (!in) problems[static_cast<int>(i)] = {"outside", -1};
  }
  std::vector<const GEOSGeometry *> others;
  std::vector<int> owner;
  std::vector<Geometry> copies;
  for (std::size_t i = 0; i < shapes.size(); ++i) {
    others.push_back(shapes[i].get());
    owner.push_back(static_cast<int>(i));
  }
  if (wrap) {
    // Round a full cylinder, an item just past the seam meets the first one.
    for (const double sign : {-1.0, 1.0}) {
      for (std::size_t i = 0; i < shapes.size(); ++i) {
        copies.push_back(moved(layout, shapes[i].get(), sign * (*wrap)[0], sign * (*wrap)[1]));
        others.push_back(copies.back().get());
        owner.push_back(static_cast<int>(i));
      }
    }
  }
  if (shapes.size() < 2) return problems;
  const double reach_gap = std::max(gap, 0.0);
  for (std::size_t i = 0; i < shapes.size(); ++i) {
    for (std::size_t k = 0; k < others.size(); ++k) {
      const int j = owner[k];
      if (j == static_cast<int>(i)) continue;
      const double apart = distance(layout, shapes[i].get(), others[k]);
      if (apart > reach_gap) continue;
      const auto shared = layout.intersection(shapes[i].get(), others[k]);
      const bool overlap = layout.area(shared.get()) > 1e-6;
      if (!overlap && (gap <= slack || apart >= gap - slack)) continue;
      if (overlap || !problems.count(static_cast<int>(i))) problems[static_cast<int>(i)] = {overlap ? "overlap" : "close", j};
    }
  }
  return problems;
}

Plan plan(const Layout &layout, const std::vector<const GEOSGeometry *> &items, const GEOSGeometry *region,
          const Settings &settings, std::optional<Bounds> bounds) {
  static const std::vector<std::string> patterns{"grid", "linear", "radial", "staggered", "packed", "custom"};
  if (std::find(patterns.begin(), patterns.end(), settings.pattern) == patterns.end()) {
    throw std::runtime_error("That arrangement pattern is not available.");
  }
  if (items.empty()) return {};
  if (layout.empty(region)) {
    Plan plan;
    for (std::size_t i = 0; i < items.size(); ++i) {
      plan.placements.push_back({0.0, 0.0, 0.0});
      plan.problems[static_cast<int>(i)] = {"outside", -1};
    }
    return plan;
  }
  std::vector<double> turns(items.size(), settings.rotation);
  if (settings.auto_rotate) {
    for (std::size_t i = 0; i < items.size(); ++i) turns[i] = settings.rotation + square_angle(layout, items[i]);
  }
  std::vector<std::vector<double>> variants{turns};
  if (settings.auto_rotate && settings.pattern != "packed" && settings.pattern != "radial") {
    auto quarter = turns;
    for (auto &turn : quarter) turn += 90.0;
    variants.push_back(quarter);
  }
  std::optional<Plan> best;
  for (const auto &variant : variants) {
    auto layout_plan = plan_once(layout, items, region, settings, bounds, variant);
    if (layout_plan.fits()) return layout_plan;
    if (!best || layout_plan.problems.size() < best->problems.size()) best = layout_plan;
  }
  return *best;
}

std::vector<Offer> alternatives(const Layout &layout, const std::vector<const GEOSGeometry *> &items,
                                const GEOSGeometry *region, const Settings &s, std::optional<Bounds> bounds,
                                int limit) {
  std::vector<Offer> found;
  const auto fits = [&](const Settings &next) { return plan(layout, items, region, next, bounds).fits(); };
  const auto offer = [&](const std::string &label, Settings next, std::vector<std::pair<std::string, std::string>> patch) {
    if (static_cast<int>(found.size()) < limit && fits(next)) found.push_back({label, next, std::move(patch)});
  };
  if (s.fixed) {
    auto next = s;
    next.fixed = false;
    offer("Use automatic spacing", next, {{"spacing", "auto"}});
  }
  if (s.gap > 0 && s.pattern != "packed" && static_cast<int>(found.size()) < limit) {
    auto bare = s;
    bare.gap = 0.0;
    if (s.gap_y) bare.gap_y = 0.0;
    if (fits(bare)) {
      double low = 0.0, high = s.gap;
      for (int i = 0; i < 8; ++i) {
        const double middle = (low + high) / 2.0;
        auto next = s;
        next.gap = middle;
        if (s.gap_y) next.gap_y = middle;
        if (fits(next)) low = middle;
        else high = middle;
      }
      low = std::floor(low * 100.0) / 100.0;
      auto next = s;
      next.gap = low;
      if (s.gap_y) next.gap_y = low;
      char text[64], exact[64];
      std::snprintf(text, sizeof text, "%g", low);
      std::snprintf(exact, sizeof exact, "%.17g", low);
      std::vector<std::pair<std::string, std::string>> patch{{"gap", exact}};
      if (s.gap_y) patch.push_back({"gap_y", exact});
      found.push_back({std::string("Use ") + text + " mm " + (s.fixed ? "spacing" : "minimum spacing"), next, patch});
    }
  }
  if (s.rows || s.columns) {
    auto next = s;
    next.rows = next.columns = 0;
    offer("Choose rows and columns automatically", next, {{"rows", "0"}, {"columns", "0"}});
  }
  if (!s.auto_rotate) {
    auto next = s;
    next.auto_rotate = true;
    offer("Allow rotation", next, {{"auto_rotate", "true"}});
  }
  for (const auto &[pattern, label] : std::vector<std::pair<std::string, std::string>>{
           {"packed", "Try Packed"}, {"staggered", "Try Staggered"}, {"grid", "Try Grid"}}) {
    if (s.pattern != pattern) {
      auto next = s;
      next.pattern = pattern;
      offer(label, next, {{"pattern", pattern}});
    }
  }
  if (s.pattern == "packed" && !s.auto_rotate) {
    auto next = s;
    next.auto_rotate = true;
    offer("Pack with rotation", next, {{"auto_rotate", "true"}});
  }
  return found;
}

}  // namespace simplecad::arrange
