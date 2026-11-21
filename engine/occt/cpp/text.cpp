#include "shim.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <vector>

#include <BRepAlgoAPI_Common.hxx>
#include <BRepAlgoAPI_Cut.hxx>
#include <BRepAlgoAPI_Fuse.hxx>
#include <BRepBuilderAPI_Transform.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRepGProp.hxx>
#include <BRepPrimAPI_MakePrism.hxx>
#include <Font_FontMgr.hxx>
#include <GProp_GProps.hxx>
#include <StdPrs_BRepFont.hxx>
#include <TopExp_Explorer.hxx>
#include <gp_Ax1.hxx>
#include <gp_Ax3.hxx>

#include "occt/src/lib.rs.h"

namespace simplecad {
namespace {

constexpr double overlap = 0.02;

template <class Operation>
TopoDS_Shape boolean(const TopoDS_Shape &body, const TopoDS_Shape &tool) {
  NCollection_List<TopoDS_Shape> bodies, tools;
  bodies.Append(body);
  tools.Append(tool);
  Operation result;
  result.SetArguments(bodies);
  result.SetTools(tools);
  result.SetNonDestructive(true);
  result.SetRunParallel(true);
  result.Build();
  if (!result.IsDone() || result.Shape().IsNull()) {
    throw std::runtime_error("The text boolean did not finish.");
  }
  return result.Shape();
}

double volume_of(const TopoDS_Shape &shape) {
  GProp_GProps props;
  BRepGProp::VolumeProperties(shape, props);
  return props.Mass();
}

TopoDS_Shape transformed(const TopoDS_Shape &shape, const gp_Trsf &placement) {
  return BRepBuilderAPI_Transform(shape, placement, true).Shape();
}

gp_Trsf translation(const gp_Vec &offset) {
  gp_Trsf shift;
  shift.SetTranslation(offset);
  return shift;
}

bool valid_glyph(const TopoDS_Shape &shape) {
  if (shape.IsNull() || !BRepCheck_Analyzer(shape).IsValid()) {
    return false;
  }
  std::vector<TopoDS_Shape> faces;
  for (TopExp_Explorer face(shape, TopAbs_FACE); face.More(); face.Next()) {
    faces.push_back(face.Current());
  }
  for (std::size_t i = 0; i < faces.size(); ++i) {
    const auto a = bounds(Shape(faces[i]));
    for (std::size_t j = i + 1; j < faces.size(); ++j) {
      const auto b = bounds(Shape(faces[j]));
      if (std::min(a[3], b[3]) - std::max(a[0], b[0]) <= 1e-7 ||
          std::min(a[4], b[4]) - std::max(a[1], b[1]) <= 1e-7) {
        continue;
      }
      GProp_GProps props;
      BRepGProp::SurfaceProperties(boolean<BRepAlgoAPI_Common>(faces[i], faces[j]), props);
      if (props.Mass() > 1e-7) {
        return false;
      }
    }
  }
  return !faces.empty();
}

std::vector<TopoDS_Shape> render(StdPrs_BRepFont &font,
                                 rust::Slice<const TextCharacter> text) {
  std::vector<TopoDS_Shape> glyphs;
  double advance = 0.0;
  for (std::size_t i = 0; i < text.size(); ++i) {
    const auto character = static_cast<char32_t>(text[i].code);
    if (!text[i].space) {
      auto glyph = font.RenderGlyph(character);
      if (!valid_glyph(glyph)) {
        return {};
      }
      glyphs.push_back(transformed(glyph, translation(gp_Vec(advance, 0, 0))));
    }
    const auto next = i + 1 < text.size() ? static_cast<char32_t>(text[i + 1].code) : U'\0';
    advance += font.AdvanceX(character, next);
  }
  return glyphs;
}

struct Glyphs {
  std::vector<TopoDS_Shape> shapes;
  std::string family;
};

Glyphs choose_font(rust::Slice<const TextCharacter> text, double height,
                   rust::Slice<const rust::String> paths, const std::string &family) {
  const auto manager = Font_FontMgr::GetInstance();
  for (const auto &path : paths) {
    try {
      const std::string file(path);
      const auto candidate = manager->CheckFont(file.c_str());
      StdPrs_BRepFont font;
      if (!candidate.IsNull() && font.Init(file.c_str(), height, 0)) {
        auto shapes = render(font, text);
        if (!shapes.empty()) {
          return {std::move(shapes), candidate->FontName().ToCString()};
        }
      }
    } catch (const Standard_Failure &) {
      // A font whose outlines cannot form faces is skipped.
    } catch (const std::runtime_error &) {
      // OCCT could not intersect the faces to check for glyph overlaps.
    }
  }
  StdPrs_BRepFont font;
  if (font.FindAndInit(family.c_str(), Font_FontAspect_Regular, height)) {
    auto shapes = render(font, text);
    if (!shapes.empty()) {
      return {std::move(shapes), family};
    }
  }
  throw std::runtime_error(
      "The selected font family cannot build this text. Try another family or remove unsupported characters.");
}

TopoDS_Shape prism(const TopoDS_Shape &profile, const gp_Vec &direction) {
  return BRepPrimAPI_MakePrism(profile, direction).Shape();
}

} // namespace

TextResult face_text(const Shape &body, const PlaneFrame &plane,
                     rust::Slice<const TextCharacter> text,
                     rust::Slice<const rust::String> font_paths,
                     rust::Str family, const TextSpec &settings) {
  auto glyphs = choose_font(text, settings.height, font_paths, std::string(family));
  auto extent = bounds(Shape(glyphs.shapes.front()));
  for (const auto &glyph : glyphs.shapes) {
    const auto box = bounds(Shape(glyph));
    for (int i = 0; i < 2; ++i) {
      extent[i] = std::min(extent[i], box[i]);
      extent[i + 3] = std::max(extent[i + 3], box[i + 3]);
    }
  }

  const gp_Dir normal(plane.normal[0], plane.normal[1], plane.normal[2]);
  const gp_Dir x = std::abs(normal.Z()) > 0.9
      ? gp::DX()
      : gp_Dir(-normal.Y(), normal.X(), 0);
  gp_Trsf placement;
  placement.SetDisplacement(
      gp_Ax3(gp::Origin(), gp::DZ(), gp::DX()),
      gp_Ax3(gp_Pnt(plane.center[0], plane.center[1], plane.center[2]), normal, x));
  gp_Trsf rotation;
  if (std::abs(settings.rotation) > 1e-12) {
    rotation.SetRotation(gp_Ax1(gp::Origin(), gp::DZ()), settings.rotation * std::acos(-1) / 180.0);
  }
  const auto local = translation(gp_Vec(
      settings.offset_x - (extent[0] + extent[3]) / 2.0,
      settings.offset_y - (extent[1] + extent[4]) / 2.0,
      settings.raised ? -overlap : overlap)).Multiplied(rotation);

  const double signed_depth = settings.raised ? settings.depth + overlap : -(settings.depth + overlap);
  const gp_Vec direction = gp_Vec(normal) * signed_depth;
  auto result = body.shape;
  for (const auto &glyph : glyphs.shapes) {
    const auto profile = transformed(transformed(glyph, local), placement);
    if (!settings.raised) {
      // Verify the blind engraving has material immediately beyond its floor.
      const double half = std::min(overlap, settings.depth * 0.25);
      const auto shift = translation(gp_Vec(normal) * (signed_depth + half));
      const auto probe = prism(transformed(profile, shift), gp_Vec(normal) * (-2.0 * half));
      const auto inside = boolean<BRepAlgoAPI_Common>(body.shape, probe);
      if (volume_of(probe) <= 1e-10 || volume_of(inside) < volume_of(probe) * 0.999) {
        throw std::runtime_error("The engraving is deeper than the material beneath the text. Reduce its depth or move it farther onto the selected face.");
      }
    }
    const auto tool = prism(profile, direction);
    result = settings.raised ? boolean<BRepAlgoAPI_Fuse>(result, tool)
                             : boolean<BRepAlgoAPI_Cut>(result, tool);
  }
  auto tidy = unify(Shape(result));
  if (!is_valid(*tidy) || solid_count(*tidy) == 0) {
    throw std::runtime_error("The text operation did not leave a valid solid. Reduce its size or depth, or move it farther onto the face.");
  }
  if ((volume_of(tidy->shape) - volume_of(body.shape)) * (settings.raised ? 1.0 : -1.0) <= 1e-7) {
    throw std::runtime_error("The text does not overlap the selected face. Move it onto the face or reduce its size.");
  }
  if (settings.raised && solid_count(*tidy) > solid_count(body)) {
    throw std::runtime_error("Some raised letters are detached from the body. Move the text onto the face or reduce its size.");
  }
  return {std::move(tidy), rust::String(glyphs.family)};
}

} // namespace simplecad
