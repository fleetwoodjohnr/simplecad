"""The document: bodies, features, parameters, and the graph that ties them.

The feature graph is deliberately **not** a per-body linear history. A feature
has N inputs and N outputs and the DAG spans the whole document, because two of
SimpleCAD's headline operations do not fit a linear model:

* **Stack/Align** makes one body's placement depend on another body's geometry,
  which is an edge *between* bodies.
* **Create Threaded Connection** is a single node with **two** outputs that
  writes into two bodies and owns the clearance they share -- which is what
  makes "change the clearance and both mating parts update" fall out for free.

A body is therefore a named output slot of whichever feature last wrote it,
never an owner of history.
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator

from .errors import CadError, RebuildError
from .naming import SubShapeRef
from .params import ParameterSet
from .units import Dimension

#: type_name -> Feature subclass, populated by @register.
REGISTRY: dict[str, type["Feature"]] = {}


def register(type_name: str):
    """Register a feature class so documents can be loaded back."""

    def decorate(cls: type["Feature"]) -> type["Feature"]:
        cls.type_name = type_name
        REGISTRY[type_name] = cls
        return cls

    return decorate


class FeatureState(str, Enum):
    OK = "ok"
    SUPPRESSED = "suppressed"
    FAILED = "failed"
    #: Built, but a reference it depends on could not be resolved.
    NEEDS_ATTENTION = "needs_attention"


class BodyRef(str):
    """An input that names another feature's output body.

    A distinct type so dependency extraction can tell "the body called Base"
    from an ordinary string input.
    """


@dataclass
class Body:
    """A solid in the document. Named output slot of its producing feature."""

    name: str
    shape: Any = None
    visible: bool = True
    color: str | None = None
    producer: str = ""
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "visible": self.visible,
            "color": self.color,
            "producer": self.producer,
            "metadata": self.metadata,
        }


@dataclass
class Group:
    """Several bodies handled as one thing.

    Deliberately **not** a feature and deliberately not geometry. Grouping is an
    organisational act: it says these parts belong together, so select one and
    you get them all, move them and they move together, hide them and they go
    away together. The bodies inside keep their own identity, their own history
    and their own separate geometry -- which is the difference between a group
    and a boolean union, and the reason grouping is not one.

    A member may name another group, so groups nest.
    """

    name: str
    members: list[str] = field(default_factory=list)
    visible: bool = True
    expanded: bool = True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "members": list(self.members),
            "visible": self.visible,
            "expanded": self.expanded,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Group":
        return cls(
            name=data["name"],
            members=list(data.get("members", [])),
            visible=data.get("visible", True),
            expanded=data.get("expanded", True),
        )


class BuildContext:
    """What a feature is given when it executes."""

    def __init__(self, document: "Document") -> None:
        self.document = document
        self.bodies: dict[str, Any] = {}
        self.warnings: list[str] = []

    def value(self, feature: "Feature", key: str, default: float = 0.0) -> float:
        """Evaluate a numeric input, which may be an expression."""
        raw = feature.inputs.get(key, default)
        if isinstance(raw, (int, float)):
            return float(raw)
        dimension = feature.dimensions.get(key, Dimension.LENGTH)
        return self.document.parameters.evaluate(str(raw), dimension)

    def shape(self, feature: "Feature", key: str):
        """Fetch the shape of a body input."""
        name = feature.inputs.get(key)
        if not name:
            raise CadError(
                "This feature is missing one of its inputs.",
                suggestion="Re-select the body it should act on.",
            )
        shape = self.bodies.get(str(name))
        if shape is None:
            raise CadError(
                f"The body '{name}' this feature needs is not available.",
                suggestion="It may have been deleted. Re-select the body.",
            )
        return shape

    def resolve(self, feature: "Feature", key: str, *, required: bool = True):
        """Resolve a stored sub-shape reference against the live geometry.

        This is where the naming layer earns its keep: the reference was taken
        before an upstream edit and has to find the same face or edge in the
        shape that edit produced.
        """
        from .naming import resolve as resolve_ref

        ref = feature.inputs.get(key)
        if ref is None:
            if not required:
                return None
            raise CadError(
                "This feature has lost the geometry it was built on.",
                suggestion="Re-select the face or edge for this feature.",
            )
        refs = ref if isinstance(ref, (list, tuple)) else [ref]
        found = []
        for item in refs:
            shape = self.bodies.get(item.body)
            if shape is None:
                raise CadError(
                    f"The body '{item.body}' this feature needs is not available.",
                    suggestion="It may have been deleted or renamed.",
                )
            found.append(resolve_ref(item, shape))
        return found if isinstance(ref, (list, tuple)) else found[0]

    def warn(self, message: str) -> None:
        self.warnings.append(message)


class Feature:
    """Base class. Subclasses implement :meth:`execute`."""

    type_name: str = "feature"
    #: Human label shown in the timeline, e.g. "Box".
    label: str = "Feature"
    #: Which inputs are angles rather than lengths, for expression evaluation.
    dimensions: dict[str, Dimension] = {}

    def __init__(
        self,
        feature_id: str | None = None,
        name: str = "",
        inputs: dict | None = None,
        outputs: list[str] | None = None,
    ) -> None:
        self.id = feature_id or f"f{uuid.uuid4().hex[:10]}"
        self.name = name
        self.inputs: dict[str, Any] = dict(inputs or {})
        self.outputs: list[str] = list(outputs or [])
        self.suppressed = False
        self.state = FeatureState.OK
        self.message = ""
        #: Output names the user has deleted. See :meth:`keep`.
        self.dropped: list[str] = []

    # -- graph ----------------------------------------------------------
    def body_inputs(self) -> list[str]:
        """Names of bodies this feature reads."""
        found = []
        for value in self.inputs.values():
            if isinstance(value, BodyRef):
                found.append(str(value))
            elif isinstance(value, (list, tuple)):
                found.extend(str(v) for v in value if isinstance(v, BodyRef))
        return found

    def shape_refs(self) -> list[SubShapeRef]:
        """Sub-shape references this feature holds."""
        found = []
        for value in self.inputs.values():
            if isinstance(value, SubShapeRef):
                found.append(value)
            elif isinstance(value, (list, tuple)):
                found.extend(v for v in value if isinstance(v, SubShapeRef))
        return found

    def parameter_names(self) -> set[str]:
        """Parameters referenced by this feature's expressions."""
        from .params import dependencies

        names: set[str] = set()
        for value in self.inputs.values():
            if isinstance(value, str) and not isinstance(value, BodyRef):
                names |= dependencies(value)
        return names

    # -- execution ------------------------------------------------------
    def execute(self, ctx: BuildContext) -> dict[str, Any]:
        """Produce ``{body_name: TopoDS_Shape}``."""
        raise NotImplementedError

    def consumed_bodies(self) -> list[str]:
        """Bodies this feature destroys in the making of its outputs.

        Almost every operation writes a body back over itself, so the question
        does not arise. Split is the exception: it turns one body into two
        differently-named ones, and without saying so the original would linger
        in the tree alongside its own halves, occupying the same space as both.
        """
        return []

    def keep(self, outputs: dict[str, Any]) -> dict[str, Any]:
        """Filter out bodies the user has deleted.

        A feature with one output can be deleted along with its body. A feature
        with *two* cannot: Split writes both halves of a part from a single
        node, so removing the feature because one half was deleted would take
        the other half with it. Instead the deleted name is recorded in
        :attr:`dropped` and the rebuild simply stops emitting it, which leaves
        the surviving half exactly as parametric as it was.
        """
        if not self.dropped:
            return outputs
        self.outputs = [name for name in self.outputs if name not in self.dropped]
        return {
            name: shape for name, shape in outputs.items()
            if name not in self.dropped
        }

    # -- serialisation --------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type_name,
            "name": self.name,
            "inputs": _encode_inputs(self.inputs),
            "outputs": list(self.outputs),
            "suppressed": self.suppressed,
            "dropped": list(self.dropped),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Feature":
        klass = REGISTRY.get(data["type"])
        if klass is None:
            raise CadError(
                f"This file uses a feature type this version does not know: "
                f"{data['type']!r}.",
                suggestion="Update SimpleCAD, or remove that feature.",
            )
        feature = klass(
            feature_id=data["id"],
            name=data.get("name", ""),
            inputs=_decode_inputs(data.get("inputs", {})),
            outputs=data.get("outputs", []),
        )
        feature.suppressed = data.get("suppressed", False)
        feature.dropped = list(data.get("dropped", []))
        return feature

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.type_name} {self.name or self.id}>"


def _encode_inputs(inputs: dict) -> dict:
    out = {}
    for key, value in inputs.items():
        if isinstance(value, SubShapeRef):
            out[key] = {"__ref__": value.to_dict()}
        elif isinstance(value, BodyRef):
            out[key] = {"__body__": str(value)}
        elif isinstance(value, (list, tuple)):
            out[key] = [_encode_inputs({"v": v})["v"] for v in value]
        else:
            out[key] = value
    return out


def _decode_inputs(data: dict) -> dict:
    def decode(value):
        if isinstance(value, dict) and "__ref__" in value:
            return SubShapeRef.from_dict(value["__ref__"])
        if isinstance(value, dict) and "__body__" in value:
            return BodyRef(value["__body__"])
        if isinstance(value, list):
            return [decode(v) for v in value]
        return value

    return {key: decode(value) for key, value in data.items()}


class Document:
    """Bodies, features and parameters, plus the dependency graph over them."""

    def __init__(self, title: str = "Untitled") -> None:
        self.title = title
        self.parameters = ParameterSet()
        self.features: list[Feature] = []
        self.bodies: dict[str, Body] = {}
        self.groups: dict[str, Group] = {}
        self.metadata: dict = {}
        self._counters: dict[str, itertools.count] = {}

    # -- features -------------------------------------------------------
    def add_feature(self, feature: Feature, index: int | None = None) -> Feature:
        if not feature.name:
            feature.name = self.unique_name(feature.label)
        if index is None:
            self.features.append(feature)
        else:
            self.features.insert(index, feature)
        return feature

    def remove_feature(self, feature_id: str) -> None:
        feature = self.feature(feature_id)
        if feature is None:
            return
        for name in feature.outputs:
            self.bodies.pop(name, None)
        self.features = [f for f in self.features if f.id != feature_id]

    def feature(self, feature_id: str) -> Feature | None:
        return next((f for f in self.features if f.id == feature_id), None)

    def feature_by_name(self, name: str) -> Feature | None:
        return next((f for f in self.features if f.name == name), None)

    def index_of(self, feature_id: str) -> int:
        return next(
            (i for i, f in enumerate(self.features) if f.id == feature_id), -1
        )

    def unique_name(self, base: str) -> str:
        """``Box``, ``Box2``, ``Box3`` -- matching how the timeline reads."""
        taken = {f.name for f in self.features} | set(self.bodies) | set(self.groups)
        if base not in taken:
            return base
        counter = self._counters.setdefault(base, itertools.count(2))
        for suffix in counter:
            candidate = f"{base}{suffix}"
            if candidate not in taken:
                return candidate
        raise AssertionError("unreachable")

    def rename_feature(self, feature_id: str, new_name: str) -> None:
        feature = self.feature(feature_id)
        if feature is None:
            raise KeyError(feature_id)
        if any(f.name == new_name and f.id != feature_id for f in self.features):
            raise CadError(f"There is already a feature called '{new_name}'.")
        feature.name = new_name

    # -- graph ----------------------------------------------------------
    def producer_of(self, body_name: str) -> Feature | None:
        """The last feature in the timeline that writes *body_name*."""
        for feature in reversed(self.features):
            if body_name in feature.outputs:
                return feature
        return None

    def dependencies_of(self, feature: Feature) -> set[str]:
        """Feature ids *feature* depends on, via bodies and sub-shape refs."""
        found: set[str] = set()
        position = self.index_of(feature.id)
        for body_name in feature.body_inputs():
            # Bind to the most recent writer *before* this feature.
            for candidate in reversed(self.features[:position]):
                if body_name in candidate.outputs:
                    found.add(candidate.id)
                    break
        for ref in feature.shape_refs():
            if ref.feature_id and ref.feature_id != feature.id:
                found.add(ref.feature_id)
        return found

    def dependents_of(self, feature_ids: set[str]) -> set[str]:
        """Everything downstream of *feature_ids*, transitively."""
        dirty = set(feature_ids)
        changed = True
        while changed:
            changed = False
            for feature in self.features:
                if feature.id in dirty:
                    continue
                if self.dependencies_of(feature) & dirty:
                    dirty.add(feature.id)
                    changed = True
        return dirty

    def features_using_parameter(self, name: str) -> set[str]:
        return {f.id for f in self.features if name in f.parameter_names()}

    def topological_order(self) -> list[Feature]:
        """Timeline order, adjusted so dependencies always come first."""
        order: list[Feature] = []
        placed: set[str] = set()
        remaining = list(self.features)
        while remaining:
            progressed = False
            for feature in list(remaining):
                if self.dependencies_of(feature) <= placed:
                    order.append(feature)
                    placed.add(feature.id)
                    remaining.remove(feature)
                    progressed = True
            if not progressed:
                # A cycle: emit the rest in timeline order and let the rebuild
                # report the failure rather than looping here.
                order.extend(remaining)
                break
        return order

    def ordering_is_valid(self, features: list[Feature]) -> bool:
        """Can this ordering be built -- is every input available in time?

        Checked directly against the candidate list rather than via
        :meth:`dependencies_of`, which binds each body input to the most recent
        writer *before* the feature. That binding is position-sensitive: asking
        it about a reordered list would just silently rebind the input to a
        different producer instead of reporting the move as invalid.
        """
        written: set[str] = set()
        seen: set[str] = set()
        for feature in features:
            for body_name in feature.body_inputs():
                if body_name not in written:
                    return False
            for ref in feature.shape_refs():
                if ref.feature_id and ref.feature_id != feature.id:
                    if ref.feature_id not in seen:
                        return False
            seen.add(feature.id)
            written.update(feature.outputs)
        return True

    def can_reorder(self, feature_id: str, new_index: int) -> bool:
        """Is moving this feature to *new_index* still a valid ordering?"""
        current = self.index_of(feature_id)
        if current < 0 or new_index < 0 or new_index >= len(self.features):
            return False
        moved = list(self.features)
        moved.insert(new_index, moved.pop(current))
        return self.ordering_is_valid(moved)

    def reorder(self, feature_id: str, new_index: int) -> None:
        if not self.can_reorder(feature_id, new_index):
            raise CadError(
                "That feature cannot move there.",
                suggestion="It depends on geometry created later in the timeline.",
            )
        current = self.index_of(feature_id)
        feature = self.features.pop(current)
        self.features.insert(new_index, feature)

    def threads_on(self, body_name: str) -> list[dict]:
        """Thread definitions recorded against a body, newest first.

        Read off the feature history rather than the geometry: a modelled thread
        is a helical surface that is expensive to recognise, and the feature
        already knows exactly what it cut.
        """
        found: list[dict] = []
        for feature in reversed(self.features):
            if feature.type_name not in (
                "thread", "threaded_connection", "hole", "matching_thread"
            ):
                continue
            designation = feature.inputs.get("designation")
            if not designation:
                continue
            if body_name not in feature.outputs:
                continue
            found.append({
                "designation": str(designation),
                "feature": feature.name,
                "clearance": feature.inputs.get("clearance", "normal"),
                "type": feature.type_name,
            })
        return found

    def any_thread(self) -> dict | None:
        """The most recent thread anywhere in the document."""
        for body in self.bodies:
            found = self.threads_on(body)
            if found:
                return found[0]
        return None

    # -- bodies ---------------------------------------------------------
    def body(self, name: str) -> Body | None:
        return self.bodies.get(name)

    def visible_bodies(self) -> Iterator[Body]:
        return (b for b in self.bodies.values() if b.visible and b.shape is not None)

    # -- groups ---------------------------------------------------------
    def group(self, name: str) -> Group | None:
        return self.groups.get(name)

    def group_of(self, member: str) -> Group | None:
        """The group *member* belongs to directly, or None."""
        for group in self.groups.values():
            if member in group.members:
                return group
        return None

    def top_group_of(self, member: str) -> Group | None:
        """The outermost group *member* belongs to, or None.

        What a click in the viewport should select: picking a bolt inside a
        sub-assembly inside an assembly selects the assembly, because that is
        the thing the user was pointing at.
        """
        group = self.group_of(member)
        if group is None:
            return None
        seen = {group.name}
        while True:
            parent = self.group_of(group.name)
            if parent is None or parent.name in seen:
                return group
            seen.add(parent.name)
            group = parent

    def add_group(self, members, name: str | None = None) -> Group:
        """Group *members*, taking each out of whatever group it was in."""
        members = [m for m in dict.fromkeys(members) if m in self.bodies or m in self.groups]
        if not members:
            raise CadError(
                "There is nothing to group.",
                suggestion="Select two or more bodies first.",
            )
        for member in members:
            existing = self.group_of(member)
            if existing is not None:
                existing.members = [m for m in existing.members if m != member]
        group = Group(name=name or self.unique_name("Group"), members=members)
        self.groups[group.name] = group
        self._prune_groups()
        return group

    def ungroup(self, name: str) -> list[str]:
        """Dissolve a group, returning its members to where it sat.

        Members of a nested group are handed up to the parent rather than
        stranded at the top level, or ungrouping an inner group would silently
        pull its contents out of the outer one as well.
        """
        group = self.groups.pop(name, None)
        if group is None:
            return []
        parent = self.group_of(name)
        if parent is not None:
            index = parent.members.index(name)
            parent.members[index:index + 1] = group.members
        return list(group.members)

    def expand(self, names) -> list[str]:
        """Flatten *names* to body names, following nested groups."""
        out: list[str] = []
        seen: set[str] = set()

        def walk(name: str) -> None:
            if name in seen:
                return
            seen.add(name)
            group = self.groups.get(name)
            if group is None:
                if name in self.bodies and name not in out:
                    out.append(name)
                return
            for member in group.members:
                walk(member)

        for name in names:
            walk(name)
        return out

    def root_items(self) -> list[tuple[str, str]]:
        """The top level of the model tree: ``("group"|"body", name)``."""
        nested = {m for g in self.groups.values() for m in g.members}
        items = [
            ("group", name) for name in self.groups if name not in nested
        ]
        items += [
            ("body", name) for name in self.bodies if name not in nested
        ]
        return items

    def group_members(self, name: str) -> list[tuple[str, str]]:
        """One group's direct children, as ``("group"|"body", name)``."""
        group = self.groups.get(name)
        if group is None:
            return []
        return [
            ("group" if m in self.groups else "body", m)
            for m in group.members
            if m in self.groups or m in self.bodies
        ]

    def forget_member(self, name: str) -> None:
        """Take a deleted body or group out of whatever group holds it."""
        for group in self.groups.values():
            if name in group.members:
                group.members = [m for m in group.members if m != name]
        self._prune_groups()

    def _prune_groups(self) -> None:
        """Drop groups that no longer hold anything.

        An empty group is an empty row in the tree that cannot be selected and
        cannot be filled -- it is not a container the user still wants, it is
        the residue of deleting what was in it.
        """
        changed = True
        while changed:
            changed = False
            for name, group in list(self.groups.items()):
                group.members = [
                    m for m in group.members
                    if m in self.bodies or m in self.groups
                ]
                if not group.members:
                    del self.groups[name]
                    self.forget_member(name)
                    changed = True

    # -- serialisation --------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "parameters": self.parameters.to_list(),
            "features": [f.to_dict() for f in self.features],
            "bodies": [b.to_dict() for b in self.bodies.values()],
            "groups": [g.to_dict() for g in self.groups.values()],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Document":
        document = cls(data.get("title", "Untitled"))
        document.parameters = ParameterSet.from_list(data.get("parameters", []))
        document.metadata = data.get("metadata", {})
        for entry in data.get("features", []):
            document.features.append(Feature.from_dict(entry))
        for entry in data.get("bodies", []):
            body = Body(
                name=entry["name"],
                visible=entry.get("visible", True),
                color=entry.get("color"),
                producer=entry.get("producer", ""),
                metadata=entry.get("metadata", {}),
            )
            document.bodies[body.name] = body
        # Absent from files written before grouping existed, which load fine
        # with no groups at all.
        for entry in data.get("groups", []):
            group = Group.from_dict(entry)
            document.groups[group.name] = group
        return document
