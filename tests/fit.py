"""Does one part fit inside another?

Shared by the thread and fastener tests, because both ask the same question and
because the obvious way to ask it is wrong.

**Booleans cannot answer this on threads.** ``BRepAlgoAPI_Common`` between the
same bolt and nut reported 0 mm3 at one rotation and 875 mm3 twenty degrees
away, and reported 0.065 mm3 where the two solids provably shared their entire
core. A nut whose thread ends up detached from the plate around it -- which is
what an undersize nut is -- comes back as a compound whose floating part the
boolean silently ignores, so the measurement reads zero for a reason that has
nothing to do with fit. A fit test built on that passes and fails at random.

So the question is asked pointwise instead: sample the bolt's surface and count
how much of it is *inside* the nut's material. Slower, and true.
"""

from __future__ import annotations

#: How finely the probe's surface is sampled. Coarse on purpose: what is being
#: measured is whether the part is buried, not the last micron of its profile.
DEFLECTION = 0.25
#: At most this many of those points are classified, taken at an even stride.
#: Each costs about 60 ms against a threaded solid, and the signal being read is
#: a fifth of the surface against none of it -- it does not need six hundred
#: samples to be unambiguous.
SAMPLES = 220
#: Share of the probe that may sit inside and still count as a fit. Not zero:
#: points on a shared boundary -- a seating face, a bore wall -- classify either
#: way, and a handful always land inside.
TOLERANCE = 0.03


def buried_fraction(probe, solid) -> float:
    """What share of *probe*'s surface lies inside *solid*'s material."""
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_IN

    from simplecad.kernel.tessellate import triangulate

    mesh = triangulate(probe, DEFLECTION)
    assert mesh.vertices, "the probe has no surface to sample"
    stride = max(1, len(mesh.vertices) // SAMPLES)
    points = mesh.vertices[::stride]

    classifier = BRepClass3d_SolidClassifier(solid)
    inside = 0
    for point in points:
        classifier.Perform(gp_Pnt(*point), 1e-4)
        if classifier.State() == TopAbs_IN:
            inside += 1
    return inside / len(points)


def turned_in_place(shape, degrees: float, axis_origin=(0.0, 0.0, 0.0)):
    """*shape* rotated about +Z without being allowed to advance.

    The standard way to make a thread misfit on purpose, and the only motion a
    working thread must refuse. A pure axial shift will not do: for a helix,
    sliding along the axis is the same as turning, so a shifted bolt is simply
    one screwed in further and fits perfectly well.
    """
    from simplecad.kernel.occ import make_transform, transformed

    return transformed(
        shape,
        make_transform(
            rotate_axis=(0, 0, 1), rotate_degrees=degrees, origin=axis_origin
        ),
    )
