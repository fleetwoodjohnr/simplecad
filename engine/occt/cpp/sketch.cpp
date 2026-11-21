#include "shim.hpp"
#include "occt/src/lib.rs.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <vector>

#include <BRepBuilderAPI_MakeEdge.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_Transform.hxx>
#include <BRepGProp.hxx>
#include <BRepPrimAPI_MakePrism.hxx>
#include <BRepPrimAPI_MakeRevol.hxx>
#include <BRep_Tool.hxx>
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

TopoDS_Face profile(const SketchPlane &plane, rust::Slice<const SketchEdge> edges) {
  occ::handle<Sequence> segments = new Sequence();
  for (const SketchEdge &edge : edges) {
    const auto curve = sketch_edge(edge, plane);
    if (!curve.IsNull()) segments->Append(curve);
  }
  if (segments->IsEmpty()) throw std::runtime_error("This sketch has no closed profile.");
  const auto wires = ShapeAnalysis_FreeBounds::ConnectEdgesToWires(segments, weld, false);
  std::vector<std::pair<double, TopoDS_Wire>> loops;
  for (int i = 1; i <= wires->Length(); ++i) {
    const TopoDS_Wire wire = TopoDS::Wire(wires->Value(i));
    TopoDS_Vertex first, last;
    TopExp::Vertices(wire, first, last);
    if (!first.IsNull() && !last.IsNull() && BRep_Tool::Pnt(first).Distance(BRep_Tool::Pnt(last)) > weld) continue;
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

std::unique_ptr<Shape> extrude_sketch(const SketchPlane &plane, rust::Slice<const SketchEdge> edges,
                                      double distance, bool symmetric) {
  const gp_Vec offset = gp_Vec(xyz(plane.normal)) * distance;
  TopoDS_Face face = profile(plane, edges);
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

std::unique_ptr<Shape> revolve_sketch(const SketchPlane &plane, rust::Slice<const SketchEdge> edges,
                                      const SketchAxis &axis, double radians) {
  const gp_Ax1 around(gp_Pnt(xyz(axis.origin)), gp_Dir(xyz(axis.direction)));
  BRepPrimAPI_MakeRevol revolve(profile(plane, edges), around, radians);
  revolve.Build();
  if (!revolve.IsDone()) throw std::runtime_error("The sketch could not be revolved.");
  return unify(Shape(revolve.Shape()));
}

} // namespace simplecad
