"""Turning viewport picks into things the document can store.

The viewport hands back OCCT sub-shapes. The document needs durable
:class:`SubShapeRef` references tied to a named body, so that a face picked
today still resolves after tomorrow's parameter change. This module is the
bridge, and it is also where SimpleCAD decides what a selection *means* -- which
is what lets the contextual toolbar offer Stack rather than a list of every
command in the program.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.naming import SubShapeRef, make_ref
from ..kernel.detect import CylinderInfo, PlaneInfo, analyse_cylinder, analyse_plane


#: Pick kinds that mean "the whole object", not a face or an edge of it.
#:
#: The viewport names a pick after its OCCT shape type, and a body built by a
#: primitive or read out of a STEP file is a ``TopAbs_SOLID`` -- so selecting a
#: whole part reports ``"solid"``, and only a compound of several solids ever
#: reports ``"body"``. Any rule about whole objects has to accept both, or it
#: silently applies to almost nothing.
WHOLE_OBJECT_KINDS = frozenset({"body", "solid", "shell", "compound"})


class PresentationMap(dict):
    """Body presentations with constant-time reverse lookup for picking."""

    def __init__(self):
        super().__init__()
        self._reverse: dict[int, str] = {}

    def __setitem__(self, name, presentation):
        old = self.get(name)
        if old is not None:
            self._reverse.pop(id(old), None)
        super().__setitem__(name, presentation)
        if presentation is not None:
            self._reverse[id(presentation)] = name

    def __delitem__(self, name):
        self._reverse.pop(id(self[name]), None)
        super().__delitem__(name)

    def pop(self, name, *default):
        if name in self:
            self._reverse.pop(id(self[name]), None)
        return super().pop(name, *default)

    def clear(self):
        super().clear()
        self._reverse.clear()

    def update(self, *args, **kwargs):
        for name, presentation in dict(*args, **kwargs).items():
            self[name] = presentation

    def setdefault(self, name, default=None):
        if name not in self:
            self[name] = default
        return self[name]

    def name_for(self, presentation):
        return self._reverse.get(id(presentation))


@dataclass
class Picked:
    """One selected entity, with everything the UI needs to act on it."""

    body: str
    kind: str                       # face | edge | vertex | body
    shape: object
    presentation: object = None
    info: PlaneInfo | CylinderInfo | None = None

    @property
    def is_planar_face(self) -> bool:
        return isinstance(self.info, PlaneInfo)

    @property
    def is_round_face(self) -> bool:
        return isinstance(self.info, CylinderInfo)

    def describe(self) -> str:
        if isinstance(self.info, CylinderInfo):
            return f"{self.body}: {self.info.describe()}"
        if isinstance(self.info, PlaneInfo):
            return f"{self.body}: flat face, {self.info.area:.0f} mm²"
        return f"{self.body}: {self.kind}"

    def reference(self, document) -> SubShapeRef:
        """A durable reference to this pick, anchored to its producing feature."""
        body = document.body(self.body)
        producer = body.producer if body else ""
        return make_ref(
            body.shape, self.shape, producer, kind=self.kind, body=self.body
        )


class SelectionModel:
    """The current selection, expressed in document terms."""

    def __init__(self, document, viewport, presentations: dict) -> None:
        self.document = document
        self.viewport = viewport
        #: body name -> AIS presentation, owned by the main window.
        self.presentations = presentations
        self.picks: list[Picked] = []

    def refresh(self) -> None:
        self.picks = []
        by_presentation = None if hasattr(self.presentations, "name_for") else {
            id(v): k for k, v in self.presentations.items()
        }
        for entry in self.viewport.selected_entries():
            presentation = entry["presentation"]
            name = (self.presentations.name_for(presentation) if by_presentation is None
                    else by_presentation.get(id(presentation)))
            if name is None:
                continue
            shape = entry["shape"]
            info = None
            if entry["kind"] == "face":
                info = analyse_plane(shape) or analyse_cylinder(shape)
            from ..core.document import BodyRef
            body = self.document.body(name)
            identity = BodyRef(name, body.id, body.producer) if body else BodyRef(name)
            self.picks.append(
                Picked(
                    body=identity, kind=entry["kind"], shape=shape,
                    presentation=presentation, info=info,
                )
            )

    # -- queries the contextual toolbar asks -----------------------------
    @property
    def count(self) -> int:
        return len(self.picks)

    @property
    def bodies(self) -> list[str]:
        seen: list[str] = []
        for pick in self.picks:
            if pick.body not in seen:
                seen.append(pick.body)
        return seen

    @property
    def groups(self) -> list[str]:
        """The top-level groups this selection lies inside."""
        found: list[str] = []
        for name in self.bodies:
            group = self.document.top_group_of(name)
            if group is not None and group.name not in found:
                found.append(group.name)
        return found

    @property
    def items(self) -> list[str]:
        """What is selected at the top level: group names, or loose bodies.

        The distinction the Group action needs. ``bodies`` is always the fully
        expanded list -- the things an operation acts on -- but grouping a group
        together with a body has to know that the first one *is* a group, or it
        would flatten the nesting instead of deepening it.
        """
        found: list[str] = []
        for name in self.bodies:
            group = self.document.top_group_of(name)
            item = group.name if group is not None else name
            if item not in found:
                found.append(item)
        return found

    @property
    def only_bodies(self) -> bool:
        """True when whole objects are selected, rather than faces or edges."""
        return bool(self.picks) and all(
            p.kind in WHOLE_OBJECT_KINDS for p in self.picks
        )

    def faces(self) -> list[Picked]:
        return [p for p in self.picks if p.kind == "face"]

    def edges(self) -> list[Picked]:
        return [p for p in self.picks if p.kind == "edge"]

    def planar_faces(self) -> list[Picked]:
        return [p for p in self.picks if p.is_planar_face]

    def round_faces(self) -> list[Picked]:
        return [p for p in self.picks if p.is_round_face]

    def spans_two_bodies(self) -> bool:
        return len(self.bodies) == 2

    def summary(self) -> str:
        if not self.picks:
            return "Nothing selected"
        if len(self.picks) == 1:
            return self.picks[0].describe()
        kinds = {p.kind for p in self.picks}
        kind = kinds.pop() if len(kinds) == 1 else "item"
        return f"{len(self.picks)} {kind}s selected across {len(self.bodies)} body(s)"


#: Actions the contextual toolbar can offer, in priority order. Each entry is
#: (key, label, icon, predicate). Everything that matches is offered -- the bar
#: decides how many fit and puts the rest one click away -- and the first match
#: is drawn as the primary action, so the bar leads with the operation the
#: selection most likely wants.
def _two_planar_faces(model: SelectionModel) -> bool:
    return len(model.planar_faces()) == 2 and model.spans_two_bodies()


def _two_round_faces(model: SelectionModel) -> bool:
    return len(model.round_faces()) == 2 and model.spans_two_bodies()


def _threadable_pair(model: SelectionModel) -> bool:
    faces = model.round_faces()
    return (
        len(faces) == 2
        and model.spans_two_bodies()
        and faces[0].info.internal != faces[1].info.internal
    )


def _body_and_target_face(model: SelectionModel) -> bool:
    faces = model.planar_faces()
    if len(faces) != 1 or len(model.bodies) != 2:
        return False
    return any(
        pick.body != faces[0].body and pick.kind in WHOLE_OBJECT_KINDS
        for pick in model.picks
    )


def _section_replaceable(model: SelectionModel) -> bool:
    return _two_planar_faces(model) or _body_and_target_face(model)


def _has_a_thread(model: SelectionModel) -> bool:
    """Is anything selected already threaded?"""
    return any(model.document.threads_on(name) for name in model.bodies)


def _matchable_target(model: SelectionModel) -> bool:
    """A face that could take the mate of a thread that exists elsewhere.

    The primary entry needs the threaded body itself in the selection, and that
    is the right thing to lead with when it is there. But "select the thread,
    then select the face its mate goes on" is an ordinary way to work, and
    clicking the target *replaces* the selection -- so the command the user was
    reaching for vanished from the bar at the moment they reached for it. The
    panel already falls back to the newest thread in the document, so the only
    thing missing was the way back in.
    """
    if _has_a_thread(model):
        return False        # already offered above, and led with
    if model.document.any_thread() is None:
        return False
    return bool(model.round_faces() or model.planar_faces())


def _roundable(model: SelectionModel) -> bool:
    """Edges, or a corner -- both are things Fillet and Chamfer understand."""
    return bool(model.edges()) or any(p.kind == "vertex" for p in model.picks)


def edge_finish_candidate(model: SelectionModel) -> bool:
    """One edge or face on a body that has a fillet or chamfer somewhere."""
    if len(model.picks) != 1 or model.picks[0].kind not in {"edge", "face"}:
        return False
    name = str(model.picks[0].body)
    graph = model.document.prepare_graph()
    head = graph.latest.get(name)
    if head is None:
        return False
    return any(graph.features[fid].type_name in {"fillet", "chamfer", "edge_finish"}
               for fid in graph.writers.get(head.object_id, ()))


def edge_probe_key(model: SelectionModel):
    pick = model.picks[0]
    try:
        reference = pick.reference(model.document).to_dict()
    except Exception:  # noqa: BLE001 - an unreferencable pick is not a blend
        return None
    import json

    return (str(pick.body), model.document.revision, json.dumps(reference, sort_keys=True))


def _has_edge_finish(model: SelectionModel) -> bool:
    """Offered only once the pick is confirmed to belong to a fillet/chamfer.

    Every edge of a filleted body used to offer "Edit", and most of them then
    failed. The window asks the geometry process which blend (if any) made
    the picked edge, and records the answer here.
    """
    if not edge_finish_candidate(model):
        return False
    return bool(getattr(model, "edge_finish_known", {}).get(edge_probe_key(model)))


def joint_for(model: SelectionModel):
    """The newest editable joint written into the picked part, if any."""
    if len(model.picks) != 1:
        return None
    graph = model.document.prepare_graph()
    head = graph.latest.get(str(model.picks[0].body))
    if head is None:
        return None
    for fid in reversed(graph.writers.get(head.object_id, ())):
        feature = graph.features[fid]
        if feature.type_name == "clip_joint" and feature.inputs.get("clip_version"):
            return feature
    return None


def _has_joint(model: SelectionModel) -> bool:
    return joint_for(model) is not None


def _one_body(model: SelectionModel) -> bool:
    return model.only_bodies and len(model.bodies) == 1


def _bodies(model: SelectionModel) -> bool:
    return model.only_bodies and bool(model.bodies)


def _groupable(model: SelectionModel) -> bool:
    """Two or more separate things, where a group is already one thing.

    Reading ``items`` rather than ``bodies`` is what makes nesting work and
    stops a selected group offering to group itself: a group counts once, so
    selecting one offers nothing, and selecting a group plus a body offers to
    put the body into a new group alongside it.
    """
    return model.only_bodies and len(model.items) >= 2


def _grouped(model: SelectionModel) -> bool:
    return bool(model.groups)


def _combinable(model: SelectionModel) -> bool:
    """Two or more whole bodies -- what a boolean needs.

    Read from ``bodies`` rather than ``items`` because a boolean acts on
    geometry: a group of two is two bodies to cut with, even though Group
    counts it as one thing.
    """
    return model.only_bodies and len(model.bodies) >= 2


def _arrangeable(model: SelectionModel) -> bool:
    targets = model.planar_faces()
    if len(targets) > 1:
        return False
    target_body = targets[0].body if targets else None
    moving = [name for name in model.bodies if name != target_body]
    return len(moving) >= 3 and all(
        pick.kind in WHOLE_OBJECT_KINDS
        or (bool(targets) and pick is targets[0])
        for pick in model.picks
    )


def texture_features(model: SelectionModel) -> list:
    """Applied textures on the one selected body, in history order.

    Relief is fused into the body, so a pick on a motif or an ordinary face
    offers the same features. Multiple textures are chosen explicitly in the UI.
    """
    if len(model.bodies) != 1:
        return []
    name = model.bodies[0]
    graph = model.document.prepare_graph()
    head = graph.latest.get(name)
    if head is None:
        return []
    ancestors, pending = set(), [head.feature_id]
    while pending:
        fid = pending.pop()
        if fid not in ancestors:
            ancestors.add(fid)
            pending.extend(graph.forward.get(fid, ()))
    return [feature for feature in model.document.features
            if feature.id in ancestors and feature.type_name == "face_texture"
            and not feature.suppressed and not feature.dropped]



CONTEXT_ACTIONS = (
    ("edit_edge_finish", "Edit fillet / chamfer", "fillet", _has_edge_finish),
    ("edit_face_texture", "Edit texture", "texture", texture_features),
    ("remove_face_texture", "Remove texture", "trash", texture_features),
    # -- relationships between two parts, which are the most specific reading
    ("edit_joint", "Edit joint", "clip", _has_joint),
    ("clip_joint", "Clip / Join Parts", "clip", _two_planar_faces),
    ("align_threaded", "Align & Thread", "thread", _threadable_pair),
    ("threaded_connection", "Create Threaded Connection", "thread", _threadable_pair),
    ("matching_part", "Create Matching Part", "thread", _has_a_thread),
    ("concentric", "Concentric", "concentric", _two_round_faces),
    ("stack", "Stack", "stack", _two_planar_faces),
    ("align_stack", "Align & Stack", "align", _two_planar_faces),
    ("center", "Center", "align", _two_planar_faces),
    ("place_on_face", "Place on Face", "align", _body_and_target_face),
    ("section_replace", "Section Replace", "cut", _section_replaceable),
    # -- a face
    #
    # Pull answers for round faces too, and leads on them. Dragging the side of
    # a shaft or a tube inward to make it thinner is the same gesture as pulling
    # a flat face, and it is the commoner thing to want to do to a cylinder than
    # threading it -- Thread sits directly underneath either way.
    ("pushpull", "Pull", "extrude",
     lambda m: m.count == 1 and (
         len(m.planar_faces()) == 1 or len(m.round_faces()) == 1
     )),
    ("thread", "Thread", "thread",
     lambda m: len(m.round_faces()) == 1 and m.count == 1),
    ("hole", "Hole", "hole", lambda m: len(m.planar_faces()) >= 1),
    ("sketch", "Sketch on face", "sketch",
     lambda m: len(m.planar_faces()) == 1 and m.count == 1),
    ("text", "Add text", "text",
     lambda m: len(m.planar_faces()) == 1 and m.count == 1),
    ("face_texture", "Face texture", "texture",
     lambda m: m.count == 1 and (
         len(m.planar_faces()) == 1 or len(m.round_faces()) == 1
     )),
    ("shell", "Hollow", "shell", lambda m: len(m.planar_faces()) >= 1),
    # Below the face actions on purpose: threading a face to match something
    # else is a real thing to want, but not more likely than pulling it.
    ("matching_part", "Create Matching Part", "thread", _matchable_target),
    # -- an edge or a corner
    ("fillet", "Fillet", "fillet", _roundable),
    ("chamfer", "Chamfer", "chamfer", _roundable),
    # -- two or more bodies, where a boolean is usually the point
    ("subtract", "Subtract", "subtract", _combinable),
    ("join", "Join", "union", _combinable),
    ("intersect", "Intersect", "intersect", _combinable),
    # -- whole objects
    ("arrange", "Arrange", "pattern", _arrangeable),
    ("group", "Group", "group", _groupable),
    ("move", "Move", "move", _bodies),
    ("rotate", "Rotate", "rotate", _bodies),
    ("scale", "Scale", "scale", _bodies),
    ("split", "Split", "split", _one_body),
    ("ungroup", "Ungroup", "ungroup", _grouped),
    ("duplicate", "Duplicate", "duplicate", _bodies),
    ("measure", "Measure", "measure", lambda m: m.count >= 1),
    ("hide", "Hide", "eye_off", _bodies),
    ("delete", "Delete", "trash", _bodies),
)


def available_actions(model: SelectionModel) -> list[tuple[str, str, str]]:
    """Which actions make sense for the current selection, best first.

    This is the rule that keeps context menus honest: only operations that can
    actually run on what is selected are offered. Every match is returned --
    truncating here is what used to make the bar look half-loaded, because the
    caller had no way to tell a short list from a cut-off one.
    """
    return [
        (key, label, icon)
        for key, label, icon, predicate in CONTEXT_ACTIONS
        if predicate(model)
    ]
