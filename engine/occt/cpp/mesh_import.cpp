#include "shim.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <stdexcept>
#include <unordered_map>
#include <vector>

#include <BRepBuilderAPI_MakeEdge.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_MakeSolid.hxx>
#include <BRepBuilderAPI_MakeVertex.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRepGProp.hxx>
#include <BRep_Builder.hxx>
#include <GProp_GProps.hxx>
#include <Poly_Triangulation.hxx>
#include <ShapeUpgrade_UnifySameDomain.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Compound.hxx>
#include <TopoDS_Edge.hxx>
#include <TopoDS_Shell.hxx>
#include <TopoDS_Solid.hxx>
#include <TopoDS_Vertex.hxx>
#include <TopoDS_Wire.hxx>
#include <gp_Pln.hxx>
#include <gp_Vec.hxx>

#include "occt/src/lib.rs.h"

namespace simplecad {
namespace {

constexpr uint32_t kUnifyTriangleLimit = 20000;
const char *kInvalid = "The mesh contains no usable triangles.";

std::unique_ptr<Shape> display_only(const std::vector<gp_Pnt> &points, rust::Slice<const uint32_t> triangles) {
  occ::handle<Poly_Triangulation> mesh = new Poly_Triangulation(
      static_cast<int>(points.size()), static_cast<int>(triangles.size() / 3), false);
  for (std::size_t i = 0; i < points.size(); ++i) {
    mesh->SetNode(static_cast<int>(i) + 1, points[i]);
  }
  for (std::size_t i = 0; i < triangles.size(); i += 3) {
    mesh->SetTriangle(static_cast<int>(i / 3) + 1,
        Poly_Triangle(triangles[i] + 1, triangles[i + 1] + 1, triangles[i + 2] + 1));
  }
  BRep_Builder builder;
  TopoDS_Face face;
  builder.MakeFace(face);
  builder.UpdateFace(face, mesh);
  return std::make_unique<Shape>(face);
}

struct SharedEdge {
  TopoDS_Edge shape;
  unsigned uses = 0;
};

TopoDS_Shape component(const std::vector<gp_Pnt> &points, rust::Slice<const uint32_t> indices,
                       uint32_t begin, uint32_t end, uint32_t &open_components) {
  BRep_Builder builder;
  TopoDS_Shell shell;
  builder.MakeShell(shell);
  std::unordered_map<uint32_t, TopoDS_Vertex> vertices;
  std::unordered_map<uint64_t, SharedEdge> edges;
  const auto vertex = [&](uint32_t index) {
    auto &found = vertices[index];
    if (found.IsNull()) {
      found = BRepBuilderAPI_MakeVertex(points[index]).Vertex();
    }
    return found;
  };
  double six_volume = 0.0;
  // Use a point on this component as origin, so a distant mesh still has an
  // accurate winding test.
  const auto origin = points[indices[begin]];
  for (uint32_t i = begin; i < end; i += 3) {
    const gp_Vec a(origin, points[indices[i]]), b(origin, points[indices[i + 1]]), c(origin, points[indices[i + 2]]);
    six_volume += a.Dot(b.Crossed(c));
  }
  unsigned faces = 0;
  for (uint32_t i = begin; i < end; i += 3) {
    std::array<uint32_t, 3> corners = { indices[i], indices[i + 1], indices[i + 2] };
    if (six_volume < 0.0) {
      std::swap(corners[0], corners[2]);
    }
    const auto a = points[corners[0]], b = points[corners[1]], c = points[corners[2]];
    const auto normal = gp_Vec(a, b).Crossed(gp_Vec(a, c));
    if (normal.Magnitude() < 1e-12) {
      continue;
    }
    TopoDS_Wire wire;
    builder.MakeWire(wire);
    std::array<SharedEdge *, 3> references{};
    for (unsigned side = 0; side < 3; ++side) {
      const auto from = corners[side], to = corners[(side + 1) % 3];
      const auto low = std::min(from, to), high = std::max(from, to);
      const auto key = (static_cast<uint64_t>(low) << 32) | high;
      auto &edge = edges[key];
      if (edge.shape.IsNull()) {
        edge.shape = BRepBuilderAPI_MakeEdge(vertex(low), vertex(high)).Edge();
      }
      builder.Add(wire, from == low ? edge.shape : edge.shape.Reversed());
      references[side] = &edge;
    }
    BRepBuilderAPI_MakeFace face(gp_Pln(a, gp_Dir(normal)), wire, true);
    if (face.IsDone()) {
      builder.Add(shell, face.Face());
      ++faces;
      for (auto *edge : references) ++edge->uses;
    }
  }
  if (faces == 0) return {};
  const bool closed = std::all_of(edges.begin(), edges.end(), [](const auto &edge) {
    return edge.second.uses == 0 || edge.second.uses == 2;
  });
  shell.Closed(closed);
  BRepBuilderAPI_MakeSolid maker(shell);
  if (maker.IsDone()) {
    const auto solid = maker.Solid();
    const double volume = std::abs(six_volume) / 6.0;
    bool valid_solid = closed;
    if (!valid_solid && volume > 0 && BRepCheck_Analyzer(solid).IsValid()) {
      GProp_GProps properties;
      BRepGProp::VolumeProperties(solid, properties);
      valid_solid = std::abs(properties.Mass() - volume) <= 1e-6 * std::max(volume, 1.0);
    }
    if (valid_solid) {
      if ((end - begin) / 3 > kUnifyTriangleLimit) return solid;
      ShapeUpgrade_UnifySameDomain merge(solid, true, true, false);
      merge.Build();
      return merge.Shape();
    }
  }
  ++open_components;
  return shell;
}

} // namespace

MeshImport import_mesh(rust::Slice<const double> coordinates, rust::Slice<const uint32_t> triangles,
                       rust::Slice<const uint32_t> boundaries) {
  if (coordinates.empty() || coordinates.size() % 3 != 0 || triangles.empty() || triangles.size() % 3 != 0 ||
      !std::all_of(coordinates.begin(), coordinates.end(), [](double value) { return std::isfinite(value); }) ||
      !std::all_of(triangles.begin(), triangles.end(), [&](uint32_t index) { return index < coordinates.size() / 3; })) {
    throw std::runtime_error(kInvalid);
  }
  std::vector<gp_Pnt> points;
  points.reserve(coordinates.size() / 3);
  for (std::size_t i = 0; i < coordinates.size(); i += 3) {
    points.emplace_back(coordinates[i], coordinates[i + 1], coordinates[i + 2]);
  }
  if (boundaries.empty()) return {display_only(points, triangles), 0};
  if (boundaries.size() < 2 || boundaries[0] != 0 || boundaries[boundaries.size() - 1] != triangles.size()) {
    throw std::runtime_error(kInvalid);
  }
  std::vector<TopoDS_Shape> shapes;
  uint32_t open_components = 0;
  for (std::size_t i = 1; i < boundaries.size(); ++i) {
    const auto begin = boundaries[i - 1], end = boundaries[i];
    if (end <= begin || end > triangles.size() || end % 3 != 0) throw std::runtime_error(kInvalid);
    auto part = component(points, triangles, begin, end, open_components);
    if (!part.IsNull()) shapes.push_back(part);
  }
  if (shapes.empty()) throw std::runtime_error(kInvalid);
  if (shapes.size() == 1) return {std::make_unique<Shape>(shapes.front()), open_components};
  BRep_Builder builder;
  TopoDS_Compound assembly;
  builder.MakeCompound(assembly);
  for (const auto &shape : shapes) builder.Add(assembly, shape);
  return {std::make_unique<Shape>(assembly), open_components};
}

} // namespace simplecad
