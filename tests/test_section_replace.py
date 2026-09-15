"""Section Replace integrates a vent while clearing material behind its holes."""

from __future__ import annotations

from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.TopAbs import TopAbs_IN, TopAbs_OUT, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.gp import gp_Pnt

from simplecad.core.document import BodyRef, Document
from simplecad.core.geometry_service import build_preview_result
from simplecad.core.naming import make_ref, sub_shapes
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.detect import analyse_plane
from simplecad.kernel.occ import is_valid
from simplecad.kernel.primitives import BoxFeature
from simplecad.kernel.section_replace import SectionReplaceFeature
from simplecad.kernel.vent import VentPlateFeature


def _face(shape, normal):
    return max(
        (analyse_plane(face).area, face)
        for face in sub_shapes(shape, "face")
        if analyse_plane(face) is not None
        and sum(a * b for a, b in zip(analyse_plane(face).normal, normal)) > .99
    )[1]


def _document_and_feature(setback=0.0):
    document = Document("Integrated vent")
    wall = BoxFeature(
        inputs={"width": 80, "depth": 80, "height": 4}, outputs=["Wall"]
    )
    vent = VentPlateFeature(inputs={
        "width": 40, "depth": 40, "thickness": 3,
        "across_flats": 5, "wall": 1.2, "margin": 2, "x": 100,
    }, outputs=["Vent"])
    document.add_feature(wall)
    document.add_feature(vent)
    rebuilder = Rebuilder(document)
    assert rebuilder.rebuild().ok
    target = document.bodies["Wall"].shape
    replacement = document.bodies["Vent"].shape
    feature = SectionReplaceFeature(inputs={
        "target": BodyRef("Wall"),
        "target_face": make_ref(
            target, _face(target, (0, 0, 1)), wall.id, kind="face", body="Wall"
        ),
        "replacement": BodyRef("Vent"),
        "replacement_face": make_ref(
            replacement, _face(replacement, (0, 0, -1)),
            vent.id, kind="face", body="Vent",
        ),
        "moving_anchor": "center", "target_anchor": "center",
        "u": 0, "v": 0, "boundary": .5, "setback": setback,
    }, outputs=["Wall"])
    return document, rebuilder, feature


def test_section_replace_consumes_vent_and_leaves_clear_openings():
    document, rebuilder, feature = _document_and_feature()
    document.add_feature(feature)
    assert rebuilder.rebuild().ok
    assert list(document.bodies) == ["Wall"]
    result = document.bodies["Wall"].shape
    assert is_valid(result)
    assert sum(1 for _ in _explore(result, TopAbs_SOLID)) == 1

    # The vent grid contains a central hexagon. The target must be absent all
    # the way through it, while surrounding wall geometry remains untouched.
    for z in (.25, 1.0, 2.0, 3.9):
        assert BRepClass3d_SolidClassifier(
            result, gp_Pnt(40, 40, z), 1e-6
        ).State() == TopAbs_OUT
    assert BRepClass3d_SolidClassifier(
        result, gp_Pnt(5, 5, 2), 1e-6
    ).State() == TopAbs_IN


def test_section_replace_preview_exposes_removed_and_replacement_regions():
    document, _rebuilder, feature = _document_and_feature(setback=.25)
    preview = build_preview_result(document, feature.to_dict())
    assert preview["error"] is None
    assert set(preview["parts"]) == {"result", "removed", "replacement"}
    assert all(is_valid(shape) for shape in preview["parts"].values())


def _explore(shape, kind):
    explorer = TopExp_Explorer(shape, kind)
    while explorer.More():
        yield explorer.Current()
        explorer.Next()
