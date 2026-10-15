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

import copy
import hashlib
from contextlib import contextmanager
import uuid
import weakref
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator

from .errors import CadError, RebuildError
from .naming import SubShapeRef
from .params import ParameterSet
from .units import Dimension
from .observable import RecipeDict, RecipeList
from .graph import ChangeSet, DependencyGraph, OutputRef, output_id

#: type_name -> Feature subclass, populated by @register.
REGISTRY: dict[str, type["Feature"]] = {}

#: Inputs holding a feature's own output names as plain strings (Split's two
#: halves, a clip joint's connectors). Execution re-derives ``outputs`` from
#: them, so anything that renames an output must rename these too.
OUTPUT_NAME_INPUTS = ("names", "connector_names")


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
    """A display alias carrying an optional permanent output identity.

    String compatibility is restricted to UI/kernel aliases. The dependency
    graph binds every operand to an OutputRef before any geometry is evaluated.
    """

    def __new__(cls, name, object_id="", feature_id=""):
        value = super().__new__(cls, name)
        value.object_id = object_id or getattr(name, "object_id", "")
        value.feature_id = feature_id or getattr(name, "feature_id", "")
        return value

    def __getnewargs__(self):
        return str(self), self.object_id, self.feature_id


@dataclass
class Body:
    """A solid in the document. Named output slot of its producing feature."""

    name: str
    shape: Any = None
    visible: bool = True
    color: str | None = None
    producer: str = ""
    metadata: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "visible": self.visible,
            "color": self.color,
            "producer": self.producer,
            "metadata": copy.deepcopy(self.metadata),
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
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "id": self.id,
            "members": list(self.members),
            "visible": self.visible,
            "expanded": self.expanded,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Group":
        return cls(
            name=data["name"],
            id=data.get("id") or output_id("group", data["name"]),
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
        self.feature_id = ""

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
        return self.named_shape(str(name))

    def named_shape(self, name: str):
        """Fetch a body's shape by name, for inputs that hold several."""
        shape = self.bodies.get(name)
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

    def __setattr__(self, key, value):
        tracked = key in {"name", "inputs", "outputs", "suppressed", "dropped", "bindings", "output_ids"}
        if key in {"inputs", "bindings", "output_ids"}:
            value = RecipeDict(value, self._touch)
        elif key in {"outputs", "dropped"}:
            value = RecipeList(value, self._touch)
        changed = tracked and getattr(self, key, None) != value
        object.__setattr__(self, key, value)
        if changed:
            self._touch()

    def _touch(self):
        object.__setattr__(self, "_version", getattr(self, "_version", 0) + 1)
        owner = getattr(self, "_owner", None)
        document = owner() if owner else None
        if document is not None:
            document._feature_changed(self)

    def dependency_key(self):
        from .graph import input_names
        return (tuple(self.outputs), tuple(input_names(self)),
                tuple((r.body, r.feature_id) for r in self.shape_refs()),
                tuple(self.bindings.items()), tuple(self.output_ids.items()),
                tuple((str(v), v.object_id, v.feature_id) for v in self.reference_values()),
                tuple(sorted(self.parameter_names())))

    def __init__(
        self,
        feature_id: str | None = None,
        name: str = "",
        inputs: dict | None = None,
        outputs: list[str] | None = None,
    ) -> None:
        self.id = feature_id or f"f{uuid.uuid4().hex}"
        self.name = name
        self.inputs: dict[str, Any] = dict(inputs or {})
        self.outputs: list[str] = list(outputs or [])
        self.output_ids: dict[str, str] = {}
        self.bindings: dict[str, OutputRef] = {}
        self.suppressed = False
        self.state = FeatureState.OK
        self.message = ""
        #: Output names the user has deleted. See :meth:`keep`.
        self.dropped: list[str] = []

    # -- graph ----------------------------------------------------------
    def reference_values(self):
        def walk(value):
            if isinstance(value, BodyRef):
                yield value
            elif isinstance(value, dict):
                for child in value.values():
                    yield from walk(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    yield from walk(child)
        yield from walk(self.inputs)

    def body_inputs(self) -> list[str]:
        """Names of bodies this feature reads."""
        return [str(value) for value in self.reference_values()]

    def shape_refs(self) -> list[SubShapeRef]:
        """Sub-shape references this feature holds, including nested settings."""
        def walk(value):
            if isinstance(value, SubShapeRef):
                yield value
            elif isinstance(value, dict):
                for child in value.values():
                    yield from walk(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    yield from walk(child)
        return list(walk(self.inputs))

    def rename_output_inputs(self, mapping: dict[str, str]) -> None:
        """Apply an output rename to the name lists in :data:`OUTPUT_NAME_INPUTS`."""
        for key in OUTPUT_NAME_INPUTS:
            if key in self.inputs:
                self.inputs[key] = [mapping.get(str(n), str(n)) for n in self.inputs[key]]

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
            "output_ids": dict(self.output_ids),
            "bindings": {name: ref.to_dict() for name, ref in self.bindings.items()},
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
        feature.output_ids = dict(data.get("output_ids", {}))
        feature.bindings = {n: OutputRef.from_dict(r) for n, r in data.get("bindings", {}).items()}
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
            if value.object_id:
                out[key].update(object_id=value.object_id, feature_id=value.feature_id)
        elif isinstance(value, (list, tuple)):
            out[key] = [_encode_inputs({"v": v})["v"] for v in value]
        elif isinstance(value, dict):
            out[key] = _encode_inputs(value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _decode_inputs(data: dict) -> dict:
    def decode(value):
        if isinstance(value, dict) and "__ref__" in value:
            return SubShapeRef.from_dict(value["__ref__"])
        if isinstance(value, dict) and "__body__" in value:
            return BodyRef(value["__body__"], value.get("object_id", ""), value.get("feature_id", ""))
        if isinstance(value, list):
            return [decode(v) for v in value]
        if isinstance(value, dict):
            return {k: decode(v) for k, v in value.items()}
        return copy.deepcopy(value)

    return {key: decode(value) for key, value in data.items()}


def _literal_mm(value, dependencies, evaluate):
    """A plain length in mm, or None when *value* is an expression of names."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    if dependencies(text):
        return None
    try:
        return float(evaluate(text))
    except Exception:  # noqa: BLE001 - anything unreadable is not folded
        return None


def _format_mm(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return f"{'0' if text in ('-0', '') else text} mm"


class Document:
    """Bodies, features and parameters, plus the dependency graph over them."""

    @property
    def features(self):
        return self._features

    @features.setter
    def features(self, value):
        self._features = RecipeList(value, self._features_changed)
        if hasattr(self, "_pending_changes"):
            self._features_changed()

    def _features_changed(self):
        old = self._graph_index
        current = {f.id: f for f in self.features}
        previous = old.features if old is not None else {}
        changed = {fid for fid in current.keys() | previous.keys()
                   if current.get(fid) is not previous.get(fid)}
        self._signature_dirty.update(old.dependents(changed) if old else changed)
        for feature in self.features:
            feature._owner = weakref.ref(self)
        self._graph_dirty = True
        self.revision += 1

    def _feature_changed(self, feature):
        self._signature_dirty.add(feature.id)
        self._pending_changes.add(feature.id)
        key = feature.dependency_key()
        if key != getattr(feature, "_dependency_key", None):
            self._graph_dirty = True
            if self._graph_index is not None:
                self._signature_dirty.update(self._graph_index.dependents({feature.id}))
        feature._dependency_key = key
        self.revision += 1

    def __init__(self, title: str = "Untitled") -> None:
        self.title = title
        self.parameters = ParameterSet()
        self.features: list[Feature] = []
        self.bodies: dict[str, Body] = {}
        self.groups: dict[str, Group] = {}
        self.metadata: dict = {}
        self._counters: dict[str, int] = {}
        self._graph_index = None
        self._graph_key = None
        self._graph_dirty = True
        self._signature_dirty = set()
        self._signature_cache = {}
        self.revision = 0
        self._pending_changes = set()
        self._retired_ids = set()
        self._history_data = {}
        self._geometry_store = None
        self._rebuilder = None
        # Imported source geometry is part of the recipe, not an evictable
        # result cache. Keep retired blobs for redo until history is saved.
        self.assets: dict[str, bytes] = {}
        self._all_assets: dict[str, bytes] = {}

    def add_asset(self, blob: bytes) -> str:
        key = hashlib.sha256(blob).hexdigest()
        self.assets[key] = blob
        self._all_assets[key] = blob
        self.revision += 1
        return key

    def active_asset_keys(self) -> set[str]:
        """Embedded geometry still referenced by the current feature recipe."""
        return {str(feature.inputs["asset"]) for feature in self.features
                if feature.type_name == "embedded_import" and feature.inputs.get("asset")}

    # -- features -------------------------------------------------------
    def add_feature(self, feature: Feature, index: int | None = None) -> Feature:
        if feature.id in self._retired_ids or any(f.id == feature.id for f in self.features):
            raise CadError("A feature with this ID already exists.")
        self.prepare_graph()
        if not feature.name:
            feature.name = self.unique_name(feature.label)
        if index is None:
            self.features.append(feature)
        else:
            self.features.insert(index, feature)
        self.prepare_graph()
        self._pending_changes.add(feature.id)
        self.revision += 1
        return feature

    def fold_translation(self, names, delta) -> Feature | None:
        """Add *delta* to the bodies' latest Move instead of recording another.

        Every committed move used to append a feature, so a long session's
        history was mostly moves and every per-feature cost grew with them.
        A move is folded only when doing so cannot change anything else: the
        bodies' newest producer is one translation-only ``move``/``move_many``
        over exactly these bodies, its offsets are plain numbers, and nothing
        reads its result. Returns the updated feature, or None when a new one
        must be added. Each call is still one undo step for the caller.
        """
        from .params import dependencies, evaluate

        names = [str(n) for n in names]
        if not names or len(delta) != 3:
            return None
        graph = self.prepare_graph()
        heads = {graph.latest[n].feature_id for n in names if n in graph.latest}
        if len(heads) != 1 or len(names) != len({n for n in names if n in graph.latest}):
            return None
        feature = graph.features.get(next(iter(heads)))
        if (feature is None or feature.suppressed or feature.state is not FeatureState.OK
                or graph.reverse.get(feature.id) or feature.dropped):
            return None
        if feature.type_name == "move":
            if [str(feature.inputs.get("body", ""))] != names or feature.inputs.get("pivot"):
                return None
            for key in ("rx", "ry", "rz"):
                if key in feature.inputs and _literal_mm(feature.inputs[key], dependencies, evaluate) != 0.0:
                    return None
        elif feature.type_name == "move_many":
            if sorted(str(b) for b in feature.inputs.get("bodies", [])) != sorted(names):
                return None
        else:
            return None
        current = [_literal_mm(feature.inputs.get(k, 0.0), dependencies, evaluate)
                   for k in ("dx", "dy", "dz")]
        if any(value is None for value in current):
            return None
        for key, old, step in zip(("dx", "dy", "dz"), current, delta):
            total = round(old + float(step), 9)
            if abs(total - old) > 1e-12:
                # Keep the form the move already used: nudges store numbers,
                # the Move panel stores expressions with units.
                raw = feature.inputs.get(key, 0.0)
                numeric = isinstance(raw, (int, float)) and not isinstance(raw, bool)
                feature.inputs[key] = total if numeric else _format_mm(total)
        self.revision += 1
        return feature

    def remove_feature(self, feature_id: str, *, splice: bool = True) -> ChangeSet:
        graph = self.prepare_graph()
        feature = graph.features.get(feature_id)
        if feature is None:
            return ChangeSet()
        affected = graph.dependents({feature_id})
        # Removing a modifier is an explicit graph edit. Removing an object
        # instead leaves dangling IDs, which can never bind to a later namesake.
        if splice:
            for child in self.features:
                for alias, ref in list(child.bindings.items()):
                    if ref.feature_id != feature_id:
                        continue
                    source = next((r for r in feature.bindings.values()
                                   if r.object_id == ref.object_id), None)
                    if source is not None:
                        child.bindings[alias] = source
                        for shape_ref in child.shape_refs():
                            if shape_ref.feature_id == feature_id and shape_ref.body == alias:
                                object.__setattr__(shape_ref, "feature_id", source.feature_id)
        self._retired_ids.add(feature_id)
        self.features = [f for f in self.features if f.id != feature_id]
        for name, oid in feature.output_ids.items():
            if not any(oid in f.output_ids.values() for f in self.features):
                body = self.bodies.get(name)
                if body is not None and body.id == oid:
                    self.bodies.pop(name, None)
        self._pending_changes.update(affected)
        self.revision += 1
        self.prepare_graph()
        return ChangeSet(removed={feature_id}, affected=affected, revision=self.revision)

    def ref(self, name: str) -> BodyRef:
        graph = self.prepare_graph()
        ref = graph.latest.get(name)
        if ref is None:
            raise CadError(f"The body '{name}' is no longer available.")
        return BodyRef(name, ref.object_id, ref.feature_id)

    def rename_body(self, old, new):
        if new in self.bodies and new != old:
            raise CadError(f"There is already a body called '{new}'.")
        self.prepare_graph()
        from dataclasses import replace
        def rename(value):
            if isinstance(value, BodyRef) and str(value) == old:
                return BodyRef(new, value.object_id, value.feature_id)
            if isinstance(value, SubShapeRef) and value.body == old:
                return replace(value, body=new)
            if isinstance(value, list):
                return [rename(v) for v in value]
            if isinstance(value, tuple):
                return tuple(rename(v) for v in value)
            if isinstance(value, dict):
                return {k: rename(v) for k, v in value.items()}
            return value
        for feature in self.features:
            feature.outputs = [new if n == old else n for n in feature.outputs]
            feature.dropped = [new if n == old else n for n in feature.dropped]
            feature.inputs = rename(feature.inputs)
            feature.rename_output_inputs({old: new})
            if old in feature.output_ids:
                feature.output_ids[new] = feature.output_ids.pop(old)
            if old in feature.bindings:
                feature.bindings[new] = feature.bindings.pop(old)
        body = self.bodies.pop(old, None)
        if body:
            body.name = new
            self.bodies[new] = body
        for group in self.groups.values():
            group.members = [new if n == old else n for n in group.members]
        self.prepare_graph()
        self.revision += 1

    @contextmanager
    def transaction(self, label: str):
        """One atomic, reversible edit; exceptions restore the original state."""
        from .history import History
        history = getattr(self, "_history", None) or History(self)
        before = self.to_dict()
        try:
            yield self
            self.prepare_graph()
        except BaseException:
            from .history import Snapshot
            history._apply(Snapshot(label, before))
            raise
        if self.to_dict() != before:
            history.record_data(label, before)
            self.revision += 1

    def feature(self, feature_id: str) -> Feature | None:
        return next((f for f in self.features if f.id == feature_id), None)

    def feature_by_name(self, name: str) -> Feature | None:
        return next((f for f in self.features if f.name == name), None)

    def index_of(self, feature_id: str) -> int:
        return next(
            (i for i, f in enumerate(self.features) if f.id == feature_id), -1
        )

    def unique_name(self, base: str) -> str:
        """Allocate a display name without recycling a retired suffix."""
        taken = {f.name for f in self.features} | set(self.bodies) | set(self.groups)
        suffix = self._counters.get(base, 1)
        while True:
            candidate = base if suffix == 1 else f"{base}{suffix}"
            suffix += 1
            if candidate not in taken:
                self._counters[base] = suffix
                return candidate

    def rename_feature(self, feature_id: str, new_name: str) -> None:
        feature = self.feature(feature_id)
        if feature is None:
            raise KeyError(feature_id)
        if any(f.name == new_name and f.id != feature_id for f in self.features):
            raise CadError(f"There is already a feature called '{new_name}'.")
        feature.name = new_name

    # -- graph ----------------------------------------------------------
    def prepare_graph(self) -> DependencyGraph:
        if self._graph_index is None or self._graph_dirty:
            self._graph_index = DependencyGraph(self)
            for feature in self.features:
                feature._owner = weakref.ref(self)
                feature._dependency_key = feature.dependency_key()
            self._graph_dirty = False
        return self._graph_index

    def producer_of(self, body_name: str) -> Feature | None:
        graph = self.prepare_graph()
        ref = graph.latest.get(body_name)
        return graph.features.get(ref.feature_id) if ref else None

    def dependencies_of(self, feature: Feature) -> set[str]:
        return set(self.prepare_graph().forward.get(feature.id, ()))

    def dependents_of(self, feature_ids: set[str]) -> set[str]:
        return self.prepare_graph().dependents(feature_ids)

    def features_using_parameter(self, name: str) -> set[str]:
        # Expressions may have changed without changing geometry bindings.
        graph = self.prepare_graph()
        graph.parameter_users.clear()
        for feature in self.features:
            for parameter in feature.parameter_names():
                graph.parameter_users[parameter].add(feature.id)
        return graph.parameter_dependents(name, self.parameters)

    def topological_order(self) -> list[Feature]:
        return list(self.prepare_graph().order)

    def ordering_is_valid(self, features: list[Feature]) -> bool:
        graph = self.prepare_graph()
        seen = set()
        for feature in features:
            if not graph.forward[feature.id] <= seen:
                return False
            if any(not ref.feature_id for ref in feature.bindings.values()):
                return False
            seen.add(feature.id)
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
            record = self._thread_record(feature, body_name)
            if record is not None:
                found.append(record)
        return found

    _THREAD_TYPES = frozenset((
        "thread", "threaded_connection", "hole", "matching_thread",
        "matching_bolt", "matching_nut",
    ))

    def _thread_record(self, feature, body_name: str) -> dict | None:
        if feature.type_name not in self._THREAD_TYPES:
            return None
        if feature.state is not FeatureState.OK or feature.suppressed:
            return None
        designation = feature.inputs.get("designation")
        if not designation or body_name not in feature.outputs:
            return None
        suffix = None
        if feature.type_name == "threaded_connection":
            if str(feature.inputs.get("body_a", "")) == body_name:
                suffix = "a"
            elif str(feature.inputs.get("body_b", "")) == body_name:
                suffix = "b"
        modelled_key = (
            f"thread_modelled_{suffix}" if suffix else "thread_modelled"
        )
        # Old files predate the flag and contained modelled geometry, so an
        # absent value remains compatible. A new failed/cosmetic feature is
        # explicitly False and must never be offered as a matching source.
        modelled = feature.inputs.get(modelled_key, True)
        if modelled is False:
            return None
        if suffix:
            internal = feature.inputs.get(f"thread_internal_{suffix}")
        elif feature.type_name in ("hole", "matching_nut"):
            internal = True
        elif feature.type_name == "matching_bolt":
            internal = False
        else:
            internal = feature.inputs.get("thread_internal")
        return {
            "body": body_name,
            "feature_id": feature.id,
            "designation": str(designation),
            "feature": feature.name,
            "clearance": feature.inputs.get("clearance", "normal"),
            "type": feature.type_name,
            "internal": internal,
            "form": feature.inputs.get("form", "printed"),
            "left_hand": bool(feature.inputs.get("left_hand", False)),
            "modelled": bool(modelled),
        }

    def any_thread(self) -> dict | None:
        """The most recent thread anywhere in the document.

        One newest-first pass over the history. Asking every body for its
        threads and then locating each in the feature list was run on every
        face selection, at bodies x features.
        """
        for feature in reversed(self.features):
            if feature.type_name not in self._THREAD_TYPES:
                continue
            for body in self.bodies:
                record = self._thread_record(feature, body)
                if record is not None:
                    return record
        return None

    # -- bodies ---------------------------------------------------------
    def body(self, name: str) -> Body | None:
        body = self.bodies.get(name)
        if body is not None and getattr(name, "object_id", body.id) not in ("", body.id):
            return None
        return body

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

    def export_items(self) -> list[tuple[str, list]]:
        """What a file should contain: ``(name, shapes)``, one entry per object.

        The one place the difference between "a group" and "two bodies" is
        carried out of the document and into a file. A slicer has no notion of a
        SimpleCAD group, so a group that reaches it as two objects is two things
        to place, orient and print -- which is precisely not what grouping them
        said. Every visible member of a group therefore comes back under a single
        name, and the writers turn each entry into exactly one object.

        Nothing is fused: the members stay separate solids in the same object,
        which is what "these belong together" means and what a boolean would
        destroy.
        """
        items: list[tuple[str, list]] = []
        for kind, name in self.root_items():
            names = self.expand([name]) if kind == "group" else [name]
            shapes = [
                body.shape
                for body in (self.bodies.get(n) for n in names)
                if body is not None and body.visible and body.shape is not None
            ]
            if shapes:
                items.append((name, shapes))
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
        self.prepare_graph()
        return {
            "title": self.title,
            "counters": dict(self._counters),
            "retired_ids": sorted(self._retired_ids),
            "parameters": self.parameters.to_list(),
            "features": [f.to_dict() for f in self.features],
            "bodies": [b.to_dict() for b in self.bodies.values()],
            "groups": [{**g.to_dict(), "member_ids": [
                self.groups[n].id if n in self.groups else self.bodies[n].id
                for n in g.members if n in self.groups or n in self.bodies
            ]} for g in self.groups.values()],
            "metadata": copy.deepcopy(self.metadata),
            "assets": sorted(self.active_asset_keys()),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Document":
        document = cls(data.get("title", "Untitled"))
        document.parameters = ParameterSet.from_list(data.get("parameters", []))
        document.metadata = copy.deepcopy(data.get("metadata", {}))
        document._asset_keys = set(data.get("assets", []))
        document._counters = dict(data.get("counters", {}))
        document._retired_ids = set(data.get("retired_ids", []))
        # Loading is one structural change. Appending through the observable
        # list repeatedly scans a growing prefix and makes large opens quadratic.
        document.features = [Feature.from_dict(entry) for entry in data.get("features", [])]
        for entry in data.get("bodies", []):
            body = Body(
                name=entry["name"],
                id=entry.get("id") or output_id(entry.get("producer", "legacy"), entry["name"]),
                visible=entry.get("visible", True),
                color=entry.get("color"),
                producer=entry.get("producer", ""),
                metadata=copy.deepcopy(entry.get("metadata", {})),
            )
            document.bodies[body.name] = body
        # Absent from files written before grouping existed, which load fine
        # with no groups at all.
        for entry in data.get("groups", []):
            group = Group.from_dict(entry)
            document.groups[group.name] = group
        names_by_id = {b.id: b.name for b in document.bodies.values()}
        names_by_id.update({g.id: g.name for g in document.groups.values()})
        for entry in data.get("groups", []):
            if "member_ids" in entry:
                document.groups[entry["name"]].members = [names_by_id[i] for i in entry["member_ids"] if i in names_by_id]
        graph = document.prepare_graph()
        for name, body in document.bodies.items():
            ref = graph.latest.get(name)
            if ref is not None:
                body.id = ref.object_id
                body.producer = ref.feature_id
        return document
