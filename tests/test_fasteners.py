"""Create Matching Part.

The promise is that a user never states the thread twice: ask for a bolt to fit
a hole, and it arrives at the right size with the clearance on the right side.
The last test is the one that matters -- a generated bolt must actually thread
into a generated nut.
"""

from __future__ import annotations

import math

import pytest

from simplecad.core.document import BodyRef, Document
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel import fasteners  # noqa: F401 - registers the features
from simplecad.kernel.fasteners import (
    MatchingBoltFeature, MatchingNutFeature, complement, head_for, make_bolt,
    make_nut,
)
from simplecad.kernel.occ import bounding_box, is_valid, volume
from simplecad.kernel.thread_specs import by_designation


def build(document):
    report = Rebuilder(document).rebuild()
    assert report.ok, report.summary()
    return document


# ----------------------------------------------------------------------
def test_head_dimensions_follow_the_standard():
    m6 = head_for(by_designation("M6"))
    assert m6["across_flats"] == pytest.approx(10.0)     # ISO 4014
    assert m6["head_height"] == pytest.approx(4.0)
    assert m6["nut_height"] == pytest.approx(5.2)        # ISO 4032


def test_a_size_outside_the_table_still_gets_sensible_proportions():
    fine = head_for(by_designation("M12x1.5"))
    assert fine["across_flats"] == pytest.approx(18.0)   # shares M12's head


def test_the_complement_of_a_hole_is_a_shaft():
    m8 = by_designation("M8")
    pairing = complement(m8, existing_internal=True)
    assert pairing.internal is False
    assert pairing.size.designation == "M8"
    assert "external" in pairing.describe()


def test_the_complement_of_a_shaft_is_a_hole():
    pairing = complement(by_designation("M8"), existing_internal=False)
    assert pairing.internal is True
    assert "internal" in pairing.describe()


# ----------------------------------------------------------------------
@pytest.mark.slow
def test_a_bolt_has_a_head_a_shank_and_a_thread():
    size = by_designation("P6")
    bolt = make_bolt(size, 20.0, thread_length=14.0)
    assert is_valid(bolt)

    low, high = bounding_box(bolt)
    dimensions = head_for(size)
    # Head height plus shank length.
    assert (high[2] - low[2]) == pytest.approx(
        dimensions["head_height"] + 20.0, abs=0.1
    )
    # Across corners of a hex is across-flats / cos(30).
    across_corners = dimensions["across_flats"] / math.cos(math.pi / 6)
    assert (high[0] - low[0]) == pytest.approx(across_corners, abs=0.1)

    from simplecad.kernel.detect import cylindrical_faces

    diameters = {round(i.diameter, 2) for _f, i in cylindrical_faces(bolt)}
    assert 6.0 in diameters, "the shank must be at nominal diameter"


@pytest.mark.slow
def test_a_nut_is_a_hex_with_a_threaded_bore():
    size = by_designation("P6")
    nut = make_nut(size)
    assert is_valid(nut)

    low, high = bounding_box(nut)
    assert (high[2] - low[2]) == pytest.approx(head_for(size)["nut_height"], abs=0.05)

    from simplecad.kernel.detect import cylindrical_faces

    bores = [i for _f, i in cylindrical_faces(nut) if i.internal]
    assert bores, "a nut needs a hole through it"


@pytest.mark.slow
def test_a_generated_bolt_threads_into_a_generated_nut():
    """The whole promise, checked by intersecting the two."""
    from simplecad.kernel.occ import make_transform, transformed

    from .fit import TOLERANCE, buried_fraction

    size = by_designation("P6")
    nut = make_nut(size, clearance="normal")
    bolt = make_bolt(size, 20.0, thread_length=20.0)

    # Seat the bolt's threaded shank inside the nut.
    head_height = head_for(size)["head_height"]
    seated = transformed(bolt, make_transform(translate=(0.0, 0.0, -head_height)))

    buried = buried_fraction(seated, nut)
    assert buried < TOLERANCE, (
        f"{buried:.1%} of the bolt is inside the nut's metal"
    )


@pytest.mark.slow
def test_turning_the_bolt_without_advancing_it_makes_the_pair_clash():
    """Control: the fit check above must be able to detect a bad fit.

    The mis-fit is a bolt turned in place rather than a nut cut undersize. An
    undersize nut's thread ends up floating free inside its own bore, and what
    that measures is the boolean's handling of a detached compound rather than
    anything about the fit -- see :mod:`tests.fit`.
    """
    from simplecad.kernel.occ import make_transform, transformed

    from .fit import buried_fraction, turned_in_place

    size = by_designation("P6")
    nut = make_nut(size, clearance="normal")
    bolt = make_bolt(size, 20.0, thread_length=20.0)
    seated = transformed(
        bolt, make_transform(translate=(0.0, 0.0, -head_for(size)["head_height"]))
    )
    clean = buried_fraction(seated, nut)
    # Half a turn is deliberately out of phase and must bind even though the
    # correctly advanced pair turns freely.
    clashing = buried_fraction(turned_in_place(seated, 180.0), nut)

    assert clashing > 0.10, "a bolt turned without advancing must bind"
    assert clashing > clean * 5, (
        f"{clashing:.1%} buried is not clearly above the seated pair's {clean:.1%}"
    )


# ----------------------------------------------------------------------
@pytest.mark.slow
def test_the_bolt_feature_names_what_it_made():
    document = Document("T")
    feature = document.add_feature(
        MatchingBoltFeature(
            inputs={"designation": "P8", "length": 25}, outputs=["Bolt"]
        )
    )
    build(document)
    assert is_valid(document.bodies["Bolt"].shape)
    assert "P8" in feature.message and "25" in feature.message


@pytest.mark.slow
def test_the_nut_feature_builds_from_a_designation_alone():
    document = Document("T")
    feature = document.add_feature(
        MatchingNutFeature(inputs={"designation": "P10"}, outputs=["Nut"])
    )
    build(document)
    assert is_valid(document.bodies["Nut"].shape)
    assert feature.message == "P10 nut"


def test_a_generated_fine_fastener_is_redirected_before_geometry_is_built():
    document = Document("T")
    document.add_feature(MatchingBoltFeature(
        inputs={"designation": "M6", "length": 20}, outputs=["Bolt"]
    ))
    report = Rebuilder(document).rebuild()
    assert not report.ok
    assert "feature limit" in report.summary()
    assert "P6" in report.summary()


def test_a_fastener_without_a_size_says_so():
    document = Document("T")
    document.add_feature(MatchingBoltFeature(inputs={}, outputs=["Bolt"]))
    report = Rebuilder(document).rebuild()
    assert not report.ok
    assert "which thread to match" in report.summary()


def test_generated_fasteners_are_discoverable_as_physical_thread_sources():
    document = Document("T")
    bolt = document.add_feature(MatchingBoltFeature(
        inputs={
            "designation": "P8", "thread_modelled": True,
            "thread_internal": False, "form": "printed", "left_hand": True,
        },
        outputs=["Bolt"],
    ))
    nut = document.add_feature(MatchingNutFeature(
        inputs={
            "designation": "P8", "thread_modelled": True,
            "thread_internal": True, "clearance": "loose",
        },
        outputs=["Nut"],
    ))

    bolt_thread = document.threads_on("Bolt")[0]
    nut_thread = document.threads_on("Nut")[0]
    assert bolt_thread["feature_id"] == bolt.id
    assert bolt_thread["internal"] is False
    assert bolt_thread["left_hand"] is True
    assert nut_thread["feature_id"] == nut.id
    assert nut_thread["internal"] is True
    assert nut_thread["clearance"] == "loose"


def test_failed_or_cosmetic_features_cannot_be_matching_sources():
    from simplecad.core.document import FeatureState

    document = Document("T")
    cosmetic = document.add_feature(MatchingBoltFeature(
        inputs={"designation": "P8", "thread_modelled": False},
        outputs=["Cosmetic"],
    ))
    failed = document.add_feature(MatchingNutFeature(
        inputs={"designation": "P8", "thread_modelled": True},
        outputs=["Failed"],
    ))
    failed.state = FeatureState.FAILED

    assert document.threads_on("Cosmetic") == []
    assert document.threads_on("Failed") == []


# ----------------------------------------------------------------------
# Matching a thread onto another part
#
# The bolt and nut above are built from a designation alone. These are the
# other half of the promise, and the half that was never covered: a thread
# already on one body, and a *second body* that has to take its mate -- a flat
# face to drill into, a bore that already exists, a tube that will become a cap.
# ----------------------------------------------------------------------
def _top_face(shape):
    from simplecad.core.naming import sub_shapes
    from simplecad.kernel.detect import analyse_plane

    best, height = None, -1e9
    for face in sub_shapes(shape, "face"):
        info = analyse_plane(face)
        if info and abs(info.normal[2]) > 0.99 and info.center[2] > height:
            best, height = face, info.center[2]
    return best


def _bore(shape):
    from simplecad.core.naming import sub_shapes
    from simplecad.kernel.detect import analyse_cylinder

    for face in sub_shapes(shape, "face"):
        info = analyse_cylinder(face)
        if info is not None and info.internal:
            return face, info
    return None, None


def _ref(document, name, face, feature):
    from simplecad.core.naming import make_ref

    return make_ref(document.body(name).shape, face, feature.id, body=name)


def _threaded_post(document, builder, *, radius=6.0, height=20.0, length=12.0):
    """A shaft with a real external thread over its free end."""
    from simplecad.core.naming import make_ref
    from simplecad.kernel.detect import cylindrical_faces
    from simplecad.kernel.operations import ThreadFeature
    from simplecad.kernel.primitives import CylinderFeature

    post = document.add_feature(
        CylinderFeature(inputs={"radius": radius, "height": height}, outputs=["Post"])
    )
    assert builder.rebuild().ok
    face = cylindrical_faces(document.body("Post").shape)[0][0]
    document.add_feature(ThreadFeature(
        inputs={
            "body": BodyRef("Post"),
            "face": make_ref(document.body("Post").shape, face, post.id, body="Post"),
            "length": length, "clearance": "normal",
            "form": "printed", "from_end": "top",
        },
        outputs=["Post"],
    ))
    assert builder.rebuild().ok
    return document.threads_on("Post")[0]


def _plate(document, builder, name, *, x=60.0, thickness=10.0, hole=None):
    """A plate, optionally with a plain hole already drilled through it."""
    from simplecad.kernel.detect import analyse_plane
    from simplecad.kernel.operations import HoleFeature
    from simplecad.kernel.primitives import BoxFeature

    plate = document.add_feature(BoxFeature(
        inputs={"width": 40, "depth": 40, "height": thickness, "x": x},
        outputs=[name],
    ))
    assert builder.rebuild().ok
    if hole is None:
        return plate
    face = _top_face(document.body(name).shape)
    document.add_feature(HoleFeature(
        inputs={
            "body": BodyRef(name),
            "face": _ref(document, name, face, plate),
            "diameter": hole,
            "depth_mode": "through",
            "position": tuple(analyse_plane(face).center),
        },
        outputs=[name],
    ))
    assert builder.rebuild().ok
    return plate


def _matching_hole(document, plate, name, thread, *, depth=20.0):
    """What Create Matching Part builds for a flat face."""
    from simplecad.kernel.detect import analyse_plane
    from simplecad.kernel.operations import HoleFeature

    face = _top_face(document.body(name).shape)
    return document.add_feature(HoleFeature(
        inputs={
            "designation": thread["designation"],
            "clearance": thread["clearance"],
            "form": thread["form"],
            "left_hand": thread["left_hand"],
            "body": BodyRef(name),
            "face": _ref(document, name, face, plate),
            "diameter": by_designation(thread["designation"]).diameter,
            "style": "threaded",
            "depth_mode": "blind",
            "depth": depth,
            "position": tuple(analyse_plane(face).center),
        },
        outputs=[name],
    ))


def _matching_thread(document, feature, name, thread, *, resize=False):
    """What Create Matching Part builds for a round face."""
    from simplecad.kernel.fasteners import ApplyMatchingThreadFeature

    face, info = _bore(document.body(name).shape)
    assert face is not None, f"{name} has no bore to thread"
    return document.add_feature(ApplyMatchingThreadFeature(
        inputs={
            "designation": thread["designation"],
            "clearance": thread["clearance"],
            "form": thread["form"],
            "left_hand": thread["left_hand"],
            "resize": resize,
            "body": BodyRef(name),
            "face": _ref(document, name, face, feature),
        },
        outputs=[name],
    )), info


@pytest.mark.slow
def test_a_matching_threaded_hole_accepts_the_part_it_was_matched_to():
    """The whole point, measured: the post screws into the plate.

    Not "the feature succeeded" -- a hole with a thread of the wrong size in it
    succeeds too, and looks perfectly fine on screen. The post is seated in the
    bore and the share of it buried in the plate's metal is counted, exactly as
    the bolt/nut pair is checked in ``test_threads``.
    """
    from simplecad.kernel.occ import make_transform, transformed

    from .fit import TOLERANCE, buried_fraction, turned_in_place

    document = Document("T")
    builder = Rebuilder(document)
    thread = _threaded_post(document, builder)
    assert thread["designation"] == "P12"
    plate = _plate(document, builder, "Plate")
    _matching_hole(document, plate, "Plate", thread)
    report = builder.rebuild()
    assert report.ok, report.summary()

    recorded = document.threads_on("Plate")
    assert recorded and recorded[0]["designation"] == "P12"
    assert recorded[0]["internal"] is True

    # The post's thread starts 8 mm up its own shaft; dropping it 8 mm puts that
    # thread at the plate's bore. A pure axial move is not a cheat -- for a
    # helix it is the same as turning, which is what screwing it in *is*.
    seated = transformed(
        document.body("Post").shape, make_transform(translate=(80.0, 20.0, -8.0))
    )
    plate_shape = document.body("Plate").shape
    buried = buried_fraction(seated, plate_shape)
    assert buried < TOLERANCE, (
        f"{buried:.1%} of the post is inside the plate's metal"
    )

    # Control: turned in place without advancing, the pair must bind. Without
    # this a fit check that always measured zero would pass either way.
    clashing = buried_fraction(
        turned_in_place(seated, 90.0, (80.0, 20.0, 0.0)), plate_shape
    )
    assert clashing > buried, (
        f"a post turned in place measured {clashing:.1%}, no worse than the "
        f"seated {buried:.1%} -- the fit check is not measuring anything"
    )


@pytest.mark.slow
def test_a_matching_thread_lands_on_a_bore_that_already_exists():
    document = Document("T")
    builder = Rebuilder(document)
    thread = _threaded_post(document, builder)
    plate = _plate(document, builder, "Bored", hole=12.0)
    _matching_thread(document, plate, "Bored", thread)
    report = builder.rebuild()
    assert report.ok, report.summary()
    assert is_valid(document.body("Bored").shape)
    assert document.threads_on("Bored")[0]["internal"] is True


@pytest.mark.slow
def test_a_matching_thread_lands_on_a_tube_bore():
    """A cap: a tube whose bore takes the mate of a threaded neck."""
    from simplecad.kernel.primitives import TubeFeature

    document = Document("T")
    builder = Rebuilder(document)
    thread = _threaded_post(document, builder)
    cap = document.add_feature(TubeFeature(
        inputs={"outer_radius": 10, "inner_radius": 6, "height": 14, "x": 120},
        outputs=["Cap"],
    ))
    assert builder.rebuild().ok
    _matching_thread(document, cap, "Cap", thread)
    report = builder.rebuild()
    assert report.ok, report.summary()
    assert is_valid(document.body("Cap").shape)


@pytest.mark.slow
def test_a_bore_too_small_is_refused_until_resizing_is_allowed():
    """Both halves matter: the refusal must be actionable, and it must lift."""
    document = Document("T")
    builder = Rebuilder(document)
    thread = _threaded_post(document, builder)
    plate = _plate(document, builder, "Small", hole=8.0)

    refused, _info = _matching_thread(document, plate, "Small", thread)
    report = builder.rebuild()
    assert not report.ok
    summary = report.summary()
    assert "⌀8.00" in summary and "⌀12.60" in summary, summary
    assert "Resize" in summary, summary
    assert is_valid(document.body("Small").shape), (
        "a refused thread must leave the body it refused to touch alone"
    )

    refused.inputs["resize"] = True
    report = builder.rebuild(force=True)
    assert report.ok, report.summary()
    assert is_valid(document.body("Small").shape)
    assert document.threads_on("Small")[0]["designation"] == "P12"


@pytest.mark.slow
@pytest.mark.parametrize(
    "hole,designation,thickness", [(5.0, "P6", 10.0), (45.0, "P50", 20.0)]
)
def test_a_hole_of_any_size_can_be_threaded_by_opening_it_first(
    hole, designation, thickness
):
    """The sizes the old ±2 mm window put out of reach entirely.

    A ⌀5 bore had no printable size within reach and a ⌀45 bore had none at all,
    so the size list came back empty and Create stayed grey with nothing the
    user could do about it.
    """
    from simplecad.kernel.operations import ThreadFeature

    document = Document("T")
    builder = Rebuilder(document)
    plate = _plate(document, builder, "P", thickness=thickness, hole=hole)
    face, info = _bore(document.body("P").shape)
    document.add_feature(ThreadFeature(
        inputs={
            "body": BodyRef("P"),
            "face": _ref(document, "P", face, plate),
            "designation": designation,
            "clearance": "normal", "form": "printed", "resize": True,
        },
        outputs=["P"],
    ))
    report = builder.rebuild()
    assert report.ok, report.summary()
    assert is_valid(document.body("P").shape)
    assert document.threads_on("P")[0]["designation"] == designation


def test_resizing_is_only_proposed_when_the_feature_is_really_undersize():
    """A bore already at the thread's nominal size needs nothing doing to it.

    Offering to open a ⌀12 hole to ⌀12.60 for a P12 thread is a change the user
    can measure for a gain they cannot: ``apply_thread`` already takes its
    envelope out to the wall. The panel and the kernel share this threshold, so
    what is offered and what happens cannot drift apart.
    """
    from simplecad.kernel.detect import CylinderInfo
    from simplecad.kernel.threads import required_bore, resize_target

    size = by_designation("P12")
    assert required_bore(size, "normal") == pytest.approx(12.6)

    def bore_of(diameter):
        return CylinderInfo(
            radius=diameter / 2.0, diameter=diameter, origin=(0.0, 0.0, 0.0),
            direction=(0.0, 0.0, 1.0), length=10.0, internal=True,
        )

    sentinel = object()
    for diameter in (12.0, 11.99, 16.0):
        body, used, note = resize_target(
            sentinel, bore_of(diameter), size, length=10.0, resize=True
        )
        assert body is sentinel and note == "" and used == diameter, (
            f"⌀{diameter} was resized when it did not need to be"
        )


def test_a_bore_that_cannot_take_the_thread_at_all_is_recognised():
    from simplecad.kernel.threads import thread_needs_resizing

    size = by_designation("P12")
    assert thread_needs_resizing(8.0, size, internal=True)
    assert not thread_needs_resizing(12.0, size, internal=True)
    assert not thread_needs_resizing(16.0, size, internal=True)
    # A shaft thinner than the tooth is the same problem from the other side.
    assert thread_needs_resizing(9.0, size, internal=False)
    assert not thread_needs_resizing(12.0, size, internal=False)
