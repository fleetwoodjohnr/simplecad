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

class Shape {
public:
  explicit Shape(TopoDS_Shape shape) : shape(std::move(shape)) {}
  TopoDS_Shape shape;
};

std::unique_ptr<Shape> make_box(double dx, double dy, double dz);
std::unique_ptr<Shape> fuse(const Shape &a, const Shape &b);
std::unique_ptr<Shape> unify(const Shape &shape);
std::unique_ptr<Shape> read_brep(rust::Slice<const uint8_t> bytes);
rust::Vec<uint8_t> write_brep(const Shape &shape);
double volume(const Shape &shape);
uint32_t solid_count(const Shape &shape);
bool is_valid(const Shape &shape);
rust::Vec<double> bounds(const Shape &shape);
rust::Vec<FaceData> faces(const Shape &shape);
std::unique_ptr<Shape> push_pull(const Shape &shape, uint32_t face, double distance);
MeshBuffers mesh(const Shape &shape, double deflection, double angle);
void crash();

} // namespace simplecad
