"""Splitting a solid in two.

The point of the operation is that the halves are *independent bodies*, so the
tests are as much about the feature graph as about the geometry: the volumes
have to add up, and deleting one half must not take the other with it.
"""

from __future__ import annotations

import pytest

from simplecad.core.document import BodyRef, BuildContext, Document
from simplecad.core.errors import CadError
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.occ import volume
from simplecad.kernel.primitives import BoxFeature
from simplecad.kernel.split import SplitFeature, side_extents, split_solid

WIDTH, DEPTH, HEIGHT = 50.0, 40.0, 20.0
TOTAL = WIDTH * DEPTH * HEIGHT


@pytest.fixture
def box():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    return BRepPrimAPI_MakeBox(WIDTH, DEPTH, HEIGHT).Shape()


class TestSplitSolid:
    def test_cuts_into_two_solids_that_add_up(self, box):
        below, above = split_solid(box, (20.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        assert volume(below) == pytest.approx(20.0 * DEPTH * HEIGHT)
        assert volume(above) == pytest.approx(30.0 * DEPTH * HEIGHT)
        assert volume(below) + volume(above) == pytest.approx(TOTAL)

    def test_an_angled_plane_still_works(self, box):
        below, above = split_solid(box, (25.0, 20.0, 10.0), (1.0, 1.0, 1.0))
        assert volume(below) + volume(above) == pytest.approx(TOTAL, rel=1e-6)

    def test_a_part_with_a_hole_keeps_every_piece(self, box):
        """One side can come back as several solids, and none may be lost."""
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

        holed = BRepAlgoAPI_Cut(
            box, BRepPrimAPI_MakeCylinder(5.0, 100.0).Shape()
        ).Shape()
        below, above = split_solid(holed, (25.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        assert volume(below) + volume(above) == pytest.approx(volume(holed))

    def test_a_plane_that_misses_leaves_one_side_empty(self, box):
        below, above = split_solid(box, (200.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        assert (below is None) != (above is None)

    def test_a_zero_normal_is_refused(self, box):
        with pytest.raises(CadError):
            split_solid(box, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))


class TestSideExtents:
    def test_reports_both_sides_of_the_cut(self, box):
        near, far = side_extents(box, (20.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        assert (near, far) == pytest.approx((20.0, 30.0))

    def test_the_two_sides_always_sum_to_the_extent(self, box):
        for position in (1.0, 12.5, 25.0, 49.0):
            near, far = side_extents(box, (position, 0.0, 0.0), (1.0, 0.0, 0.0))
            assert near + far == pytest.approx(WIDTH)


def _document_with_split(position=20.0):
    document = Document("Split")
    document.add_feature(
        BoxFeature(
            inputs={"width": WIDTH, "depth": DEPTH, "height": HEIGHT},
            outputs=["Block"],
        )
    )
    document.add_feature(
        SplitFeature(
            inputs={
                "body": BodyRef("Block"),
                "normal": [1.0, 0.0, 0.0],
                "origin": [0.0, 0.0, 0.0],
                "position": position,
                "names": ["Left", "Right"],
            },
            outputs=["Left", "Right"],
        )
    )
    return document


class TestSplitFeature:
    def test_produces_two_independent_bodies(self):
        document = _document_with_split()
        report = Rebuilder(document).rebuild()
        assert report.ok, report.summary()
        # Exactly the two halves: the part that was cut up is gone, not left
        # sitting in the tree occupying the same space as both of its pieces.
        assert set(document.bodies) == {"Left", "Right"}
        assert volume(document.body("Left").shape) == pytest.approx(
            20.0 * DEPTH * HEIGHT
        )
        assert volume(document.body("Right").shape) == pytest.approx(
            30.0 * DEPTH * HEIGHT
        )

    def test_moving_the_cut_moves_both_halves(self):
        document = _document_with_split(position=10.0)
        Rebuilder(document).rebuild()
        assert volume(document.body("Left").shape) == pytest.approx(
            10.0 * DEPTH * HEIGHT
        )

    def test_a_cut_that_misses_reports_a_useful_error(self):
        document = _document_with_split(position=500.0)
        report = Rebuilder(document).rebuild()
        assert not report.ok
        message = str(next(iter(report.failures.values())))
        assert "misses" in message.lower()

    def test_dropping_one_half_leaves_the_other(self):
        """Deleting one half must not delete the feature, and so the other."""
        document = _document_with_split()
        rebuilder = Rebuilder(document)
        rebuilder.rebuild()

        split = next(f for f in document.features if f.type_name == "split")
        split.dropped = ["Left"]
        rebuilder.invalidate({split.id})
        report = rebuilder.rebuild()

        assert report.ok, report.summary()
        assert "Left" not in document.bodies
        assert "Right" in document.bodies
        assert volume(document.body("Right").shape) == pytest.approx(
            30.0 * DEPTH * HEIGHT
        )

    def test_the_source_body_is_consumed(self):
        document = _document_with_split()
        Rebuilder(document).rebuild()
        assert "Block" not in document.bodies

    def test_dropped_outputs_survive_a_save_and_reload(self):
        document = _document_with_split()
        split = next(f for f in document.features if f.type_name == "split")
        split.dropped = ["Left"]
        restored = Document.from_dict(document.to_dict())
        reloaded = next(f for f in restored.features if f.type_name == "split")
        assert reloaded.dropped == ["Left"]
