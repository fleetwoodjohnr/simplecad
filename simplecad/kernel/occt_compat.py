"""OCCT 7.9 collection names, mapped onto the OCCT 8 bindings.

OCCT 8 dropped the old typedefs (``TopTools_IndexedMapOfShape`` and friends),
and the OCP bindings now name each collection after its template instead. This
is the one place that knows both spellings, so the rest of the code keeps the
names every OCCT reference and example uses.
"""

from OCP.OCP import collections as _c

TopTools_IndexedMapOfShape = _c.IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher
TopTools_IndexedDataMapOfShapeListOfShape = (
    _c.IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher
)
TopTools_ListOfShape = _c.List_TopoDS_Shape
TopTools_HSequenceOfShape = _c.HSequence_TopoDS_Shape
TColgp_Array1OfPnt = _c.Array1_gp_Pnt
TColStd_SequenceOfHAsciiString = _c.Sequence_TCollection_HAsciiString
TDF_LabelSequence = _c.Sequence_TDF_Label
AIS_ListOfInteractive = _c.List_AIS_InteractiveObject
AIS_ManipulatorObjectSequence = _c.HSequence_AIS_InteractiveObject
Graphic3d_Vec2i = _c.Vec2_int
