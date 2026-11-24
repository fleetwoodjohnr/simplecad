// Where several footprints go inside a region: SimpleCAD 0.4's arrange_layout.py over GEOS.
//
// Everything is in face coordinates (millimetres along the target face's U and V). A footprint is an item's outline
// seen from above the face, drawn around its reference point at the origin; a placement (u, v, angle) turns it by
// `angle` degrees about that point and moves the point to (u, v).
#pragma once

#include "texture_layout.hpp"

#include <map>
#include <optional>
#include <string>
#include <vector>

namespace simplecad::arrange {

using texture::Bounds;
using texture::Geometry;
using texture::Layout;

struct Settings {
  std::string pattern = "grid";
  bool fixed = false;            // "fixed" spacing holds `gap` exactly; otherwise it spreads out
  double gap = 5.0;              // fixed: the clear gap; auto: the least allowed
  std::optional<double> gap_y;   // custom: a separate gap between rows
  double direction = 0.0;        // degrees from +U; the whole pattern turns with it
  std::string align = "center";
  int rows = 0, columns = 0;     // 0 = choose
  bool fill_columns = false;
  bool evenly = false;           // gaps "evenly" including the edges, else "between" items
  double rotation = 0.0;
  bool auto_rotate = false;
  double radius = 0.0;           // radial: 0 = choose
  double start_angle = 0.0;
  bool face_center = false;
  bool contain = true;           // the whole footprint inside, or only its centre
  std::optional<double> period;  // circumference of a full cylinder
};

struct Problem {
  std::string kind;  // "outside", "overlap" or "close"
  int other = -1;
};

struct Plan {
  std::vector<std::array<double, 3>> placements;
  std::map<int, Problem> problems;
  double gap = 0, radius = 0;
  int rows = 0, columns = 0;
  bool fits() const { return problems.empty(); }
};

/** Lay `items` out in `region` following `settings`; `bounds` is the extent a pattern spreads across when that is
 * not the region's own (round a full cylinder, one turn). */
Plan plan(const Layout &layout, const std::vector<const GEOSGeometry *> &items, const GEOSGeometry *region,
          const Settings &settings, std::optional<Bounds> bounds = std::nullopt);

/** Which placed items leave the region or come closer than `gap` to another. */
std::map<int, Problem> check(const Layout &layout, const std::vector<const GEOSGeometry *> &items,
                             const std::vector<std::array<double, 3>> &placements, const GEOSGeometry *region,
                             double gap, bool contain, std::optional<std::array<double, 2>> wrap,
                             const GEOSGeometry *inside = nullptr);

/** A suggestion: what to say, and the settings that would fit. */
struct Offer {
  std::string label;
  Settings settings;
  std::vector<std::pair<std::string, std::string>> patch;  // what changes, for the window
};
std::vector<Offer> alternatives(const Layout &layout, const std::vector<const GEOSGeometry *> &items,
                                const GEOSGeometry *region, const Settings &settings,
                                std::optional<Bounds> bounds, int limit = 3);

/** A footprint placed: turned by its angle about the origin, then moved. */
Geometry placed(const Layout &layout, const GEOSGeometry *footprint, const std::array<double, 3> &placement);

}  // namespace simplecad::arrange
