"""M24 matching holes must leave room for the clearance-grown screw."""

import math
from types import SimpleNamespace

import pytest

from simplecad.core.document import BodyRef, Document
from simplecad.core.geometry_service import build_preview_result
from simplecad.core.naming import make_ref, sub_shapes
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.detect import analyse_plane, cylindrical_faces
from simplecad.kernel.occ import axis_transform, is_valid, make_transform, transformed
from simplecad.kernel.operations import HoleFeature, ThreadFeature
from simplecad.kernel.primitives import BoxFeature, CylinderFeature
from simplecad.kernel.thread_specs import by_designation, effective_clearance_for
from simplecad.kernel.threads import thread_solid


def connection(*, bottom=False, blind=False, left_hand=False, preview=False):
    from simplecad.ui.selection import Picked
    from simplecad.ui.tools.matching import MatchingPartPanel

    document = Document("M24 matching hole")
    builder = Rebuilder(document)
    post = document.add_feature(CylinderFeature(
        inputs={"radius": 12, "height": 18}, outputs=["Screw"],
    ))
    document.add_feature(BoxFeature(
        inputs={"width": 40, "depth": 40, "height": 10, "x": 60}, outputs=["Plate"],
    ))
    assert builder.rebuild().ok
    shaft = document.body("Screw").shape
    face = cylindrical_faces(shaft)[0][0]
    document.add_feature(ThreadFeature(inputs={
        "body": BodyRef("Screw"),
        "face": make_ref(shaft, face, post.id, body="Screw"),
        "designation": "M24", "length": 18, "form": "printed",
        "clearance": "normal", "left_hand": left_hand,
    }, outputs=["Screw"]))
    assert builder.rebuild().ok
    thread = document.threads_on("Screw")[0]
    plate = document.body("Plate").shape
    face, info = next((face, info) for face in sub_shapes(plate, "face")
                      if (info := analyse_plane(face)) is not None
                      and info.normal[2] == (-1 if bottom else 1))
    pick = Picked(body="Plate", kind="face", shape=face, info=info)
    # Use the panel's actual feature builder, without needing a GUI window.
    panel = SimpleNamespace(
        thread=thread, kind="hole", window_=SimpleNamespace(document=document),
        clearance=SimpleNamespace(currentData=lambda: "normal"), _placement=(0, 0, 0),
        _target_planar_face=lambda: pick,
        expression=lambda *args: "6" if blind else "20",
    )
    feature = MatchingPartPanel._build_feature(panel)
    if not blind:
        feature.inputs["depth_mode"] = "through"
    answer = build_preview_result(document, feature.to_dict()) if preview else None
    if answer is not None:
        assert answer["error"] is None, answer
    document.add_feature(feature)
    report = builder.rebuild()
    assert report.ok, report.summary()
    placement = axis_transform(info.center, tuple(-v for v in info.normal))
    return SimpleNamespace(document=document, feature=feature, thread=thread,
                           placement=placement, length=6 if blind else 10,
                           preview=answer)


@pytest.fixture(scope="module")
def m24():
    return connection(preview=True)


def assert_clearance(shape, pair):
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN, TopAbs_SOLID
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape
    from OCP.gp import gp_Pnt

    assert is_valid(shape)
    solids = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_SOLID, solids)
    assert solids.Extent() == 1, "the thread must stay attached to the plate"
    size = by_designation(pair.thread["designation"])
    gap = effective_clearance_for(pair.thread["clearance"])
    local_hole = transformed(shape, pair.placement.Inverted())
    gauge = thread_solid(size.diameter + gap, size.pitch, pair.length,
                         form=pair.thread["form"], swell=gap / 2,
                         left_hand=pair.thread["left_hand"])
    female = BRepClass3d_SolidClassifier(local_hole)
    envelope = BRepClass3d_SolidClassifier(gauge)
    probes = 0
    for index in range(72):
        angle = index * math.pi / 36
        radius = size.diameter / 2 + gap / 4
        point = gp_Pnt(radius * math.cos(angle), radius * math.sin(angle), pair.length / 2)
        envelope.Perform(point, 1e-5)
        if envelope.State() != TopAbs_IN:
            continue
        probes += 1
        female.Perform(point, 1e-5)
        assert female.State() != TopAbs_IN, "the original bore wall occupies required clearance"
    assert probes >= 10, "the check must sample the helical clearance, not empty space"


def test_m24_preview_and_committed_hole_preserve_the_source_and_its_clearance(m24):
    recorded = m24.document.threads_on("Plate")[0]
    for key in ("designation", "clearance", "form", "left_hand"):
        assert recorded[key] == m24.thread[key]
    assert recorded["internal"] is True
    assert m24.thread["internal"] is False
    assert_clearance(m24.preview["shape"], m24)
    assert_clearance(m24.document.body("Plate").shape, m24)


@pytest.mark.slow
def test_m24_screws_through_a_full_turn_and_still_engages(m24):
    from .fit import TOLERANCE, buried_fraction, turned_in_place

    screw = m24.document.body("Screw").shape
    female = transformed(m24.document.body("Plate").shape, m24.placement.Inverted())
    for step in range(5):
        advanced = transformed(screw, make_transform(
            translate=(0, 0, 3 * step / 4), rotate_axis=(0, 0, 1),
            rotate_degrees=90 * step,
        ))
        assert buried_fraction(advanced, female) < TOLERANCE
    assert buried_fraction(turned_in_place(screw, 90), female) > .10, (
        "a smooth oversized hole would clear the screw but would not hold it"
    )


@pytest.mark.parametrize("bottom,blind,left_hand", [(False, True, False), (True, False, True)])
def test_m24_clearance_also_applies_to_blind_and_reversed_left_hand_holes(bottom, blind, left_hand):
    pair = connection(bottom=bottom, blind=blind, left_hand=left_hand)
    assert_clearance(pair.document.body("Plate").shape, pair)


def test_saved_m24_matching_hole_rebuilds_with_correct_clearance(m24, tmp_path):
    from simplecad.core.project import load, save

    path = save(m24.document, str(tmp_path / "m24.scad3"))
    reopened, cached = load(path)
    assert cached
    assert Rebuilder(reopened).rebuild(force=True).ok
    assert_clearance(reopened.body("Plate").shape, m24)


@pytest.mark.parametrize("diameter", [22, 24, 24.6, 26])
def test_threaded_drilling_reserves_clearance_and_preserves_larger_requested_bores(monkeypatch, diameter):
    document = Document("Drill")
    plate = document.add_feature(BoxFeature(
        inputs={"width": 40, "depth": 40, "height": 10}, outputs=["Plate"],
    ))
    builder = Rebuilder(document)
    assert builder.rebuild().ok
    shape = document.body("Plate").shape
    face = next(f for f in sub_shapes(shape, "face")
                if (p := analyse_plane(f)) and p.normal[2] == 1)
    # Inspect the actual drilled wall before the helical filler is applied.
    def inspect(self, ctx, shape, actual_diameter, inward, position, *, size):
        assert size.designation == "M24"
        assert actual_diameter == pytest.approx(max(diameter, 24.6))
        assert cylindrical_faces(shape)[0][1].diameter == pytest.approx(actual_diameter)
        return shape
    monkeypatch.setattr(HoleFeature, "_thread_the_bore", inspect)
    document.add_feature(HoleFeature(inputs={
        "body": BodyRef("Plate"), "face": make_ref(shape, face, plate.id, body="Plate"),
        "diameter": diameter, "designation": "M24", "style": "threaded",
    }, outputs=["Plate"]))
    assert builder.rebuild().ok
