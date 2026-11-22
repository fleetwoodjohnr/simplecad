// The OCCT operations SimpleCAD's engine uses, as plain functions over an
// owned TopoDS_Shape. Declared to Rust in src/lib.rs; see there for contracts.
#pragma once

#include <exception>
#include <memory>
#include <string>

#include <Standard_Failure.hxx>
#include <NCollection_List.hxx>
#include <TopoDS_Shape.hxx>
#include <gp_Trsf.hxx>

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
struct EdgeFingerprint;
struct EdgeAxis;
struct EdgeFinishSpec;
struct PlaneFrame;
struct CylinderData;
struct SplitParts;
struct HoleCut;
struct TextFont;
struct ImportedBody;
struct MeshImport;
struct TextCharacter;
struct TextSpec;
struct TextResult;
struct TextureSpec;
struct TextureRegion;
struct ClipSpec;
struct ClipPlacementSpec;
struct ClipOutput;
struct SketchProfile;
struct SketchAxis;

class Shape {
public:
  explicit Shape(TopoDS_Shape shape) : shape(std::move(shape)) {}
  TopoDS_Shape shape;
};

// Shapes passed together, in order (a boolean's tools).
class Shapes {
public:
  NCollection_List<TopoDS_Shape> list;
};

// A gp_Trsf, kept whole: composing it here, as OCP does, keeps placements
// identical to the Python app's to the last bit.
class Trsf {
public:
  explicit Trsf(gp_Trsf trsf) : trsf(trsf) {}
  gp_Trsf trsf;
};

std::unique_ptr<Shape> extrude_sketch(const SketchProfile &profile,
                                      double distance, bool symmetric);
std::unique_ptr<Shape> revolve_sketch(const SketchProfile &profile,
                                      const SketchAxis &axis, double radians);
std::unique_ptr<Shape> sweep_sketch(const SketchProfile &profile, const SketchProfile &path,
                                    bool keep_orientation, bool contact);
std::unique_ptr<Shape> loft_sketches(rust::Slice<const SketchProfile> sections, bool ruled);
rust::Vec<double> sketch_path_offsets(const SketchProfile &path, uint32_t count);
std::unique_ptr<Shape> make_box(double dx, double dy, double dz);
TextResult face_text(const Shape &body, const PlaneFrame &plane,
                     rust::Slice<const TextCharacter> text,
                     rust::Slice<const TextFont> fonts,
                     rust::Str family, const TextSpec &settings);
std::unique_ptr<Shape> face_texture(const Shape &body, const Shape &face, rust::Str pattern,
                                    rust::Str mode, const TextureSpec &settings,
                                    rust::Slice<const TextureRegion> text_regions);
std::unique_ptr<Shape> section_cutter(const Shape &face, rust::Slice<const double> normal,
                                      double boundary, double depth);
ClipOutput clip_connectors(const Shape &body_a, const Shape &body_b,
                           const Shape &face_a, const Shape &face_b,
                           const ClipSpec &clip, const ClipPlacementSpec &placement,
                           rust::Slice<const double> manual_points);
std::unique_ptr<Shape> make_cylinder(double radius, double height);
std::unique_ptr<Shape> sweep_thread_tooth(double radius, double pitch, double length,
                                         bool left_hand, rust::Slice<const double> profile);
std::unique_ptr<Shape> make_sphere(double radius);
std::unique_ptr<Shape> make_cone(double bottom, double top, double height);
std::unique_ptr<Shape> make_torus(double major, double minor);
std::unique_ptr<Shape> make_wedge(double dx, double dy, double dz, double top);
std::unique_ptr<Shape> make_tube(double outer, double inner, double height);
std::unique_ptr<Shape> polygon_profile(rust::Slice<const double> points, double height);
std::unique_ptr<Shape> oval(double width, double depth, double height);
std::unique_ptr<Shape> pill(double width, double depth, double height);
std::unique_ptr<Shape> crescent(double size, double height, double thickness);
std::unique_ptr<Shape> place(const Shape &shape, rust::Slice<const double> placement);
std::unique_ptr<Shape> move_shape(const Shape &shape, rust::Slice<const double> move);
std::unique_ptr<Shape> scale_shape(const Shape &shape, rust::Slice<const double> pivot,
                                   rust::Slice<const double> factors, bool uniform);
std::unique_ptr<Shape> shell(const Shape &shape, rust::Slice<const uint32_t> faces, double thickness);
std::unique_ptr<Shape> draft_angle(const Shape &shape, rust::Slice<const uint32_t> faces,
                                   rust::Slice<const double> origin, rust::Slice<const double> pull,
                                   double radians);
std::unique_ptr<Shape> fuse(const Shape &a, const Shape &b);
std::unique_ptr<Shape> unify(const Shape &shape);
std::unique_ptr<Shape> read_brep(rust::Slice<const uint8_t> bytes);
rust::Vec<uint8_t> write_brep(const Shape &shape);
rust::Vec<ImportedBody> read_step(rust::Slice<const uint8_t> bytes, rust::Str fallback_name);
rust::Vec<ImportedBody> read_iges_file(rust::Str path, rust::Str fallback_name);
rust::Vec<ImportedBody> read_brep_parts(rust::Slice<const uint8_t> bytes, rust::Str fallback_name);
MeshImport import_mesh(rust::Slice<const double> points, rust::Slice<const uint32_t> triangles,
                       rust::Slice<const uint32_t> boundaries);
rust::Vec<uint8_t> write_step(const Shapes &shapes);
std::unique_ptr<Shape> located(const Shape &shape, rust::Slice<const double> matrix);
double volume(const Shape &shape);
double surface_area(const Shape &shape);
rust::Vec<double> volume_center(const Shape &shape);
double minimum_distance(const Shape &first, const Shape &second);
uint32_t solid_count(const Shape &shape);
uint32_t face_occurrences(const Shape &shape);
bool is_valid(const Shape &shape);
rust::Vec<double> bounds(const Shape &shape);
rust::Vec<FaceData> faces(const Shape &shape);
FaceFingerprint face_fingerprint(const Shape &shape, uint32_t face);
rust::Vec<EdgeFingerprint> edge_fingerprints(const Shape &shape);
EdgeAxis edge_axis(const Shape &shape, uint32_t index);
rust::Vec<FaceFingerprint> vertex_fingerprints(const Shape &shape);
std::unique_ptr<Shape> finish_edges(const Shape &shape, rust::Slice<const uint32_t> indices,
                                    double size, bool fillet);
std::unique_ptr<Shape> finish_edge_specs(const Shape &shape, rust::Slice<const EdgeFinishSpec> specs,
                                         bool fillet);
std::unique_ptr<Shape> push_pull(const Shape &shape, uint32_t face, double distance);
std::unique_ptr<Shape> resize_round(const Shape &shape, uint32_t face, double delta);
std::unique_ptr<Shape> hole_cut(const Shape &shape, const HoleCut &cut);
double first_ray_hit(const Shape &shape, rust::Slice<const double> position,
                     rust::Slice<const double> inward);
SplitParts split_solid(const Shape &shape, rust::Slice<const double> point, rust::Slice<const double> normal);
MeshBuffers mesh(const Shape &shape, double deflection, double angle);
std::unique_ptr<Trsf> trsf_identity();
std::unique_ptr<Trsf> trsf_rotation(rust::Slice<const double> pivot, rust::Slice<const double> axis, double angle);
std::unique_ptr<Trsf> trsf_translation(rust::Slice<const double> by);
std::unique_ptr<Trsf> trsf_axis_frame(rust::Slice<const double> origin, rust::Slice<const double> normal);
std::unique_ptr<Trsf> trsf_mirror(rust::Slice<const double> origin, rust::Slice<const double> normal);
std::unique_ptr<Trsf> trsf_multiplied(const Trsf &a, const Trsf &b);
rust::Vec<double> trsf_point(const Trsf &trsf, rust::Slice<const double> point);
rust::Vec<double> trsf_values(const Trsf &trsf);
std::unique_ptr<Shape> transform_shape(const Shape &shape, const Trsf &trsf, bool copy);
std::unique_ptr<Shape> sub_face(const Shape &shape, uint32_t face);
std::unique_ptr<Shape> sub_edge(const Shape &shape, uint32_t edge);
void messages_to_stderr();
std::unique_ptr<Shape> sub_vertex(const Shape &shape, uint32_t vertex);
PlaneFrame plane_frame(const Shape &face);
CylinderData cylinder_data(const Shape &face);
rust::Vec<double> x_extent(const Shape &shape);
std::unique_ptr<Shapes> shapes_new();
void shapes_push(Shapes &shapes, const Shape &shape);
std::unique_ptr<Shape> shapes_compound(const Shapes &shapes);
std::unique_ptr<Shape> fuse_copies(const Shape &shape, const Shapes &copies);
std::unique_ptr<Shape> combine(uint8_t operation, const Shape &target, const Shapes &tools, double fuzzy);
uint32_t distinct_solids(const Shape &shape);
double join_bridge_gap(const Shape &shape);
void crash();

} // namespace simplecad
