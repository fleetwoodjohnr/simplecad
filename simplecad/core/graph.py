"""Persistent output references and indexed feature dependencies.

Names are aliases at the kernel/UI boundary, never the identity of an operand.
An output is identified by its producing feature and the object it writes.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
import uuid

from .errors import CadError


@dataclass(frozen=True)
class OutputRef:
    feature_id: str
    object_id: str

    def to_dict(self):
        return {"feature_id": self.feature_id, "object_id": self.object_id}

    @classmethod
    def from_dict(cls, data):
        return cls(data.get("feature_id", ""), data["object_id"])


@dataclass
class ChangeSet:
    changed: set[str] = field(default_factory=set)
    removed: set[str] = field(default_factory=set)
    affected: set[str] = field(default_factory=set)
    revision: int = 0


def output_id(feature_id, name):
    # Deterministic only for initial allocation/migration. Thereafter the ID is
    # stored, including across rename, suppression, deletion and history restore.
    return uuid.uuid5(uuid.NAMESPACE_URL, f"simplecad:output:{feature_id}:{name}").hex


def input_names(feature):
    names = list(feature.body_inputs())
    names.extend(ref.body for ref in feature.shape_refs() if ref.body)
    # Older sketch features use plain strings. Keep their UI representation,
    # but give these internal results exactly the same graph semantics.
    keys = {
        "extrude": ("sketch",), "revolve": ("sketch",),
        "sweep": ("profile", "path"), "loft": ("sketches",),
        "path_pattern": ("path",),
    }.get(feature.type_name, ())
    for key in keys:
        values = feature.inputs.get(key, [])
        for value in values if isinstance(values, (tuple, list)) else [values]:
            if value:
                names.append(f"__sketch__{value}")
    return list(dict.fromkeys(names))


def declared_outputs(feature):
    if feature.outputs:
        return feature.outputs
    kind = feature.type_name
    if kind == "sketch":
        return [f"__sketch__{feature.name}"]
    if kind in {"offset_plane", "midplane", "plane_at_angle", "plane_through_points",
                "tangent_plane", "construction_axis", "construction_point"}:
        return [f"__construction__{feature.name}"]
    if kind == "split":
        source = str(feature.inputs.get("body", ""))
        return list(feature.inputs.get("names") or [f"{source} A", f"{source} B"])
    if kind in {"threaded_connection", "clip_joint"}:
        return [str(feature.inputs[k]) for k in ("body_a", "body_b") if k in feature.inputs] + list(feature.inputs.get("connector_names", []))
    if kind in {"arrange", "move_many"}:
        return [str(n) for n in feature.inputs.get("bodies", [])]
    if kind == "section_replace":
        return [str(feature.inputs.get("target", ""))]
    # Matching generators read a reference body but produce a separate object.
    if kind not in {"matching_bolt", "matching_nut"} and "body" in feature.inputs:
        return [str(feature.inputs["body"])]
    return [feature.name] if feature.name else []


class DependencyGraph:
    def __init__(self, document):
        self.features = {f.id: f for f in document.features}
        if len(self.features) != len(document.features):
            raise CadError("This project contains duplicate feature IDs.")
        self.forward = {}
        self.reverse = defaultdict(set)
        self.heads = {}
        self.names = {}
        self.writers = defaultdict(list)
        self.parameter_users = defaultdict(set)
        self.positions = {f.id: i for i, f in enumerate(document.features)}
        latest = {}
        for feature in document.features:
            if not feature.outputs:
                feature.outputs = list(declared_outputs(feature))
            needed = input_names(feature)
            # Remove obsolete aliases, but never reinterpret an existing one.
            feature.bindings = {n: ref for n, ref in feature.bindings.items() if n in needed}
            explicit_refs = {}
            for value in feature.reference_values():
                if getattr(value, "object_id", ""):
                    explicit_refs.setdefault(str(value), value)
            for name in needed:
                explicit = explicit_refs.get(name)
                if explicit is not None and (name not in feature.bindings
                        or feature.bindings[name].object_id != explicit.object_id):
                    feature.bindings[name] = OutputRef(explicit.feature_id, explicit.object_id)
                if name not in feature.bindings:
                    feature.bindings[name] = latest.get(name, OutputRef("", output_id(feature.id, "missing:" + name)))
            for name in feature.outputs:
                if name not in feature.output_ids:
                    source = feature.bindings.get(name)
                    feature.output_ids[name] = source.object_id if source else output_id(feature.id, name)
                oid = feature.output_ids[name]
                ref = OutputRef(feature.id, oid)
                latest[name] = ref
                self.heads[oid] = ref
                self.names[oid] = name
                self.writers[oid].append(feature.id)
            deps = {r.feature_id for r in feature.bindings.values() if r.feature_id}
            # A face reference can originate further upstream than its body's
            # immediate input. Preserve that anchor as well.
            deps.update(r.feature_id for r in feature.shape_refs() if r.feature_id and r.feature_id != feature.id)
            deps.discard(feature.id)
            self.forward[feature.id] = deps
            for dep in deps:
                self.reverse[dep].add(feature.id)
            for name in feature.parameter_names():
                self.parameter_users[name].add(feature.id)
        self.latest = latest
        self.order = self._order()
        self.ranks = {f.id: i for i, f in enumerate(self.order)}

    def _order(self):
        indegree = {fid: len(deps & self.features.keys()) for fid, deps in self.forward.items()}
        ready = deque(fid for fid in self.features if not indegree[fid])
        order = []
        while ready:
            fid = ready.popleft()
            order.append(self.features[fid])
            for child in sorted(self.reverse[fid], key=self.positions.get):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
        if len(order) != len(self.features):
            raise CadError("The project contains a circular feature dependency.",
                           suggestion="Remove or repair the circular references.")
        return order

    def dependents(self, ids):
        found = set(ids)
        queue = deque(found)
        while queue:
            for child in self.reverse.get(queue.popleft(), ()):
                if child not in found:
                    found.add(child)
                    queue.append(child)
        return found

    def parameter_dependents(self, name, parameters):
        from .params import dependencies
        reverse = defaultdict(set)
        for parameter in parameters:
            for source in dependencies(parameter.expression):
                reverse[source].add(parameter.name)
        names, queue = {name}, deque([name])
        while queue:
            for child in reverse[queue.popleft()]:
                if child not in names:
                    names.add(child)
                    queue.append(child)
        return set().union(*(self.parameter_users[n] for n in names))
