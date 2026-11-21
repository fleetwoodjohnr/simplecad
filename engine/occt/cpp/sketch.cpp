#include "shim.hpp"
#include "occt/src/lib.rs.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <vector>

#include <BRepAdaptor_CompCurve.hxx>
#include <BRepBuilderAPI_MakeEdge.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_Transform.hxx>
#include <BRepGProp.hxx>
#include <BRepOffsetAPI_MakePipeShell.hxx>
#include <BRepOffsetAPI_ThruSections.hxx>
#include <BRepPrimAPI_MakePrism.hxx>
#include <BRepPrimAPI_MakeRevol.hxx>
#include <BRep_Tool.hxx>
#include <BRepTools.hxx>
#include <GCPnts_AbscissaPoint.hxx>
#include <GProp_GProps.hxx>
#include <GeomAPI_PointsToBSpline.hxx>
#include <Geom_BSplineCurve.hxx>
#include <NCollection_Array1.hxx>
#include <ShapeAnalysis_FreeBounds.hxx>
#include <TopExp.hxx>
#include <TopoDS.hxx>
#include <gp_Ax1.hxx>
#include <gp_Ax2.hxx>
#include <gp_Circ.hxx>
#include <gp_Elips.hxx>
#include <gp_Vec.hxx>

namespace simplecad {
namespace {

constexpr double weld = 1e-6;
using Sequence = NCollection_HSequence<TopoDS_Shape>;

gp_XYZ xyz(const std::array<double, 3> &v) { return gp_XYZ(v[0], v[1], v[2]); }

TopoDS_Edge sketch_edge(const SketchEdge &edge, const SketchPlane &plane) {
  const gp_XYZ x = xyz(plane.x_axis), normal = xyz(plane.normal), y = normal.Crossed(x);
  const auto size = edge.points.size();
  if (size % 2 || size / 2 > std::numeric_limits<int>::max() ||
      (edge.curve == SketchCurve::Line && size != 4) ||
      (edge.curve != SketchCurve::Line && edge.curve != SketchCurve::Spline && size != 2)) {
    throw std::runtime_error("Invalid sketch curve coordinates.");
  }
  const auto point = [&](size_t index) {
    return gp_Pnt(xyz(plane.origin) + x * edge.points[2 * index] + y * edge.points[2 * index + 1]);
  };
  switch (edge.curve) {
  case SketchCurve::Line: {
    const gp_Pnt start = point(0), end = point(1);
    if (start.Distance(end) < weld) return {};
    return BRepBuilderAPI_MakeEdge(start, end).Edge();
  }
  case SketchCurve::Circle:
  case SketchCurve::Arc: {
    if (edge.parameters[0] <= 0.0) throw std::runtime_error("A sketch circle or arc needs a positive radius.");
    const gp_Circ circle(gp_Ax2(point(0), gp_Dir(normal), gp_Dir(x)), edge.parameters[0]);
    if (edge.curve == SketchCurve::Circle) return BRepBuilderAPI_MakeEdge(circle).Edge();
    return BRepBuilderAPI_MakeEdge(circle, edge.parameters[1], edge.parameters[2]).Edge();
  }
  case SketchCurve::Ellipse: {
    const double major = std::max(edge.parameters[0], edge.parameters[1]);
    const double minor = std::min(edge.parameters[0], edge.parameters[1]);
    if (minor <= 0.0) return {};
    const double rotation = edge.parameters[2];
    const gp_Ax2 axis(point(0), gp_Dir(normal), gp_Dir(x * std::cos(rotation) + y * std::sin(rotation)));
    return BRepBuilderAPI_MakeEdge(gp_Elips(axis, major, minor)).Edge();
  }
  case SketchCurve::Spline: {
    const auto count = static_cast<int>(edge.points.size() / 2);
    if (count < 2) return {};
    if (count == 2) return BRepBuilderAPI_MakeEdge(point(0), point(1)).Edge();
    NCollection_Array1<gp_Pnt> points(1, count);
    for (int i = 1; i <= count; ++i) points.SetValue(i, point(i - 1));
    GeomAPI_PointsToBSpline fit(points);
    if (fit.IsDone()) return BRepBuilderAPI_MakeEdge(fit.Curve()).Edge();
    return {};
  }
  }
  throw std::runtime_error("Unknown sketch curve.");
}

double area(const TopoDS_Face &face) {
  GProp_GProps properties;
  BRepGProp::SurfaceProperties(face, properties);
  return properties.Mass();
}

occ::handle<Sequence> connected_wires(const SketchProfile &sketch) {
  occ::handle<Sequence> segments = new Sequence();
  for (const SketchEdge &edge : sketch.edges) {
    const auto curve = sketch_edge(edge, sketch.plane);
    if (!curve.IsNull()) segments->Append(curve);
  }
  if (segments->IsEmpty()) return new Sequence();
  return ShapeAnalysis_FreeBounds::ConnectEdgesToWires(segments, weld, false);
}

bool closed(const TopoDS_Wire &wire) {
  TopoDS_Vertex first, last;
  TopExp::Vertices(wire, first, last);
  return first.IsNull() || last.IsNull() || BRep_Tool::Pnt(first).Distance(BRep_Tool::Pnt(last)) <= weld;
}

TopoDS_Wire path_wire(const SketchProfile &sketch, bool prefer_closed) {
  const auto wires = connected_wires(sketch);
  if (wires->IsEmpty()) throw std::runtime_error("The path sketch has no usable curve.");
  if (prefer_closed) {
    for (int i = 1; i <= wires->Length(); ++i) {
      const auto wire = TopoDS::Wire(wires->Value(i));
      if (closed(wire)) return wire;
    }
  }
  return TopoDS::Wire(wires->Value(1));
}

TopoDS_Face profile(const SketchProfile &sketch) {
  const auto wires = connected_wires(sketch);
  std::vector<std::pair<double, TopoDS_Wire>> loops;
  for (int i = 1; i <= wires->Length(); ++i) {
    const TopoDS_Wire wire = TopoDS::Wire(wires->Value(i));
    if (!closed(wire)) continue;
    BRepBuilderAPI_MakeFace face(wire, true);
    if (face.IsDone()) loops.emplace_back(area(face.Face()), wire);
  }
  if (loops.empty()) throw std::runtime_error("This sketch has no closed profile. Join the ends of its open edges.");
  std::stable_sort(loops.begin(), loops.end(), [](const auto &a, const auto &b) { return a.first > b.first; });

  TopoDS_Face face = BRepBuilderAPI_MakeFace(loops.front().second, true).Face();
  for (size_t i = 1; i < loops.size(); ++i) {
    const auto &wire = loops[i].second;
    BRepBuilderAPI_MakeFace with_hole(face);
    with_hole.Add(TopoDS::Wire(wire.Reversed()));
    if (with_hole.IsDone() && area(with_hole.Face()) <= area(face)) {
      face = with_hole.Face();
    } else {
      BRepBuilderAPI_MakeFace opposite(face);
      opposite.Add(wire);
      if (!opposite.IsDone()) throw std::runtime_error("A sketch loop could not be added to the profile.");
      face = opposite.Face();
    }
  }
  return face;
}

} // namespace

std::unique_ptr<Shape> extrude_sketch(const SketchProfile &sketch,
                                      double distance, bool symmetric) {
  const gp_Vec offset = gp_Vec(xyz(sketch.plane.normal)) * distance;
  TopoDS_Face face = profile(sketch);
  if (symmetric) {
    gp_Trsf shift;
    shift.SetTranslation(offset * -0.5);
    face = TopoDS::Face(BRepBuilderAPI_Transform(face, shift).Shape());
  }
  BRepPrimAPI_MakePrism extrude(face, offset);
  extrude.Build();
  if (!extrude.IsDone()) throw std::runtime_error("The sketch could not be extruded.");
  return unify(Shape(extrude.Shape()));
}

std::unique_ptr<Shape> revolve_sketch(const SketchProfile &sketch,
                                      const SketchAxis &axis, double radians) {
  const gp_Ax1 around(gp_Pnt(xyz(axis.origin)), gp_Dir(xyz(axis.direction)));
  BRepPrimAPI_MakeRevol revolve(profile(sketch), around, radians);
  revolve.Build();
  if (!revolve.IsDone()) throw std::runtime_error("The sketch could not be revolved.");
  return unify(Shape(revolve.Shape()));
}

std::unique_ptr<Shape> sweep_sketch(const SketchProfile &section, const SketchProfile &path,
                                    bool keep_orientation, bool contact) {
  BRepOffsetAPI_MakePipeShell sweep(path_wire(path, true));
  if (keep_orientation) sweep.SetMode(gp_Dir(xyz(section.plane.normal)));
  sweep.Add(BRepTools::OuterWire(profile(section)), contact, true);
  sweep.Build();
  if (!sweep.IsDone() || !sweep.MakeSolid()) {
    throw std::runtime_error("The profile could not be swept along that path. Try a gentler path or smaller profile.");
  }
  return unify(Shape(sweep.Shape()));
}

std::unique_ptr<Shape> loft_sketches(rust::Slice<const SketchProfile> sections, bool ruled) {
  if (sections.size() < 2) throw std::runtime_error("A loft needs at least two profiles.");
  BRepOffsetAPI_ThruSections loft(true, ruled);
  for (const auto &section : sections) loft.AddWire(BRepTools::OuterWire(profile(section)));
  loft.Build();
  if (!loft.IsDone()) throw std::runtime_error("These profiles could not be blended. Check that each profile is closed.");
  return unify(Shape(loft.Shape()));
}

rust::Vec<double> sketch_path_offsets(const SketchProfile &path, uint32_t count) {
  const BRepAdaptor_CompCurve curve(path_wire(path, false));
  const double total = GCPnts_AbscissaPoint::Length(curve);
  const auto start = curve.Value(curve.FirstParameter());
  rust::Vec<double> offsets;
  for (uint32_t index = 1; index < count; ++index) {
    const double distance = total * index / (count - 1);
    const GCPnts_AbscissaPoint locator(curve, distance, curve.FirstParameter());
    if (!locator.IsDone()) throw std::runtime_error("A copy could not be located on the pattern path.");
    const gp_XYZ offset = curve.Value(locator.Parameter()).XYZ() - start.XYZ();
    offsets.push_back(offset.X());
    offsets.push_back(offset.Y());
    offsets.push_back(offset.Z());
  }
  return offsets;
}

} // namespace simplecad
