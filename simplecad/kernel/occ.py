"""Thin helpers over OCP. Keeps OCCT idioms out of the feature code."""

from __future__ import annotations

import math

from ..core.errors import check_done, guard


def built_shape(builder, operation: str):
    """Build an OCCT builder and return its shape, checked.

    OCCT builders report ``IsDone() == False`` until ``Build()`` has run, so
    checking before building always looks like a failure. This runs the steps in
    the one order that is correct for every builder.

    Booleans are run in parallel mode. OCCT parallelises them with C++ threads,
    which -- unlike Python threads -- are not blocked by the GIL that the OCP
    bindings hold, so this is the one form of concurrency actually available to
    us. Measured: a thread-groove cut plus fuse drops from 1.17 s to 0.63 s.
    """
    run_parallel = getattr(builder, "SetRunParallel", None)
    if run_parallel is not None:
        try:
            run_parallel(True)
        except Exception:  # noqa: BLE001 - not every builder accepts it
            pass

    build = getattr(builder, "Build", None)
    if build is not None:
        try:
            build()
        except TypeError:
            # Some builders take a progress range; the default call is enough.
            build(None)
    check_done(builder, operation)
    return builder.Shape()


def make_transform(
    translate: tuple[float, float, float] = (0.0, 0.0, 0.0),
    rotate_axis: tuple[float, float, float] | None = None,
    rotate_degrees: float = 0.0,
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
):
    """A ``gp_Trsf`` combining an optional rotation about *origin* then a move."""
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

    transform = gp_Trsf()
    if rotate_axis is not None and abs(rotate_degrees) > 1e-12:
        axis = gp_Ax1(gp_Pnt(*origin), gp_Dir(*rotate_axis))
        transform.SetRotation(axis, math.radians(rotate_degrees))
    if any(abs(v) > 1e-15 for v in translate):
        move = gp_Trsf()
        move.SetTranslation(gp_Vec(*translate))
        transform = move.Multiplied(transform)
    return transform


def axis_transform(origin, direction):
    """Move geometry built on +Z at the world origin onto an arbitrary axis.

    Threads, and anything else swept or revolved about a cylindrical feature,
    are far easier to build upright at the origin and then placed. Shared so the
    thread builder and the round push/pull agree on exactly what "on this axis"
    means -- two independent copies of this is how a tool ends up a hair off the
    face it is meant to be concentric with.
    """
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf

    target = gp_Ax3(gp_Pnt(*origin), gp_Dir(*direction))
    source = gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
    transform = gp_Trsf()
    transform.SetDisplacement(source, target)
    return transform


def transformed(shape, transform):
    """Apply a ``gp_Trsf``, copying so the original is untouched."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform

    with guard("move"):
        builder = BRepBuilderAPI_Transform(shape, transform, True)
        check_done(builder, "move")
        return builder.Shape()


def unify(shape, edges: bool = True, faces: bool = True):
    """Merge coplanar faces and collinear edges left behind by a boolean.

    Without this a Press/Pull leaves a seam right across the side of the part:
    the original face and the new prism face are coplanar but still separate, so
    a box that should have four vertical edges reports eight. That degrades every
    later operation -- filleting "the vertical edges" would catch half of them --
    and it looks wrong. Real CAD unifies after every boolean; so do we.
    """
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain

    try:
        unifier = ShapeUpgrade_UnifySameDomain(shape, edges, faces, False)
        unifier.Build()
        result = unifier.Shape()
        return result if result is not None and not result.IsNull() else shape
    except Exception:  # noqa: BLE001 - a failed tidy-up must not fail the feature
        return shape


def bounding_box(shape, optimal: bool = True):
    """Axis-aligned bounds as ``(min, max)``.

    Uses OCCT's *optimal* bounding box by default. The cheap one is computed
    from surface control points and can overshoot badly on B-spline geometry --
    a threaded shaft reports a radius half again its real one -- which matters
    because build-volume checks and Zoom to Fit both read this.
    """
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    if optimal:
        try:
            BRepBndLib.AddOptimal_s(shape, box, True, True)
        except Exception:  # noqa: BLE001 - fall back to the cheap bound
            BRepBndLib.Add_s(shape, box, True)
    else:
        BRepBndLib.Add_s(shape, box, True)
    if box.IsVoid():
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    low, high = box.CornerMin(), box.CornerMax()
    return ((low.X(), low.Y(), low.Z()), (high.X(), high.Y(), high.Z()))


def compound(shapes):
    """Bundle shapes for display without fusing or changing their identity."""
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    shapes = [shape for shape in shapes if shape is not None]
    if not shapes:
        return None
    if len(shapes) == 1:
        return shapes[0]
    result = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(result)
    for shape in shapes:
        builder.Add(result, shape)
    return result


def volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()


def area(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, props)
    return props.Mass()


def center_of_mass(shape) -> tuple[float, float, float]:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    point = props.CentreOfMass()
    return (point.X(), point.Y(), point.Z())


def is_valid(shape) -> bool:
    from OCP.BRepCheck import BRepCheck_Analyzer

    try:
        return BRepCheck_Analyzer(shape).IsValid()
    except Exception:  # noqa: BLE001
        return False
