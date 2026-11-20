#include "shim.hpp"

#include <cmath>
#include <stdexcept>

#include <BRepBuilderAPI_MakeEdge.hxx>
#include <BRepBuilderAPI_MakePolygon.hxx>
#include <BRepBuilderAPI_MakeWire.hxx>
#include <BRepLib.hxx>
#include <BRepOffsetAPI_MakePipeShell.hxx>
#include <Geom2d_Line.hxx>
#include <Geom2d_TrimmedCurve.hxx>
#include <Geom_CylindricalSurface.hxx>
#include <TopoDS_Edge.hxx>
#include <TopoDS_Wire.hxx>
#include <gp_Ax3.hxx>

namespace simplecad {

std::unique_ptr<Shape> sweep_thread_tooth(double radius, double pitch, double length,
                                         bool left_hand, rust::Slice<const double> profile) {
  if (radius <= 0.0 || pitch <= 0.0 || length <= 0.0 || profile.size() != 8) {
    throw std::runtime_error("A thread needs a positive diameter, pitch and length.");
  }
  const double turn = 2.0 * std::acos(-1.0);
  const double slope = pitch / turn;
  const double span = turn * (length / pitch) * std::hypot(1.0, slope);
  // On a cylinder, U is the angle and V is the axial position. A straight
  // line on that surface makes a helix without approximating it by segments.
  Handle(Geom_CylindricalSurface) surface = new Geom_CylindricalSurface(gp_Ax3(), radius);
  Handle(Geom2d_Line) line = new Geom2d_Line(gp_Pnt2d(0, 0),
                                            gp_Dir2d(left_hand ? -1.0 : 1.0, slope));
  Handle(Geom2d_TrimmedCurve) curve = new Geom2d_TrimmedCurve(line, 0.0, span);
  TopoDS_Edge edge = BRepBuilderAPI_MakeEdge(curve, surface).Edge();
  BRepLib::BuildCurves3d(edge);
  TopoDS_Wire spine = BRepBuilderAPI_MakeWire(edge).Wire();

  BRepBuilderAPI_MakePolygon tooth;
  for (size_t i = 0; i < profile.size(); i += 2) {
    tooth.Add(gp_Pnt(profile[i], 0.0, profile[i + 1]));
  }
  tooth.Close();
  BRepOffsetAPI_MakePipeShell sweep(spine);
  sweep.SetMode(gp_Dir(0, 0, 1));
  sweep.Add(tooth.Wire(), false, false);
  sweep.Build();
  if (!sweep.IsDone() || !sweep.MakeSolid()) {
    throw std::runtime_error("The thread geometry could not be swept along its helix.");
  }
  return std::make_unique<Shape>(sweep.Shape());
}

} // namespace simplecad
