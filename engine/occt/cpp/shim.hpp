// The OCCT operations SimpleCAD's engine uses, as plain functions over an
// owned TopoDS_Shape. Declared to Rust in src/lib.rs; see there for contracts.
#pragma once

#include <exception>
#include <memory>
#include <string>

#include <Standard_Failure.hxx>
#include <TopoDS_Shape.hxx>

#include "rust/cxx.h"

// How cxx turns a C++ exception into a Rust Err. Many OCCT exceptions carry no
// message; those report their type rather than an empty error.
namespace rust {
namespace behavior {
template <typename Try, typename Fail>
static void trycatch(Try &&func, Fail &&fail) noexcept try {
  func();
} catch (const Standard_Failure &e) {
  const char *message = e.what();
  const std::string text = message && *message
      ? std::string(message)
      : std::string("The geometry kernel failed (") + e.ExceptionType() + ").";
  fail(text.c_str());
} catch (const std::exception &e) {
  fail(e.what());
}
} // namespace behavior
} // namespace rust

namespace simplecad {

struct MeshBuffers;
struct FaceData;
struct FaceFingerprint;

class Shape {
public:
  explicit Shape(TopoDS_Shape shape) : shape(std::move(shape)) {}
  TopoDS_Shape shape;
};

std::unique_ptr<Shape> make_box(double dx, double dy, double dz);
std::unique_ptr<Shape> make_cylinder(double radius, double height);
std::unique_ptr<Shape> make_sphere(double radius);
std::unique_ptr<Shape> make_cone(double bottom, double top, double height);
std::unique_ptr<Shape> make_torus(double major, double minor);
std::unique_ptr<Shape> make_wedge(double dx, double dy, double dz, double top);
std::unique_ptr<Shape> make_tube(double outer, double inner, double height);
std::unique_ptr<Shape> place(const Shape &shape, rust::Slice<const double> placement);
std::unique_ptr<Shape> move_shape(const Shape &shape, rust::Slice<const double> move);
std::unique_ptr<Shape> shell(const Shape &shape, rust::Slice<const uint32_t> faces, double thickness);
std::unique_ptr<Shape> fuse(const Shape &a, const Shape &b);
std::unique_ptr<Shape> unify(const Shape &shape);
std::unique_ptr<Shape> read_brep(rust::Slice<const uint8_t> bytes);
rust::Vec<uint8_t> write_brep(const Shape &shape);
std::unique_ptr<Shape> located(const Shape &shape, rust::Slice<const double> matrix);
double volume(const Shape &shape);
uint32_t solid_count(const Shape &shape);
uint32_t face_occurrences(const Shape &shape);
bool is_valid(const Shape &shape);
rust::Vec<double> bounds(const Shape &shape);
rust::Vec<FaceData> faces(const Shape &shape);
FaceFingerprint face_fingerprint(const Shape &shape, uint32_t face);
std::unique_ptr<Shape> push_pull(const Shape &shape, uint32_t face, double distance);
MeshBuffers mesh(const Shape &shape, double deflection, double angle);
void crash();

} // namespace simplecad
