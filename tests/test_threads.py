"""Threads: standards, recommendation, geometry, and -- above all -- fit.

The last test here is the one that matters. SimpleCAD promises that a generated
thread pair assembles without the user calculating tolerances, so the pair is
checked by intersecting the bolt with the nut's metal and asserting they do not
share space.
"""

from __future__ import annotations

import math

import pytest

from simplecad.kernel.detect import analyse_cylinder, cylindrical_faces
from simplecad.kernel.occ import bounding_box, is_valid, volume
from simplecad.kernel.thread_specs import (
    by_designation, clearance_for, clearance_presets, load_sizes,
    printer_profile, recommend,
)
from simplecad.kernel.printing import Severity, check_overhangs
from simplecad.kernel.threads import (
    ThreadResult, apply_thread, require_modelled, thread_form,
    thread_profile_points, thread_solid,
)

from .fit import TOLERANCE


def plate_with_hole(size=40.0, thickness=10.0, hole_radius=3.0):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    plate = BRepPrimAPI_MakeBox(size, size, thickness).Shape()
    bore = BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(size / 2, size / 2, -1), gp_Dir(0, 0, 1)),
        hole_radius, thickness + 2,
    ).Shape()
    cut = BRepAlgoAPI_Cut(plate, bore)
    cut.Build()
    return cut.Shape()


def shaft(radius=3.0, height=12.0, at=(0.0, 0.0, 0.0)):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    return BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(*at), gp_Dir(0, 0, 1)), radius, height
    ).Shape()


# ----------------------------------------------------------------------
# Standards
# ----------------------------------------------------------------------
def test_every_standard_loads():
    codes = {size.code for size in load_sizes()}
    assert codes == {"printed", "iso_metric", "unc", "unf", "bsp", "npt"}
    assert len(load_sizes()) > 80


def test_iso_derived_dimensions_match_the_standard():
    m6 = by_designation("M6")
    assert m6.pitch == pytest.approx(1.0)
    # ISO 68-1: H = P * sqrt(3) / 2 for a 60-degree form.
    assert m6.height == pytest.approx(1.0 * math.sqrt(3) / 2, rel=1e-6)
    assert m6.tap_drill == pytest.approx(4.917, abs=0.01)   # 5.0 mm drill
    assert m6.pitch_diameter == pytest.approx(5.350, abs=0.01)


def test_a_six_millimetre_hole_leads_with_the_printable_size():
    """SimpleCAD makes parts to print, so the printable series is offered first.

    The spec's original example was M6, and M6 is still offered -- second, and
    still exact. What changed is which one a user gets by pressing Enter: an ISO
    tooth at a 1 mm pitch is 0.61 mm deep with a 63-degree overhang, which is a
    support-hungry sliver in plastic. P6 is the one that prints.
    """
    best = recommend(6.0, internal=True)[0]
    assert best.designation == "P6"
    assert best.exact
    assert "M6" in [r.designation for r in recommend(6.0, internal=True, limit=8)]


def test_the_iso_size_is_still_reachable_by_name():
    assert by_designation("M6").pitch == pytest.approx(1.0)
    assert recommend(6.0, internal=True, code="iso_metric")[0].designation == "M6"


def test_recommendation_prefers_metric_over_imperial_on_a_near_tie():
    metric = [
        r.designation for r in recommend(6.2, internal=True, limit=8)
        if r.size.code != "printed"
    ]
    assert metric[0] == "M6"


def test_a_genuine_imperial_size_still_wins_when_exact():
    imperial = [
        r.designation for r in recommend(0.25 * 25.4, internal=False, limit=8)
        if r.size.code != "printed"
    ]
    assert imperial[0] == "1/4-20"


def test_twelve_millimetre_shaft_suggests_the_printable_coarse_size():
    best = recommend(12.0, internal=False)[0]
    assert best.designation == "P12"
    assert best.size.pitch == pytest.approx(3.0)


# ----------------------------------------------------------------------
# Detection
# ----------------------------------------------------------------------
def test_a_bore_is_recognised_as_a_hole():
    faces = cylindrical_faces(plate_with_hole())
    assert len(faces) == 1
    info = faces[0][1]
    assert info.internal is True
    assert info.diameter == pytest.approx(6.0)
    assert info.length == pytest.approx(10.0)


def test_a_cylinder_is_recognised_as_a_shaft():
    info = cylindrical_faces(shaft(6.0, 15.0))[0][1]
    assert info.internal is False
    assert info.diameter == pytest.approx(12.0)


# ----------------------------------------------------------------------
# Geometry
# ----------------------------------------------------------------------
@pytest.mark.parametrize("designation,length", [("M3", 6.0), ("M6", 10.0), ("M12", 16.0), ("M20", 20.0)])
def test_thread_solid_is_valid_and_correctly_sized(designation, length):
    size = by_designation(designation)
    solid = thread_solid(size.diameter, size.pitch, length)

    assert is_valid(solid)
    low, high = bounding_box(solid)
    assert high[0] == pytest.approx(size.diameter / 2, abs=0.05)

    core = math.pi * (size.external_minor / 2) ** 2 * length
    full = math.pi * (size.diameter / 2) ** 2 * length
    assert core * 0.95 < volume(solid) < full


def test_external_thread_removes_material_from_a_shaft():
    size = by_designation("M6")
    blank = shaft(size.diameter / 2, 12.0)
    before = volume(blank)

    result = apply_thread(
        blank, size=size, origin=(0, 0, 0), direction=(0, 0, 1),
        length=10.0, internal=False,
    )
    assert result.modelled
    assert is_valid(result.shape)
    assert volume(result.shape) < before


def test_internal_thread_adds_material_to_a_hole():
    size = by_designation("M6")
    plate = plate_with_hole(hole_radius=size.diameter / 2)
    before = volume(plate)

    result = apply_thread(
        plate, size=size, origin=(20, 20, 0), direction=(0, 0, 1),
        length=10.0, internal=True, clearance="normal",
    )
    assert result.modelled
    assert is_valid(result.shape)
    assert volume(result.shape) > before, "nut metal must protrude into the bore"


def test_a_cosmetic_fallback_is_rejected_by_user_facing_features():
    unchanged = object()
    with pytest.raises(Exception, match="helix failed"):
        require_modelled(ThreadResult(unchanged, False, "helix failed"))


# ----------------------------------------------------------------------
# The promise: a generated pair fits
# ----------------------------------------------------------------------
def _buried(bolt, nut) -> float:
    """What share of the bolt's surface lies inside the nut's metal.

    See :mod:`tests.fit` for why this is not a boolean.
    """
    from .fit import buried_fraction

    return buried_fraction(bolt, nut)


def _pair(designation: str, clearance, length: float = 10.0):
    """A bolt at nominal and the nut cut to accept it."""
    from simplecad.kernel.occ import make_transform, transformed

    size = by_designation(designation)
    bolt = thread_solid(size.diameter, size.pitch, length)
    plate = plate_with_hole(size=40.0, thickness=length, hole_radius=size.diameter / 2)
    nut = apply_thread(
        plate, size=size, origin=(20, 20, 0), direction=(0, 0, 1),
        length=length, internal=True, clearance=clearance,
    )
    assert nut.modelled
    seated = transformed(bolt, make_transform(translate=(20.0, 20.0, 0.0)))
    return seated, nut.shape, size


@pytest.mark.slow
@pytest.mark.parametrize("designation", ["P6", "P12"])
def test_a_matched_pair_assembles_without_interference(designation):
    """A bolt at nominal must not collide with its nut's thread metal."""
    bolt, nut, _size = _pair(designation, "normal")
    buried = _buried(bolt, nut)
    assert buried < TOLERANCE, (
        f"{designation}: {buried:.1%} of the bolt is inside the nut's metal"
    )


@pytest.mark.slow
def test_the_fit_check_can_actually_detect_a_misfit():
    """Control for the test above.

    Without this, a fit test that always measured zero would pass whether or not
    the clearance logic worked.

    The mis-fit is a bolt **turned in place**: rotated about its axis without
    being allowed to advance, which is the one motion a thread must refuse.
    A pure axial shift would not do -- for a helix, sliding along the axis is
    the same as turning, so a shifted bolt is simply a bolt screwed in further
    and fits perfectly well.
    """
    from .fit import turned_in_place

    bolt, nut, _size = _pair("P12", "normal")
    clean = _buried(bolt, nut)

    clashing = _buried(turned_in_place(bolt, 60.0, (20.0, 20.0, 0.0)), nut)

    assert clashing > 0.10, "a bolt turned without advancing must bind"
    assert clashing > clean * 5, (
        f"{clashing:.1%} buried is not clearly above the seated pair's {clean:.1%}"
    )


@pytest.mark.slow
def test_tighter_clearance_leaves_less_room():
    """Clearance must actually change the geometry, in the right direction."""
    size = by_designation("M6")
    volumes = {}
    for preset in ("tight", "normal", "loose"):
        plate = plate_with_hole(thickness=10.0, hole_radius=size.diameter / 2)
        result = apply_thread(
            plate, size=size, origin=(20, 20, 0), direction=(0, 0, 1),
            length=10.0, internal=True, clearance=preset,
        )
        volumes[preset] = volume(result.shape)

    assert clearance_for("tight") < clearance_for("normal") < clearance_for("loose")
    # More clearance = a wider bore = less metal left in the nut.
    assert volumes["tight"] > volumes["normal"] > volumes["loose"]


def test_clearance_presets_cover_the_documented_range():
    presets = clearance_presets()
    assert set(presets) == {"tight", "normal", "loose", "very_loose"}
    assert presets["normal"]["clearance"] == pytest.approx(0.20)


# ----------------------------------------------------------------------
# Threading a feature that is not exactly nominal
# ----------------------------------------------------------------------
def _shaft_diameters(shape) -> list[float]:
    return sorted({round(info.diameter, 2) for _f, info in cylindrical_faces(shape)})


def test_an_oversize_shaft_is_turned_down_to_nominal():
    """A die cuts the shaft to size. So should we.

    Threading a 12.4 mm shaft as M12 must not leave a sleeve of the original
    diameter wrapped around the thread.
    """
    size = by_designation("M12")
    blank = shaft(12.4 / 2, 20.0)
    result = apply_thread(
        blank, size=size, origin=(0, 0, 0), direction=(0, 0, 1),
        length=20.0, internal=False, feature_diameter=12.4,
    )
    assert result.modelled and is_valid(result.shape)
    assert 12.4 not in _shaft_diameters(result.shape), (
        "an unthreaded sleeve at the original diameter survived"
    )
    assert "turned down" in result.message


def test_an_undersize_shaft_says_the_thread_is_shallow():
    size = by_designation("M12")
    blank = shaft(11.6 / 2, 20.0)
    result = apply_thread(
        blank, size=size, origin=(0, 0, 0), direction=(0, 0, 1),
        length=20.0, internal=False, feature_diameter=11.6,
    )
    assert result.modelled and is_valid(result.shape)
    assert "not reach full depth" in result.message
    assert "smaller size" in result.message


def test_an_exact_shaft_gets_no_warning():
    size = by_designation("P12")
    blank = shaft(6.0, 20.0)
    result = apply_thread(
        blank, size=size, origin=(0, 0, 0), direction=(0, 0, 1),
        length=20.0, internal=False, feature_diameter=12.0,
    )
    assert result.modelled
    assert result.message == ""


def test_a_thread_too_fine_for_the_nozzle_says_so():
    """An ISO pitch on a 12 mm shaft is a 0.55 mm tooth. Say it, don't print it."""
    size = by_designation("M12")
    result = apply_thread(
        shaft(6.0, 20.0), size=size, origin=(0, 0, 0), direction=(0, 0, 1),
        length=20.0, internal=False, feature_diameter=12.0,
    )
    assert result.modelled
    assert "coarser" in result.message


# ----------------------------------------------------------------------
# The part has to survive being threaded
# ----------------------------------------------------------------------
def lid(thickness: float, bore_radius: float = 6.0):
    """A drilled plate sitting up in space, the way a stacked part does."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    plate = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 20), 60.0, 40.0, thickness).Shape()
    bore = BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(30, 20, 19), gp_Dir(0, 0, 1)), bore_radius, thickness + 2
    ).Shape()
    cut = BRepAlgoAPI_Cut(plate, bore)
    cut.Build()
    return cut.Shape()


@pytest.mark.parametrize("thickness,length", [(3.0, 3.0), (6.0, 3.0), (6.0, 6.0)])
def test_threading_a_hole_never_loses_the_part(thickness, length):
    """A thread must not be able to replace the part it is threading.

    It could. Fusing the nut's thread into a drilled plate came back as *the
    thread alone* -- a loose ring floating where the lid had been -- and OCCT
    called it a success, so the rebuild reported fine and the model was
    destroyed. The mismatched thickness-and-length cases are the ones that
    triggered it, which is why they are here rather than a single tidy one.
    """
    size = by_designation("P12")
    body = lid(thickness)
    before = volume(body)
    result = apply_thread(
        body, size=size, origin=(30, 20, 20), direction=(0, 0, 1),
        length=length, internal=True, clearance="normal", feature_diameter=12.0,
    )
    assert is_valid(result.shape)
    assert volume(result.shape) >= before, (
        "an internal thread adds metal; it can never leave less than it started"
    )
    low, high = bounding_box(result.shape)
    assert (high[0] - low[0]) == pytest.approx(60.0, abs=0.01), (
        "the plate is gone -- the thread replaced the body"
    )


def test_threading_a_shaft_never_loses_the_shaft():
    """The mirror failure: the waste coming back instead of the threaded part."""
    size = by_designation("P12")
    blank = shaft(size.diameter / 2, 20.0)
    before = volume(blank)
    result = apply_thread(
        blank, size=size, origin=(0, 0, 0), direction=(0, 0, 1),
        length=18.0, internal=False, feature_diameter=size.diameter,
    )
    assert is_valid(result.shape)
    # A thread takes a little off and leaves the shaft: it is neither the whole
    # blank nor the shaving.
    assert before * 0.5 < volume(result.shape) < before


# ----------------------------------------------------------------------
# The tooth: the shape that decides whether any of this prints
# ----------------------------------------------------------------------
def test_the_iso_tooth_is_as_wide_as_the_standard_says():
    """The bug this file exists to keep fixed.

    The root flat in ISO 68-1 is the width of the *groove*, so the tooth is
    ``pitch - P/4`` wide where it meets the core. Reading it as the tooth's own
    width made every thread a razor fin with a six-degree flank -- unprintable,
    unscrewable, and perfectly plausible-looking in the viewport.
    """
    form = thread_form(1.0, 60.0, "iso")
    assert form.root_half == pytest.approx(3.0 / 8.0)
    assert form.crest_half == pytest.approx(1.0 / 16.0)
    assert 25.0 < form.flank_angle < 32.0


@pytest.mark.parametrize("pitch", [1.0, 1.75, 2.4, 3.0, 4.0])
def test_the_printed_tooth_never_overhangs_past_45_degrees(pitch):
    form = thread_form(pitch, 90.0, "printed")
    assert form.flank_angle == pytest.approx(45.0)
    assert form.overhang == pytest.approx(45.0)
    assert form.depth == pytest.approx(0.3125 * pitch)


def test_the_profile_corners_sit_where_the_form_says():
    form = thread_form(2.4, 90.0, "printed")
    points = thread_profile_points(3.0, form)
    assert [round(z, 6) for _r, z in points] == [
        round(-form.root_half, 6), round(-form.crest_half, 6),
        round(form.crest_half, 6), round(form.root_half, 6),
    ]
    assert points[1][0] == pytest.approx(3.0)
    assert points[0][0] == pytest.approx(3.0 - form.depth)


def _printer_limit() -> float:
    return float(printer_profile()["max_overhang_angle"])


def _threaded_shaft(designation: str, form: str):
    size = by_designation(designation)
    result = apply_thread(
        shaft(size.diameter / 2, 20.0), size=size, origin=(0, 0, 0),
        direction=(0, 0, 1), length=18.0, internal=False, form=form,
        feature_diameter=size.diameter,
    )
    assert result.modelled
    return result.shape


@pytest.mark.slow
@pytest.mark.parametrize("designation", ["P6", "P12", "P20"])
def test_a_printed_thread_needs_no_supports(designation):
    """The whole point, measured the way a slicer would measure it.

    ``check_overhangs`` works off the triangulation, so this is not a claim
    about the profile maths -- it is the surface a printer would actually see.
    Measured against the printer's own limit rather than a round number,
    because that is the number that decides whether supports go in.
    """
    finding = check_overhangs(
        _threaded_shaft(designation, "printed"), _printer_limit()
    )
    assert finding.severity is not Severity.WARNING, finding.message


@pytest.mark.slow
def test_the_printed_form_overhangs_less_than_the_iso_one():
    """Control for the test above: the tooth shape must be doing the work.

    Compared as fractions rather than severities. Both forms land in the same
    band at the printed series' coarse pitches -- the pitch does most of the
    work there -- so a severity comparison would say nothing, while the actual
    overhanging area still separates them by better than two to one.
    """
    limit = _printer_limit()
    printed = check_overhangs(_threaded_shaft("P12", "printed"), limit)
    iso = check_overhangs(_threaded_shaft("P12", "iso"), limit)
    assert iso.value > printed.value


@pytest.mark.slow
def test_the_iso_form_at_a_fine_pitch_is_the_one_that_needs_supports():
    """Where the two forms really part company.

    An ISO tooth at an ISO pitch is both too fine for the nozzle and steep
    enough to want support. This is the thread the user had before, and the
    reason threading was unusable.
    """
    limit = _printer_limit()
    printed = check_overhangs(_threaded_shaft("M12", "printed"), limit)
    iso = check_overhangs(_threaded_shaft("M12", "iso"), limit)
    assert iso.value > printed.value
    assert iso.severity.rank() >= printed.severity.rank()


@pytest.mark.slow
def test_a_printed_pair_turns_rather_than_merely_seating():
    """Seating is not screwing together.

    A pair can clear one another in one position and bind everywhere else,
    which is exactly what an over-tight printed thread does. Turning the bolt
    through a full turn in steps -- advancing as it goes, because that is what
    turning a thread does -- and finding it clear at every one of them is what
    "it screws together" actually means.
    """
    from simplecad.kernel.occ import make_transform, transformed

    bolt, nut, size = _pair("P12", "normal")
    for step in range(1, 4):
        angle = 360.0 * step / 4.0
        turned = transformed(
            bolt,
            make_transform(
                translate=(0.0, 0.0, size.pitch * step / 4.0),
                rotate_axis=(0, 0, 1), rotate_degrees=angle,
                origin=(20.0, 20.0, 0.0),
            ),
        )
        buried = _buried(turned, nut)
        assert buried < TOLERANCE, (
            f"binds after {angle:.0f}° of turn: {buried:.1%} buried"
        )


# ----------------------------------------------------------------------
# Which end of the face the thread lands on
# ----------------------------------------------------------------------
def _plate_with_bore(thickness=12.0, radius=3.0):
    """A plate bored through from the top, and the bore's own description."""
    from simplecad.kernel.detect import analyse_cylinder
    from simplecad.core.naming import sub_shapes

    shape = plate_with_hole(thickness=thickness, hole_radius=radius)
    info = next(
        a for face in sub_shapes(shape, "face")
        if (a := analyse_cylinder(face)) is not None and a.internal
    )
    return shape, info


def test_a_short_thread_starts_at_the_end_the_user_asked_for():
    """Not at whichever end OCCT happened to parameterise first.

    ``analyse_cylinder`` reports the face's ``vmin`` end, which is an artefact
    of how the surface got built and is invisible from outside -- so a thread
    shorter than the bore landed on an arbitrary end, and a hole came back
    threaded at the bottom when the point was to start a screw at the top.
    """
    from simplecad.kernel.occ import bounding_box
    from simplecad.kernel.operations import thread_origin
    from simplecad.core.naming import sub_shapes
    from simplecad.kernel.detect import analyse_cylinder

    shape, info = _plate_with_bore()
    size = by_designation("P6")
    length = 4.0

    spans = {}
    for end in ("top", "bottom"):
        outcome = apply_thread(
            shape, size=size, origin=thread_origin(info, length, end),
            direction=info.direction, length=length, internal=True,
            feature_diameter=info.diameter, form="printed",
        )
        assert outcome.modelled, outcome.message
        boxes = [
            bounding_box(face, optimal=False)
            for face in sub_shapes(outcome.shape, "face")
            if (a := analyse_cylinder(face)) is not None
            and a.internal and a.diameter < info.diameter - 0.05
        ]
        assert boxes, f"no thread was cut for from_end={end}"
        spans[end] = (
            min(b[0][2] for b in boxes), max(b[1][2] for b in boxes)
        )

    # The plate spans z 0..12 and the thread is 4 mm of it, so the two must sit
    # in opposite halves -- which is the whole of what the option promises.
    assert spans["top"][0] > 6.0, spans
    assert spans["bottom"][1] < 6.0, spans


def test_the_full_length_case_is_unaffected_by_the_choice():
    """A thread that spans the face has no end to choose."""
    from simplecad.kernel.operations import thread_origin

    _shape, info = _plate_with_bore()
    for end in ("top", "bottom"):
        assert thread_origin(info, info.length, end) == info.origin


def test_a_boolean_that_changed_nothing_is_not_a_thread():
    """OCCT reports handing an operand straight back as done.

    The old check -- "most of the body is still there" -- was satisfied by
    exactly that, so a thread that never got applied came back marked
    ``modelled`` with no message, and the user got a plain hole and no
    explanation. Compare ``_carved``, which does verify its subtraction.
    """
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    from simplecad.kernel.threads import _apply_tool

    body = BRepPrimAPI_MakeBox(20.0, 20.0, 20.0).Shape()
    # A tool nowhere near the body: cutting removes nothing and fusing adds a
    # separate lump, and neither is the thread anyone asked for.
    away = BRepPrimAPI_MakeBox(
        gp_Pnt(500.0, 500.0, 500.0), 1.0, 1.0, 1.0
    ).Shape()
    assert _apply_tool(body, away, cut=True) is None

    # And the honest case still works: a tool that genuinely bites.
    biting = BRepPrimAPI_MakeBox(gp_Pnt(-1.0, -1.0, -1.0), 5.0, 5.0, 5.0).Shape()
    assert _apply_tool(body, biting, cut=True) is not None
