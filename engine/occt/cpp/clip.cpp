#include "shim.hpp"
#include "texture_layout.hpp"

#include <algorithm>
#include <optional>
#include <sstream>

#include <BRepAdaptor_Surface.hxx>
#include <BRepAlgoAPI_Common.hxx>
#include <BRepAlgoAPI_Cut.hxx>
#include <BRepAlgoAPI_Fuse.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_MakePolygon.hxx>
#include <BRepBuilderAPI_Transform.hxx>
#include <BRepOffsetAPI_ThruSections.hxx>
#include <BRepPrimAPI_MakeBox.hxx>
#include <BRepPrimAPI_MakePrism.hxx>
#include <TopoDS.hxx>
#include <gp_Pln.hxx>

#include "occt/src/lib.rs.h"

namespace simplecad {
namespace {

using namespace texture;
constexpr int max_connectors = 64;

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
  const double shank = s.slot + 2 * s.arm_width;
  const double maximum = shank + 2 * s.barb + s.clearance;
  const double mouth = shank + s.clearance;
  const double across = s.thickness + s.clearance;
  const double reach = s.engagement + s.bridge / 2;
  const double lead = 2 * s.lead_in + 0.8;
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

TopoDS_Shape socket(const ClipSpec &s, const Dimensions &d) {
  const double start = -0.02;
  const double total = d.reach - start;
  const double latch = std::min(s.engagement * 0.46, std::max(s.barb * 2.2, s.engagement * 0.25));
  const double throat = std::max(s.bridge / 2, total - latch);
  auto pocket = boolean<BRepAlgoAPI_Fuse>(
      box(d.mouth_width, d.mouth_thickness, start, throat),
      box(d.maximum, d.mouth_thickness, start + throat, total - throat + s.depth_clearance));
  if (s.lead_in > 0) {
    BRepOffsetAPI_ThruSections loft(true, true);
    loft.AddWire(rectangle(d.mouth_width / 2 + s.lead_in + 0.02,
                           d.mouth_thickness / 2 + s.lead_in + 0.02, start));
    loft.AddWire(rectangle(d.mouth_width / 2, d.mouth_thickness / 2, s.lead_in));
    loft.Build();
    pocket = boolean<BRepAlgoAPI_Fuse>(pocket, loft.Shape());
  }
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
  const auto d = dimensions(clip);
  const auto picks = plan(layout, seam.get(), d, placement, frame_b, manual_points);
  const auto clip_shape = connector(clip, d);
  const auto pocket = socket(clip, d);
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
    wall(male, male_check, placement.male_on_a);
    male = boolean<BRepAlgoAPI_Cut>(male, moved(male_pocket, where));
    result.connectors.push_back({{}, std::make_unique<Shape>(moved(clip_shape->shape, where)), {}});
  }
  result.first = unify(Shape(placement.male_on_a ? male : female));
  result.second = unify(Shape(placement.male_on_a ? female : male));
  if (!is_valid(*result.first) || !is_valid(*result.second)) {
    throw std::runtime_error("The joint did not leave valid printable parts.");
  }
  return result;
}

} // namespace simplecad
