"""Vents: a hex grid of holes, as a plate and as a cut into an existing wall.

The two things that matter are that the border stays solid -- a grille with
half-holes chewed out of its edge is scrap -- and that the whole grid is cut in
one boolean, because doing them one at a time turns seconds into minutes.
"""

from __future__ import annotations

import math

import pytest

from simplecad.core.document import BodyRef, Document
from simplecad.core.errors import CadError
from simplecad.core.naming import fingerprint, make_ref, sub_shapes
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.occ import bounding_box, is_valid, volume
from simplecad.kernel.primitives import BoxFeature
from simplecad.kernel.vent import (
    VentCutFeature, VentPlateFeature, hex_positions,
)


def build(document):
    report = Rebuilder(document).rebuild()
    assert report.ok, report.summary()
    return report


def upward_face(shape):
    return max(
        (f for f in sub_shapes(shape, "face")
         if (p := fingerprint(f, "face")).direction and p.direction[2] > 0.99),
        key=lambda f: fingerprint(f, "face").center[2],
    )


# ----------------------------------------------------------------------
def test_holes_are_packed_on_a_hex_lattice():
    """Alternate rows offset by half a pitch. That is what makes it hex."""
    positions = hex_positions(60, 60, 5.0, 1.2, 2.0)
    assert positions

    pitch = 5.0 + 1.2
    spacing = pitch * math.sqrt(3) / 2
    # Group by row, keeping the exact y so comparisons stay exact.
    rows: dict[float, list[float]] = {}
    for x, y in positions:
        rows.setdefault(round(y / spacing), []).append(x)
    ordered = sorted(rows)
    assert len(ordered) > 2

    # Consecutive rows sit one row-step apart...
    assert (ordered[1] - ordered[0]) == 1

    # ...and are staggered by half a pitch, which is what makes it hexagonal
    # packing rather than a square grid.
    first = sorted(rows[0])
    second = sorted(rows[1])
    assert first and second
    assert abs((second[0] - first[0]) % pitch - pitch / 2) < 1e-6


def test_the_border_stays_solid():
    """No hole may cross the margin, corners included."""
    width = height = 60.0
    across_flats, margin = 5.0, 3.0
    circum = across_flats / math.sqrt(3.0)
    for x, y in hex_positions(width, height, across_flats, 1.2, margin):
        assert abs(x) + across_flats / 2 <= width / 2 - margin + 1e-9
        assert abs(y) + circum <= height / 2 - margin + 1e-9


def test_no_holes_fit_when_the_border_eats_the_plate():
    assert hex_positions(20, 20, 5.0, 1.2, 12.0) == []


def test_a_vent_plate_is_a_watertight_solid_with_material_removed():
    doc = Document("Vent")
    doc.add_feature(VentPlateFeature(
        inputs={"width": 60, "depth": 60, "thickness": 3,
                "across_flats": 5, "wall": 1.2, "margin": 2},
        outputs=["Vent"]))
    build(doc)
    shape = doc.bodies["Vent"].shape

    assert is_valid(shape)
    solid = 60 * 60 * 3
    assert volume(shape) < solid, "a vent must have holes in it"
    assert volume(shape) > solid * 0.3, "it must still be mostly plate"
    # The outline is untouched: holes go through, they do not trim the edge.
    low, high = bounding_box(shape)
    assert (high[0] - low[0]) == pytest.approx(60.0, abs=1e-6)
    assert (high[2] - low[2]) == pytest.approx(3.0, abs=1e-6)

    # Every hole is six-sided, so the face count follows the hole count exactly.
    holes = len(hex_positions(60, 60, 5, 1.2, 2))
    assert len(sub_shapes(shape, "face")) == 6 + holes * 6


def test_a_vent_cut_perforates_a_chosen_wall():
    doc = Document("Cut")
    box = doc.add_feature(
        BoxFeature(inputs={"width": 60, "depth": 60, "height": 4}, outputs=["Wall"])
    )
    build(doc)
    before = volume(doc.bodies["Wall"].shape)

    doc.add_feature(VentCutFeature(
        inputs={
            "body": BodyRef("Wall"),
            "face": make_ref(
                doc.bodies["Wall"].shape,
                upward_face(doc.bodies["Wall"].shape),
                box.id, kind="face", body="Wall",
            ),
            "across_flats": 5, "wall": 1.2, "margin": 3,
        },
        outputs=["Wall"]))
    build(doc)
    after = doc.bodies["Wall"].shape

    assert is_valid(after)
    assert volume(after) < before
    # The wall keeps its outline; only the middle is perforated.
    low, high = bounding_box(after)
    assert (high[0] - low[0]) == pytest.approx(60.0, abs=1e-6)
    assert (high[2] - low[2]) == pytest.approx(4.0, abs=1e-6)


def test_unprintable_dimensions_are_refused_with_a_reason():
    for inputs, expected in (
        ({"across_flats": 0.3}, "too small"),
        ({"wall": 0.1}, "thinner than a printed line"),
    ):
        doc = Document("Vent")
        base = {"width": 60, "depth": 60, "thickness": 3,
                "across_flats": 5, "wall": 1.2, "margin": 2}
        base.update(inputs)
        doc.add_feature(VentPlateFeature(inputs=base, outputs=["Vent"]))
        report = Rebuilder(doc).rebuild()
        assert not report.ok
        assert expected in report.summary()
