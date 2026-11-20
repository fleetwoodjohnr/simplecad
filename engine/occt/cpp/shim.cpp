#include "shim.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <limits>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <tuple>
#include <vector>

#include <BRepAdaptor_Curve.hxx>
#include <BRepAdaptor_Surface.hxx>
#include <BRepAlgoAPI_Common.hxx>
#include <BRepAlgoAPI_Cut.hxx>
#include <BRepAlgoAPI_Fuse.hxx>
#include <BRepAlgoAPI_Splitter.hxx>
#include <BRepBndLib.hxx>
#include <BRepBuilderAPI_GTransform.hxx>
#include <BRepBuilderAPI_MakeEdge.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_MakePolygon.hxx>
#include <BRepBuilderAPI_MakeWire.hxx>
#include <BRepBuilderAPI_Transform.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRepExtrema_DistShapeShape.hxx>
#include <BRepFilletAPI_MakeChamfer.hxx>
#include <BRepFilletAPI_MakeFillet.hxx>
#include <BRepGProp.hxx>
#include <BRepIntCurveSurface_Inter.hxx>
#include <BRepLProp_SLProps.hxx>
#include <BRepLib_ToolTriangulatedShape.hxx>
#include <BRepMesh_IncrementalMesh.hxx>
#include <BRepOffsetAPI_MakeThickSolid.hxx>
#include <BRepPrimAPI_MakeBox.hxx>
#include <BRepPrimAPI_MakeCone.hxx>
#include <BRepPrimAPI_MakeCylinder.hxx>
#include <BRepPrimAPI_MakeSphere.hxx>
#include <BRepPrimAPI_MakeTorus.hxx>
#include <BRepPrimAPI_MakeWedge.hxx>
#include <TopTools_ListOfShape.hxx>
#include <BRepPrimAPI_MakePrism.hxx>
#include <BRepTools.hxx>
#include <BRep_Builder.hxx>
#include <BRep_Tool.hxx>
#include <Bnd_Box.hxx>
#include <GCPnts_TangentialDeflection.hxx>
#include <GC_MakeArcOfCircle.hxx>
#include <GC_MakeSegment.hxx>
#include <GProp_GProps.hxx>
#include <GeomAbs_SurfaceType.hxx>
#include <Geom_TrimmedCurve.hxx>
#include <NCollection_IndexedDataMap.hxx>
#include <NCollection_IndexedMap.hxx>
#include <NCollection_List.hxx>
#include <Poly_Connect.hxx>
#include <Poly_Triangulation.hxx>
#include <ShapeAnalysis_ShapeTolerance.hxx>
#include <ShapeUpgrade_UnifySameDomain.hxx>
#include <Standard_Failure.hxx>
#include <TopExp.hxx>
#include <TopExp_Explorer.hxx>
#include <TopTools_ShapeMapHasher.hxx>
#include <TopoDS.hxx>
#include <TopLoc_Location.hxx>
#include <TopoDS_Compound.hxx>
#include <gp_Ax1.hxx>
#include <gp_Ax2.hxx>
#include <gp_Ax3.hxx>
#include <gp_Dir.hxx>
#include <gp_Elips.hxx>
#include <gp_GTrsf.hxx>
#include <gp_Lin.hxx>
#include <gp_Mat.hxx>
#include <gp_Pln.hxx>
#include <gp_Pnt.hxx>
#include <gp_Sphere.hxx>
#include <gp_Torus.hxx>
#include <gp_Cone.hxx>
#include <gp_Cylinder.hxx>
#include <gp_Trsf.hxx>
#include <gp_Vec.hxx>

#include "occt/src/lib.rs.h"

namespace simplecad {

namespace {

using ShapeMap = NCollection_IndexedMap<TopoDS_Shape, TopTools_ShapeMapHasher>;

std::unique_ptr<Shape> owned(const TopoDS_Shape &shape, const char *operation) {
  if (shape.IsNull()) {
    throw std::runtime_error(std::string(operation) + " produced no shape");
  }
  return std::make_unique<Shape>(shape);
}

using ShapeList = NCollection_List<TopoDS_Shape>;
using AncestorMap = NCollection_IndexedDataMap<TopoDS_Shape, ShapeList, TopTools_ShapeMapHasher>;

// Push/pull is a port of PushPullFeature in simplecad/kernel/operations.py;
// the constants, the order of the booleans and the messages are the same, so
// both engines give the same solid for the same face and distance.

// How far below the deepest blind floor a carrying push/pull cuts the face's column.
constexpr double kSectionClearance = 0.01;
// Smaller than this along the normal is not a recess or a boss.
constexpr double kFeatureTolerance = 0.05;
constexpr const char *kPullFailed =
    "The pull operation did not succeed on this geometry. "
    "Try adjusting the inputs slightly, or undo and take a different approach.";

struct Plane {
  gp_XYZ center;
  gp_XYZ normal;
  double area;
};

// A planar face's centre, outward normal and area; nothing for a curved face.
std::optional<Plane> analyse_plane(const TopoDS_Face &face) {
  BRepAdaptor_Surface adaptor(face);
  if (adaptor.GetType() != GeomAbs_Plane) {
    return std::nullopt;
  }
  GProp_GProps props;
  BRepGProp::SurfaceProperties(face, props);
  const gp_Pln plane = adaptor.Plane();
  gp_XYZ normal = plane.Axis().Direction().XYZ();
  // A mirrored body has left-handed planes, whose axis points into the part.
  if (!plane.Position().Direct()) {
    normal.Reverse();
  }
  if (face.Orientation() == TopAbs_REVERSED) {
    normal.Reverse();
  }
  return Plane{props.CentreOfMass().XYZ(), normal, props.Mass()};
}

double offset(const gp_XYZ &point, const gp_XYZ &origin, const gp_XYZ &normal) {
  return (point - origin).Dot(normal);
}

// OCCT's optimal bounding box (the cheap one overshoots on B-splines), as (min, max).
std::pair<gp_XYZ, gp_XYZ> corners(const TopoDS_Shape &shape) {
  Bnd_Box box;
  try {
    BRepBndLib::AddOptimal(shape, box, true, true);
  } catch (const Standard_Failure &) {
    box = Bnd_Box();
    BRepBndLib::Add(shape, box, true);
  }
  if (box.IsVoid()) {
    return {gp_XYZ(0, 0, 0), gp_XYZ(0, 0, 0)};
  }
  return {box.CornerMin().XYZ(), box.CornerMax().XYZ()};
}

// The offsets *shape* covers along *normal*, from its bounding box.
std::pair<double, double> span(const TopoDS_Shape &shape, const gp_XYZ &origin, const gp_XYZ &normal) {
  const auto [low, high] = corners(shape);
  double least = std::numeric_limits<double>::infinity();
  double most = -least;
  for (double x : {low.X(), high.X()}) {
    for (double y : {low.Y(), high.Y()}) {
      for (double z : {low.Z(), high.Z()}) {
        const double o = offset(gp_XYZ(x, y, z), origin, normal);
        least = std::min(least, o);
        most = std::max(most, o);
      }
    }
  }
  return {least, most};
}

// How far *shape* reaches along *direction*, from its bounding box.
double depth_along(const TopoDS_Shape &shape, const gp_XYZ &direction) {
  const auto [low, high] = corners(shape);
  double depth = 0;
  for (int i = 1; i <= 3; ++i) {
    depth += std::abs((high.Coord(i) - low.Coord(i)) * direction.Coord(i));
  }
  return depth;
}

uint32_t count_solids(const TopoDS_Shape &shape) {
  uint32_t count = 0;
  for (TopExp_Explorer e(shape, TopAbs_SOLID); e.More(); e.Next()) {
    ++count;
  }
  return count;
}

template <class Operation>
TopoDS_Shape boolean(const TopoDS_Shape &first, const TopoDS_Shape &second,
                     const char *failure = kPullFailed) {
  ShapeList arguments, tools;
  arguments.Append(first);
  tools.Append(second);
  Operation operation;
  operation.SetArguments(arguments);
  operation.SetTools(tools);
  operation.SetNonDestructive(true);
  operation.SetRunParallel(true);
  operation.Build();
  if (!operation.IsDone()) {
    throw std::runtime_error(failure);
  }
  return operation.Shape();
}

TopoDS_Shape translated(const TopoDS_Shape &shape, const gp_XYZ &by) {
  gp_Trsf move;
  move.SetTranslation(gp_Vec(by));
  BRepBuilderAPI_Transform builder(shape, move, true);
  if (!builder.IsDone()) {
    throw std::runtime_error(kPullFailed);
  }
  return builder.Shape();
}

TopoDS_Shape prism(const TopoDS_Shape &profile, const gp_Vec &along, const char *failure = kPullFailed) {
  BRepPrimAPI_MakePrism builder(profile, along);
  builder.Build();
  if (!builder.IsDone()) {
    throw std::runtime_error(failure);
  }
  return builder.Shape();
}

// A face on *wire* facing along *normal*: a prism of a reversed face is inside out.
TopoDS_Face planar_face(const TopoDS_Wire &wire, const gp_XYZ &normal) {
  TopoDS_Face face = BRepBuilderAPI_MakeFace(wire, true).Face();
  const auto plane = analyse_plane(face);
  if (plane && plane->normal.Dot(normal) < 0) {
    face = TopoDS::Face(face.Reversed());
  }
  return face;
}

// The solid *face* sweeps between offsets *low* and *high* along *normal*.
TopoDS_Shape column(const TopoDS_Face &face, const gp_XYZ &normal, double low, double high) {
  return translated(prism(face, gp_Vec(normal) * (high - low)), normal * low);
}

void for_each_neighbour(const AncestorMap &adjacency, const TopoDS_Shape &shape,
                        std::vector<TopoDS_Shape> &into, const TopoDS_Shape *skip) {
  for (TopExp_Explorer edges(shape, TopAbs_EDGE); edges.More(); edges.Next()) {
    for (ShapeList::Iterator it(adjacency.FindFromKey(edges.Current())); it.More(); it.Next()) {
      if (!skip || !it.Value().IsSame(*skip)) {
        into.push_back(it.Value());
      }
    }
  }
}

// The deepest blind floor and the tallest boss among *face*'s inner loops; both
// 0 when every loop is a through-hole. Each loop is followed from its walls
// across every face not parallel to *face* until a parallel face ends it: one
// facing the same way is a floor (a recess) or a top (a boss); one facing the
// other way is the far side, so the hole goes through.
std::pair<double, double> face_features(const TopoDS_Shape &body, const TopoDS_Face &face,
                                        const Plane &info) {
  const TopoDS_Wire outer = BRepTools::OuterWire(face);
  std::vector<TopoDS_Shape> loops;
  for (TopExp_Explorer e(face, TopAbs_WIRE); e.More(); e.Next()) {
    if (!e.Current().IsSame(outer)) {
      loops.push_back(e.Current());
    }
  }
  if (loops.empty()) {
    return {0.0, 0.0};
  }
  AncestorMap adjacency;
  TopExp::MapShapesAndAncestors(body, TopAbs_EDGE, TopAbs_FACE, adjacency);

  double pocket = 0, boss = 0;
  for (const TopoDS_Shape &wire : loops) {
    std::vector<TopoDS_Shape> seen{face};
    std::vector<TopoDS_Shape> frontier;
    for_each_neighbour(adjacency, wire, frontier, &face);
    double low = 0, high = 0;
    bool through = false;
    // A cap, as in the Python engine, not a proof the walk is local.
    while (!frontier.empty() && seen.size() < 500) {
      const TopoDS_Shape current = frontier.back();
      frontier.pop_back();
      if (std::any_of(seen.begin(), seen.end(), [&](const TopoDS_Shape &s) { return current.IsSame(s); })) {
        continue;
      }
      seen.push_back(current);
      const auto plane = analyse_plane(TopoDS::Face(current));
      const double facing = plane ? plane->normal.Dot(info.normal) : 0.0;
      if (std::abs(facing) > 0.999) {
        const double o = offset(plane->center, info.center, info.normal);
        if (facing < 0) {
          through = true;
        } else {
          low = std::min(low, o);
          high = std::max(high, o);
        }
        continue;
      }
      const auto [a, b] = span(current, info.center, info.normal);
      low = std::min(low, a);
      high = std::max(high, b);
      for_each_neighbour(adjacency, current, frontier, nullptr);
    }
    if (high > kFeatureTolerance) {
      boss = std::max(boss, high);
    } else if (!through && -low > kFeatureTolerance) {
      pocket = std::max(pocket, -low);
    }
  }
  return {pocket, boss};
}

TopoDS_Shape compound(const std::vector<TopoDS_Shape> &shapes) {
  if (shapes.size() == 1) {
    return shapes.front();
  }
  TopoDS_Compound result;
  BRep_Builder builder;
  builder.MakeCompound(result);
  for (const auto &shape : shapes) {
    builder.Add(result, shape);
  }
  return result;
}

// Move *face* by *distance* and take its recesses and bosses with it. The
// column over the face's outline is cut just below the deepest recess floor:
// everything above the cut moves, everything below stays, losing a slab (push)
// or gaining one drawn from its own cross-section at the cut (pull).
TopoDS_Shape carry_push_pull(const TopoDS_Shape &body, const TopoDS_Face &face, const Plane &info,
                             double distance, double pocket, double boss) {
  const gp_XYZ &normal = info.normal;
  const TopoDS_Face outline = planar_face(BRepTools::OuterWire(face), normal);
  const double cut = pocket != 0 ? -(pocket + kSectionClearance) : 0.0;
  const double top = boss != 0 ? boss + 1.0 : kSectionClearance;
  const double deep = -(depth_along(body, normal) + 1.0);

  const TopoDS_Shape rest = boolean<BRepAlgoAPI_Cut>(body, column(outline, normal, deep, top));
  TopoDS_Shape lower = boolean<BRepAlgoAPI_Common>(body, column(outline, normal, deep, cut));
  const TopoDS_Shape upper = boolean<BRepAlgoAPI_Common>(body, column(outline, normal, cut, top));
  std::vector<TopoDS_Shape> parts{translated(upper, normal * distance)};
  if (distance < 0) {
    lower = boolean<BRepAlgoAPI_Cut>(lower, column(outline, normal, cut + distance, cut));
  } else {
    const TopoDS_Shape thin =
        boolean<BRepAlgoAPI_Common>(body, column(outline, normal, cut - kSectionClearance, cut));
    std::vector<TopoDS_Shape> section;
    for (TopExp_Explorer e(thin, TopAbs_FACE); e.More(); e.Next()) {
      const auto plane = analyse_plane(TopoDS::Face(e.Current()));
      if (plane && std::abs(offset(plane->center, info.center, normal) - cut) < 1e-6 &&
          plane->normal.Dot(normal) > 0.999) {
        section.push_back(e.Current());
      }
    }
    if (!section.empty()) {
      parts.push_back(prism(compound(section), gp_Vec(normal) * distance));
    }
  }
  TopoDS_Shape result = boolean<BRepAlgoAPI_Fuse>(rest, lower);
  for (const auto &part : parts) {
    result = boolean<BRepAlgoAPI_Fuse>(result, part);
  }
  return result;
}

// Merge coplanar faces and collinear edges; a failed tidy-up keeps the shape.
TopoDS_Shape unified(const TopoDS_Shape &shape) {
  try {
    ShapeUpgrade_UnifySameDomain unifier(shape, true, true, false);
    unifier.Build();
    const TopoDS_Shape result = unifier.Shape();
    return result.IsNull() ? shape : result;
  } catch (const Standard_Failure &) {
    return shape;
  }
}

std::string mm(double value) {
  char text[64];
  std::snprintf(text, sizeof text, "%.2f", value);
  return text;
}

} // namespace

uint32_t solid_count(const Shape &shape) { return count_solids(shape.shape); }

uint32_t face_occurrences(const Shape &shape) {
  uint32_t count = 0;
  for (TopExp_Explorer e(shape.shape, TopAbs_FACE); e.More(); e.Next()) {
    ++count;
  }
  return count;
}

bool is_valid(const Shape &shape) { return BRepCheck_Analyzer(shape.shape).IsValid(); }

rust::Vec<double> bounds(const Shape &shape) {
  const auto [low, high] = corners(shape.shape);
  rust::Vec<double> out;
  for (const gp_XYZ &corner : {low, high}) {
    for (int i = 1; i <= 3; ++i) {
      out.push_back(corner.Coord(i));
    }
  }
  return out;
}

rust::Vec<FaceData> faces(const Shape &shape) {
  ShapeMap map;
  TopExp::MapShapes(shape.shape, TopAbs_FACE, map);
  rust::Vec<FaceData> out;
  out.reserve(map.Extent());
  for (int i = 1; i <= map.Extent(); ++i) {
    const TopoDS_Face face = TopoDS::Face(map.FindKey(i));
    FaceData data{};
    if (const auto plane = analyse_plane(face)) {
      data.planar = true;
      data.center = {plane->center.X(), plane->center.Y(), plane->center.Z()};
      data.normal = {plane->normal.X(), plane->normal.Y(), plane->normal.Z()};
      data.area = plane->area;
    } else {
      GProp_GProps props;
      BRepGProp::SurfaceProperties(face, props);
      const gp_Pnt c = props.CentreOfMass();
      data.center = {c.X(), c.Y(), c.Z()};
      data.area = props.Mass();
    }
    out.push_back(data);
  }
  return out;
}

static FaceFingerprint describe_face(const TopoDS_Face &face) {
  FaceFingerprint out{};
  GProp_GProps props;
  BRepGProp::SurfaceProperties(face, props);
  const gp_Pnt c = props.CentreOfMass();
  out.center = {c.X(), c.Y(), c.Z()};
  out.measure = props.Mass();
  BRepAdaptor_Surface adaptor(face);
  const GeomAbs_SurfaceType type = adaptor.GetType();
  const bool reversed = face.Orientation() == TopAbs_REVERSED;
  const auto set_direction = [&](const gp_Dir &d) {
    out.has_direction = true;
    const double sign = reversed ? -1.0 : 1.0;
    out.direction = {sign * d.X(), sign * d.Y(), sign * d.Z()};
  };
  const auto set_radius = [&](double r) {
    out.has_radius = true;
    out.radius = r;
  };
  switch (type) {
  case GeomAbs_Plane: out.geometry = "plane"; break;
  case GeomAbs_Cylinder: out.geometry = "cylinder"; break;
  case GeomAbs_Cone: out.geometry = "cone"; break;
  case GeomAbs_Sphere: out.geometry = "sphere"; break;
  case GeomAbs_Torus: out.geometry = "torus"; break;
  case GeomAbs_BezierSurface: out.geometry = "bezier"; break;
  case GeomAbs_BSplineSurface: out.geometry = "bspline"; break;
  default: out.geometry = "surface"; break;
  }
  // Degenerate surfaces still get a fingerprint; as in Python, what was read before a failure stays.
  try {
    if (type == GeomAbs_Plane) {
      set_direction(adaptor.Plane().Axis().Direction());
    } else if (type == GeomAbs_Cylinder) {
      const gp_Cylinder cylinder = adaptor.Cylinder();
      set_direction(cylinder.Axis().Direction());
      set_radius(cylinder.Radius());
    } else if (type == GeomAbs_Cone) {
      const gp_Cone cone = adaptor.Cone();
      set_direction(cone.Axis().Direction());
      set_radius(cone.RefRadius());
    } else if (type == GeomAbs_Sphere) {
      set_radius(adaptor.Sphere().Radius());
    } else if (type == GeomAbs_Torus) {
      const gp_Torus torus = adaptor.Torus();
      set_direction(torus.Axis().Direction());
      set_radius(torus.MajorRadius());
    }
  } catch (const Standard_Failure &) {
  }
  return out;
}

FaceFingerprint face_fingerprint(const Shape &shape, uint32_t index) {
  ShapeMap map;
  TopExp::MapShapes(shape.shape, TopAbs_FACE, map);
  if (index >= static_cast<uint32_t>(map.Extent())) {
    throw std::runtime_error("There is no such face.");
  }
  return describe_face(TopoDS::Face(map.FindKey(static_cast<int>(index) + 1)));
}

rust::Vec<EdgeFingerprint> edge_fingerprints(const Shape &shape) {
  ShapeMap map;
  TopExp::MapShapes(shape.shape, TopAbs_EDGE, map);
  AncestorMap adjacent;
  TopExp::MapShapesAndAncestors(shape.shape, TopAbs_EDGE, TopAbs_FACE, adjacent);
  rust::Vec<EdgeFingerprint> result;
  result.reserve(map.Extent());
  for (int i = 1; i <= map.Extent(); ++i) {
    const TopoDS_Edge edge = TopoDS::Edge(map.FindKey(i));
    EdgeFingerprint data{};
    FaceFingerprint &out = data.curve;
    GProp_GProps props;
    BRepGProp::LinearProperties(edge, props);
    const gp_Pnt c = props.CentreOfMass();
    out.center = {c.X(), c.Y(), c.Z()};
    out.measure = props.Mass();
    out.geometry = "curve";
    const auto direction = [&](const gp_Dir &d) {
      out.has_direction = true;
      out.direction = {d.X(), d.Y(), d.Z()};
    };
    const auto radius = [&](double r) { out.has_radius = true; out.radius = r; };
    try {
      const BRepAdaptor_Curve curve(edge);
      switch (curve.GetType()) {
      case GeomAbs_Line: out.geometry = "line"; direction(curve.Line().Direction()); break;
      case GeomAbs_Circle: out.geometry = "circle"; direction(curve.Circle().Axis().Direction()); radius(curve.Circle().Radius()); break;
      case GeomAbs_Ellipse: out.geometry = "ellipse"; direction(curve.Ellipse().Axis().Direction()); radius(curve.Ellipse().MajorRadius()); break;
      case GeomAbs_Hyperbola: out.geometry = "hyperbola"; break;
      case GeomAbs_Parabola: out.geometry = "parabola"; break;
      case GeomAbs_BezierCurve: out.geometry = "bezier"; break;
      case GeomAbs_BSplineCurve: out.geometry = "bspline"; break;
      default: break;
      }
    } catch (const Standard_Failure &) {
    }
    if (adjacent.Contains(edge)) {
      for (const auto &face : adjacent.FindFromKey(edge)) {
        data.neighbours.push_back(describe_face(TopoDS::Face(face)));
      }
    }
    result.push_back(std::move(data));
  }
  return result;
}

std::unique_ptr<Shape> finish_edges(const Shape &shape, rust::Slice<const uint32_t> indices,
                                    double size, bool fillet) {
  std::vector<EdgeFinishSpec> specs;
  specs.reserve(indices.size());
  for (const uint32_t index : indices) {
    specs.push_back({index, size, 0, 0.0, -1, false});
  }
  return finish_edge_specs(shape, rust::Slice<const EdgeFinishSpec>(specs.data(), specs.size()), fillet);
}

std::unique_ptr<Shape> finish_edge_specs(const Shape &shape, rust::Slice<const EdgeFinishSpec> specs,
                                         bool fillet) {
  if (specs.empty()) {
    throw std::runtime_error("This edge finish has no edges.");
  }
  ShapeMap edges;
  TopExp::MapShapes(shape.shape, TopAbs_EDGE, edges);
  const char *operation = fillet ? "fillet" : "chamfer";
  const auto selected_edge = [&](uint32_t index) {
    if (index >= static_cast<uint32_t>(edges.Extent())) {
      throw std::runtime_error("Select an edge of this body.");
    }
    return TopoDS::Edge(edges.FindKey(static_cast<int>(index) + 1));
  };
  const auto finish = [&](BRepBuilderAPI_MakeShape &builder) {
    builder.Build();
    if (!builder.IsDone()) {
      throw std::runtime_error(std::string("The ") + operation + " operation did not succeed on this geometry.");
    }
    return owned(builder.Shape(), operation);
  };
  if (fillet) {
    BRepFilletAPI_MakeFillet builder(shape.shape);
    for (const auto &spec : specs) {
      if (!std::isfinite(spec.first) || spec.first <= 0) {
        throw std::runtime_error("A fillet needs a radius greater than zero.");
      }
      builder.Add(spec.first, selected_edge(spec.index));
    }
    return finish(builder);
  }

  ShapeMap faces;
  AncestorMap adjacent;
  if (std::any_of(specs.begin(), specs.end(), [](const auto &spec) { return spec.mode != 0; })) {
    TopExp::MapShapes(shape.shape, TopAbs_FACE, faces);
    TopExp::MapShapesAndAncestors(shape.shape, TopAbs_EDGE, TopAbs_FACE, adjacent);
  }
  // The Python fallback and flip both use the body's face traversal order.
  const auto first_adjacent = [&](const TopoDS_Edge &edge, const TopoDS_Face &exclude) {
    int first = 0;
    if (adjacent.Contains(edge)) {
      for (const auto &candidate : adjacent.FindFromKey(edge)) {
        if (!candidate.IsSame(exclude)) {
          const int index = faces.FindIndex(candidate);
          if (index > 0 && (first == 0 || index < first)) {
            first = index;
          }
        }
      }
    }
    return first > 0 ? TopoDS::Face(faces.FindKey(first)) : TopoDS_Face();
  };

  BRepFilletAPI_MakeChamfer builder(shape.shape);
  for (const auto &spec : specs) {
    const TopoDS_Edge edge = selected_edge(spec.index);
    if (!std::isfinite(spec.first) || spec.first <= 0) {
      throw std::runtime_error("A chamfer needs a distance greater than zero.");
    }
    if (spec.mode == 0) {
      builder.Add(spec.first, edge);
      continue;
    }
    TopoDS_Face side;
    if (spec.side == -1) {
      side = first_adjacent(edge, {});
    } else if (spec.side >= 0 && spec.side < faces.Extent()) {
      side = TopoDS::Face(faces.FindKey(spec.side + 1));
    }
    if (side.IsNull()) {
      throw std::runtime_error("The chamfer's reference face is missing.");
    }
    if (spec.flip) {
      const TopoDS_Face other = first_adjacent(edge, side);
      if (!other.IsNull()) {
        side = other;
      }
    }
    switch (spec.mode) {
    case 1:
      if (!std::isfinite(spec.second) || spec.second <= 0) {
        throw std::runtime_error("Both chamfer distances must be positive.");
      }
      builder.Add(spec.first, spec.second, edge, side);
      break;
    case 2:
      if (!(spec.second > 0 && spec.second < M_PI / 2)) {
        throw std::runtime_error("A chamfer angle must be between 0 and 90 degrees.");
      }
      builder.AddDA(spec.first, spec.second, edge, side);
      break;
    default:
      throw std::runtime_error("Unknown chamfer setting.");
    }
  }
  return finish(builder);
}

std::unique_ptr<Shape> push_pull(const Shape &shape, uint32_t index, double distance) {
  const TopoDS_Shape &body = shape.shape;
  ShapeMap map;
  TopExp::MapShapes(body, TopAbs_FACE, map);
  if (index >= static_cast<uint32_t>(map.Extent())) {
    throw std::runtime_error("Select a face of this body.");
  }
  const TopoDS_Face face = TopoDS::Face(map.FindKey(static_cast<int>(index) + 1));
  const auto info = analyse_plane(face);
  if (!info) {
    throw std::runtime_error(
        "Press/Pull works on flat faces. Select a planar face, or use Move for the whole body.");
  }
  if (!std::isfinite(distance)) {
    throw std::runtime_error("Enter a finite distance.");
  }
  if (std::abs(distance) < 1e-9) {
    return std::make_unique<Shape>(body);
  }
  if (distance < 0) {
    // OCCT reports cutting away the whole solid as a success that returns
    // nothing, so this has to be checked here.
    const double available = depth_along(body, info->normal);
    if (std::abs(distance) >= available) {
      throw std::runtime_error("Pushing in " + mm(std::abs(distance)) +
                               " mm would go straight through this body. Push in less than " +
                               mm(available) + " mm.");
    }
  }
  try {
    const auto [pocket, boss] = face_features(body, face, *info);
    TopoDS_Shape result;
    if (pocket != 0 || boss != 0) {
      // A prism of the face itself would leave the material behind a recess
      // standing as a pillar, and a boss stranded.
      result = carry_push_pull(body, face, *info, distance, pocket, boss);
    } else {
      const TopoDS_Shape tool = prism(face, gp_Vec(info->normal) * distance);
      result = distance > 0 ? boolean<BRepAlgoAPI_Fuse>(body, tool) : boolean<BRepAlgoAPI_Cut>(body, tool);
    }
    if (result.IsNull() || !TopExp_Explorer(result, TopAbs_SOLID).More()) {
      throw std::runtime_error(
          "That push would leave nothing behind. Push in less far, or delete the body instead.");
    }
    const uint32_t pieces = count_solids(result);
    if (pieces > count_solids(body)) {
      throw std::runtime_error("That would cut the part into " + std::to_string(pieces) + " pieces. " +
                               (distance < 0 ? "Push in less far: the wall is thinner than that here."
                                             : "Pull a shorter distance."));
    }
    return owned(unified(result), "pull");
  } catch (const Standard_Failure &) {
    throw std::runtime_error(kPullFailed);
  }
}

std::unique_ptr<Shape> make_box(double dx, double dy, double dz) {
  return owned(BRepPrimAPI_MakeBox(dx, dy, dz).Shape(), "box");
}

// The primitives (`kernel/primitives.py`), each at the origin as OCCT builds it.
std::unique_ptr<Shape> make_cylinder(double radius, double height) {
  return owned(BRepPrimAPI_MakeCylinder(radius, height).Shape(), "cylinder");
}

std::unique_ptr<Shape> make_sphere(double radius) {
  return owned(BRepPrimAPI_MakeSphere(radius).Shape(), "sphere");
}

std::unique_ptr<Shape> make_cone(double bottom, double top, double height) {
  return owned(BRepPrimAPI_MakeCone(bottom, top, height).Shape(), "cone");
}

std::unique_ptr<Shape> make_torus(double major, double minor) {
  return owned(BRepPrimAPI_MakeTorus(major, minor).Shape(), "torus");
}

std::unique_ptr<Shape> make_wedge(double dx, double dy, double dz, double top) {
  return owned(BRepPrimAPI_MakeWedge(dx, dy, dz, top).Shape(), "wedge");
}

// A copy of `shape` moved by `trsf`, its geometry transformed (`occ.transformed`).
TopoDS_Shape transformed_copy(const TopoDS_Shape &shape, const gp_Trsf &trsf) {
  BRepBuilderAPI_Transform builder(shape, trsf, true);
  if (!builder.IsDone()) {
    throw std::runtime_error("The move operation did not succeed on this geometry.");
  }
  return builder.Shape();
}

// `TubeFeature`: a cylinder less a bore three times as long, cut as `boolean_builder` cuts.
std::unique_ptr<Shape> make_tube(double outer, double inner, double height) {
  const TopoDS_Shape solid = BRepPrimAPI_MakeCylinder(outer, height).Shape();
  gp_Trsf down;
  down.SetTranslation(gp_Vec(0, 0, -height));
  const TopoDS_Shape bore = transformed_copy(BRepPrimAPI_MakeCylinder(inner, height * 3).Shape(), down);
  TopTools_ListOfShape arguments, tools;
  arguments.Append(solid);
  tools.Append(bore);
  BRepAlgoAPI_Cut cut;
  cut.SetArguments(arguments);
  cut.SetTools(tools);
  cut.SetNonDestructive(true);
  cut.SetRunParallel(true);
  cut.Build();
  if (!cut.IsDone()) {
    throw std::runtime_error("The tube operation did not succeed on this geometry.");
  }
  return owned(cut.Shape(), "tube");
}

// The Python primitive and decorative profiles use the wire's own winding.
static std::unique_ptr<Shape> extrude_profile(const TopoDS_Wire &wire, double height, const char *operation) {
  const std::string failure = std::string("The ") + operation + " profile could not be built.";
  BRepBuilderAPI_MakeFace face(wire);
  if (!face.IsDone()) {
    throw std::runtime_error(failure);
  }
  return owned(prism(face.Face(), gp_Vec(0, 0, height), failure.c_str()), operation);
}

std::unique_ptr<Shape> polygon_profile(rust::Slice<const double> points, double height) {
  if (points.size() < 6 || points.size() % 2 != 0) {
    throw std::runtime_error("A polygon needs at least three XY points.");
  }
  BRepBuilderAPI_MakePolygon polygon;
  for (size_t i = 0; i < points.size(); i += 2) {
    polygon.Add(gp_Pnt(points[i], points[i + 1], 0));
  }
  polygon.Close();
  if (!polygon.IsDone()) {
    throw std::runtime_error("The polygon profile could not be built.");
  }
  return extrude_profile(polygon.Wire(), height, "polygon");
}

std::unique_ptr<Shape> oval(double width, double depth, double height) {
  const gp_Dir major = width >= depth ? gp_Dir(1, 0, 0) : gp_Dir(0, 1, 0);
  const gp_Elips ellipse(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1), major),
                         std::max(width, depth) / 2, std::min(width, depth) / 2);
  BRepBuilderAPI_MakeWire wire(BRepBuilderAPI_MakeEdge(ellipse).Edge());
  return extrude_profile(wire.Wire(), height, "oval");
}

std::unique_ptr<Shape> pill(double width, double depth, double height) {
  const double radius = std::min(width, depth) / 2;
  const double run = std::abs(width - depth) / 2;
  if (run < 1e-6) {
    throw std::runtime_error("A pill must be longer than it is wide. Make the width and depth different, or use Cylinder for a round one.");
  }
  const auto point = [width, depth](double u, double v) {
    return width > depth ? gp_Pnt(u, v, 0) : gp_Pnt(v, u, 0);
  };
  const gp_Pnt lower_left = point(-run, -radius), lower_right = point(run, -radius);
  const gp_Pnt upper_left = point(-run, radius), upper_right = point(run, radius);
  BRepBuilderAPI_MakeWire wire;
  wire.Add(BRepBuilderAPI_MakeEdge(GC_MakeSegment(lower_left, lower_right).Value()).Edge());
  wire.Add(BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(lower_right, point(run + radius, 0), upper_right).Value()).Edge());
  wire.Add(BRepBuilderAPI_MakeEdge(GC_MakeSegment(upper_right, upper_left).Value()).Edge());
  wire.Add(BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(upper_left, point(-run - radius, 0), lower_left).Value()).Edge());
  return extrude_profile(wire.Wire(), height, "pill");
}

std::unique_ptr<Shape> crescent(double size, double height, double thickness) {
  if (thickness <= 0 || thickness >= size) {
    throw std::runtime_error("The crescent thickness must be smaller than its size. Use a thickness between 0 and " + mm(size) + " mm.");
  }
  const double radius = size / 2;
  const TopoDS_Shape outer = BRepPrimAPI_MakeCylinder(radius, height).Shape();
  gp_Trsf move;
  move.SetTranslation(gp_Vec(thickness, 0, -height));
  const TopoDS_Shape cutter = transformed_copy(BRepPrimAPI_MakeCylinder(radius, 3 * height).Shape(), move);
  const TopoDS_Shape cut = boolean<BRepAlgoAPI_Cut>(outer, cutter, "The crescent operation did not succeed on this geometry.");
  return owned(unified(cut), "crescent");
}

// `_Primitive._placement` then `_finish`: moved to x y z, turned by rx ry rz
// degrees about the world axes, as one `gp_Trsf` composed as Python composes it.
std::unique_ptr<Shape> place(const Shape &shape, rust::Slice<const double> p) {
  if (p.size() != 6) {
    throw std::runtime_error("a placement needs x, y, z, rx, ry and rz");
  }
  gp_Trsf transform;
  if (std::abs(p[0]) > 1e-15 || std::abs(p[1]) > 1e-15 || std::abs(p[2]) > 1e-15) {
    transform.SetTranslation(gp_Vec(p[0], p[1], p[2]));
  }
  const gp_Dir axes[3] = {gp_Dir(1, 0, 0), gp_Dir(0, 1, 0), gp_Dir(0, 0, 1)};
  for (int i = 0; i < 3; ++i) {
    if (std::abs(p[3 + i]) > 1e-12) {
      gp_Trsf rotation;
      rotation.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), axes[i]), p[3 + i] * (M_PI / 180.0));
      transform = transform.Multiplied(rotation);
    }
  }
  return owned(transformed_copy(shape.shape, transform), "move");
}

// `make_transform(rotate_axis=axis, rotate_degrees=degrees, origin=pivot)`.
gp_Trsf turn(const gp_Pnt &pivot, const gp_Dir &axis, double degrees) {
  gp_Trsf rotation;
  rotation.SetRotation(gp_Ax1(pivot, axis), degrees * (M_PI / 180.0));
  return rotation;
}

// `MoveFeature` (`move_transform`, then `transformed(copy_geometry=False)`).
// `m`: dx dy dz, rx ry rz (degrees), the pivot, 1 for the "xyz" order, then
// 1 and an axis and angle for a turn about any axis, or 0.
std::unique_ptr<Shape> move_shape(const Shape &shape, rust::Slice<const double> m) {
  if (m.size() != 15) {
    throw std::runtime_error("a move needs 15 numbers");
  }
  gp_Trsf transform;
  if (std::abs(m[0]) > 1e-15 || std::abs(m[1]) > 1e-15 || std::abs(m[2]) > 1e-15) {
    transform.SetTranslation(gp_Vec(m[0], m[1], m[2]));
  }
  const gp_Pnt pivot(m[6], m[7], m[8]);
  if (m[10] != 0.0 && std::abs(m[14]) > 1e-12) {
    transform = transform.Multiplied(turn(pivot, gp_Dir(m[11], m[12], m[13]), m[14]));
  }
  const gp_Dir axes[3] = {gp_Dir(1, 0, 0), gp_Dir(0, 1, 0), gp_Dir(0, 0, 1)};
  for (int step = 0; step < 3; ++step) {
    const int i = m[9] != 0.0 ? 2 - step : step;
    if (std::abs(m[3 + i]) > 1e-12) {
      transform = transform.Multiplied(turn(pivot, axes[i], m[3 + i]));
    }
  }
  BRepBuilderAPI_Transform builder(shape.shape, transform, false);
  if (!builder.IsDone()) {
    throw std::runtime_error("The move operation did not succeed on this geometry.");
  }
  return owned(builder.Shape(), "move");
}

std::unique_ptr<Shape> scale_shape(const Shape &shape, rust::Slice<const double> pivot,
                                   rust::Slice<const double> factors, bool uniform) {
  if (pivot.size() != 3 || factors.size() != 3) {
    throw std::runtime_error("Scaling needs a three-coordinate pivot and three scale factors.");
  }
  const auto scaled = [](BRepBuilderAPI_MakeShape &builder) {
    if (!builder.IsDone()) {
      throw std::runtime_error("The scale operation did not succeed on this geometry.");
    }
    return owned(builder.Shape(), "scale");
  };

  if (uniform) {
    gp_Trsf transform;
    transform.SetScale(gp_Pnt(pivot[0], pivot[1], pivot[2]), factors[0]);
    BRepBuilderAPI_Transform builder(shape.shape, transform, true);
    return scaled(builder);
  }

  gp_GTrsf transform;
  transform.SetVectorialPart(gp_Mat(factors[0], 0, 0, 0, factors[1], 0, 0, 0, factors[2]));
  transform.SetTranslationPart(gp_XYZ(pivot[0] * (1.0 - factors[0]),
                                     pivot[1] * (1.0 - factors[1]),
                                     pivot[2] * (1.0 - factors[2])));
  BRepBuilderAPI_GTransform builder(shape.shape, transform, true);
  return scaled(builder);
}

// `ShellFeature`'s thick solid, accepted only as `_usable_shell` accepts it.
// "unusable" when OCCT gives no hollow body.
std::unique_ptr<Shape> shell(const Shape &shape, rust::Slice<const uint32_t> indices, double thickness) {
  ShapeMap map;
  TopExp::MapShapes(shape.shape, TopAbs_FACE, map);
  TopTools_ListOfShape removed;
  for (const uint32_t index : indices) {
    if (index >= static_cast<uint32_t>(map.Extent())) {
      throw std::runtime_error("Select a face of this body.");
    }
    removed.Append(TopoDS::Face(map.FindKey(static_cast<int>(index) + 1)));
  }
  TopoDS_Shape result;
  try {
    BRepOffsetAPI_MakeThickSolid builder;
    builder.MakeThickSolidByJoin(shape.shape, removed, -std::abs(thickness), 1.0e-3);
    builder.Build();
    if (builder.IsDone()) {
      result = builder.Shape();
    }
  } catch (const Standard_Failure &) {
  }
  if (result.IsNull() || !TopExp_Explorer(result, TopAbs_SOLID).More() || !BRepCheck_Analyzer(result).IsValid()) {
    throw std::runtime_error("unusable");
  }
  GProp_GProps before, after;
  BRepGProp::VolumeProperties(shape.shape, before);
  BRepGProp::VolumeProperties(result, after);
  if (before.Mass() - after.Mass() <= std::max(1.0e-7, before.Mass() * 1.0e-8)) {
    throw std::runtime_error("unusable");
  }
  const auto [old_low, old_high] = corners(shape.shape);
  const auto [new_low, new_high] = corners(result);
  for (int axis = 1; axis <= 3; ++axis) {
    if (std::abs(old_low.Coord(axis) - new_low.Coord(axis)) > 1.0e-5 ||
        std::abs(old_high.Coord(axis) - new_high.Coord(axis)) > 1.0e-5) {
      throw std::runtime_error("unusable");
    }
  }
  return owned(result, "shell");
}

std::unique_ptr<Shape> fuse(const Shape &a, const Shape &b) {
  BRepAlgoAPI_Fuse builder(a.shape, b.shape);
  if (!builder.IsDone()) {
    throw std::runtime_error("fuse did not succeed on this geometry");
  }
  return owned(builder.Shape(), "fuse");
}

std::unique_ptr<Shape> unify(const Shape &shape) {
  ShapeUpgrade_UnifySameDomain unifier(shape.shape, true, true, false);
  unifier.Build();
  return owned(unifier.Shape(), "unify");
}

std::unique_ptr<Shape> read_brep(rust::Slice<const uint8_t> bytes) {
  std::istringstream stream(std::string(reinterpret_cast<const char *>(bytes.data()), bytes.size()));
  TopoDS_Shape shape;
  BRep_Builder builder;
  BRepTools::Read(shape, stream, builder);
  return owned(shape, "reading the B-Rep");
}

rust::Vec<uint8_t> write_brep(const Shape &shape) {
  std::ostringstream stream;
  BRepTools::Write(shape.shape, stream);
  const std::string text = stream.str();
  rust::Vec<uint8_t> bytes;
  bytes.reserve(text.size());
  for (char c : text) {
    bytes.push_back(static_cast<uint8_t>(c));
  }
  return bytes;
}

std::unique_ptr<Shape> located(const Shape &shape, rust::Slice<const double> m) {
  if (m.size() != 12) {
    throw std::runtime_error("a placement needs 12 numbers");
  }
  gp_Trsf trsf;
  trsf.SetValues(m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7], m[8], m[9], m[10], m[11]);
  return owned(shape.shape.Located(TopLoc_Location(trsf)), "placing a shape");
}

double volume(const Shape &shape) {
  GProp_GProps props;
  BRepGProp::VolumeProperties(shape.shape, props);
  return props.Mass();
}

// `split_solid` and `SplitFeature` in simplecad/kernel/split.py.
SplitParts split_solid(const Shape &shape, rust::Slice<const double> point,
                      rust::Slice<const double> normal) {
  if (point.size() != 3 || normal.size() != 3) {
    throw std::runtime_error("The split plane needs a three-coordinate point and direction.");
  }
  const double length = std::hypot(normal[0], normal[1], normal[2]);
  if (!std::isfinite(length) || length < 1e-12) {
    throw std::runtime_error("The split plane has no direction. Choose X, Y, Z or a flat face to cut along.");
  }
  const gp_XYZ origin(point[0], point[1], point[2]);
  const gp_XYZ axis(normal[0] / length, normal[1] / length, normal[2] / length);
  const auto [low, high] = corners(shape.shape);
  const double reach = (high - low).Modulus() * 1.75 + 10.0;
  const gp_Pln plane{gp_Pnt(origin), gp_Dir(axis)};
  const TopoDS_Face cutter = BRepBuilderAPI_MakeFace(plane, -reach, reach, -reach, reach).Face();
  const TopoDS_Shape result = boolean<BRepAlgoAPI_Splitter>(
      shape.shape, cutter, "The split could not be completed. Move the cut so it passes cleanly through the part.");

  std::vector<TopoDS_Shape> below, above;
  for (TopExp_Explorer solids(result, TopAbs_SOLID); solids.More(); solids.Next()) {
    const auto &solid = solids.Current();
    GProp_GProps properties;
    BRepGProp::VolumeProperties(solid, properties);
    const double side = offset(properties.CentreOfMass().XYZ(), origin, axis);
    (side > 0 ? above : below).push_back(solid);
  }
  if (below.empty() || above.empty()) {
    const auto [start, end] = span(shape.shape, origin, axis);
    const double width = std::max(0.0, -start) + std::max(0.0, end);
    throw std::runtime_error("The cut misses the body, so there is nothing to divide. "
                             "Move the plane between 0 and " + mm(width) + " mm along the part.");
  }
  return {owned(unified(compound(below)), "split"), owned(unified(compound(above)), "split")};
}

MeshBuffers mesh(const Shape &shape, double deflection, double angle) {
  BRepMesh_IncrementalMesh mesher(shape.shape, deflection, false, angle, true);
  MeshBuffers out;

  ShapeMap faces;
  TopExp::MapShapes(shape.shape, TopAbs_FACE, faces);
  out.face_tri_start.push_back(0);
  Poly_Connect connect;
  for (int i = 1; i <= faces.Extent(); ++i) {
    const TopoDS_Face face = TopoDS::Face(faces.FindKey(i));
    TopLoc_Location location;
    const occ::handle<Poly_Triangulation> tris = BRep_Tool::Triangulation(face, location);
    if (!tris.IsNull()) {
      BRepLib_ToolTriangulatedShape::ComputeNormals(face, tris, connect);
      const gp_Trsf place = location.Transformation();
      // Surface normals ignore the face's orientation; a reversed face's
      // outside is the surface's back, so its normals and winding flip.
      const bool reversed = face.Orientation() == TopAbs_REVERSED;
      const uint32_t base = static_cast<uint32_t>(out.positions.size() / 3);
      for (int n = 1; n <= tris->NbNodes(); ++n) {
        const gp_Pnt p = tris->Node(n).Transformed(place);
        gp_Dir normal = tris->Normal(n).Transformed(place);
        if (reversed) {
          normal.Reverse();
        }
        out.positions.push_back(static_cast<float>(p.X()));
        out.positions.push_back(static_cast<float>(p.Y()));
        out.positions.push_back(static_cast<float>(p.Z()));
        out.normals.push_back(static_cast<float>(normal.X()));
        out.normals.push_back(static_cast<float>(normal.Y()));
        out.normals.push_back(static_cast<float>(normal.Z()));
      }
      for (int t = 1; t <= tris->NbTriangles(); ++t) {
        int a, b, c;
        tris->Triangle(t).Get(a, b, c);
        if (reversed) {
          std::swap(b, c);
        }
        out.indices.push_back(base + a - 1);
        out.indices.push_back(base + b - 1);
        out.indices.push_back(base + c - 1);
      }
    }
    out.face_tri_start.push_back(static_cast<uint32_t>(out.indices.size() / 3));
  }

  ShapeMap edges;
  TopExp::MapShapes(shape.shape, TopAbs_EDGE, edges);
  out.edge_start.push_back(0);
  for (int i = 1; i <= edges.Extent(); ++i) {
    const TopoDS_Edge edge = TopoDS::Edge(edges.FindKey(i));
    if (!BRep_Tool::Degenerated(edge)) {
      BRepAdaptor_Curve curve(edge);
      GCPnts_TangentialDeflection points(curve, angle, deflection);
      for (int p = 1; p <= points.NbPoints(); ++p) {
        const gp_Pnt point = points.Value(p);
        out.edge_points.push_back(static_cast<float>(point.X()));
        out.edge_points.push_back(static_cast<float>(point.Y()));
        out.edge_points.push_back(static_cast<float>(point.Z()));
      }
    }
    out.edge_start.push_back(static_cast<uint32_t>(out.edge_points.size() / 3));
  }
  return out;
}

// -- Align (simplecad/kernel/align.py): the transforms and face data it reads ----------

std::unique_ptr<Trsf> trsf_identity() { return std::make_unique<Trsf>(gp_Trsf()); }

std::unique_ptr<Trsf> trsf_rotation(rust::Slice<const double> p, rust::Slice<const double> a, double angle) {
  gp_Trsf trsf;
  trsf.SetRotation(gp_Ax1(gp_Pnt(p[0], p[1], p[2]), gp_Dir(a[0], a[1], a[2])), angle);
  return std::make_unique<Trsf>(trsf);
}

std::unique_ptr<Trsf> trsf_translation(rust::Slice<const double> v) {
  gp_Trsf trsf;
  trsf.SetTranslation(gp_Vec(v[0], v[1], v[2]));
  return std::make_unique<Trsf>(trsf);
}

std::unique_ptr<Trsf> trsf_multiplied(const Trsf &a, const Trsf &b) {
  return std::make_unique<Trsf>(a.trsf.Multiplied(b.trsf));
}

rust::Vec<double> trsf_point(const Trsf &trsf, rust::Slice<const double> point) {
  gp_Pnt p(point[0], point[1], point[2]);
  p.Transform(trsf.trsf);
  rust::Vec<double> out;
  for (double v : {p.X(), p.Y(), p.Z()}) {
    out.push_back(v);
  }
  return out;
}

rust::Vec<double> trsf_values(const Trsf &trsf) {
  rust::Vec<double> out;
  for (int row = 1; row <= 3; ++row) {
    for (int column = 1; column <= 4; ++column) {
      out.push_back(trsf.trsf.Value(row, column));
    }
  }
  return out;
}

std::unique_ptr<Shape> transform_shape(const Shape &shape, const Trsf &trsf, bool copy) {
  BRepBuilderAPI_Transform builder(shape.shape, trsf.trsf, copy);
  if (!builder.IsDone()) {
    throw std::runtime_error("The move operation did not succeed on this geometry.");
  }
  return owned(builder.Shape(), "move");
}

std::unique_ptr<Shape> sub_face(const Shape &shape, uint32_t index) {
  ShapeMap map;
  TopExp::MapShapes(shape.shape, TopAbs_FACE, map);
  if (index >= static_cast<uint32_t>(map.Extent())) {
    throw std::runtime_error("no such face");
  }
  return owned(map.FindKey(static_cast<int>(index) + 1), "face");
}

// `detect.analyse_plane` and what `align.planar_frame` reads off the plane.
PlaneFrame plane_frame(const Shape &face_shape) {
  PlaneFrame out{};
  if (face_shape.shape.IsNull() || face_shape.shape.ShapeType() != TopAbs_FACE) {
    return out;
  }
  const TopoDS_Face face = TopoDS::Face(face_shape.shape);
  const auto plane = analyse_plane(face);
  if (!plane) {
    return out;
  }
  out.planar = true;
  out.center = {plane->center.X(), plane->center.Y(), plane->center.Z()};
  out.normal = {plane->normal.X(), plane->normal.Y(), plane->normal.Z()};
  out.area = plane->area;
  const gp_Ax3 position = BRepAdaptor_Surface(face).Plane().Position();
  const gp_Pnt origin = position.Location();
  const gp_Dir x = position.XDirection();
  const gp_Dir y = position.YDirection();
  out.origin = {origin.X(), origin.Y(), origin.Z()};
  out.x_dir = {x.X(), x.Y(), x.Z()};
  out.y_dir = {y.X(), y.Y(), y.Z()};
  BRepTools::UVBounds(face, out.uv[0], out.uv[1], out.uv[2], out.uv[3]);
  return out;
}

// `detect.analyse_cylinder`.
CylinderData cylinder_data(const Shape &face_shape) {
  CylinderData out{};
  if (face_shape.shape.IsNull() || face_shape.shape.ShapeType() != TopAbs_FACE) {
    return out;
  }
  const TopoDS_Face face = TopoDS::Face(face_shape.shape);
  BRepAdaptor_Surface adaptor(face);
  if (adaptor.GetType() != GeomAbs_Cylinder) {
    return out;
  }
  const gp_Cylinder cylinder = adaptor.Cylinder();
  const gp_Pnt location = cylinder.Axis().Location();
  const gp_Dir direction = cylinder.Axis().Direction();
  double umin, umax, vmin, vmax;
  BRepTools::UVBounds(face, umin, umax, vmin, vmax);
  out.cylindrical = true;
  out.radius = cylinder.Radius();
  out.length = std::abs(vmax - vmin);
  out.direction = {direction.X(), direction.Y(), direction.Z()};
  out.origin = {location.X() + direction.X() * vmin, location.Y() + direction.Y() * vmin,
                location.Z() + direction.Z() * vmin};
  BRepLProp_SLProps props(BRepAdaptor_Surface(face), (umin + umax) / 2.0, (vmin + vmax) / 2.0, 1, 1e-6);
  if (props.IsNormalDefined()) {
    gp_Dir normal = props.Normal();
    const gp_Pnt point = props.Value();
    const double middle = (vmin + vmax) / 2.0;
    const double radial[3] = {point.X() - (location.X() + direction.X() * middle),
                              point.Y() - (location.Y() + direction.Y() * middle),
                              point.Z() - (location.Z() + direction.Z() * middle)};
    double n[3] = {normal.X(), normal.Y(), normal.Z()};
    if (face.Orientation() == TopAbs_REVERSED) {
      for (double &v : n) {
        v = -v;
      }
    }
    const double dot = n[0] * radial[0] + n[1] * radial[1] + n[2] * radial[2];
    out.internal = dot < 0.0;
  }
  return out;
}

// `RoundPushPullFeature`: moving an inner or outer cylindrical wall along its radius.
std::unique_ptr<Shape> resize_round(const Shape &shape, uint32_t face, double delta) {
  const CylinderData cylinder = cylinder_data(*sub_face(shape, face));
  if (!cylinder.cylindrical) {
    throw std::runtime_error("This only works on round faces. Select the side of a shaft, a tube or a hole.");
  }
  if (std::abs(delta) < 1e-9) {
    return owned(shape.shape, "resize");
  }
  constexpr double radial_margin = 0.02;
  constexpr double min_wall = 0.05;
  constexpr const char *diameter = u8"\u2300";
  const bool adding = delta > 0;
  const double radius = cylinder.radius + (cylinder.internal ? -delta : delta);
  if (radius <= min_wall) {
    const char *kind = cylinder.internal ? "hole" : "shaft";
    throw std::runtime_error(std::string("That would take the ") + kind + " down to " + diameter +
                             mm(radius * 2) + " mm. Keep the diameter above " + diameter + mm(min_wall * 2) + " mm.");
  }

  const gp_XYZ origin(cylinder.origin[0], cylinder.origin[1], cylinder.origin[2]);
  const gp_XYZ axis(cylinder.direction[0], cylinder.direction[1], cylinder.direction[2]);
  if (!adding) {
    double opposite_radius = 0;
    ShapeMap faces;
    TopExp::MapShapes(shape.shape, TopAbs_FACE, faces);
    for (int index = 1; index <= faces.Extent(); ++index) {
      const CylinderData other = cylinder_data(Shape(faces.FindKey(index)));
      if (!other.cylindrical || other.internal == cylinder.internal) {
        continue;
      }
      const gp_XYZ other_axis(other.direction[0], other.direction[1], other.direction[2]);
      if (std::abs(other_axis.Dot(axis)) < 0.999) {
        continue;
      }
      const gp_XYZ offset = gp_XYZ(other.origin[0], other.origin[1], other.origin[2]) - origin;
      const double along = offset.Dot(axis);
      const double sideways = std::sqrt(std::max(0.0, offset.SquareModulus() - along * along));
      if (sideways <= 1e-6) {
        opposite_radius = std::max(opposite_radius, other.radius);
      }
    }
    if (opposite_radius > 0 && cylinder.internal && radius >= opposite_radius - min_wall) {
      throw std::runtime_error(std::string("Opening the hole to ") + diameter + mm(radius * 2) +
                               " mm would break through the " + diameter + mm(opposite_radius * 2) +
                               " mm wall around it. Stay under " + diameter + mm((opposite_radius - min_wall) * 2) + " mm.");
    }
    if (opposite_radius > 0 && !cylinder.internal && radius <= opposite_radius + min_wall) {
      throw std::runtime_error(std::string("Thinning to ") + diameter + mm(radius * 2) +
                               " mm would break through the " + diameter + mm(opposite_radius * 2) +
                               " mm bore inside it. Stay above " + diameter + mm((opposite_radius + min_wall) * 2) + " mm.");
    }
  }

  // The annulus overlaps existing material or reaches past the old face, avoiding tangent booleans.
  constexpr const char *failure = "The resize operation did not succeed on this geometry. Try adjusting the inputs slightly.";
  const double overlap = cylinder.radius + (cylinder.internal == adding ? radial_margin : -radial_margin);
  const auto [inner, outer] = std::minmax(radius, overlap);
  TopoDS_Shape ring = BRepPrimAPI_MakeCylinder(outer, cylinder.length).Shape();
  if (inner > 1e-6) {
    ring = boolean<BRepAlgoAPI_Cut>(ring, BRepPrimAPI_MakeCylinder(inner, cylinder.length).Shape(), failure);
  }
  gp_Trsf placement;
  placement.SetDisplacement(gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), gp_Ax3(gp_Pnt(origin), gp_Dir(axis)));
  ring = transformed_copy(ring, placement);
  const TopoDS_Shape result = adding ? boolean<BRepAlgoAPI_Fuse>(shape.shape, ring, failure)
                                    : boolean<BRepAlgoAPI_Cut>(shape.shape, ring, failure);
  if (result.IsNull() || count_solids(result) == 0) {
    throw std::runtime_error("That would leave nothing behind. Take less off, or delete the body instead.");
  }
  return owned(unified(result), "resize");
}

static gp_Ax2 drill_axis(rust::Slice<const double> position, rust::Slice<const double> inward) {
  if (position.size() != 3 || inward.size() != 3) {
    throw std::runtime_error("A hole needs a three-coordinate start point and direction.");
  }
  return gp_Ax2(gp_Pnt(position[0], position[1], position[2]), gp_Dir(inward[0], inward[1], inward[2]));
}

// `HoleFeature`'s drill and optional counterbore or countersink.
std::unique_ptr<Shape> hole_cut(const Shape &shape, const HoleCut &cut) {
  const auto &[position, inward, diameter, depth, cap_diameter, cap_depth, tapered] = cut;
  if (!std::isfinite(diameter) || diameter <= 0) {
    throw std::runtime_error("A hole needs a diameter greater than zero.");
  }
  if (!std::isfinite(depth) || depth <= 0) {
    throw std::runtime_error("A hole needs a depth greater than zero.");
  }
  const gp_Ax2 axis = drill_axis({position.data(), position.size()}, {inward.data(), inward.size()});
  const double radius = diameter / 2.0;
  constexpr const char *failure = "The hole operation did not succeed on this geometry. Try adjusting the inputs slightly.";
  TopoDS_Shape tool = BRepPrimAPI_MakeCylinder(axis, radius, depth).Shape();
  if (cap_diameter != 0) {
    if (!std::isfinite(cap_diameter) || cap_diameter < 0 || !std::isfinite(cap_depth) || cap_depth <= 0) {
      throw std::runtime_error("The counterbore or countersink needs a positive diameter and depth.");
    }
    const double cap_radius = cap_diameter / 2.0;
    const TopoDS_Shape cap = tapered ? BRepPrimAPI_MakeCone(axis, cap_radius, radius, cap_depth).Shape()
                                     : BRepPrimAPI_MakeCylinder(axis, cap_radius, cap_depth).Shape();
    tool = boolean<BRepAlgoAPI_Fuse>(tool, cap, failure);
  }
  return owned(boolean<BRepAlgoAPI_Cut>(shape.shape, tool, failure), "hole");
}

double first_ray_hit(const Shape &shape, rust::Slice<const double> position,
                     rust::Slice<const double> inward) {
  const gp_Ax2 axis = drill_axis(position, inward);
  constexpr double tolerance = 1e-6;
  BRepIntCurveSurface_Inter finder;
  finder.Init(shape.shape, gp_Lin(axis.Location(), axis.Direction()), tolerance);
  double nearest = std::numeric_limits<double>::infinity();
  for (; finder.More(); finder.Next()) {
    const double distance = finder.W();
    if (distance > tolerance && distance < nearest) {
      nearest = distance;
    }
  }
  return std::isfinite(nearest) ? nearest : 0.0;
}

namespace {
std::optional<std::pair<double, double>> optimal_x(const TopoDS_Shape &shape) {
  Bnd_Box box;
  BRepBndLib::AddOptimal(shape, box, false, true);
  if (box.IsVoid()) {
    return std::nullopt;
  }
  return std::make_pair(box.CornerMin().X(), box.CornerMax().X());
}
} // namespace

// `align._x_extent`: the X range of AddOptimal(shape, exact geometry), bounding
// only the faces whose cheap box can reach an extreme.
rust::Vec<double> x_extent(const Shape &shape_in) {
  const TopoDS_Shape &shape = shape_in.shape;
  const bool loose = TopExp_Explorer(shape, TopAbs_EDGE, TopAbs_FACE).More() ||
                     TopExp_Explorer(shape, TopAbs_VERTEX, TopAbs_EDGE).More();
  struct Entry {
    double low, high;
    TopoDS_Shape face;
  };
  std::vector<Entry> faces;
  double margin = ShapeAnalysis_ShapeTolerance().Tolerance(shape, 1);
  for (TopExp_Explorer e(shape, TopAbs_FACE); e.More(); e.Next()) {
    Bnd_Box cheap;
    BRepBndLib::Add(e.Current(), cheap, false);
    if (!cheap.IsVoid()) {
      faces.push_back({cheap.CornerMin().X(), cheap.CornerMax().X(), e.Current()});
    }
  }
  rust::Vec<double> out;
  if (loose || faces.empty()) {
    const auto whole = optimal_x(shape);
    out.push_back(whole ? whole->first : 0.0);
    out.push_back(whole ? whole->second : 0.0);
    return out;
  }
  margin = 2.0 * margin + 1e-9;
  double low = std::numeric_limits<double>::infinity(), high = -low;
  std::vector<size_t> order(faces.size());
  for (size_t i = 0; i < order.size(); ++i) {
    order[i] = i;
  }
  std::vector<bool> done(faces.size(), false);
  std::stable_sort(order.begin(), order.end(), [&](size_t a, size_t b) { return -faces[a].high < -faces[b].high; });
  for (size_t index : order) {
    if (faces[index].high + margin < high) {
      break;
    }
    if (const auto found = optimal_x(faces[index].face)) {
      done[index] = true;
      low = std::min(low, found->first);
      high = std::max(high, found->second);
    }
  }
  std::stable_sort(order.begin(), order.end(), [&](size_t a, size_t b) { return faces[a].low < faces[b].low; });
  for (size_t index : order) {
    if (faces[index].low - margin > low) {
      break;
    }
    if (!done[index]) {
      if (const auto found = optimal_x(faces[index].face)) {
        low = std::min(low, found->first);
        high = std::max(high, found->second);
      }
    }
  }
  out.push_back(low <= high ? low : 0.0);
  out.push_back(low <= high ? high : 0.0);
  return out;
}

// -- Combine (BooleanFeature in simplecad/kernel/operations.py) ------------------------

std::unique_ptr<Shapes> shapes_new() { return std::make_unique<Shapes>(); }

void shapes_push(Shapes &shapes, const Shape &shape) { shapes.list.Append(shape.shape); }

namespace {
template <class Operation>
TopoDS_Shape combined(const ShapeList &arguments, const ShapeList &tools, double fuzzy) {
  Operation operation;
  operation.SetArguments(arguments);
  operation.SetTools(tools);
  operation.SetNonDestructive(true);
  if (fuzzy != 0.0) {
    operation.SetFuzzyValue(fuzzy);
  }
  operation.SetRunParallel(true);
  operation.Build();
  if (!operation.IsDone()) {
    // `check_done`, translated: what the Python app says for StdFail_NotDone.
    throw std::runtime_error("The operation could not be completed on this geometry. "
                             "Try adjusting the size or position of the inputs slightly.");
  }
  return operation.Shape();
}
} // namespace

// `BooleanFeature.execute`'s `combine`: join (0) or cut (1) as one boolean with
// every tool; intersect (2) one tool at a time, in order.
std::unique_ptr<Shape> combine(uint8_t operation, const Shape &target, const Shapes &tools, double fuzzy) {
  ShapeList arguments;
  arguments.Append(target.shape);
  if (operation == 0) {
    return owned(combined<BRepAlgoAPI_Fuse>(arguments, tools.list, fuzzy), "join");
  }
  if (operation == 1) {
    return owned(combined<BRepAlgoAPI_Cut>(arguments, tools.list, fuzzy), "cut");
  }
  TopoDS_Shape result = target.shape;
  for (const TopoDS_Shape &tool : tools.list) {
    ShapeList argument, one;
    argument.Append(result);
    one.Append(tool);
    result = combined<BRepAlgoAPI_Common>(argument, one, fuzzy);
  }
  return owned(result, "intersect");
}

// `_join_solids`: the distinct solids in a result.
uint32_t distinct_solids(const Shape &shape) {
  ShapeMap map;
  TopExp::MapShapes(shape.shape, TopAbs_SOLID, map);
  return static_cast<uint32_t>(map.Extent());
}

// `_join_bridge_gap`: the smallest tolerance connecting every solid, 0 when they
// already touch, or -1 when some solid is more than 0.2 mm from the rest.
double join_bridge_gap(const Shape &shape) {
  constexpr double kMaxJoinGap = 0.2;
  ShapeMap map;
  TopExp::MapShapes(shape.shape, TopAbs_SOLID, map);
  const int count = map.Extent();
  if (count == 0) {
    return -1;
  }
  if (count == 1) {
    return 0;
  }
  std::vector<int> parent(count);
  for (int i = 0; i < count; ++i) {
    parent[i] = i;
  }
  auto root = [&](int index) {
    while (parent[index] != index) {
      parent[index] = parent[parent[index]];
      index = parent[index];
    }
    return index;
  };
  std::vector<std::pair<gp_XYZ, gp_XYZ>> bounds;
  for (int i = 1; i <= count; ++i) {
    Bnd_Box box;
    BRepBndLib::Add(map.FindKey(i), box, true);
    bounds.push_back(box.IsVoid() ? std::make_pair(gp_XYZ(0, 0, 0), gp_XYZ(0, 0, 0))
                                  : std::make_pair(box.CornerMin().XYZ(), box.CornerMax().XYZ()));
  }
  std::vector<int> order(count);
  for (int i = 0; i < count; ++i) {
    order[i] = i;
  }
  std::stable_sort(order.begin(), order.end(), [&](int a, int b) { return bounds[a].first.X() < bounds[b].first.X(); });
  std::vector<std::tuple<double, int, int>> nearby;
  for (size_t position = 0; position < order.size(); ++position) {
    const int first = order[position];
    const auto &[first_low, first_high] = bounds[first];
    for (size_t next = position + 1; next < order.size(); ++next) {
      const int second = order[next];
      const auto &[second_low, second_high] = bounds[second];
      if (second_low.X() - first_high.X() > kMaxJoinGap + 1.0e-9) {
        break;
      }
      double box_gap_sq = 0;
      for (int axis = 1; axis <= 3; ++axis) {
        const double gap = std::max({0.0, first_low.Coord(axis) - second_high.Coord(axis),
                                     second_low.Coord(axis) - first_high.Coord(axis)});
        box_gap_sq += gap * gap;
      }
      if (box_gap_sq > (kMaxJoinGap + 1.0e-9) * (kMaxJoinGap + 1.0e-9)) {
        continue;
      }
      BRepExtrema_DistShapeShape distance;
      distance.LoadS1(map.FindKey(first + 1));
      distance.LoadS2(map.FindKey(second + 1));
      distance.Perform();
      if (!distance.IsDone()) {
        continue;
      }
      const double gap = distance.Value();
      if (gap <= kMaxJoinGap + 1.0e-9) {
        nearby.emplace_back(gap, first, second);
      }
    }
  }
  std::sort(nearby.begin(), nearby.end());
  double largest = 0;
  for (const auto &[gap, first, second] : nearby) {
    const int a = root(first), b = root(second);
    if (a != b) {
      parent[a] = b;
      largest = gap;
    }
  }
  const int one = root(0);
  for (int i = 1; i < count; ++i) {
    if (root(i) != one) {
      return -1;
    }
  }
  return largest;
}

void crash() {
  volatile int *nothing = nullptr;
  *nothing = 1;
}

} // namespace simplecad
