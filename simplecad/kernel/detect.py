"""Reading intent off selected geometry.

Most of SimpleCAD's ease comes from not asking the user things it can work out.
Select a cylindrical face and the software should already know whether it is a
shaft or a hole, how big it is, which way its axis runs and how long it is --
that is what turns "add a thread" into two clicks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .occ import bounding_box


@dataclass(frozen=True)
class CylinderInfo:
    """What a cylindrical face tells us."""

    radius: float
    diameter: float
    origin: tuple[float, float, float]      # a point on the axis, at the low end
    direction: tuple[float, float, float]   # unit axis direction
    length: float
    internal: bool                          # True for a hole, False for a shaft

    @property
    def kind(self) -> str:
        return "hole" if self.internal else "shaft"

    def describe(self) -> str:
        return (
            f"{self.kind} ⌀{self.diameter:.2f} mm × {self.length:.2f} mm"
        )


@dataclass(frozen=True)
class PlaneInfo:
    """What a planar face tells us."""

    center: tuple[float, float, float]
    normal: tuple[float, float, float]      # outward from the solid
    area: float


def _as_face(shape):
    """Cast to ``TopoDS_Face``, or None when it is not one.

    The analyse_* functions promise None for anything that is not the shape
    they describe, so they must not raise when handed a solid or an edge --
    callers reasonably pass whatever the user selected.
    """
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopoDS import TopoDS

    if shape is None or shape.IsNull() or shape.ShapeType() != TopAbs_FACE:
        return None
    return TopoDS.Face_s(shape)


def face_surface_type(face) -> str:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Plane, GeomAbs_Sphere
    from OCP.TopoDS import TopoDS

    shaped = _as_face(face)
    if shaped is None:
        return "shape"
    kind = BRepAdaptor_Surface(shaped).GetType()
    return {
        GeomAbs_Plane: "plane",
        GeomAbs_Cylinder: "cylinder",
        GeomAbs_Cone: "cone",
        GeomAbs_Sphere: "sphere",
    }.get(kind, "surface")


def analyse_cylinder(face) -> CylinderInfo | None:
    """Describe a cylindrical face, or return None if it is not one.

    Internal versus external is decided from the face's outward normal: on a
    shaft it points away from the axis, in a bore it points back toward it.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepTools import BRepTools
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.TopAbs import TopAbs_REVERSED
    from OCP.TopoDS import TopoDS

    shaped = _as_face(face)
    if shaped is None:
        return None
    adaptor = BRepAdaptor_Surface(shaped)
    if adaptor.GetType() != GeomAbs_Cylinder:
        return None

    cylinder = adaptor.Cylinder()
    axis = cylinder.Axis()
    location = axis.Location()
    direction = axis.Direction()
    radius = cylinder.Radius()

    umin, umax, vmin, vmax = BRepTools.UVBounds_s(shaped)
    # v runs along the axis for a cylindrical surface.
    length = abs(vmax - vmin)
    origin = (
        location.X() + direction.X() * vmin,
        location.Y() + direction.Y() * vmin,
        location.Z() + direction.Z() * vmin,
    )

    # Outward normal at the middle of the face, versus the radial direction.
    from OCP.BRepLProp import BRepLProp_SLProps
    from OCP.BRepAdaptor import BRepAdaptor_Surface as _S

    props = BRepLProp_SLProps(_S(shaped), (umin + umax) / 2.0, (vmin + vmax) / 2.0, 1, 1e-6)
    internal = False
    if props.IsNormalDefined():
        normal = props.Normal()
        point = props.Value()
        radial = (
            point.X() - (location.X() + direction.X() * ((vmin + vmax) / 2.0)),
            point.Y() - (location.Y() + direction.Y() * ((vmin + vmax) / 2.0)),
            point.Z() - (location.Z() + direction.Z() * ((vmin + vmax) / 2.0)),
        )
        normal_vec = (normal.X(), normal.Y(), normal.Z())
        if shaped.Orientation() == TopAbs_REVERSED:
            normal_vec = tuple(-v for v in normal_vec)
        dot = sum(a * b for a, b in zip(normal_vec, radial))
        internal = dot < 0.0

    return CylinderInfo(
        radius=radius,
        diameter=radius * 2.0,
        origin=origin,
        direction=(direction.X(), direction.Y(), direction.Z()),
        length=length,
        internal=internal,
    )


def analyse_plane(face) -> PlaneInfo | None:
    """Describe a planar face with an outward normal."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_REVERSED
    from OCP.TopoDS import TopoDS

    shaped = _as_face(face)
    if shaped is None:
        return None
    adaptor = BRepAdaptor_Surface(shaped)
    if adaptor.GetType() != GeomAbs_Plane:
        return None

    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shaped, props)
    centre = props.CentreOfMass()

    direction = adaptor.Plane().Axis().Direction()
    normal = (direction.X(), direction.Y(), direction.Z())
    if shaped.Orientation() == TopAbs_REVERSED:
        normal = tuple(-v for v in normal)
    return PlaneInfo(
        center=(centre.X(), centre.Y(), centre.Z()),
        normal=normal,
        area=props.Mass(),
    )


def cylindrical_faces(shape) -> list:
    """Every cylindrical face of *shape*, largest first."""
    from ..core.naming import sub_shapes

    found = []
    for face in sub_shapes(shape, "face"):
        info = analyse_cylinder(face)
        if info is not None:
            found.append((face, info))
    found.sort(key=lambda item: item[1].radius, reverse=True)
    return found
