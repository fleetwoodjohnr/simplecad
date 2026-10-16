"""Face textures stay cheap: few faces per mark, exact circles, colours exported.

Following the project's convention these assert work (face counts), not
machine-dependent timings; scripts/benchmark_texture.py reports the times.
"""

from __future__ import annotations

import zipfile
from xml.etree import ElementTree

import pytest
from shapely.geometry import Point, box

from simplecad.core.document import BodyRef, Document
from simplecad.core.geometry_service import build_preview_result
from simplecad.core.naming import make_ref, sub_shapes
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.detect import analyse_cylinder, analyse_plane
from simplecad.kernel.face_texture import FaceTextureFeature, _circle_of, layout_for_face
from simplecad.kernel.io_formats import export_shapes
from simplecad.kernel.occ import is_valid, volume
from simplecad.kernel.primitives import BoxFeature, CylinderFeature


def test_an_unclipped_dot_is_recognised_as_a_circle():
    dot = Point(4, 5).buffer(1.5, resolution=8)
    (u, v), radius = _circle_of(dot)
    assert (u, v) == pytest.approx((4, 5)) and radius == pytest.approx(1.5)


def test_a_clipped_dot_keeps_its_polygon():
    dot = Point(0, 0).buffer(1.5, resolution=8).intersection(box(-2, -2, 2, 0.5))
    assert _circle_of(dot) is None


def test_a_hexagon_ring_is_not_a_circle():
    from simplecad.kernel.face_texture import _repeated

    hexagon = _repeated((-1, -1, 1, 1), 3.0, 1.0, "hexagon")[0]
    assert _circle_of(hexagon) is None


def _plate(settings, size=40):
    document = Document("Texture")
    producer = document.add_feature(BoxFeature(
        inputs={"width": size, "depth": size, "height": 5}, outputs=["Plate"]))
    assert Rebuilder(document).rebuild().ok
    shape = document.body("Plate").shape
    top = max((f for f in sub_shapes(shape, "face")
               if (info := analyse_plane(f)) is not None and info.normal[2] > 0.9),
              key=lambda f: analyse_plane(f).area)
    feature = FaceTextureFeature(inputs={
        "body": BodyRef("Plate"), "face": make_ref(shape, top, producer.id, body="Plate"),
        "depth": 0.6, **settings}, outputs=["Plate"])
    return document, top, feature


@pytest.mark.parametrize("mode", ["recessed", "raised"])
def test_dots_cost_a_handful_of_faces_each(mode):
    from collections import Counter

    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.TopoDS import TopoDS

    settings = {"pattern": "dots", "mode": mode, "size": 3, "spacing": 1}
    document, top, feature = _plate(settings)
    motifs = layout_for_face(top, {**settings, "depth": 0.6}).motifs
    circles = sum(1 for motif in motifs if _circle_of(motif))
    result = build_preview_result(document, feature.to_dict())
    assert result["error"] is None, result["error"]
    shape = result["shape"]
    assert is_valid(shape)
    kinds = Counter(BRepAdaptor_Surface(TopoDS.Face_s(face)).GetType().name
                    for face in sub_shapes(shape, "face"))
    # Every whole dot is one exact cylindrical wall (it used to be 48 flat
    # facets); only dots clipped by a clearance keep a polygon outline.
    assert circles > len(motifs) // 2
    assert kinds["GeomAbs_Cylinder"] == circles
    assert sum(kinds.values()) < 4 * circles + 40 * (len(motifs) - circles) + 20
    before = 40 * 40 * 5
    assert (volume(shape) < before) if mode == "recessed" else (volume(shape) > before)


def test_a_cylinder_texture_still_builds_a_valid_solid():
    document = Document("Wrapped")
    producer = document.add_feature(CylinderFeature(inputs={"radius": 10, "height": 20},
                                                    outputs=["Roller"]))
    assert Rebuilder(document).rebuild().ok
    shape = document.body("Roller").shape
    wall = next(f for f in sub_shapes(shape, "face") if analyse_cylinder(f) is not None)
    feature = FaceTextureFeature(inputs={
        "body": BodyRef("Roller"), "face": make_ref(shape, wall, producer.id, body="Roller"),
        "pattern": "dots", "mode": "raised", "size": 3, "spacing": 2, "depth": 0.6,
    }, outputs=["Roller"])
    result = build_preview_result(document, feature.to_dict())
    assert result["error"] is None, result["error"]
    assert is_valid(result["shape"])
    assert volume(result["shape"]) > volume(shape)


def test_3mf_export_carries_body_colours(tmp_path):
    document = Document("Colours")
    document.add_feature(BoxFeature(inputs={"width": 10, "depth": 10, "height": 10}, outputs=["Red"]))
    document.add_feature(BoxFeature(inputs={"width": 10, "depth": 10, "height": 10, "x": 20},
                                    outputs=["Plain"]))
    assert Rebuilder(document).rebuild().ok
    document.body("Red").color = "#cc2200"
    path = str(tmp_path / "colours.3mf")
    export_shapes(document.export_items(), path, colours=document.export_colours())
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("3D/3dmodel.model"))
    ns = {"m": root.tag.split("}")[0].strip("{")}
    bases = root.findall(".//m:basematerials/m:base", ns)
    assert [b.get("displaycolor") for b in bases] == ["#CC2200FF"]
    objects = {o.get("name"): o for o in root.findall(".//m:object", ns)}
    assert objects["Red"].get("pindex") == "0"
    assert objects["Plain"].get("pid") is None
