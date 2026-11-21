#include "shim.hpp"
#include "texture_layout.hpp"

#include <algorithm>
#include <map>
#include <optional>
#include <string>
#include <utility>

#include <BRepAdaptor_Curve.hxx>
#include <BRepAdaptor_Curve2d.hxx>
#include <BRepAdaptor_Surface.hxx>
#include <BRepAlgoAPI_Common.hxx>
#include <BRepAlgoAPI_Cut.hxx>
#include <BRepAlgoAPI_Fuse.hxx>
#include <BRepBuilderAPI_MakeEdge.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_MakePolygon.hxx>
#include <BRepBuilderAPI_MakeSolid.hxx>
#include <BRepBuilderAPI_MakeWire.hxx>
#include <BRepBuilderAPI_Sewing.hxx>
#include <BRepPrimAPI_MakeCylinder.hxx>
#include <BRepPrimAPI_MakePrism.hxx>
#include <BRepTools.hxx>
#include <BRepTools_WireExplorer.hxx>
#include <BRep_Tool.hxx>
#include <GCPnts_QuasiUniformDeflection.hxx>
#include <ShapeFix_Solid.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS.hxx>
#include <gp_Circ.hxx>
#include <gp_Lin.hxx>

#include "occt/src/lib.rs.h"

namespace simplecad {
namespace {

using namespace texture;
constexpr int max_motifs = 4000;
constexpr double overlap = 0.02;

struct Mapping {
  gp_Ax3 frame;
  gp_Dir normal;
  double radius = 0;
  double sign = 1;
  Bounds uv{};

  bool wrapped() const { return radius > 0; }
  double period() const { return wrapped() && uv[1] - uv[0] >= 2 * pi - 1e-4 ? 2 * pi * radius : 0; }
  gp_Pnt axis_point(double t) const {
    return frame.Location().Translated(gp_Vec(frame.Direction()) * (uv[2] + t));
  }
  gp_Dir radial(double s) const {
    const double angle = uv[0] + s / radius;
    return gp_Dir(gp_Vec(frame.XDirection()) * std::cos(angle) +
                  gp_Vec(frame.YDirection()) * std::sin(angle));
  }
  gp_Pnt point(Point p, double depth) const {
    if (wrapped()) {
      const double angle = uv[0] + p[0] / radius;
      const auto offset = gp_Vec(frame.XDirection()) * std::cos(angle) +
                          gp_Vec(frame.YDirection()) * std::sin(angle);
      return axis_point(p[1]).Translated(offset * (radius + sign * depth));
    }
    return frame.Location().Translated(gp_Vec(frame.XDirection()) * p[0] +
        gp_Vec(frame.YDirection()) * p[1] + gp_Vec(normal) * depth);
  }
};

Mapping mapping_of(const Shape &face) {
  const auto plane = plane_frame(face);
  if (plane.planar) {
    const auto &n = plane.normal;
    const gp_Dir normal(n[0], n[1], n[2]);
    const auto surface = BRepAdaptor_Surface(TopoDS::Face(face.shape)).Plane();
    return {surface.Position(), normal, 0, 1, plane.uv};
  }
  const auto cylinder = cylinder_data(face);
  if (!cylinder.cylindrical) {
    throw std::runtime_error("Textures need a flat or cylindrical face.");
  }
  const auto surface = BRepAdaptor_Surface(TopoDS::Face(face.shape)).Cylinder();
  Mapping mapping{surface.Position(), surface.Axis().Direction(), surface.Radius(),
                  cylinder.internal ? -1.0 : 1.0, {}};
  BRepTools::UVBounds(TopoDS::Face(face.shape), mapping.uv[0], mapping.uv[1], mapping.uv[2], mapping.uv[3]);
  return mapping;
}

Ring planar_edge(const TopoDS_Edge &edge, const TopoDS_Vertex &start, const Mapping &mapping) {
  BRepAdaptor_Curve curve(edge);
  std::vector<gp_Pnt> samples;
  try {
    GCPnts_QuasiUniformDeflection sample(curve, 0.035);
    if (sample.IsDone()) {
      for (int i = 1; i <= sample.NbPoints(); ++i) samples.push_back(sample.Value(i));
    }
  } catch (const Standard_Failure &) {
    samples.clear();
  }
  if (samples.size() < 2) {
    samples = {curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter())};
  }
  const auto first = BRep_Tool::Pnt(start);
  if (samples.back().SquareDistance(first) < samples.front().SquareDistance(first)) {
    std::reverse(samples.begin(), samples.end());
  }
  Ring points;
  for (const auto &sample : samples) {
    const gp_Vec delta(mapping.frame.Location(), sample);
    points.push_back({delta.Dot(gp_Vec(mapping.frame.XDirection())),
                      delta.Dot(gp_Vec(mapping.frame.YDirection()))});
  }
  return points;
}

Ring cylindrical_edge(const TopoDS_Edge &edge, const TopoDS_Face &face, const Mapping &mapping) {
  const BRepAdaptor_Curve2d curve(edge, face);
  const double first = curve.FirstParameter();
  const double span = curve.LastParameter() - first;
  const int count = static_cast<int>(std::clamp(std::ceil(std::abs(span) * mapping.radius / 0.35), 2.0, 512.0));
  Ring points;
  for (int i = 0; i <= count; ++i) {
    const auto point = curve.Value(first + span * i / count);
    points.push_back({(point.X() - mapping.uv[0]) * mapping.radius, point.Y() - mapping.uv[2]});
  }
  if (edge.Orientation() == TopAbs_REVERSED) std::reverse(points.begin(), points.end());
  return points;
}

Geometry face_profile(const Layout &layout, const TopoDS_Face &face, const Mapping &mapping) {
  const double tolerance = mapping.wrapped() ? 1e-7 : 1e-8;
  std::vector<Ring> rings;
  for (TopExp_Explorer wire(face, TopAbs_WIRE); wire.More(); wire.Next()) {
    Ring points;
    for (BRepTools_WireExplorer edge(TopoDS::Wire(wire.Current()), face); edge.More(); edge.Next()) {
      auto samples = mapping.wrapped()
          ? cylindrical_edge(edge.Current(), face, mapping)
          : planar_edge(edge.Current(), edge.CurrentVertex(), mapping);
      if (mapping.wrapped() && !points.empty() &&
          distance(points.back(), samples.back()) < distance(points.back(), samples.front())) {
        std::reverse(samples.begin(), samples.end());
      }
      for (auto point : samples) {
        if (points.empty() || distance(points.back(), point) > tolerance) points.push_back(point);
      }
    }
    if (points.size() >= 3) {
      if (distance(points.front(), points.back()) > tolerance) points.push_back(points.front());
      else points.back() = points.front();
      rings.push_back(std::move(points));
    }
  }
  if (rings.empty()) {
    if (mapping.wrapped()) {
      return layout.rectangle(0, 0, (mapping.uv[1] - mapping.uv[0]) * mapping.radius,
                              mapping.uv[3] - mapping.uv[2]);
    }
    throw std::runtime_error("The selected face has no usable boundary.");
  }
  auto area = [](const Ring &ring) {
    double total = 0;
    for (std::size_t i = 1; i < ring.size(); ++i) {
      total += ring[i - 1][0] * ring[i][1] - ring[i][0] * ring[i - 1][1];
    }
    return std::abs(total);
  };
  std::stable_sort(rings.begin(), rings.end(), [&](const Ring &a, const Ring &b) { return area(a) > area(b); });
  auto profile = layout.polygon(rings.front(), {rings.begin() + 1, rings.end()});
  if (GEOSisValid_r(layout.context, profile.get()) != 1) profile = layout.buffer(profile.get(), 0);
  if (layout.polygons(profile.get()).empty()) {
    throw std::runtime_error("The selected face has no usable boundary.");
  }
  return profile;
}

std::vector<Geometry> strokes(const Layout &layout, const Bounds &bounds, const TextureSpec &spec,
                              const std::vector<int> &angles, double factor = 0.23, bool wave = false) {
  const double pitch = spec.size + spec.spacing;
  const double radius = std::hypot(bounds[2] - bounds[0], bounds[3] - bounds[1]) + pitch * 2;
  const double count = std::ceil(radius / pitch) + 1;
  if (count * angles.size() > max_motifs) {
    throw std::runtime_error("This texture is too dense for the selected face. Increase its size or spacing.");
  }
  const int n = static_cast<int>(count);
  const double width = std::max(0.08, spec.size * factor);
  std::vector<Geometry> shapes;
  for (int angle : angles) {
    for (int i = -n; i <= n; ++i) {
      const double y = i * pitch;
      Ring points{{-radius, y}, {radius, y}};
      if (wave) {
        const int samples = static_cast<int>(std::max(24.0, std::ceil(radius * 4 / std::max(spec.size, 0.5))));
        points.clear();
        for (int k = 0; k <= samples; ++k) {
          const double x = -radius + 2 * radius * k / samples;
          points.push_back({x, y + spec.size * 0.28 * std::sin(2 * pi * x / std::max(spec.size * 3, 0.1))});
        }
      }
      const auto centerline = layout.line(points);
      const auto line = layout.buffer(centerline.get(), width / 2, 16, GEOSBUF_CAP_FLAT, GEOSBUF_JOIN_MITRE);
      shapes.push_back(layout.shift(line.get(), {0, 0}, angle));
    }
  }
  return shapes;
}

Geometry repeated_motif(const Layout &layout, const std::string &pattern, Point p, double size) {
  const auto [x, y] = p;
  if (pattern == "dots" || pattern == "raised_bumps") {
    const auto center = layout.own(GEOSGeom_createPointFromXY_r(layout.context, x, y));
    return layout.buffer(center.get(), size / 2, 8);
  }
  if (pattern == "vent" || pattern == "grip") {
    const auto centerline = pattern == "vent"
        ? layout.line({{x - size * 0.35, y}, {x + size * 0.35, y}})
        : layout.line({{x - size / 2, y - size / 4}, {x, y + size / 4}, {x + size / 2, y - size / 4}});
    const double width = pattern == "vent" ? size * 0.15 : std::max(0.08, size * 0.12);
    return layout.buffer(centerline.get(), width, 4);
  }
  Ring points;
  if (pattern == "hexagon") {
    const double radius = size / std::sqrt(3.0);
    for (int i = 0; i < 6; ++i) {
      const double angle = pi / 6 + i * pi / 3;
      points.push_back({x + radius * std::cos(angle), y + radius * std::sin(angle)});
    }
  } else if (pattern == "diamond") {
    points = {{x, y + size / 2}, {x + size / 2, y}, {x, y - size / 2}, {x - size / 2, y}};
  } else {
    points = {{x, y + size / 2}, {x + size / 2, y - size / 2}, {x - size / 2, y - size / 2}};
  }
  const auto polygon = layout.polygon(points);
  const auto boundary = layout.own(GEOSBoundary_r(layout.context, polygon.get()));
  const auto factor = pattern == "diamond" ? 0.13 : 0.12;
  return layout.buffer(boundary.get(), std::max(0.08, size * factor) / 2, 16,
                       GEOSBUF_CAP_ROUND, GEOSBUF_JOIN_MITRE);
}

std::vector<Geometry> motifs(const Layout &layout, const std::string &pattern, const Bounds &bounds, const TextureSpec &spec) {
  if (pattern == "horizontal_lines") return strokes(layout, bounds, spec, {0});
  if (pattern == "vertical_lines") return strokes(layout, bounds, spec, {90});
  if (pattern == "diagonal_lines") return strokes(layout, bounds, spec, {45});
  if (pattern == "crosshatch") return strokes(layout, bounds, spec, {45, -45});
  if (pattern == "grid") return strokes(layout, bounds, spec, {0, 90});
  if (pattern == "knurling") return strokes(layout, bounds, spec, {45, -45}, 0.38);
  if (pattern == "ribbed") return strokes(layout, bounds, spec, {0}, 0.55);
  if (pattern == "wave") return strokes(layout, bounds, spec, {0}, 0.23, true);
  if (pattern == "recessed_grooves") return strokes(layout, bounds, spec, {0}, 0.18);
  const double pitch = spec.size + spec.spacing;
  const double reach = std::max({std::abs(bounds[0]), std::abs(bounds[1]), std::abs(bounds[2]), std::abs(bounds[3])});
  const double count = std::ceil((reach + pitch * 3) / pitch);
  if ((2 * count + 1) * (2 * count + 1) > max_motifs) {
    throw std::runtime_error("This texture is too dense for the selected face. Increase its size or spacing.");
  }
  const int n = static_cast<int>(count);
  std::vector<Geometry> shapes;
  for (int row = -n; row <= n; ++row) {
    const double shift = (pattern == "hexagon" || pattern == "vent") && row % 2 != 0 ? pitch / 2 : 0;
    for (int column = -n; column <= n; ++column) {
      shapes.push_back(repeated_motif(layout, pattern, {column * pitch + shift, row * pitch}, spec.size));
    }
  }
  return shapes;
}

Geometry periodic_union(const Layout &layout, Geometry region, double period) {
  if (period == 0) return region;
  std::vector<Geometry> copies;
  copies.push_back(layout.shift(region.get(), {period, 0}));
  copies.push_back(layout.shift(region.get(), {-period, 0}));
  copies.insert(copies.begin(), std::move(region));
  return layout.unite(std::move(copies));
}

Geometry clipped_layout(const Layout &layout, const GEOSGeometry *domain, double period,
                        const std::string &pattern, const TextureSpec &spec,
                        rust::Slice<const TextureRegion> text_regions) {
  const auto context = layout.context;
  const auto domain_bounds = layout.bounds(domain);
  std::vector<Geometry> shells, protected_holes, labels;
  for (const auto &region : text_regions) {
    Ring ring;
    for (std::size_t i = 0; i + 1 < region.outline.size(); i += 2) {
      ring.push_back({region.outline[i], region.outline[i + 1]});
    }
    if (ring.size() >= 3) labels.push_back(layout.polygon(ring));
  }
  for (const auto *part : layout.polygons(domain)) {
    shells.push_back(layout.polygon(layout.coordinates(GEOSGetExteriorRing_r(context, part))));
    for (int i = 0; i < GEOSGetNumInteriorRings_r(context, part); ++i) {
      const auto *ring = GEOSGetInteriorRingN_r(context, part, i);
      const auto hole = layout.polygon(layout.coordinates(ring));
      const double area = layout.area(hole.get());
      if (area < 1e-8) continue;
      double perimeter = 0;
      GEOSLength_r(context, ring, &perimeter);
      const bool touches_text = std::any_of(labels.begin(), labels.end(), [&](const Geometry &label) {
        return GEOSIntersects_r(context, label.get(), hole.get()) == 1;
      });
      const bool round_hole = 4 * pi * area / std::max(perimeter * perimeter, 1e-12) > 0.87;
      const double clearance = touches_text ? spec.text_clearance
          : round_hole ? spec.hole_clearance : spec.other_clearance;
      protected_holes.push_back(layout.buffer(hole.get(), clearance / std::cos(pi / 32), 8));
    }
  }
  auto exterior = periodic_union(layout, layout.unite(std::move(shells)), period);
  auto allowed = layout.buffer(exterior.get(), -spec.edge_clearance, 16, GEOSBUF_CAP_ROUND, GEOSBUF_JOIN_MITRE);
  if (period > 0) {
    const auto window = layout.rectangle(domain_bounds[0], domain_bounds[1] - spec.edge_clearance,
                                          domain_bounds[2], domain_bounds[3] + spec.edge_clearance);
    allowed = layout.intersection(allowed.get(), window.get());
  }
  if (layout.empty(allowed.get())) throw std::runtime_error("The edge clearance leaves no room for a texture.");
  if (!protected_holes.empty()) {
    const auto exclusions = periodic_union(layout, layout.unite(std::move(protected_holes)), period);
    allowed = layout.own(GEOSDifference_r(context, allowed.get(), exclusions.get()));
  }
  if (layout.empty(allowed.get())) throw std::runtime_error("The selected clearances leave no room for a texture.");
  const auto a = layout.bounds(allowed.get());
  const Point center{(a[0] + a[2]) / 2, (a[1] + a[3]) / 2};
  auto shapes = motifs(layout, pattern,
      {a[0] - center[0], a[1] - center[1], a[2] - center[0], a[3] - center[1]}, spec);
  for (auto &shape : shapes) {
    shape = layout.shift(shape.get(), {center[0] + spec.offset_u, center[1] + spec.offset_v}, spec.rotation);
  }
  auto raw = layout.unite(std::move(shapes));
  if (period > 0) {
    const auto [left, bottom, right, top] = domain_bounds;
    const double reach = std::max(spec.size * 2, spec.spacing);
    const auto left_window = layout.rectangle(left - reach, bottom - reach, left, top + reach);
    const auto right_window = layout.rectangle(right, bottom - reach, right + reach, top + reach);
    auto crossing_left = layout.intersection(raw.get(), left_window.get());
    auto crossing_right = layout.intersection(raw.get(), right_window.get());
    std::vector<Geometry> pieces;
    pieces.push_back(std::move(raw));
    pieces.push_back(layout.shift(crossing_left.get(), {period, 0}));
    pieces.push_back(layout.shift(crossing_right.get(), {-period, 0}));
    raw = layout.unite(std::move(pieces));
  }
  auto result = layout.intersection(raw.get(), allowed.get());
  if (layout.polygons(result.get()).empty()) {
    throw std::runtime_error("No pattern elements fit inside the clearances. Reduce spacing or clearance, or change the offset.");
  }
  return result;
}

struct Circle {
  Point center;
  double radius;
};

std::optional<Circle> circle_of(const Layout &layout, const GEOSGeometry *polygon) {
  if (GEOSGetNumInteriorRings_r(layout.context, polygon) != 0) return std::nullopt;
  auto ring = layout.coordinates(GEOSGetExteriorRing_r(layout.context, polygon));
  if (ring.size() < 25) return std::nullopt;
  ring.pop_back();
  const auto centroid = layout.own(GEOSGetCentroid_r(layout.context, polygon));
  const Point center = layout.coordinates(centroid.get()).front();
  double high = 0, low = INFINITY;
  for (auto point : ring) {
    const double d = distance(point, center);
    high = std::max(high, d);
    low = std::min(low, d);
  }
  if (high <= 0 || low < high * 0.995) return std::nullopt;
  return Circle{center, high};
}

TopoDS_Wire mapped_ring(const Layout &layout, const GEOSGeometry *ring, const Mapping &mapping, double offset) {
  auto points = layout.coordinates(ring);
  points.pop_back();
  BRepBuilderAPI_MakePolygon wire;
  for (auto point : points) wire.Add(mapping.point(point, offset));
  wire.Close();
  return wire.Wire();
}

template <class Operation>
TopoDS_Shape texture_boolean(const TopoDS_Shape &body, const TopoDS_Shape &tool) {
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
    throw std::runtime_error("The face texture boolean could not finish.");
  }
  return operation.Shape();
}

std::unique_ptr<Shape> planar_tools(const Layout &layout, const GEOSGeometry *motifs, const Mapping &mapping,
                                    double start, double depth) {
  auto tools = shapes_new();
  for (const auto *polygon : layout.polygons(motifs)) {
    TopoDS_Wire outside;
    if (const auto circle = circle_of(layout, polygon)) {
      const gp_Ax2 frame(mapping.point(circle->center, start), mapping.normal);
      const auto edge = BRepBuilderAPI_MakeEdge(gp_Circ(frame, circle->radius)).Edge();
      outside = BRepBuilderAPI_MakeWire(edge).Wire();
    } else {
      outside = mapped_ring(layout, GEOSGetExteriorRing_r(layout.context, polygon), mapping, start);
    }
    BRepBuilderAPI_MakeFace face(outside);
    for (int i = 0; i < GEOSGetNumInteriorRings_r(layout.context, polygon); ++i) {
      face.Add(mapped_ring(layout, GEOSGetInteriorRingN_r(layout.context, polygon, i), mapping, start));
    }
    if (!face.IsDone()) throw std::runtime_error("A texture element could not be built.");
    const auto prism = BRepPrimAPI_MakePrism(face.Face(), gp_Vec(mapping.normal) * depth).Shape();
    shapes_push(*tools, Shape(prism));
  }
  return shapes_compound(*tools);
}

TopoDS_Face triangle(const gp_Pnt &a, const gp_Pnt &b, const gp_Pnt &c) {
  const auto wire = BRepBuilderAPI_MakePolygon(a, b, c, true).Wire();
  return BRepBuilderAPI_MakeFace(wire).Face();
}

TopoDS_Solid wrapped_motif(const Layout &layout, const GEOSGeometry *polygon, const Mapping &mapping,
                          double outer, double inner, double step) {
  BRepBuilderAPI_Sewing sewing(1e-5);
  const auto b = layout.bounds(polygon);
  const auto first_strip = static_cast<int64_t>(std::floor(b[0] / step));
  const auto last_strip = static_cast<int64_t>(std::ceil(b[2] / step));
  struct Segment { Point a; Point b; int occurrences = 1; };
  std::vector<Segment> boundary;
  std::map<std::pair<Point, Point>, std::size_t> keys;
  auto rounded = [](Point p) {
    for (auto &value : p) value = std::nearbyint(value * 1e7) / 1e7;
    return p;
  };
  for (auto strip = first_strip; strip < last_strip; ++strip) {
    const auto window = layout.rectangle(strip * step, b[1] - 0.1, (strip + 1) * step, b[3] + 0.1);
    const auto cut = layout.intersection(polygon, window.get());
    for (const auto *piece : layout.polygons(cut.get())) {
      const auto facets = layout.own(GEOSConstrainedDelaunayTriangulation_r(layout.context, piece));
      for (const auto *facet : layout.polygons(facets.get(), 1e-9)) {
        const auto points = layout.coordinates(GEOSGetExteriorRing_r(layout.context, facet));
        sewing.Add(triangle(mapping.point(points[0], outer), mapping.point(points[1], outer), mapping.point(points[2], outer)));
        sewing.Add(triangle(mapping.point(points[2], inner), mapping.point(points[1], inner), mapping.point(points[0], inner)));
        for (int i = 0; i < 3; ++i) {
          const Point a = points[i], b = points[(i + 1) % 3];
          auto first = rounded(a), last = rounded(b);
          if (last < first) std::swap(first, last);
          const auto [found, added] = keys.emplace(std::make_pair(first, last), boundary.size());
          if (added) boundary.push_back({a, b});
          else ++boundary[found->second].occurrences;
        }
      }
    }
  }
  for (const auto &edge : boundary) {
    if (edge.occurrences != 1 || distance(edge.a, edge.b) < 1e-8) continue;
    const auto a = mapping.point(edge.a, inner), b = mapping.point(edge.b, inner);
    const auto c = mapping.point(edge.b, outer), d = mapping.point(edge.a, outer);
    sewing.Add(triangle(a, b, c));
    sewing.Add(triangle(a, c, d));
  }
  sewing.Perform();
  const auto shape = sewing.SewedShape();
  if (shape.IsNull() || shape.ShapeType() != TopAbs_SHELL) {
    throw std::runtime_error("A wrapped texture element did not form a shell.");
  }
  ShapeFix_Solid solid(BRepBuilderAPI_MakeSolid(TopoDS::Shell(shape)).Solid());
  solid.Perform();
  return TopoDS::Solid(solid.Solid());
}

TopoDS_Shape radial_dots(const std::vector<Circle> &circles, const Mapping &mapping, double outer, double inner) {
  const double first = mapping.radius + mapping.sign * outer;
  const double second = mapping.radius + mapping.sign * inner;
  const double small = std::min(first, second), large = std::max(first, second);
  double low = INFINITY, high = -INFINITY;
  for (const auto &circle : circles) {
    low = std::min(low, circle.center[1] - circle.radius);
    high = std::max(high, circle.center[1] + circle.radius);
  }
  low -= 1;
  high += 1;
  const gp_Ax2 frame(mapping.axis_point(low), mapping.frame.Direction());
  const auto shell = texture_boolean<BRepAlgoAPI_Cut>(
      BRepPrimAPI_MakeCylinder(frame, large, high - low).Shape(),
      BRepPrimAPI_MakeCylinder(frame, small, high - low).Shape());
  auto rods = shapes_new();
  for (const auto &dot : circles) {
    const gp_Ax2 frame(mapping.axis_point(dot.center[1]), mapping.radial(dot.center[0]));
    shapes_push(*rods, Shape(BRepPrimAPI_MakeCylinder(frame, dot.radius, large + 1).Shape()));
  }
  return texture_boolean<BRepAlgoAPI_Common>(shapes_compound(*rods)->shape, shell);
}

std::unique_ptr<Shape> wrapped_tools(const Layout &layout, const GEOSGeometry *motifs, const Mapping &mapping,
                                    double outer, double inner) {
  if (mapping.radius + mapping.sign * outer <= 0 || mapping.radius + mapping.sign * inner <= 0) {
    throw std::runtime_error("The texture depth reaches the cylinder axis.");
  }
  const double step = std::clamp(std::sqrt(8 * mapping.radius * 0.05), 0.25, 2.0);
  auto tools = shapes_new();
  std::vector<Circle> dots;
  const auto parts = layout.polygons(motifs, 1e-7);
  if (parts.size() > max_motifs) throw std::runtime_error("This wrapped texture is too dense.");
  for (const auto *motif : parts) {
    if (const auto circle = circle_of(layout, motif)) dots.push_back(*circle);
    else shapes_push(*tools, Shape(wrapped_motif(layout, motif, mapping, outer, inner, step)));
  }
  if (!dots.empty()) shapes_push(*tools, Shape(radial_dots(dots, mapping, outer, inner)));
  return shapes_compound(*tools);
}

double opposite_wall(const Shape &body, const Mapping &mapping) {
  double wall = INFINITY;
  for (TopExp_Explorer faces(body.shape, TopAbs_FACE); faces.More(); faces.Next()) {
    const auto surface = cylinder_data(Shape(faces.Current()));
    if (!surface.cylindrical || surface.internal == (mapping.sign < 0)) continue;
    const auto &d = surface.direction;
    if (std::abs(gp_Vec(d[0], d[1], d[2]).Dot(gp_Vec(mapping.frame.Direction()))) < 0.999) continue;
    const auto &o = surface.origin;
    if (gp_Lin(mapping.frame.Axis()).Distance(gp_Pnt(o[0], o[1], o[2])) > 1e-3) continue;
    const auto difference = (mapping.radius - surface.radius) * mapping.sign;
    if (difference > 0.05) wall = std::min(wall, difference);
  }
  if (!std::isfinite(wall)) {
    throw std::runtime_error("Through vents need an identifiable opposite cylindrical wall.");
  }
  return wall;
}

} // namespace

texture::Geometry texture::planar_profile(const Layout &layout, const Shape &face) {
  const auto mapping = mapping_of(face);
  if (mapping.wrapped()) throw std::runtime_error("Select a planar face.");
  return face_profile(layout, TopoDS::Face(face.shape), mapping);
}

std::unique_ptr<Shape> face_texture(const Shape &body, const Shape &face, rust::Str pattern_name,
                                    rust::Str mode_name, const TextureSpec &spec,
                                    rust::Slice<const TextureRegion> text_regions) {
  const std::string pattern(pattern_name), mode(mode_name);
  const bool raised = mode == "raised";
  const bool through = mode == "through";
  const auto mapping = mapping_of(face);
  const Layout layout;
  const auto domain = face_profile(layout, TopoDS::Face(face.shape), mapping);
  const auto pattern_layout = clipped_layout(layout, domain.get(), mapping.period(), pattern, spec, text_regions);
  std::unique_ptr<Shape> tools;
  if (mapping.wrapped()) {
    const double inner = through ? -opposite_wall(body, mapping) - 0.08 : raised ? -0.08 : -spec.depth;
    tools = wrapped_tools(layout, pattern_layout.get(), mapping, raised ? spec.depth : 0.08, inner);
  } else {
    double depth = raised ? spec.depth + overlap : -(spec.depth + overlap);
    if (through) {
      const auto b = bounds(body);
      depth = -(2 * gp_Pnt(b[0], b[1], b[2]).Distance(gp_Pnt(b[3], b[4], b[5])) + 1);
    }
    tools = planar_tools(layout, pattern_layout.get(), mapping, raised ? -overlap : overlap, depth);
  }
  if (mode == "recessed") {
    const auto probe = mapping.wrapped()
        ? wrapped_tools(layout, pattern_layout.get(), mapping, -spec.depth - 0.005, -spec.depth - 0.02)
        : planar_tools(layout, pattern_layout.get(), mapping, -spec.depth - 0.005, -0.01);
    const Shape inside(texture_boolean<BRepAlgoAPI_Common>(body.shape, probe->shape));
    if (volume(inside) < volume(*probe) * 0.995) {
      throw std::runtime_error("The recessed texture is deeper than the material below it.");
    }
  }
  auto result = std::make_unique<Shape>(raised
      ? texture_boolean<BRepAlgoAPI_Fuse>(body.shape, tools->shape)
      : texture_boolean<BRepAlgoAPI_Cut>(body.shape, tools->shape));
  if (!mapping.wrapped()) result = unify(*result);
  const auto solids = solid_count(*result);
  if (!is_valid(*result) || solids == 0 || (raised && solids != solid_count(body)) ||
      std::abs(volume(*result) - volume(body)) < 1e-7) {
    throw std::runtime_error("The texture did not produce a valid printable solid. Try a larger size or smaller depth.");
  }
  return result;
}

} // namespace simplecad
