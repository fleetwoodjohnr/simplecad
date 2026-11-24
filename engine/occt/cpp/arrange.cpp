// SimpleCAD 0.4's `arrange.arrange_on_face`: separately named bodies laid out in a pattern on a flat face, the round
// side of a cylinder, or the build plate. Each step keeps its Python original's order of work, so bodies land where
// 0.4 put them; the planning itself is `arrange_layout.cpp`.
#include "shim.hpp"
#include "arrange_layout.hpp"

#include <algorithm>
#include <cmath>
#include <map>
#include <optional>
#include <tuple>

#include <BRepAdaptor_Curve.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_Transform.hxx>
#include <BRepMesh_IncrementalMesh.hxx>
#include <BRep_Tool.hxx>
#include <GCPnts_QuasiUniformDeflection.hxx>
#include <Poly_Triangulation.hxx>
#include <TopExp_Explorer.hxx>
#include <TopLoc_Location.hxx>
#include <TopoDS.hxx>
#include <gp_Ax1.hxx>
#include <gp_Pln.hxx>

#include "occt/src/lib.rs.h"

namespace simplecad {
namespace {

using namespace texture;
using arrange::Settings;
using V3 = std::array<double, 3>;

V3 sub(const V3 &a, const V3 &b) { return {a[0] - b[0], a[1] - b[1], a[2] - b[2]}; }
V3 scaled(const V3 &a, double k) { return {a[0] * k, a[1] * k, a[2] * k}; }
double dot(const V3 &a, const V3 &b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
V3 cross(const V3 &a, const V3 &b) {
  return {a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]};
}
V3 unit(const V3 &a) {
  const double magnitude = std::sqrt(dot(a, a));
  if (magnitude < 1e-12) throw std::runtime_error("That geometry has no usable direction.");
  return scaled(a, 1.0 / magnitude);
}
gp_Pnt pnt(const V3 &p) { return gp_Pnt(p[0], p[1], p[2]); }
gp_Dir dir(const V3 &d) { return gp_Dir(d[0], d[1], d[2]); }
V3 xyz(const gp_XYZ &p) { return {p.X(), p.Y(), p.Z()}; }

/** Python's `%` on floats: the result takes the divisor's sign. */
double py_mod(double a, double b) {
  double r = std::fmod(a, b);
  if (r != 0 && ((r < 0) != (b < 0))) r += b;
  return r;
}

/** `align.rotation_between`. */
gp_Trsf rotation_between(V3 source, V3 target, const V3 &pivot) {
  source = unit(source);
  target = unit(target);
  V3 axis = cross(source, target);
  const double sine = std::sqrt(dot(axis, axis));
  const double cosine = dot(source, target);
  gp_Trsf transform;
  double angle;
  if (sine < 1e-9) {
    if (cosine > 0) return transform;
    const V3 reference = std::abs(source[0]) < 0.9 ? V3{1, 0, 0} : V3{0, 1, 0};
    axis = unit(cross(source, reference));
    angle = pi;
  } else {
    axis = unit(axis);
    angle = std::atan2(sine, cosine);
  }
  transform.SetRotation(gp_Ax1(pnt(pivot), dir(axis)), angle);
  return transform;
}

gp_Trsf translation(const V3 &by) {
  gp_Trsf transform;
  transform.SetTranslation(gp_Vec(by[0], by[1], by[2]));
  return transform;
}

/** `occ.transformed`: geometry copied, no mesh carried over. */
TopoDS_Shape transformed(const TopoDS_Shape &shape, const gp_Trsf &transform) {
  BRepBuilderAPI_Transform builder(shape, transform, true);
  if (!builder.IsDone()) throw std::runtime_error("The move operation did not succeed on this geometry.");
  return builder.Shape();
}

std::array<V3, 2> box_of(const TopoDS_Shape &shape) {
  const auto b = bounds(Shape(shape));
  return {V3{b[0], b[1], b[2]}, V3{b[3], b[4], b[5]}};
}

/** A face to lay things out on, in the U/V millimetres the pattern is planned in. */
struct Target {
  bool round = false;
  V3 center{}, x{}, y{}, normal{};  // flat: `align.reference_frame`
  Mapping mapping;                  // round: `CylindricalMapping`
  double sign = 1;                  // round: V flipped so U × V is the outward normal
  bool internal = false;
  double period = 0;                // round a full cylinder: its circumference
  Geometry domain;

  V3 axis() const { return xyz(mapping.frame.Direction().XYZ()); }

  std::array<V3, 3> raw_axes(double u) const {
    const double theta = mapping.uv[0] + u / mapping.radius;
    const double c = std::cos(theta), s = std::sin(theta);
    const V3 ox = xyz(mapping.frame.XDirection().XYZ()), oy = xyz(mapping.frame.YDirection().XYZ());
    V3 radial, tangent;
    for (int i = 0; i < 3; ++i) {
      radial[i] = ox[i] * c + oy[i] * s;
      tangent[i] = -ox[i] * s + oy[i] * c;
    }
    return {tangent, axis(), scaled(radial, mapping.sign)};
  }

  V3 point(double u, double v, double lift) const {
    V3 out;
    if (!round) {
      for (int i = 0; i < 3; ++i) out[i] = center[i] + x[i] * u + y[i] * v + normal[i] * lift;
      return out;
    }
    const double theta = mapping.uv[0] + u / mapping.radius;
    const double radius = mapping.radius + mapping.sign * lift;
    const double along = mapping.uv[2] + sign * v;
    const V3 origin = xyz(mapping.frame.Location().XYZ()), a = axis();
    const V3 ox = xyz(mapping.frame.XDirection().XYZ()), oy = xyz(mapping.frame.YDirection().XYZ());
    for (int i = 0; i < 3; ++i) {
      out[i] = origin[i] + a[i] * along + radius * (ox[i] * std::cos(theta) + oy[i] * std::sin(theta));
    }
    return out;
  }

  std::array<V3, 3> axes(double u = 0.0) const {
    if (!round) return {x, y, normal};
    auto [tangent, along, out] = raw_axes(u);
    return {tangent, scaled(along, sign), out};
  }

  Point to_uv(const V3 &p) const {
    if (!round) {
      const auto delta = sub(p, center);
      return {dot(delta, x), dot(delta, y)};
    }
    const auto delta = sub(p, xyz(mapping.frame.Location().XYZ()));
    const double theta = std::atan2(dot(delta, xyz(mapping.frame.YDirection().XYZ())),
                                    dot(delta, xyz(mapping.frame.XDirection().XYZ())));
    const double u = py_mod(theta - mapping.uv[0], 2.0 * pi) * mapping.radius;
    return {u, sign * (dot(delta, axis()) - mapping.uv[2])};
  }
};

Geometry affine(const Layout &layout, const GEOSGeometry *shape, std::array<double, 6> m) {
  return layout.own(GEOSGeom_transformXY_r(layout.context, shape, [](double *x, double *y, void *data) {
    const auto &[a, b, d, e, xoff, yoff] = *static_cast<const std::array<double, 6> *>(data);
    const double original_x = *x;
    *x = a * original_x + b * *y + xoff;
    *y = d * original_x + e * *y + yoff;
    return 1;
  }, &m));
}

/** `_target`: `_FlatTarget` or `_RoundTarget`. */
Target target_of(const Layout &layout, const Shape &face, const std::array<double, 12> &frame) {
  Target target;
  const auto plane = plane_frame(face);
  if (plane.planar) {
    for (int i = 0; i < 3; ++i) {
      target.center[i] = frame[i];
      target.x[i] = frame[3 + i];
      target.y[i] = frame[6 + i];
      target.normal[i] = frame[9 + i];
    }
    target.mapping = mapping_of(face);
    const auto profile = face_profile(layout, TopoDS::Face(face.shape), target.mapping, 0.035);
    const auto &position = target.mapping.frame;
    const V3 px = xyz(position.XDirection().XYZ()), py = xyz(position.YDirection().XYZ());
    const auto offset = sub(xyz(position.Location().XYZ()), target.center);
    const auto &u = target.x, &v = target.y;
    target.domain = affine(layout, profile.get(),
                           {dot(px, u), dot(py, u), dot(px, v), dot(py, v), dot(offset, u), dot(offset, v)});
    return target;
  }
  const auto cylinder = cylinder_data(face);
  if (!cylinder.cylindrical) {
    throw std::runtime_error(
        "Arrange needs a flat or cylindrical target face. Pick a flat face, or the round side of a cylinder or tube.");
  }
  target.round = true;
  target.mapping = mapping_of(face);
  target.internal = cylinder.internal;
  target.period = target.mapping.period();
  const auto [tangent, along, out] = target.raw_axes(0.0);
  target.sign = dot(cross(tangent, along), out) > 0 ? 1.0 : -1.0;
  auto domain = face_profile(layout, TopoDS::Face(face.shape), target.mapping);
  target.domain = target.sign > 0 ? std::move(domain) : affine(layout, domain.get(), {1, 0, 0, -1, 0, 0});
  return target;
}

/** shapely's `getattr(g, "geoms", [g])`, Polygons only. */
std::vector<const GEOSGeometry *> polygons_of(const Layout &layout, const GEOSGeometry *shape) {
  std::vector<const GEOSGeometry *> out;
  const int type = GEOSGeomTypeId_r(layout.context, shape);
  if (type == GEOS_POLYGON) {
    if (!layout.empty(shape)) out.push_back(shape);
    return out;
  }
  if (type == GEOS_MULTIPOLYGON || type == GEOS_GEOMETRYCOLLECTION) {
    for (int i = 0; i < GEOSGetNumGeometries_r(layout.context, shape); ++i) {
      const auto *part = GEOSGetGeometryN_r(layout.context, shape, i);
      if (GEOSGeomTypeId_r(layout.context, part) == GEOS_POLYGON && !layout.empty(part)) out.push_back(part);
    }
  }
  return out;
}

Geometry clone(const Layout &layout, const GEOSGeometry *shape) { return layout.own(GEOSGeom_clone_r(layout.context, shape)); }

Ring ring_of(const Layout &layout, const GEOSGeometry *ring) { return layout.coordinates(ring); }

/** `_outline_2d`: the shape seen along x × y, as one polygon. */
Geometry outline_2d(const Layout &layout, const TopoDS_Shape &shape, const V3 &origin, const V3 &x_axis,
                    const V3 &y_axis, double deflection) {
  // Mesh a copy: the document's shape may be shared with what is on screen (`tessellate.triangulate`).
  const auto copy = transformed(shape, translation({0.0, 0.0, 0.0}));
  BRepMesh_IncrementalMesh(copy, deflection, false, 0.35, true);
  std::map<std::array<double, 3>, int> index_of;
  std::vector<V3> vertices;
  std::vector<std::array<int, 3>> triangles;
  for (TopExp_Explorer explorer(copy, TopAbs_FACE); explorer.More(); explorer.Next()) {
    const TopoDS_Face face = TopoDS::Face(explorer.Current());
    TopLoc_Location location;
    const auto triangulation = BRep_Tool::Triangulation(face, location);
    if (triangulation.IsNull()) continue;
    const gp_Trsf transform = location.Transformation();
    const bool reversed = face.Orientation() == TopAbs_REVERSED;
    std::vector<int> local(triangulation->NbNodes() + 1);
    for (int node = 1; node <= triangulation->NbNodes(); ++node) {
      const gp_Pnt point = triangulation->Node(node).Transformed(transform);
      // Welded on a 1 nm grid, the first point seen at each keeping its coordinates.
      const std::array<double, 3> key{std::nearbyint(point.X() * 1e6), std::nearbyint(point.Y() * 1e6),
                                      std::nearbyint(point.Z() * 1e6)};
      const auto [at, added] = index_of.try_emplace(key, static_cast<int>(vertices.size()));
      if (added) vertices.push_back({point.X(), point.Y(), point.Z()});
      local[node] = at->second;
    }
    for (int index = 1; index <= triangulation->NbTriangles(); ++index) {
      int a, b, c;
      triangulation->Triangle(index).Get(a, b, c);
      if (reversed) std::swap(a, c);
      const int va = local[a], vb = local[b], vc = local[c];
      if (va != vb && vb != vc && va != vc) triangles.push_back({va, vb, vc});
    }
  }
  if (triangles.empty()) throw std::runtime_error("One of the objects has no surface to arrange by.");
  std::vector<Point> flat;
  flat.reserve(vertices.size());
  for (const auto &vertex : vertices) {
    const auto p = sub(vertex, origin);
    flat.push_back({dot(p, x_axis), dot(p, y_axis)});
  }
  const auto area = [&](const std::array<int, 3> &t) {
    const auto &a = flat[t[0]], &b = flat[t[1]], &c = flat[t[2]];
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
  };
  // For a closed surface the triangles facing the viewer already cover the whole silhouette.
  std::vector<Geometry> facing;
  for (const auto &t : triangles) {
    if (area(t) > 1e-10) facing.push_back(layout.polygon({flat[t[0]], flat[t[1]], flat[t[2]]}));
  }
  if (facing.empty()) {
    for (const auto &t : triangles) {
      if (std::abs(area(t)) > 1e-12) facing.push_back(layout.polygon({flat[t[0]], flat[t[1]], flat[t[2]]}));
    }
  }
  auto merged = layout.unite(std::move(facing));
  merged = layout.buffer(merged.get(), 0);
  merged = layout.own(GEOSTopologyPreserveSimplify_r(layout.context, merged.get(), deflection / 4.0));
  const auto parts = polygons_of(layout, merged.get());
  if (parts.empty()) throw std::runtime_error("One of the objects has no outline to arrange by.");
  if (parts.size() == 1) return clone(layout, parts[0]);
  // Separate shells (an imported mesh, say): their hull is a safe outline.
  const auto hull = layout.own(GEOSConvexHull_r(layout.context, merged.get()));
  return layout.polygon(ring_of(layout, GEOSGetExteriorRing_r(layout.context, hull.get())));
}

/** `_Seat`: a body moved to stand on z = 0 with its outline centred on the origin. */
struct Seat {
  gp_Trsf transform;
  Geometry footprint;
  double height = 0;
  bool warning = false;
};

Seat seat_of(const Layout &layout, const TopoDS_Shape &shape, const Target &target, int support, bool orient) {
  std::optional<PlaneFrame> info;
  if (orient && support >= 0) {
    const auto face = sub_face(Shape(shape), static_cast<uint32_t>(support));
    const auto plane = plane_frame(*face);
    if (plane.planar) info = plane;
  }
  Seat seat;
  gp_Trsf turn;
  std::optional<double> plane;
  if (info) {
    turn = rotation_between({info->normal[0], info->normal[1], info->normal[2]}, {0.0, 0.0, -1.0},
                            {info->center[0], info->center[1], info->center[2]});
    plane = info->center[2];
  } else {
    seat.warning = orient;
    const auto axes = target.axes();
    turn.SetTransformation(gp_Ax3(gp_Pnt(0, 0, 0), dir(axes[2]), dir(axes[0])));
  }
  const auto seated = transformed(shape, turn);
  const auto [low, high] = box_of(seated);
  if (!plane) plane = low[2];
  const double size = std::hypot(high[0] - low[0], high[1] - low[1], high[2] - low[2]);
  const double deflection = std::min(std::max(size / 200.0, 0.02), 0.5);
  const auto outline = outline_2d(layout, seated, {0, 0, 0}, {1, 0, 0}, {0, 1, 0}, deflection);
  const auto b = layout.bounds(outline.get());
  const double cx = (b[0] + b[2]) / 2.0, cy = (b[1] + b[3]) / 2.0;
  seat.transform = translation({-cx, -cy, -*plane}).Multiplied(turn);
  seat.footprint = layout.shift(outline.get(), {-cx, -cy});
  seat.height = high[2] - *plane;
  return seat;
}

/** `_edge_line`: a picked edge of the target face in its U/V. */
Geometry edge_line(const Layout &layout, const TopoDS_Shape &edge, const Target &target) {
  BRepAdaptor_Curve curve(TopoDS::Edge(edge));
  std::vector<gp_Pnt> points;
  GCPnts_QuasiUniformDeflection sampler(curve, 0.02);
  if (sampler.IsDone() && sampler.NbPoints() >= 2) {
    for (int i = 1; i <= sampler.NbPoints(); ++i) points.push_back(sampler.Value(i));
  } else {
    points = {curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter())};
  }
  Ring line;
  for (const auto &p : points) line.push_back(target.to_uv({p.X(), p.Y(), p.Z()}));
  return layout.line(line);
}

struct RegionOptions {
  bool manual = false;
  double margin = 0;
  std::array<double, 4> insets{};  // left, right, top, bottom
  bool avoid_holes = true;
};

/** `_region`: the usable part of the face, and the extent a pattern spreads across. */
std::pair<Geometry, std::optional<Bounds>> region_of(const Layout &layout, const Target &target,
                                                     const RegionOptions &options,
                                                     const std::vector<Geometry> &edges,
                                                     const std::vector<Geometry> &obstacles) {
  const auto parts = polygons_of(layout, target.domain.get());
  std::vector<Geometry> outlines, holes;
  for (const auto *part : parts) {
    outlines.push_back(layout.polygon(ring_of(layout, GEOSGetExteriorRing_r(layout.context, part))));
    for (int i = 0; i < GEOSGetNumInteriorRings_r(layout.context, part); ++i) {
      holes.push_back(layout.polygon(ring_of(layout, GEOSGetInteriorRingN_r(layout.context, part, i))));
    }
  }
  auto outer = layout.unite(std::move(outlines));
  const auto [lo_x, lo_y, hi_x, hi_y] = layout.bounds(target.domain.get());
  const double period = target.period;
  if (period) {
    // The seam is a parameter boundary, not an edge anything should keep off.
    std::vector<Geometry> copies;
    copies.push_back(clone(layout, outer.get()));
    for (const int k : {-1, 1}) copies.push_back(layout.shift(outer.get(), {k * period, 0.0}));
    outer = layout.unite(std::move(copies));
    const std::size_t count = holes.size();
    for (std::size_t h = 0; h < count; ++h) {
      for (const int k : {-1, 1}) holes.push_back(layout.shift(holes[h].get(), {k * period, 0.0}));
    }
  }
  const double margin = std::max(options.margin, 0.0);
  // Eight segments per quarter, scaled by 1/cos(pi/32) so the buffer circumscribes the true offset.
  const double clear = margin / std::cos(pi / 32);
  Geometry area;
  std::vector<Geometry> keep_off;
  if (options.manual) {
    const auto [left, right, top, bottom] = options.insets;
    const auto window = layout.rectangle(lo_x + left, lo_y + bottom, hi_x - right, hi_y - top);
    area = layout.intersection(outer.get(), window.get());
    if (!edges.empty() && margin > 0) {
      std::vector<Geometry> bands;
      for (const auto &line : edges) bands.push_back(layout.buffer(line.get(), clear, 8));
      const auto cut = layout.unite(std::move(bands));
      area = layout.own(GEOSDifference_r(layout.context, area.get(), cut.get()));
    }
    if (options.avoid_holes) {
      for (const auto &h : holes) keep_off.push_back(clone(layout, h.get()));
    }
  } else {
    area = margin > 0 ? layout.buffer(outer.get(), -margin, 16, GEOSBUF_CAP_ROUND, GEOSBUF_JOIN_MITRE, 5.0)
                      : clone(layout, outer.get());
    if (period) {
      const auto band = layout.rectangle(lo_x - period, lo_y + margin, hi_x + period, hi_y - margin);
      area = layout.intersection(area.get(), band.get());
    }
    if (options.avoid_holes) {
      for (const auto &h : holes) keep_off.push_back(margin > 0 ? layout.buffer(h.get(), clear, 8) : clone(layout, h.get()));
    }
  }
  if (!keep_off.empty()) {
    const auto cut = layout.unite(std::move(keep_off));
    area = layout.own(GEOSDifference_r(layout.context, area.get(), cut.get()));
  }
  if (!obstacles.empty()) {
    std::vector<Geometry> copies;
    for (const auto &o : obstacles) copies.push_back(clone(layout, o.get()));
    const auto cut = layout.unite(std::move(copies));
    area = layout.own(GEOSDifference_r(layout.context, area.get(), cut.get()));
  }
  // Round a full cylinder the pattern spans exactly one turn; along the axis it spans what the margins left.
  std::optional<Bounds> spread;
  if (period && !layout.empty(area.get())) {
    const auto b = layout.bounds(area.get());
    spread = Bounds{lo_x, b[1], lo_x + period, b[3]};
  }
  return {std::move(area), spread};
}

/** `_obstacles`: outlines of other bodies standing on or over a flat face. */
std::vector<Geometry> obstacles_of(const Layout &layout, const Target &target, const Shapes &bodies, double reach,
                                   double gap) {
  std::vector<Geometry> found;
  if (target.round) return found;  // ponytail: other objects are only kept clear of on flat faces, as 0.4
  const auto [lo_x, lo_y, hi_x, hi_y] = layout.bounds(target.domain.get());
  const auto near = layout.rectangle(lo_x - gap, lo_y - gap, hi_x + gap, hi_y + gap);
  for (const auto &shape : bodies.list) {
    const auto [low, high] = box_of(shape);
    std::vector<V3> corners;
    for (const double x : {low[0], high[0]}) {
      for (const double y : {low[1], high[1]}) {
        for (const double z : {low[2], high[2]}) corners.push_back({x, y, z});
      }
    }
    double top = -1e300, bottom = 1e300;
    double min_u = 1e300, max_u = -1e300, min_v = 1e300, max_v = -1e300;
    for (const auto &c : corners) {
      const double h = dot(sub(c, target.center), target.normal);
      top = std::max(top, h);
      bottom = std::min(bottom, h);
      const auto [u, v] = target.to_uv(c);
      min_u = std::min(min_u, u), max_u = std::max(max_u, u), min_v = std::min(min_v, v), max_v = std::max(max_v, v);
    }
    if (top < 1e-3 || bottom > reach) continue;
    const auto extent = layout.rectangle(min_u, min_v, max_u, max_v);
    if (GEOSIntersects_r(layout.context, extent.get(), near.get()) != 1) continue;
    const double size = std::hypot(high[0] - low[0], high[1] - low[1], high[2] - low[2]);
    const double deflection = std::min(std::max(size / 200.0, 0.02), 0.5);
    auto outline = outline_2d(layout, shape, target.center, target.x, target.y, deflection);
    found.push_back(gap > 0 ? layout.buffer(outline.get(), gap, 8) : std::move(outline));
  }
  return found;
}

/** `boundary_wires`: the usable region drawn just above the face, for the preview, as closed rings of points. */
void boundary_wires(const Layout &layout, const Target &target, const GEOSGeometry *region, ArrangeOutput &out) {
  Geometry clipped;
  if (target.period) {
    const auto b = layout.bounds(target.domain.get());
    const auto window = layout.rectangle(b[0], b[1], b[2], b[3]);
    clipped = layout.intersection(region, window.get());
    region = clipped.get();
  }
  constexpr double lift = 0.05;
  for (const auto *polygon : polygons_of(layout, region)) {
    std::vector<const GEOSGeometry *> rings{GEOSGetExteriorRing_r(layout.context, polygon)};
    for (int i = 0; i < GEOSGetNumInteriorRings_r(layout.context, polygon); ++i) {
      rings.push_back(GEOSGetInteriorRingN_r(layout.context, polygon, i));
    }
    for (const auto *ring : rings) {
      const auto coords = ring_of(layout, ring);
      uint32_t count = 0;
      const auto add = [&](const V3 &p) {
        for (const double c : p) out.boundary.push_back(c);
        ++count;
      };
      for (std::size_t i = 0; i + 1 < coords.size(); ++i) {
        const auto [u0, v0] = coords[i];
        const auto [u1, v1] = coords[i + 1];
        // Short steps, so a ring on a cylinder follows the surface.
        const int steps = target.round ? std::max(1, static_cast<int>(std::hypot(u1 - u0, v1 - v0) / 2.0)) : 1;
        for (int k = 0; k < steps; ++k) {
          const double t = static_cast<double>(k) / steps;
          add(target.point(u0 + (u1 - u0) * t, v0 + (v1 - v0) * t, lift));
        }
      }
      if (count) add(target.point(coords[0][0], coords[0][1], lift));
      out.boundary_rings.push_back(count);
    }
  }
}

Settings settings_of(const ArrangeSpec &spec, double period) {
  Settings s;
  s.pattern = std::string(spec.pattern);
  s.fixed = spec.fixed;
  s.gap = spec.gap;
  if (spec.has_gap_y) s.gap_y = spec.gap_y;
  s.direction = spec.direction;
  s.align = std::string(spec.align);
  s.rows = spec.rows;
  s.columns = spec.columns;
  s.fill_columns = spec.fill_columns;
  s.evenly = spec.evenly;
  s.rotation = spec.rotation;
  s.auto_rotate = spec.auto_rotate;
  s.radius = spec.radius;
  s.start_angle = spec.start_angle;
  s.face_center = spec.face_center;
  s.contain = spec.contain;
  if (period) s.period = period;
  return s;
}

} // namespace

std::unique_ptr<Shape> plate_face(double x, double y) {
  const gp_Pln plane(gp_Ax3(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0), gp_Dir(1.0, 0.0, 0.0)));
  BRepBuilderAPI_MakeFace maker(plane, 0.0, x, 0.0, y);
  if (!maker.IsDone()) throw std::runtime_error("The build plate could not be made.");
  return std::make_unique<Shape>(maker.Face());
}

ArrangeOutput arrange_face(const Shapes &items, rust::Slice<const int32_t> supports, const Shape &target_face,
                           const Shapes &edges, const Shapes &others, const ArrangeSpec &spec) {
  const Layout layout;
  const auto target = target_of(layout, target_face, spec.frame);
  const Settings settings = settings_of(spec, target.period);
  if (settings.pattern == "radial" && target.round) {
    throw std::runtime_error("A radial arrangement needs a flat face. Use Linear to go round a cylinder.");
  }
  std::vector<TopoDS_Shape> shapes(items.list.begin(), items.list.end());
  const std::size_t n = shapes.size();
  std::vector<Seat> seats;
  for (std::size_t i = 0; i < n; ++i) {
    seats.push_back(seat_of(layout, shapes[i], target, i < supports.size() ? supports[i] : -1, spec.orient));
  }
  std::vector<Geometry> footprints;
  for (const auto &seat : seats) {
    if (target.internal) {
      // Parts stood inside a bore lean together at the top; plan with the outline they have at their tips.
      const double r = target.mapping.radius;
      footprints.push_back(affine(layout, seat.footprint.get(), {r / std::max(r - seat.height, 1e-3), 0, 0, 1, 0, 0}));
    } else {
      footprints.push_back(clone(layout, seat.footprint.get()));
    }
  }

  // Keep the order the objects already have across the face, so a layout changes as little as it can.
  const double turn = settings.direction * (pi / 180);
  double tallest = 0;
  for (const auto &fp : footprints) {
    const auto b = layout.bounds(fp.get());
    tallest = std::max(tallest, b[3] - b[1]);
  }
  if (!tallest) tallest = 1.0;
  std::vector<std::pair<double, double>> keys;
  for (const auto &shape : shapes) {
    const auto [low, high] = box_of(shape);
    const auto [u, v] = target.to_uv({(low[0] + high[0]) / 2.0, (low[1] + high[1]) / 2.0, (low[2] + high[2]) / 2.0});
    const double along = u * std::cos(turn) + v * std::sin(turn);
    const double across = -u * std::sin(turn) + v * std::cos(turn);
    keys.push_back({settings.pattern == "linear" ? 0.0 : std::nearbyint(-across / tallest), along});
  }
  std::vector<uint32_t> order(n);
  for (std::size_t i = 0; i < n; ++i) order[i] = static_cast<uint32_t>(i);
  std::stable_sort(order.begin(), order.end(), [&](uint32_t a, uint32_t b) { return keys[a] < keys[b]; });

  std::vector<const GEOSGeometry *> planned;
  double reach = 0;
  for (const auto index : order) {
    planned.push_back(footprints[index].get());
    reach = std::max(reach, seats[index].height);
  }
  const double gap = settings.gap_y ? std::min(settings.gap, *settings.gap_y) : settings.gap;
  reach += spec.standoff + gap;
  const auto blockers = spec.avoid_bodies ? obstacles_of(layout, target, others, reach, gap) : std::vector<Geometry>{};
  std::vector<Geometry> lines;
  for (const auto &edge : edges.list) lines.push_back(edge_line(layout, edge, target));
  const RegionOptions options{spec.manual, spec.margin, spec.insets, spec.avoid_holes};
  auto [region, spread] = region_of(layout, target, options, lines, blockers);
  auto plan = arrange::plan(layout, planned, region.get(), settings, spread);
  const auto [du, dv, move_normal] = spec.offset;
  if (du || dv) {
    for (auto &p : plan.placements) p = {p[0] + du, p[1] + dv, p[2]};
    std::optional<std::array<double, 2>> wrap;
    if (settings.period) wrap = std::array<double, 2>{*settings.period, 0.0};
    plan.problems = arrange::check(layout, planned, plan.placements, region.get(), gap, settings.contain, wrap);
  }
  const double lift = move_normal + spec.standoff;

  ArrangeOutput out{};
  for (std::size_t slot = 0; slot < n; ++slot) {
    const auto index = order[slot];
    const auto &seat = seats[index];
    const auto [u, v, angle] = plan.placements[slot];
    double rise = lift;
    if (target.internal) {
      const double r = target.mapping.radius;
      const auto spun = layout.shift(seat.footprint.get(), {0.0, 0.0}, angle);
      double half = 0;
      for (const auto &[x, y] : ring_of(layout, GEOSGetExteriorRing_r(layout.context, spun.get()))) {
        half = std::max(half, std::abs(x));
      }
      if (half >= r) {
        plan.problems.try_emplace(static_cast<int>(slot), arrange::Problem{"outside", -1});
        half = r * 0.999;
      }
      // Drop the flat base until its corners, not its middle, touch the bore.
      rise += r - std::sqrt(r * r - half * half);
    }
    const auto axes = target.axes(u);
    gp_Trsf spin;
    spin.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), angle * (pi / 180));
    gp_Trsf move;
    move.SetDisplacement(gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1), gp_Dir(1, 0, 0)),
                         gp_Ax3(pnt(target.point(u, v, rise)), dir(axes[2]), dir(axes[0])));
    const auto placed = transformed(shapes[index], move.Multiplied(spin.Multiplied(seat.transform)));
    out.shapes.push_back(ImportedBody{rust::String(), std::make_unique<Shape>(placed), rust::String()});
    out.order.push_back(index);
    if (seat.warning) out.unseated.push_back(static_cast<uint32_t>(slot));
  }
  for (const auto &[slot, problem] : plan.problems) {
    out.problems.push_back(slot);
    out.problems.push_back(problem.kind == "outside" ? 0 : problem.kind == "overlap" ? 1 : 2);
    out.problems.push_back(problem.other);
  }
  out.gap = plan.gap;
  out.rows = plan.rows;
  out.columns = plan.columns;
  out.radius = plan.radius;

  // `face_axes`: U, V and the normal at the face's middle, for dragging the pattern.
  const auto middle = layout.own(GEOSGetCentroid_r(layout.context, target.domain.get()));
  double mx = 0;
  GEOSGeomGetX_r(layout.context, middle.get(), &mx);
  const auto axes = target.axes(mx);
  for (int a = 0; a < 3; ++a) {
    for (int i = 0; i < 3; ++i) out.axes[a * 3 + i] = axes[a][i];
  }

  if (spec.suggest && !plan.problems.empty()) {
    auto offers = arrange::alternatives(layout, planned, region.get(), settings, spread);
    std::vector<std::pair<std::string, std::vector<std::pair<std::string, std::string>>>> found;
    for (auto &offer : offers) found.push_back({offer.label, offer.patch});
    if (found.size() < 3 && !spec.manual && spec.margin > 0) {
      RegionOptions bare = options;
      bare.margin = 0;
      auto [bare_region, bare_spread] = region_of(layout, target, bare, lines, blockers);
      if (arrange::plan(layout, planned, bare_region.get(), settings, bare_spread).fits()) {
        found.push_back({"Remove the edge margin", {{"margin", "0"}}});
      }
    }
    for (const auto &[label, patch] : found) {
      ArrangeOffer offer;
      offer.label = rust::String(label);
      for (const auto &[key, value] : patch) {
        offer.keys.push_back(rust::String(key));
        offer.values.push_back(rust::String(value));
      }
      out.offers.push_back(std::move(offer));
    }
  }
  boundary_wires(layout, target, region.get(), out);
  return out;
}

} // namespace simplecad
