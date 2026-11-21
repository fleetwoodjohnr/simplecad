#pragma once

#include <array>
#include <cmath>
#include <memory>
#include <stdexcept>
#include <vector>

#include <geos_c.h>

namespace simplecad { class Shape; }
namespace simplecad::texture {

using Point = std::array<double, 2>;
using Bounds = std::array<double, 4>;
using Ring = std::vector<Point>;
constexpr double pi = 3.14159265358979323846;

struct DestroyGeometry {
  GEOSContextHandle_t context;
  void operator()(GEOSGeometry *geometry) const { GEOSGeom_destroy_r(context, geometry); }
};
using Geometry = std::unique_ptr<GEOSGeometry, DestroyGeometry>;

// All geometry returned here has the operation's context as its deleter.
class Layout {
public:
  GEOSContextHandle_t context = GEOS_init_r();

  Layout() {
    if (!context) {
      throw std::runtime_error("Could not start the texture layout engine.");
    }
  }
  Layout(const Layout &) = delete;
  Layout &operator=(const Layout &) = delete;
  ~Layout() { GEOS_finish_r(context); }

  Geometry own(GEOSGeometry *geometry) const {
    if (!geometry) {
      throw std::runtime_error("The texture could not be laid out on this face.");
    }
    return Geometry(geometry, {context});
  }

  GEOSCoordSequence *sequence(const Ring &points) const {
    auto *result = GEOSCoordSeq_create_r(context, static_cast<unsigned>(points.size()), 2);
    if (!result) {
      throw std::runtime_error("Could not allocate the texture outline.");
    }
    for (unsigned i = 0; i < points.size(); ++i) {
      if (!GEOSCoordSeq_setXY_r(context, result, i, points[i][0], points[i][1])) {
        GEOSCoordSeq_destroy_r(context, result);
        throw std::runtime_error("The texture outline has invalid coordinates.");
      }
    }
    return result;
  }

  Geometry line(const Ring &points) const {
    return own(GEOSGeom_createLineString_r(context, sequence(points)));
  }

  Geometry polygon(const Ring &outside, const std::vector<Ring> &inside = {}) const {
    auto make_ring = [this](Ring points) {
      if (points.front() != points.back()) {
        points.push_back(points.front());
      }
      return own(GEOSGeom_createLinearRing_r(context, sequence(points)));
    };
    auto shell = make_ring(outside);
    std::vector<Geometry> rings;
    for (const auto &hole : inside) {
      rings.push_back(make_ring(hole));
    }
    std::vector<GEOSGeometry *> holes;
    holes.reserve(rings.size());
    for (auto &ring : rings) {
      holes.push_back(ring.release());
    }
    return own(GEOSGeom_createPolygon_r(context, shell.release(), holes.data(),
                                      static_cast<unsigned>(holes.size())));
  }

  Geometry rectangle(double x0, double y0, double x1, double y1) const {
    return polygon({{x1, y0}, {x1, y1}, {x0, y1}, {x0, y0}, {x1, y0}});
  }

  Geometry unite(std::vector<Geometry> shapes) const {
    std::vector<GEOSGeometry *> inputs;
    inputs.reserve(shapes.size());
    for (auto &shape : shapes) {
      inputs.push_back(shape.release());
    }
    auto collection = own(GEOSGeom_createCollection_r(context, GEOS_GEOMETRYCOLLECTION,
                                                       inputs.data(), static_cast<unsigned>(inputs.size())));
    return own(GEOSUnaryUnion_r(context, collection.get()));
  }

  Geometry buffer(const GEOSGeometry *shape, double distance, int segments = 16,
                  int cap = GEOSBUF_CAP_ROUND, int join = GEOSBUF_JOIN_ROUND) const {
    return own(GEOSBufferWithStyle_r(context, shape, distance, segments, cap, join, 5.0));
  }

  Geometry intersection(const GEOSGeometry *a, const GEOSGeometry *b) const {
    return own(GEOSIntersection_r(context, a, b));
  }

  Geometry shift(const GEOSGeometry *shape, Point offset, double rotation = 0.0) const {
    const double radians = rotation * (pi / 180.0);
    auto exact = [](double value) { return std::abs(value) < 2.5e-16 ? 0.0 : value; };
    std::array<double, 4> transform{exact(std::cos(radians)), exact(std::sin(radians)), offset[0], offset[1]};
    return own(GEOSGeom_transformXY_r(context, shape, [](double *x, double *y, void *data) {
      const auto &[c, s, u, v] = *static_cast<const std::array<double, 4> *>(data);
      const double original_x = *x;
      *x = c * original_x - s * *y + u;
      *y = s * original_x + c * *y + v;
      return 1;
    }, &transform));
  }

  Ring coordinates(const GEOSGeometry *ring) const {
    const auto *sequence = GEOSGeom_getCoordSeq_r(context, ring);
    unsigned count = 0;
    if (!sequence || !GEOSCoordSeq_getSize_r(context, sequence, &count)) {
      throw std::runtime_error("A texture element has no boundary.");
    }
    Ring result(count);
    for (unsigned i = 0; i < count; ++i) {
      if (!GEOSCoordSeq_getXY_r(context, sequence, i, &result[i][0], &result[i][1])) {
        throw std::runtime_error("The texture outline could not be read.");
      }
    }
    return result;
  }

  Bounds bounds(const GEOSGeometry *shape) const {
    Bounds b{};
    if (!GEOSGeom_getExtent_r(context, shape, &b[0], &b[1], &b[2], &b[3])) {
      throw std::runtime_error("The texture clears the entire face.");
    }
    return b;
  }

  double area(const GEOSGeometry *shape) const {
    double value = 0;
    if (!GEOSArea_r(context, shape, &value)) {
      throw std::runtime_error("The texture area could not be measured.");
    }
    return value;
  }

  bool empty(const GEOSGeometry *shape) const {
    const auto empty = GEOSisEmpty_r(context, shape);
    if (empty > 1) {
      throw std::runtime_error("The texture outline could not be read.");
    }
    return empty;
  }

  std::vector<const GEOSGeometry *> polygons(const GEOSGeometry *shape, double min_area = 1e-8) const {
    const auto type = GEOSGeomTypeId_r(context, shape);
    if (type == GEOS_POLYGON) {
      return area(shape) > min_area ? std::vector{shape} : std::vector<const GEOSGeometry *>{};
    }
    std::vector<const GEOSGeometry *> result;
    if (type == GEOS_MULTIPOLYGON || type == GEOS_GEOMETRYCOLLECTION) {
      const int count = GEOSGetNumGeometries_r(context, shape);
      for (int i = 0; i < count; ++i) {
        const auto parts = polygons(GEOSGetGeometryN_r(context, shape, i), min_area);
        result.insert(result.end(), parts.begin(), parts.end());
      }
    }
    return result;
  }
};

inline double distance(Point a, Point b) {
  return std::hypot(a[0] - b[0], a[1] - b[1]);
}

Geometry planar_profile(const Layout &layout, const Shape &face);

} // namespace simplecad::texture
