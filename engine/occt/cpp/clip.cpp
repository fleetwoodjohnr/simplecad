#include "shim.hpp"
#include "texture_layout.hpp"

#include <algorithm>
#include <cstdint>
#include <optional>
#include <cstdio>
#include <sstream>
#include <string>

#include <BRepAdaptor_Surface.hxx>
#include <BRepBndLib.hxx>
#include <Bnd_Box.hxx>
#include <BRepAlgoAPI_Common.hxx>
#include <BRepAlgoAPI_Cut.hxx>
#include <BRepAlgoAPI_Fuse.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_MakePolygon.hxx>
#include <BRepBuilderAPI_Transform.hxx>
#include <BRepOffsetAPI_ThruSections.hxx>
#include <BRepPrimAPI_MakeBox.hxx>
#include <BRepPrimAPI_MakeCylinder.hxx>
#include <BRepPrimAPI_MakePrism.hxx>
#include <TopoDS.hxx>
#include <gp_Pln.hxx>

#include "occt/src/lib.rs.h"

namespace simplecad {
namespace {

using namespace texture;
constexpr int max_connectors = 64;
enum Style : std::uint8_t { printed_connector = 0, snap_tab = 1, tab_slot = 2 };

struct Dimensions {
  double shank;
  double mouth_width;
  double mouth_thickness;
  double maximum;
  double reach;
  double depth;
  double wall_width;
  double wall_thickness;
};

Dimensions dimensions(const ClipSpec &s) {
  const double lead = 2 * s.lead_in + 0.8;
  if (s.style == tab_slot) {
    const double mouth = s.key_width + s.clearance, across = s.key_thickness + s.clearance;
    const double footprint_across = s.key_thickness + 2 * s.bump + s.clearance;
    const double depth = s.key_length + s.depth_clearance;
    return {0, mouth, across, mouth, s.key_length, depth,
            std::max(mouth + 2 * s.min_wall, mouth + lead),
            std::max(footprint_across + 2 * s.min_wall, across + lead)};
  }
  const double shank = s.slot + 2 * s.arm_width;
  const double maximum = shank + 2 * s.barb + s.clearance;
  const double mouth = shank + s.clearance;
  const double across = s.thickness + s.clearance;
  const double reach = s.engagement + s.bridge / 2;
  return {shank, mouth, across, maximum, reach, reach + s.depth_clearance,
          std::max(maximum + 2 * s.min_wall, mouth + lead),
          std::max(across + 2 * s.min_wall, across + lead)};
}

template <class Operation>
TopoDS_Shape boolean(const TopoDS_Shape &body, const TopoDS_Shape &tool) {
  NCollection_List<TopoDS_Shape> bodies, tools;
  bodies.Append(body);
  tools.Append(tool);
  Operation operation;
  operation.SetArguments(bodies);
  operation.SetTools(tools);
  operation.SetNonDestructive(true);
  operation.SetRunParallel(true);
  operation.Build();
  if (!operation.IsDone() || operation.Shape().IsNull()) {
    throw std::runtime_error("The clip joint boolean could not finish.");
  }
  return operation.Shape();
}

TopoDS_Shape box(double width, double thickness, double start, double length) {
  return BRepPrimAPI_MakeBox(gp_Pnt(-width / 2, -thickness / 2, start), width, thickness, length).Shape();
}

TopoDS_Shape moved(const TopoDS_Shape &shape, const gp_Trsf &placement) {
  return BRepBuilderAPI_Transform(shape, placement, true).Shape();
}

TopoDS_Wire rectangle(double half_x, double half_y, double z) {
  BRepBuilderAPI_MakePolygon polygon;
  for (const auto &[x, y] : Ring{{-half_x, -half_y}, {half_x, -half_y},
                                 {half_x, half_y}, {-half_x, half_y}}) {
    polygon.Add(gp_Pnt(x, y, z));
  }
  polygon.Close();
  return polygon.Wire();
}

/** A 45° frustum widening the pocket mouth by the lead-in on every side. */
TopoDS_Shape lead_in(const ClipSpec &s, const Dimensions &d) {
  BRepOffsetAPI_ThruSections loft(true, true);
  loft.AddWire(rectangle(d.mouth_width / 2 + s.lead_in + 0.02, d.mouth_thickness / 2 + s.lead_in + 0.02, -0.02));
  loft.AddWire(rectangle(d.mouth_width / 2, d.mouth_thickness / 2, s.lead_in));
  loft.Build();
  return loft.Shape();
}

/** Two half-round detents across the key's broad faces, near its tip; grown for the grooves they snap into. */
std::vector<TopoDS_Shape> key_bumps(const ClipSpec &s, double radius_growth, double length_growth) {
  if (s.bump <= 0) return {};
  const double radius = std::max(s.bump * 2, 0.6) + radius_growth;
  const double z = s.key_length * 0.62;
  const double span = s.key_width * 0.8 + length_growth;
  std::vector<TopoDS_Shape> bumps;
  for (double side : {-1.0, 1.0}) {
    // The axis sits inside the key so only the bump stands proud.
    const double y = side * (s.key_thickness / 2 - (radius - radius_growth) + s.bump);
    bumps.push_back(BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(-span / 2, y, z), gp::DX()), radius, span).Shape());
  }
  return bumps;
}

/** The tab & slot pocket: the key's slot with grooves for its bumps, and the lead-in. */
TopoDS_Shape key_pocket(const ClipSpec &s, const Dimensions &d) {
  auto pocket = box(d.mouth_width, d.mouth_thickness, -0.02, d.depth + 0.02);
  for (const auto &groove : key_bumps(s, s.clearance / 2, s.clearance)) pocket = boolean<BRepAlgoAPI_Fuse>(pocket, groove);
  if (s.lead_in > 0) pocket = boolean<BRepAlgoAPI_Fuse>(pocket, lead_in(s, d));
  return pocket;
}

TopoDS_Shape socket(const ClipSpec &s, const Dimensions &d) {
  const double start = -0.02;
  const double total = d.reach - start;
  const double latch = std::min(s.engagement * 0.46, std::max(s.barb * 2.2, s.engagement * 0.25));
  const double throat = std::max(s.bridge / 2, total - latch);
  auto pocket = boolean<BRepAlgoAPI_Fuse>(
      box(d.mouth_width, d.mouth_thickness, start, throat),
      box(d.maximum, d.mouth_thickness, start + throat, total - throat + s.depth_clearance));
  if (s.lead_in > 0) pocket = boolean<BRepAlgoAPI_Fuse>(pocket, lead_in(s, d));
  return pocket;
}

TopoDS_Shape prong(const ClipSpec &s, int direction, int side) {
  const double inner = s.slot / 2;
  const double outer = inner + s.arm_width;
  const double tip = outer - s.arm_width * 0.15;
  double insertion = s.barb / std::tan(30 * (pi / 180));
  double removal = s.barb / std::tan(s.removal_angle * (pi / 180));
  const double ramp = insertion + removal;
  if (ramp > s.engagement * 0.42) {
    const double scale = s.engagement * 0.42 / ramp;
    insertion *= scale;
    removal *= scale;
  }
  const double peak = s.engagement - insertion;
  const double z = s.bridge / 2;
  const double radius = s.arm_width * 0.25;
  BRepBuilderAPI_MakePolygon wire;
  const auto add = [&](double x, double axial) {
    wire.Add(gp_Pnt(side * x, -s.thickness / 2, direction * axial));
  };
  for (double angle : {0.0, -22.5, -45.0, -67.5, -90.0}) {
    const double rad = angle * (pi / 180);
    add(inner - radius + radius * std::cos(rad), z + radius + radius * std::sin(rad));
  }
  for (double angle : {-90.0, -67.5, -45.0, -22.5, 0.0}) {
    const double rad = angle * (pi / 180);
    add(outer - radius + radius * std::cos(rad), z + radius + radius * std::sin(rad));
  }
  add(tip, z + (peak - removal));
  add(tip + s.barb, z + peak);
  add(tip, z + s.engagement);
  add(inner, z + s.engagement);
  wire.Close();
  const auto profile = BRepBuilderAPI_MakeFace(wire.Wire()).Face();
  return BRepPrimAPI_MakePrism(profile, gp_Vec(0, s.thickness, 0)).Shape();
}

std::unique_ptr<Shape> connector(const ClipSpec &s, const Dimensions &d) {
  auto result = box(d.shank, s.thickness, -s.bridge / 2, s.bridge);
  for (int direction : {-1, 1}) {
    for (int side : {-1, 1}) {
      result = boolean<BRepAlgoAPI_Fuse>(result, prong(s, direction, side));
    }
  }
  auto clean = unify(Shape(result));
  if (!is_valid(*clean)) throw std::runtime_error("The requested clip dimensions do not make a valid connector.");
  return clean;
}

/** What stands proud of the male face (snap tab, tab & slot), rooted 0.3 mm into it. */
TopoDS_Shape male_half(const ClipSpec &s, const Dimensions &d) {
  constexpr double root = 0.3;
  TopoDS_Shape part;
  if (s.style == tab_slot) {
    part = box(s.key_width, s.key_thickness, -root, s.key_length + root);
    for (const auto &bump : key_bumps(s, 0, 0)) part = boolean<BRepAlgoAPI_Fuse>(part, bump);
  } else {
    part = box(d.shank, s.thickness, -root, s.bridge / 2 + root);
    for (int side : {-1, 1}) part = boolean<BRepAlgoAPI_Fuse>(part, prong(s, 1, side));
  }
  return unify(Shape(part))->shape;
}

gp_Pnt world(const gp_Ax3 &frame, Point point) {
  return frame.Location().Translated(gp_Vec(frame.XDirection()) * point[0] +
                                      gp_Vec(frame.YDirection()) * point[1]);
}

Point local(const gp_Ax3 &frame, const gp_Pnt &point) {
  const gp_Vec delta(frame.Location(), point);
  return {delta.Dot(gp_Vec(frame.XDirection())), delta.Dot(gp_Vec(frame.YDirection()))};
}

Geometry shared_face(const Layout &layout, const Shape &a, const Shape &b, const gp_Ax3 &frame_b) {
  const auto profile_a = planar_profile(layout, a);
  const auto profile_b = planar_profile(layout, b);
  const auto frame_a = BRepAdaptor_Surface(TopoDS::Face(a.shape)).Plane().Position();
  std::pair<gp_Ax3, gp_Ax3> frames{frame_a, frame_b};
  auto projected = layout.own(GEOSGeom_transformXY_r(layout.context, profile_a.get(),
      [](double *u, double *v, void *data) {
        const auto &[a, b] = *static_cast<const std::pair<gp_Ax3, gp_Ax3> *>(data);
        const auto p = local(b, world(a, {*u, *v}));
        *u = p[0];
        *v = p[1];
        return 1;
      }, &frames));
  const auto clean_a = layout.buffer(projected.get(), 0);
  const auto clean_b = layout.buffer(profile_b.get(), 0);
  return layout.intersection(clean_b.get(), clean_a.get());
}

struct Pick {
  Point at;
  double angle;
};

double wrap(double position, double total) {
  const double result = std::fmod(position, total);
  return result < 0 ? result + total : result;
}

std::vector<Pick> plan(const Layout &layout, const GEOSGeometry *region, const Dimensions &d,
                       const ClipPlacementSpec &settings, const gp_Ax3 &frame,
                       rust::Slice<const double> anchors) {
  const auto ctx = layout.context;
  const auto allowed = settings.edge_offset > 0
      ? layout.buffer(region, -settings.edge_offset, 16, GEOSBUF_CAP_ROUND, GEOSBUF_JOIN_MITRE)
      : layout.own(GEOSGeom_clone_r(ctx, region));
  const auto core = layout.buffer(region, -(settings.edge_offset + d.wall_thickness / 2 + 0.02),
                                   16, GEOSBUF_CAP_ROUND, GEOSBUF_JOIN_MITRE);
  struct Track { const GEOSGeometry *ring; double length; int share; };
  std::vector<Track> tracks;
  double total = 0;
  for (auto *polygon : layout.polygons(core.get())) {
    const auto *ring = GEOSGetExteriorRing_r(ctx, polygon);
    double length;
    if (GEOSLength_r(ctx, ring, &length) && length > 1e-6) {
      total += length;
      tracks.push_back({ring, length, 0});
    }
  }
  const double rotation = settings.rotation * (pi / 180);
  std::vector<Geometry> reserved;
  std::vector<Pick> result;
  const auto at = [&](const Track &track, double position) {
    const auto point = layout.own(GEOSInterpolate_r(ctx, track.ring, wrap(position, track.length)));
    return layout.coordinates(point.get()).front();
  };
  const auto tangent = [&](const Track &track, double position) {
    const auto a = at(track, position - 0.5), b = at(track, position + 0.5);
    return std::atan2(b[1] - a[1], b[0] - a[0]);
  };
  const auto place = [&](Point position, double angle) {
    const double max_x = d.wall_width / 2, max_y = d.wall_thickness / 2;
    const double c = std::cos(angle), s = std::sin(angle);
    Ring corners;
    for (const auto &[x, y] : Ring{{-max_x, -max_y}, {max_x, -max_y}, {max_x, max_y}, {-max_x, max_y}}) {
      corners.push_back({position[0] + x * c - y * s, position[1] + x * s + y * c});
    }
    auto footprint = layout.polygon(corners);
    if (GEOSCovers_r(ctx, allowed.get(), footprint.get()) != 1) return false;
    for (const auto &take : reserved) {
      if (GEOSIntersects_r(ctx, footprint.get(), take.get()) != 0) return false;
    }
    result.push_back({position, angle});
    reserved.push_back(std::move(footprint));
    return true;
  };
  if (!anchors.empty()) {
    for (std::size_t i = 0; i < anchors.size(); i += 3) {
      const auto position = local(frame, gp_Pnt(anchors[i], anchors[i + 1], anchors[i + 2]));
      double angle = rotation;
      if (!tracks.empty()) {
        const auto point = layout.own(GEOSGeom_createPointFromXY_r(ctx, position[0], position[1]));
        const auto separation = [&](const Track &track) {
          double distance = 0;
          GEOSDistance_r(ctx, track.ring, point.get(), &distance);
          return distance;
        };
        const auto track = std::min_element(tracks.begin(), tracks.end(), [&](const Track &a, const Track &b) {
          return separation(a) < separation(b);
        });
        angle += tangent(*track, GEOSProject_r(ctx, track->ring, point.get()));
      }
      if (!place(position, angle)) {
        throw std::runtime_error("Clip " + std::to_string(i / 3 + 1) +
            " does not fit clear of edges, holes, and other clips.");
      }
    }
    return result;
  }
  if (tracks.empty()) {
    throw std::runtime_error("The shared face is too narrow for this joint. Use a smaller clip or edge offset.");
  }
  int count = static_cast<int>(settings.count);
  if (count == 0) {
    const double preferred = std::nearbyint(total / std::max(settings.spacing, d.wall_width + 0.5));
    count = std::max(static_cast<int>(tracks.size()), static_cast<int>(std::min(preferred, 64.0)));
    if (count == 1 && total > 6 * d.wall_width) count = 2;
  }
  count = std::clamp(count, 1, max_connectors);
  int remaining = 0;
  for (auto &track : tracks) {
    track.share = std::max(1, static_cast<int>(std::nearbyint(count * track.length / total)));
    remaining += track.share;
  }
  while (remaining > count) {
    auto highest = std::max_element(tracks.begin(), tracks.end(), [](const Track &a, const Track &b) {
      return a.share < b.share;
    });
    if (highest->share <= 1) break;
    --highest->share;
    --remaining;
  }
  for (const auto &track : tracks) {
    const double pitch = track.length / track.share;
    const int steps = static_cast<int>(std::min(pitch / 2, track.length / 2) / 0.5) + 1;
    for (int i = 0; i < track.share; ++i) {
      const double target = wrap(settings.shift + (i + 0.5) * pitch, track.length);
      bool placed = false;
      for (int k = 0; !placed && k < steps; ++k) {
        for (int sign : {1, -1}) {
          if (k == 0 && sign == -1) break;
          const double position = target + sign * k * 0.5;
          if (place(at(track, position), tangent(track, position) + rotation)) {
            placed = true;
            break;
          }
        }
      }
    }
  }
  if (result.empty()) throw std::runtime_error("No joint fits on this seam clear of edges, holes, and walls.");
  return result;
}

bool contained(const TopoDS_Shape &body, const TopoDS_Shape &cutter) {
  const double amount = volume(Shape(cutter));
  if (amount <= 1e-8) return false;
  return volume(Shape(boolean<BRepAlgoAPI_Common>(body, cutter))) >= amount * 0.985;
}


// -- Sliding joints: SimpleCAD 0.4's slides.py -------------------------------
// Canonical frame: Z out of the part carrying the rails into its mate, X the way
// the rails slide in, Y across them. Profiles are (Y, Z) polygons.

std::string number(const char *format, double value) {
  char text[64];
  std::snprintf(text, sizeof text, format, value);
  return text;
}

constexpr double rail_root = 0.3;

double slide_gap(const SlideSpec &s) { return s.clearance / 2; }

double neck(const SlideSpec &s) { return s.width - 2 * s.height * std::tan(s.angle * (pi / 180)); }

/** The male cross-section, counter-clockwise, from below the face. */
Ring rail_profile(const SlideSpec &s, double root = rail_root) {
  const double w = s.width / 2, h = s.height;
  if (s.style == 2) return {{-w, -root}, {w, -root}, {w, h}, {-w, h}};
  if (s.style == 0) {
    const double n = neck(s) / 2;
    return {{-n, -root}, {n, -root}, {n, 0.0}, {w, h}, {-w, h}, {-n, 0.0}};
  }
  // T-rail: a stem half the width up to half height, then a flange as wide as the rail.
  const double stem = s.width * 0.5 / 2, middle = h * 0.5;
  return {{-stem, -root}, {stem, -root}, {stem, middle}, {w, middle},
          {w, h}, {-w, h}, {-w, middle}, {-stem, middle}};
}

Geometry mitred(const Layout &layout, const GEOSGeometry *shape, double distance) {
  return layout.buffer(shape, distance, 16, GEOSBUF_CAP_ROUND, GEOSBUF_JOIN_MITRE);
}

/** The cavity a rail runs in: its profile grown by half the clearance. */
Geometry channel_profile(const Layout &layout, const SlideSpec &s) {
  const auto profile = layout.polygon(rail_profile(s, slide_gap(s) + 0.5));
  return mitred(layout, profile.get(), slide_gap(s));
}

/** A polygon's outline, without the closing point (shapely's exterior less its last). */
Ring outline(const Layout &layout, const GEOSGeometry *polygon) {
  auto ring = layout.coordinates(GEOSGetExteriorRing_r(layout.context, polygon));
  if (!ring.empty()) ring.pop_back();
  return ring;
}

/** Only the deepest edge pushed `extra` further: a T-rail's edges under the flange stay. */
Ring deepened(Ring points, double extra) {
  double top = -1e300;
  for (const auto &p : points) top = std::max(top, p[1]);
  for (auto &p : points) {
    if (p[1] >= top - 1e-9) p[1] += extra;
  }
  return points;
}

/** A (Y, Z) polygon extruded along X from x0 to x1. */
TopoDS_Shape prism(const Ring &points, double x0, double x1) {
  BRepBuilderAPI_MakePolygon polygon;
  for (const auto &[y, z] : points) polygon.Add(gp_Pnt(x0, y, z));
  polygon.Close();
  const auto face = BRepBuilderAPI_MakeFace(polygon.Wire(), true).Face();
  return BRepPrimAPI_MakePrism(face, gp_Vec(x1 - x0, 0, 0)).Shape();
}

double half_span(const Layout &layout, const GEOSGeometry *channel) {
  const auto b = layout.bounds(channel);
  return std::max(std::abs(b[0]), std::abs(b[2]));
}

/** The channel cut into a thick mate: open at x_open, closed at x_end. */
TopoDS_Shape groove(const Layout &layout, const SlideSpec &s, double x_open, double x_end) {
  const auto channel = channel_profile(layout, s);
  return prism(deepened(outline(layout, channel.get()), s.depth_clearance), x_open, x_end);
}

/** The raised channel standing on a thin mate's face (at z = height + gap); a cap closes the far end. */
TopoDS_Shape lips(const Layout &layout, const SlideSpec &s, double x_open, double x_end, bool cap) {
  const double face_z = s.height + slide_gap(s);
  const auto channel = channel_profile(layout, s);
  const double half = half_span(layout, channel.get());
  const auto outer = layout.rectangle(-(half + s.lip), slide_gap(s), half + s.lip, face_z + 0.3);
  const auto walls = layout.own(GEOSDifference_r(layout.context, outer.get(), channel.get()));
  const auto pieces = layout.polygons(walls.get(), 1e-6);
  if (pieces.empty()) throw std::runtime_error("The raised channel has no walls left once the rail's room is cut out.");
  std::optional<TopoDS_Shape> solid;
  for (const auto *piece : pieces) {
    const auto part = prism(outline(layout, piece), x_open, x_end);
    solid = solid ? boolean<BRepAlgoAPI_Fuse>(*solid, part) : part;
  }
  if (cap) {
    const auto end = BRepPrimAPI_MakeBox(gp_Pnt(x_end, -(half + s.lip), slide_gap(s)),
                                         gp_Pnt(x_end + s.lip, half + s.lip, face_z + 0.3)).Shape();
    solid = boolean<BRepAlgoAPI_Fuse>(*solid, end);
  }
  return *solid;
}

/** A flared mouth at the open end, so the rail finds its way in. */
std::optional<TopoDS_Shape> slide_lead_in(const Layout &layout, const SlideSpec &s, double x_open) {
  if (s.lead_in <= 0) return std::nullopt;
  const auto channel = channel_profile(layout, s);
  const auto wide = mitred(layout, channel.get(), s.lead_in);
  const auto narrow_points = outline(layout, channel.get()), wide_points = outline(layout, wide.get());
  if (narrow_points.size() != wide_points.size()) return std::nullopt;
  const auto ring = [](const Ring &points, double x) {
    BRepBuilderAPI_MakePolygon polygon;
    for (const auto &[y, z] : points) polygon.Add(gp_Pnt(x, y, z));
    polygon.Close();
    return polygon.Wire();
  };
  BRepOffsetAPI_ThruSections loft(true, true);
  loft.AddWire(ring(wide_points, x_open - 0.02));
  loft.AddWire(ring(narrow_points, x_open + s.lead_in));
  loft.Build();
  if (!loft.IsDone()) return std::nullopt;
  return loft.Shape();
}

/** A half-round ridge across the rail top at x (grown, the dimple for it). */
TopoDS_Shape detent_bump(const SlideSpec &s, double detent, double x, double grow = 0) {
  const double radius = std::max(detent * 2, 0.8);
  const double span = s.width * 0.6 + 2 * grow;
  const double centre_z = s.height - radius + detent;
  return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, -span / 2, centre_z), gp_Dir(0, 1, 0)), radius + grow, span).Shape();
}

/** The world direction the male part moves to go in: +u, -u, +v, -v of face B's frame. */
gp_Vec insertion_axis(const gp_Ax3 &frame, int insertion) {
  const gp_Vec axis(insertion < 2 ? frame.XDirection() : frame.YDirection());
  return insertion % 2 ? -axis : axis;
}

/** Downward on a wall (things drop into brackets); else along the longer side of the shared face. */
int default_insertion(const Layout &layout, const gp_Ax3 &frame, const GEOSGeometry *region) {
  int best = 0;
  for (int key = 1; key < 4; ++key) {
    if (insertion_axis(frame, key).Z() < insertion_axis(frame, best).Z()) best = key;
  }
  if (insertion_axis(frame, best).Z() < -0.5) return best;
  const auto b = layout.bounds(region);
  return b[2] - b[0] >= b[3] - b[1] ? 0 : 2;
}

struct RailLayout {
  std::vector<double> rails;  // Y of each rail's centre line
  double x_open, x_start, x_end, region_end;
};

/** Where the rails run in the joint's (X, Y) plane: spread across the shared face, clear of its edge. */
RailLayout plan_rails(const Layout &layout, const GEOSGeometry *region, const SlideSpec &s, bool raised) {
  const int count = std::clamp(static_cast<int>(s.rails), 1, 8);
  const auto channel = channel_profile(layout, s);
  const double keep = half_span(layout, channel.get()) + (raised ? s.lip : s.min_wall);
  const auto [low_x, low_y, high_x, high_y] = layout.bounds(region);
  const double span = (high_y - low_y) - 2 * (s.edge_offset + keep);
  if (span < -1e-9 || (count > 1 && span < (count - 1) * 2 * keep)) {
    const double need = 2 * (s.edge_offset + keep) + (count - 1) * 2 * keep;
    throw std::runtime_error(std::to_string(count) + " rail" + (count != 1 ? "s" : "") + " need " +
        number("%.1f", need) + " mm across the shared face; it is " + number("%.1f", high_y - low_y) + " mm.");
  }
  RailLayout out{};
  const double first = low_y + s.edge_offset + keep;
  for (int k = 0; k < count; ++k) {
    out.rails.push_back(count > 1 ? first + span * k / (count - 1) : (low_y + high_y) / 2);
  }
  double x_lo = low_x, x_hi = high_x;
  for (const double y : out.rails) {
    const auto band = layout.rectangle(low_x - 1, y - keep, high_x + 1, y + keep);
    const auto strip = layout.intersection(region, band.get());
    if (layout.empty(strip.get())) throw std::runtime_error("A rail would run off the shared face.");
    const auto b = layout.bounds(strip.get());
    x_lo = std::max(x_lo, b[0]);
    x_hi = std::min(x_hi, b[2]);
  }
  const auto grown = layout.buffer(region, 1e-6);
  for (const double y : out.rails) {
    const auto path = layout.rectangle(x_lo + 0.01, y - keep, x_hi - 0.01, y + keep);
    if (GEOSCovers_r(layout.context, grown.get(), path.get()) != 1) {
      throw std::runtime_error("A hole or an edge interrupts the path of a rail.");
    }
  }
  const double end_room = s.stop ? slide_gap(s) + (raised ? s.lip : s.min_wall) : 0;
  out.x_end = s.stop ? x_hi - s.edge_offset - end_room : x_hi;
  out.x_start = s.rail_length <= 0 ? x_lo : out.x_end - s.rail_length;
  if (out.x_start < x_lo - 1e-9) {
    throw std::runtime_error("The rails can be at most " + number("%.1f", out.x_end - x_lo) + " mm long here.");
  }
  if (out.x_end - out.x_start < std::max(2 * s.width, 6.0)) {
    throw std::runtime_error("Only " + number("%.1f", out.x_end - out.x_start) +
        " mm of rail fits; the part would rock on it.");
  }
  out.x_open = x_lo;
  out.region_end = x_hi;
  return out;
}

TopoDS_Shape translated(const TopoDS_Shape &shape, const gp_Vec &by) {
  gp_Trsf move;
  move.SetTranslation(by);
  return moved(shape, move);
}

/** Volume two solids share; zero when they only touch or stand apart (0.4's interference_volume). */
double interference(const TopoDS_Shape &a, const TopoDS_Shape &b) {
  Bnd_Box box_a, box_b;
  BRepBndLib::Add(a, box_a);
  BRepBndLib::Add(b, box_b);
  if (box_a.IsOut(box_b)) return 0;
  return std::abs(volume(Shape(boolean<BRepAlgoAPI_Common>(a, b))));
}


// -- Version-1 clips: SimpleCAD 0.4's ClipJointFeature._execute_legacy -------

/** `make_socket`: the stepped cutter for one end of a connector (sign +1 into A, -1 into B). */
TopoDS_Shape legacy_socket(const ClipSpec &s, int sign, double inset) {
  const double shank = s.slot + 2 * s.arm_width;
  const double narrow_width = shank + s.clearance;
  const double wide_width = shank + 2 * s.barb + s.clearance;
  const double height = s.thickness + s.clearance;
  const double total = s.engagement + s.bridge / 2 - inset;
  const double latch = std::min(s.engagement * 0.46, std::max(s.barb * 2.2, s.engagement * 0.25));
  const double narrow_length = std::max(s.bridge / 2, total - latch);
  const double narrow_z = sign > 0 ? inset : -(inset + narrow_length);
  const double catch_z = sign > 0 ? inset + narrow_length : -(inset + total);
  const auto narrow = BRepPrimAPI_MakeBox(gp_Pnt(-narrow_width / 2, -height / 2, narrow_z),
                                          narrow_width, height, narrow_length).Shape();
  const auto pocket = BRepPrimAPI_MakeBox(gp_Pnt(-wide_width / 2, -height / 2, catch_z),
                                          wide_width, height, total - narrow_length).Shape();
  return boolean<BRepAlgoAPI_Fuse>(narrow, pocket);
}

/** A face's sampled outline in its plane's own frame, grown a hair as 0.4 checks against it. */
std::pair<Geometry, gp_Ax3> face_outline(const Layout &layout, const Shape &face) {
  const auto profile = planar_profile(layout, face);
  return {layout.buffer(profile.get(), 1e-6), BRepAdaptor_Surface(TopoDS::Face(face.shape)).Plane().Position()};
}

Geometry projected(const Layout &layout, const std::vector<gp_Pnt> &corners, const gp_Ax3 &frame) {
  Ring points;
  for (const auto &corner : corners) points.push_back(local(frame, corner));
  return layout.polygon(points);
}

} // namespace

ClipOutput clip_connectors(const Shape &body_a, const Shape &body_b,
                           const Shape &face_a, const Shape &face_b,
                           const ClipSpec &clip, const ClipPlacementSpec &placement,
                           rust::Slice<const double> manual_points) {
  if (manual_points.size() % 3 != 0) throw std::runtime_error("A clip anchor could not be read.");
  const auto a = plane_frame(face_a), b = plane_frame(face_b);
  if (!a.planar || !b.planar) throw std::runtime_error("Clips join flat faces. Select the flat mating faces.");
  const gp_Dir normal_a(a.normal[0], a.normal[1], a.normal[2]);
  const gp_Dir normal_b(b.normal[0], b.normal[1], b.normal[2]);
  const double alignment = normal_a.Dot(normal_b);
  if (alignment > -0.9994) throw std::runtime_error("The two faces are not facing each other.");
  const gp_Vec difference(a.center[0] - b.center[0], a.center[1] - b.center[1], a.center[2] - b.center[2]);
  if (std::abs(difference.Dot(gp_Vec(normal_b))) > 0.05) {
    throw std::runtime_error("The two faces are apart. Bring the parts together before cutting sockets.");
  }
  const auto frame_b = BRepAdaptor_Surface(TopoDS::Face(face_b.shape)).Plane().Position();
  const Layout layout;
  const auto seam = shared_face(layout, face_a, face_b, frame_b);
  if (layout.empty(seam.get()) || layout.area(seam.get()) < 1e-6) {
    throw std::runtime_error("The two faces do not overlap.");
  }
  if (clip.style > tab_slot) throw std::runtime_error("Unknown joint style.");
  const auto d = dimensions(clip);
  const auto picks = plan(layout, seam.get(), d, placement, frame_b, manual_points);
  const bool connectors = clip.style == printed_connector;
  const auto clip_shape = connectors ? connector(clip, d) : nullptr;
  const auto part = connectors ? TopoDS_Shape() : male_half(clip, d);
  const auto pocket = clip.style == tab_slot ? key_pocket(clip, d) : socket(clip, d);
  const auto check = box(d.wall_width, d.wall_thickness, 0.05, d.depth + clip.min_wall - 0.05);
  gp_Trsf mirror;
  mirror.SetMirror(gp_Ax2(gp::Origin(), gp::DZ()));
  const auto male_pocket = moved(pocket, mirror), male_check = moved(check, mirror);

  ClipOutput result;
  auto male = placement.male_on_a ? body_a.shape : body_b.shape;
  auto female = placement.male_on_a ? body_b.shape : body_a.shape;
  const gp_Dir normal = placement.male_on_a ? normal_a : normal_a.Reversed();
  for (std::size_t i = 0; i < picks.size(); ++i) {
    const auto pick = picks[i];
    const auto along = gp_Vec(frame_b.XDirection()) * std::cos(pick.angle) +
                       gp_Vec(frame_b.YDirection()) * std::sin(pick.angle);
    const gp_Vec z(normal);
    const gp_Dir x(along - z * along.Dot(z));
    gp_Trsf where;
    where.SetDisplacement(gp_Ax3(gp::Origin(), gp::DZ(), gp::DX()), gp_Ax3(world(frame_b, pick.at), normal, x));
    const auto wall = [&](const TopoDS_Shape &body, const TopoDS_Shape &cutter, bool first) {
      if (contained(body, moved(cutter, where))) return;
      std::ostringstream message;
      message << "Clip " << i + 1 << " would break through the " << (first ? "first" : "second")
              << " part or leave a wall under " << clip.min_wall << " mm.";
      throw std::runtime_error(message.str());
    };
    wall(female, check, !placement.male_on_a);
    female = boolean<BRepAlgoAPI_Cut>(female, moved(pocket, where));
    if (!connectors) {
      male = boolean<BRepAlgoAPI_Fuse>(male, moved(part, where));
      continue;
    }
    wall(male, male_check, placement.male_on_a);
    male = boolean<BRepAlgoAPI_Cut>(male, moved(male_pocket, where));
    result.connectors.push_back({{}, std::make_unique<Shape>(moved(clip_shape->shape, where)), {}});
  }
  result.placed = static_cast<std::uint32_t>(picks.size());
  result.first = unify(Shape(placement.male_on_a ? male : female));
  result.second = unify(Shape(placement.male_on_a ? female : male));
  if (!is_valid(*result.first) || !is_valid(*result.second)) {
    throw std::runtime_error("The joint did not leave valid printable parts.");
  }
  return result;
}

ClipOutput clip_legacy(const Shape &body_a, const Shape &body_b, const Shape &face_a, const Shape &face_b,
                       const ClipSpec &s, rust::Slice<const double> frame, rust::Slice<const double> points) {
  if (frame.size() != 13 || points.size() % 2 != 0) throw std::runtime_error("A clip anchor could not be read.");
  const double rotation = frame[12];
  const gp_Pnt centre(frame[0], frame[1], frame[2]);
  const gp_Vec x_axis(frame[3], frame[4], frame[5]), y_axis(frame[6], frame[7], frame[8]);
  const gp_Dir normal(frame[9], frame[10], frame[11]);
  const auto at = [&](double u, double v) { return centre.Translated(x_axis * u + y_axis * v); };
  const double angle = rotation * (pi / 180), c = std::cos(angle), sn = std::sin(angle);

  // `_validate_placements`: each footprint on both faces, and clear of the others.
  const Layout layout;
  const auto [target, target_frame] = face_outline(layout, face_b);
  const auto [moving, moving_frame] = face_outline(layout, face_a);
  const double half_x = (s.slot + 2 * s.arm_width + 2 * s.barb + s.clearance) / 2;
  const double half_y = (s.thickness + s.clearance) / 2;
  std::vector<Geometry> footprints;
  for (std::size_t i = 0; i < points.size() / 2; ++i) {
    const double u = points[2 * i], v = points[2 * i + 1];
    std::vector<gp_Pnt> world;
    Ring flat;
    for (const auto &[x, y] : Ring{{-half_x, -half_y}, {half_x, -half_y}, {half_x, half_y}, {-half_x, half_y}}) {
      const double rx = x * c - y * sn, ry = x * sn + y * c;
      world.push_back(at(u + rx, v + ry));
      flat.push_back({u + rx, v + ry});
    }
    const auto label = "Clip " + std::to_string(i + 1);
    if (GEOSCovers_r(layout.context, target.get(), projected(layout, world, target_frame).get()) != 1) {
      throw std::runtime_error(label + " does not fit on the target face.");
    }
    if (GEOSCovers_r(layout.context, moving.get(), projected(layout, world, moving_frame).get()) != 1) {
      throw std::runtime_error(label + " does not fit on the moving face after alignment.");
    }
    auto footprint = layout.polygon(flat);
    for (const auto &other : footprints) {
      const auto shared = layout.intersection(footprint.get(), other.get());
      if (layout.area(shared.get()) > 1e-7) throw std::runtime_error(label + " overlaps another clip.");
    }
    footprints.push_back(std::move(footprint));
  }

  const auto d = dimensions(s);
  const auto clip = connector(s, d);
  const auto check_a = legacy_socket(s, 1, 0.02), check_b = legacy_socket(s, -1, 0.02);
  const auto socket_a = legacy_socket(s, 1, -0.02), socket_b = legacy_socket(s, -1, -0.02);
  ClipOutput result;
  auto first = body_a.shape, second = body_b.shape;
  for (std::size_t i = 0; i < points.size() / 2; ++i) {
    const gp_Vec along = x_axis * c + y_axis * sn;
    gp_Trsf where;
    where.SetDisplacement(gp_Ax3(gp::Origin(), gp::DZ(), gp::DX()),
                          gp_Ax3(at(points[2 * i], points[2 * i + 1]), normal, gp_Dir(along)));
    const auto label = "Clip " + std::to_string(i + 1);
    if (!contained(first, moved(check_a, where))) throw std::runtime_error(label + " would break through the moving part.");
    if (!contained(second, moved(check_b, where))) throw std::runtime_error(label + " would break through the target part.");
    first = boolean<BRepAlgoAPI_Cut>(first, moved(socket_a, where));
    second = boolean<BRepAlgoAPI_Cut>(second, moved(socket_b, where));
    result.connectors.push_back({{}, std::make_unique<Shape>(moved(clip->shape, where)), {}});
  }
  result.placed = static_cast<std::uint32_t>(points.size() / 2);
  result.first = unify(Shape(first));
  result.second = unify(Shape(second));
  if (!is_valid(*result.first) || !is_valid(*result.second)) {
    throw std::runtime_error("The clip sockets did not leave valid printable parts.");
  }
  return result;
}

SlideOutput clip_slides(const Shape &body_a, const Shape &body_b, const Shape &face_a, const Shape &face_b,
                        const SlideSpec &s) {
  const auto a = plane_frame(face_a), b = plane_frame(face_b);
  if (!a.planar || !b.planar) throw std::runtime_error("Clips join flat faces; one of the selected faces is curved.");
  const gp_Vec normal_a(a.normal[0], a.normal[1], a.normal[2]);
  const double alignment = normal_a.Dot(gp_Vec(b.normal[0], b.normal[1], b.normal[2]));
  if (alignment > -0.9994) {
    const double angle = std::acos(std::clamp(-alignment, -1.0, 1.0)) * (180 / pi);
    throw std::runtime_error("The two faces are not facing each other (" + number("%.1f", angle) + "° apart).");
  }
  const auto frame = BRepAdaptor_Surface(TopoDS::Face(face_b.shape)).Plane().Position();
  const Layout layout;
  const auto seam = shared_face(layout, face_a, face_b, frame);
  if (layout.empty(seam.get()) || layout.area(seam.get()) < 1e-6) throw std::runtime_error("The two faces do not overlap.");

  const gp_Vec z_dir = s.male_on_a ? normal_a : -normal_a;
  const int insertion = s.insertion < 4 ? s.insertion : default_insertion(layout, frame, seam.get());
  const gp_Vec x_dir = insertion_axis(frame, insertion);
  const gp_Vec y_dir = z_dir.Crossed(x_dir);
  // The shared face, in the joint's own (X, Y) plane.
  const gp_Vec u(frame.XDirection()), v(frame.YDirection());
  std::array<double, 4> affine{u.Dot(x_dir), v.Dot(x_dir), u.Dot(y_dir), v.Dot(y_dir)};
  const auto region = layout.own(GEOSGeom_transformXY_r(layout.context, seam.get(), [](double *x, double *y, void *data) {
    const auto &[m0, m1, m2, m3] = *static_cast<const std::array<double, 4> *>(data);
    const double px = *x;
    *x = m0 * px + m1 * *y;
    *y = m2 * px + m3 * *y;
    return 1;
  }, &affine));

  const auto place = [&](const gp_Pnt &origin) {
    gp_Trsf where;
    where.SetDisplacement(gp_Ax3(gp::Origin(), gp::DZ(), gp::DX()), gp_Ax3(origin, gp_Dir(z_dir), gp_Dir(x_dir)));
    return where;
  };
  const gp_Pnt seam_origin = frame.Location();
  auto body_moving = body_a.shape;
  bool raised = s.female_mode == 2;
  RailLayout rails{};
  if (!raised) {
    // Cut: the faces touch, so the joint's origin is on the shared plane.
    rails = plan_rails(layout, region.get(), s, false);
    const auto world = place(seam_origin);
    const auto channel = channel_profile(layout, s);
    const auto grown = mitred(layout, channel.get(), s.min_wall);
    auto wall = deepened(outline(layout, grown.get()), s.depth_clearance);
    for (auto &p : wall) p[1] = std::max(p[1], 0.05);
    const auto &female = s.male_on_a ? body_b.shape : body_a.shape;
    bool thick = true;
    for (const double y : rails.rails) {
      const auto test = moved(translated(prism(wall, rails.x_open, rails.x_end + slide_gap(s)), gp_Vec(0, y, 0)), world);
      if (!contained(female, test)) {
        thick = false;
        break;
      }
    }
    if (!thick) {
      if (s.female_mode == 1) {
        throw std::runtime_error(std::string("The ") + (s.male_on_a ? "second" : "first") + " part is too thin for a " +
            number("%.1f", s.height + slide_gap(s) + s.depth_clearance) + " mm deep channel with " +
            number("%g", s.min_wall) + " mm of wall behind it.");
      }
      raised = true;
    }
  }
  const double gap = raised ? s.height + slide_gap(s) : 0;
  if (raised) rails = plan_rails(layout, region.get(), s, true);
  // A raised channel holds the parts the rail's height apart; a cut one holds them together.
  if (s.seated) {
    body_moving = translated(body_moving, -normal_a * gap);
  } else {
    const double apart = gp_Vec(b.center[0] - a.center[0], b.center[1] - a.center[1], b.center[2] - a.center[2]).Dot(normal_a);
    if (std::abs(apart - gap) > 0.05) {
      throw std::runtime_error("The parts are " + number("%.2f", apart) + " mm apart; this joint holds them " +
          number("%.2f", gap) + " mm apart.");
    }
  }
  auto male = s.male_on_a ? body_moving : body_b.shape;
  auto female = s.male_on_a ? body_b.shape : body_moving;
  // Everything is built from the male face: face B carries the seam's origin, and A's face is the gap behind it.
  const gp_Pnt male_origin = s.male_on_a ? seam_origin.Translated(-z_dir * gap) : seam_origin;
  const auto world = place(male_origin);
  const auto at = [&](const TopoDS_Shape &shape, double y) { return moved(translated(shape, gp_Vec(0, y, 0)), world); };
  const auto mouth = slide_lead_in(layout, s, rails.x_open);
  for (const double y : rails.rails) {
    if (raised) {
      female = boolean<BRepAlgoAPI_Fuse>(female, at(lips(layout, s, rails.x_open, rails.x_end + slide_gap(s), s.stop), y));
    } else {
      const double end = s.stop ? rails.x_end + slide_gap(s) : rails.region_end + 1;
      female = boolean<BRepAlgoAPI_Cut>(female, at(groove(layout, s, rails.x_open - 1, end), y));
    }
    if (mouth) female = boolean<BRepAlgoAPI_Cut>(female, at(*mouth, y));
    male = boolean<BRepAlgoAPI_Fuse>(male, at(prism(rail_profile(s), rails.x_start, rails.x_end), y));
  }
  male = unify(Shape(male))->shape;
  female = unify(Shape(female))->shape;

  SlideOutput out;
  const double overlap = interference(male, female);
  if (overlap > 1e-3) {
    throw std::runtime_error("Assembled, the parts overlap by " + number("%.2f", overlap) + " mm³.");
  }
  const double travel = rails.x_end - rails.x_open + 1;
  for (const double step : {0.2, 0.4, 0.6, 0.8, 1.0}) {
    const double hit = interference(translated(male, -x_dir * (travel * step)), female);
    if (hit > 1e-3) {
      throw std::runtime_error("The parts cannot be slid apart: they collide " + number("%.1f", travel * step) +
          " mm out (" + number("%.2f", hit) + " mm³).");
    }
  }
  if (s.stop && interference(translated(male, x_dir * (slide_gap(s) + 0.5)), female) <= 1e-3) {
    out.warnings.push_back("The stop does not catch the rail; check the slide direction.");
  }
  const double ceiling = slide_gap(s) + (raised ? 0 : s.depth_clearance);
  const double detent = s.detent < 0 ? std::round((ceiling + 0.3) * 1000) / 1000 : s.detent;
  if (detent > 0) {
    const double where = rails.x_end - std::max(3.0, s.width * 0.5);
    for (const double y : rails.rails) {
      male = boolean<BRepAlgoAPI_Fuse>(male, at(detent_bump(s, detent, where), y));
      female = boolean<BRepAlgoAPI_Cut>(female, at(detent_bump(s, detent, where, slide_gap(s) * 0.5), y));
    }
    if (detent <= ceiling) out.warnings.push_back("A " + number("%g", detent) + " mm detent is inside the clearance and will not click.");
  }
  if (!is_valid(Shape(male)) || !is_valid(Shape(female))) throw std::runtime_error("The joint did not leave valid printable parts.");
  const double engagement = rails.x_end - rails.x_start;
  if (engagement < 3 * s.width) {
    out.warnings.push_back(number("%.0f", engagement) + " mm of rail is short for its " + number("%g", s.width) +
        " mm width; the part may rock.");
  }
  const gp_Vec exit = s.male_on_a ? -x_dir : x_dir;
  out.first = std::make_unique<Shape>(s.male_on_a ? male : female);
  out.second = std::make_unique<Shape>(s.male_on_a ? female : male);
  out.rails = static_cast<std::uint32_t>(rails.rails.size());
  out.raised = raised;
  out.insertion = static_cast<std::uint8_t>(insertion);
  out.exit_a = {exit.X(), exit.Y(), exit.Z()};
  out.travel = travel;
  out.engagement = engagement;
  out.detent = detent;
  return out;
}

} // namespace simplecad
