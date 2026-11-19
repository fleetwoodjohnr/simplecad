#include "shim.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <limits>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <vector>

#include <BRepAdaptor_Curve.hxx>
#include <BRepAdaptor_Surface.hxx>
#include <BRepAlgoAPI_Common.hxx>
#include <BRepAlgoAPI_Cut.hxx>
#include <BRepAlgoAPI_Fuse.hxx>
#include <BRepBndLib.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_Transform.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRepGProp.hxx>
#include <BRepLib_ToolTriangulatedShape.hxx>
#include <BRepMesh_IncrementalMesh.hxx>
#include <BRepPrimAPI_MakeBox.hxx>
#include <BRepPrimAPI_MakePrism.hxx>
#include <BRepTools.hxx>
#include <BRep_Builder.hxx>
#include <BRep_Tool.hxx>
#include <Bnd_Box.hxx>
#include <GCPnts_TangentialDeflection.hxx>
#include <GProp_GProps.hxx>
#include <GeomAbs_SurfaceType.hxx>
#include <NCollection_IndexedDataMap.hxx>
#include <NCollection_IndexedMap.hxx>
#include <NCollection_List.hxx>
#include <Poly_Connect.hxx>
#include <Poly_Triangulation.hxx>
#include <ShapeUpgrade_UnifySameDomain.hxx>
#include <Standard_Failure.hxx>
#include <TopExp.hxx>
#include <TopExp_Explorer.hxx>
#include <TopTools_ShapeMapHasher.hxx>
#include <TopoDS.hxx>
#include <TopLoc_Location.hxx>
#include <TopoDS_Compound.hxx>
#include <gp_Pln.hxx>
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
TopoDS_Shape boolean(const TopoDS_Shape &first, const TopoDS_Shape &second) {
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
    throw std::runtime_error(kPullFailed);
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

TopoDS_Shape prism(const TopoDS_Shape &profile, const gp_Vec &along) {
  BRepPrimAPI_MakePrism builder(profile, along);
  builder.Build();
  if (!builder.IsDone()) {
    throw std::runtime_error(kPullFailed);
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

FaceFingerprint face_fingerprint(const Shape &shape, uint32_t index) {
  ShapeMap map;
  TopExp::MapShapes(shape.shape, TopAbs_FACE, map);
  if (index >= static_cast<uint32_t>(map.Extent())) {
    throw std::runtime_error("There is no such face.");
  }
  const TopoDS_Face face = TopoDS::Face(map.FindKey(static_cast<int>(index) + 1));
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

void crash() {
  volatile int *nothing = nullptr;
  *nothing = 1;
}

} // namespace simplecad
