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
    size = by_designation("M6")
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
    size = by_designation("M6")
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

    size = by_designation("M6")
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

    size = by_designation("M6")
    nut = make_nut(size, clearance="normal")
    bolt = make_bolt(size, 20.0, thread_length=20.0)
    seated = transformed(
        bolt, make_transform(translate=(0.0, 0.0, -head_for(size)["head_height"]))
    )
    clean = buried_fraction(seated, nut)
    # Half a turn on a 1 mm pitch is half a millimetre of phase error, which is
    # well past the clearance. A sixth of a turn is only 0.17 mm and lands
    # inside it -- correctly, since a thread has to tolerate some slop.
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
            inputs={"designation": "M8", "length": 25}, outputs=["Bolt"]
        )
    )
    build(document)
    assert is_valid(document.bodies["Bolt"].shape)
    assert "M8" in feature.message and "25" in feature.message


@pytest.mark.slow
def test_the_nut_feature_builds_from_a_designation_alone():
    document = Document("T")
    feature = document.add_feature(
        MatchingNutFeature(inputs={"designation": "M10"}, outputs=["Nut"])
    )
    build(document)
    assert is_valid(document.bodies["Nut"].shape)
    assert feature.message == "M10 nut"


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
