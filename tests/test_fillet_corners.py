"""Every box edge and corner must be usable by the fillet handle."""

import math
from types import SimpleNamespace

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.TopLoc import TopLoc_Location

from simplecad.core.document import BodyRef, Document
from simplecad.core.naming import sub_shapes
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.edge_frame import edge_drag_frame, edge_midpoint, edges_at_vertex
from simplecad.kernel.operations import FilletFeature
from simplecad.kernel.primitives import BoxFeature
from simplecad.ui.selection import Picked
from simplecad.ui.tools.modeling import FilletPanel


def test_located_vertical_and_horizontal_edges_have_drag_frames():
    box = BRepPrimAPI_MakeBox(40, 30, 20).Shape()
    edges = sub_shapes(box, "edge")
    assert len(edges) == 12
    vertical = 0
    for edge in edges:
        # Selection hands the panel a generic TopoDS_Shape with EDGE topology.
        located = edge.Located(TopLoc_Location())
        anchor, direction, limit = edge_drag_frame(box, located)
        assert anchor == edge_midpoint(located)
        assert all(math.isfinite(value) for value in (*anchor, *direction, limit))
        assert math.isclose(sum(value * value for value in direction), 1.0)
        assert limit > 0
        if 0 < anchor[2] < 20:
            vertical += 1
    assert vertical == 4


def test_each_exterior_vertex_expands_to_its_three_edges():
    box = BRepPrimAPI_MakeBox(40, 30, 20).Shape()
    vertices = sub_shapes(box, "vertex")
    assert len(vertices) == 8
    for vertex in vertices:
        edges = edges_at_vertex(box, vertex.Located(TopLoc_Location()))
        assert len(edges) == 3
        for edge in edges:
            assert edge_drag_frame(box, edge)[2] > 0


def test_selected_corner_builds_one_three_edge_fillet():
    document = Document()
    document.add_feature(BoxFeature(
        inputs={"width": 40, "depth": 30, "height": 20}, outputs=["Box"],
    ))
    assert Rebuilder(document).rebuild().ok
    shape = document.body("Box").shape
    vertex = sub_shapes(shape, "vertex")[0].Located(TopLoc_Location())
    selection = SimpleNamespace(
        picks=[Picked("Box", "vertex", vertex)], edges=lambda: [],
    )
    panel = SimpleNamespace(selection=selection, window_=SimpleNamespace(document=document))
    picks = FilletPanel._edge_picks(panel)
    assert len(picks) == 3

    document.add_feature(FilletFeature(
        inputs={
            "body": BodyRef("Box"),
            "edges": [pick.reference(document) for pick in picks],
            "radius": 1.0,
        },
        outputs=["Box"],
    ))
    assert Rebuilder(document).rebuild(force=True).ok
