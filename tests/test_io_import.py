"""Importing 3D files.

Round-trips through every writer the app has, because that is the only way to
know the reader agrees with something real. What matters is not that a file
opens but that what comes out is the *same part*: the same size, the same number
of bodies, and geometry solid enough that the rest of the application can
actually work on it.
"""

from __future__ import annotations

import os

import pytest

from simplecad.core.document import BuildContext, Document
from simplecad.core.errors import CadError
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.importing import ImportFeature, forget, read_bodies
from simplecad.kernel.io_formats import (
    READERS, export_shapes, import_bodies, import_filter,
)
from simplecad.kernel.occ import bounding_box, is_valid, volume
from simplecad.kernel.split import split_solid

WIDTH, DEPTH, HEIGHT = 50.0, 40.0, 20.0
#: Meshes are an approximation of a curve, so a cylinder never round-trips
#: exactly. A box does, and everything must round-trip to well within a print
#: layer either way.
MESH_TOLERANCE = 0.15


@pytest.fixture
def box():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    return BRepPrimAPI_MakeBox(WIDTH, DEPTH, HEIGHT).Shape()


@pytest.fixture
def cylinder():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

    return BRepPrimAPI_MakeCylinder(10.0, 30.0).Shape()


class TestRoundTrip:
    @pytest.mark.parametrize("extension", [".step", ".stp", ".stl", ".obj", ".3mf"])
    def test_dimensions_survive(self, box, tmp_path, extension):
        path = str(tmp_path / f"part{extension}")
        export_shapes([box], path)
        bodies = import_bodies(path)
        assert len(bodies) == 1
        low, high = bounding_box(bodies[0].shape)
        assert high[0] - low[0] == pytest.approx(WIDTH, abs=MESH_TOLERANCE)
        assert high[1] - low[1] == pytest.approx(DEPTH, abs=MESH_TOLERANCE)
        assert high[2] - low[2] == pytest.approx(HEIGHT, abs=MESH_TOLERANCE)

    @pytest.mark.parametrize("extension", [".step", ".3mf"])
    def test_separate_bodies_stay_separate(
        self, box, cylinder, tmp_path, extension
    ):
        """The formats that can describe several objects must keep them apart."""
        path = str(tmp_path / f"two{extension}")
        export_shapes([box, cylinder], path)
        bodies = import_bodies(path)
        assert len(bodies) == 2
        assert len({b.name for b in bodies}) == 2

    def test_stl_is_one_body_because_the_format_says_so(
        self, box, cylinder, tmp_path
    ):
        path = str(tmp_path / "two.stl")
        export_shapes([box, cylinder], path)
        assert len(import_bodies(path)) == 1

    @pytest.mark.parametrize("extension", [".step", ".stl", ".obj", ".3mf"])
    def test_imported_geometry_is_valid_and_editable(
        self, box, tmp_path, extension
    ):
        """A mesh has to come back as a solid, or Split and the booleans fail."""
        path = str(tmp_path / f"part{extension}")
        export_shapes([box], path)
        shape = import_bodies(path)[0].shape
        assert is_valid(shape)
        below, above = split_solid(shape, (25.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        assert below is not None and above is not None
        assert volume(below) + volume(above) == pytest.approx(
            volume(shape), rel=1e-6
        )

    def test_brep_round_trips(self, box, tmp_path):
        from OCP.BRepTools import BRepTools

        path = str(tmp_path / "part.brep")
        BRepTools.Write_s(box, path)
        bodies = import_bodies(path)
        assert volume(bodies[0].shape) == pytest.approx(volume(box))

    def test_step_units_arrive_as_millimetres(self, box, tmp_path):
        path = str(tmp_path / "mm.step")
        export_shapes([box], path)
        low, high = bounding_box(import_bodies(path)[0].shape)
        assert high[0] - low[0] == pytest.approx(WIDTH, abs=1e-6)

    def test_names_are_not_exporter_boilerplate(self, box, tmp_path):
        path = str(tmp_path / "bracket.step")
        export_shapes([box], path)
        for body in import_bodies(path):
            assert "open cascade" not in body.name.lower()
            assert "translator" not in body.name.lower()


class TestErrors:
    def test_a_missing_file_says_so(self, tmp_path):
        with pytest.raises(CadError, match="could not be found"):
            import_bodies(str(tmp_path / "nope.step"))

    def test_an_empty_file_says_so(self, tmp_path):
        path = tmp_path / "empty.stl"
        path.write_bytes(b"")
        with pytest.raises(CadError, match="empty"):
            import_bodies(str(path))

    def test_an_unsupported_extension_lists_what_is_supported(self, tmp_path):
        path = tmp_path / "model.xyz"
        path.write_text("nope")
        with pytest.raises(CadError) as caught:
            import_bodies(str(path))
        assert "STEP" in str(caught.value)

    def test_a_corrupt_file_does_not_return_empty_geometry(self, tmp_path):
        path = tmp_path / "junk.step"
        path.write_text("this is not a STEP file\n" * 20)
        with pytest.raises(CadError):
            import_bodies(str(path))

    def test_the_filter_only_offers_what_can_be_read(self):
        offered = import_filter()
        for extension in READERS:
            assert f"*{extension}" in offered


class TestImportFeature:
    def test_rebuilds_from_the_file(self, box, tmp_path):
        path = str(tmp_path / "part.step")
        export_shapes([box], path)

        document = Document("Imported")
        document.add_feature(
            ImportFeature(inputs={"path": path, "index": 0}, outputs=["Part"])
        )
        report = Rebuilder(document).rebuild()
        assert report.ok, report.summary()
        assert volume(document.body("Part").shape) == pytest.approx(volume(box))

    def test_placement_offsets_are_applied(self, box, tmp_path):
        path = str(tmp_path / "part.step")
        export_shapes([box], path)

        document = Document("Imported")
        document.add_feature(
            ImportFeature(
                inputs={"path": path, "index": 0, "dx": 100.0, "dz": 5.0},
                outputs=["Part"],
            )
        )
        Rebuilder(document).rebuild()
        low, _high = bounding_box(document.body("Part").shape)
        assert low[0] == pytest.approx(100.0, abs=1e-6)
        assert low[2] == pytest.approx(5.0, abs=1e-6)

    def test_a_vanished_file_reports_its_path(self, box, tmp_path):
        path = str(tmp_path / "part.step")
        export_shapes([box], path)
        document = Document("Imported")
        document.add_feature(
            ImportFeature(inputs={"path": path, "index": 0}, outputs=["Part"])
        )
        Rebuilder(document).rebuild()

        os.remove(path)
        forget(path)
        rebuilder = Rebuilder(document)
        report = rebuilder.rebuild(force=True)
        assert not report.ok
        assert "part.step" in str(next(iter(report.failures.values())))

    def test_the_feature_survives_a_save_and_reload(self, box, tmp_path):
        path = str(tmp_path / "part.step")
        export_shapes([box], path)
        document = Document("Imported")
        document.add_feature(
            ImportFeature(
                inputs={"path": path, "index": 0, "dx": 7.0}, outputs=["Part"]
            )
        )
        restored = Document.from_dict(document.to_dict())
        Rebuilder(restored).rebuild()
        low, _high = bounding_box(restored.body("Part").shape)
        assert low[0] == pytest.approx(7.0, abs=1e-6)

    def test_one_file_is_parsed_once_for_many_bodies(
        self, box, cylinder, tmp_path
    ):
        """Ten parts in a file must not mean ten reads of it per rebuild."""
        path = str(tmp_path / "two.step")
        export_shapes([box, cylinder], path)
        forget(path)

        import simplecad.kernel.importing as importing

        calls = []
        original = importing.import_bodies
        importing.import_bodies = lambda p: (calls.append(p), original(p))[1]
        try:
            document = Document("Imported")
            for index in range(2):
                document.add_feature(
                    ImportFeature(
                        inputs={"path": path, "index": index},
                        outputs=[f"Part{index}"],
                    )
                )
            Rebuilder(document).rebuild()
        finally:
            importing.import_bodies = original
        assert len(calls) == 1
