"""Independent parametric fragments for model copy and paste.

The clipboard stores feature state rather than B-Rep bytes.  Pasting therefore
creates a new dependency subgraph with new feature/body identities: editing the
copy cannot mutate the original, while dimensions and downstream operations
remain parametric.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field, replace

from .document import BodyRef, Document, Feature, Group
from .naming import SubShapeRef


@dataclass
class ModelFragment:
    items: list[str]
    root_bodies: list[str]
    features: list[dict]
    groups: list[dict] = field(default_factory=list)
    bounds: tuple[tuple[float, float, float], tuple[float, float, float]] | None = None

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "items": list(self.items),
            "root_bodies": list(self.root_bodies),
            "features": copy.deepcopy(self.features),
            "groups": copy.deepcopy(self.groups),
            "bounds": self.bounds,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ModelFragment":
        if int(data.get("version", 0)) != 1:
            raise ValueError("Unsupported SimpleCAD clipboard version")
        bounds = data.get("bounds")
        return cls(
            items=list(data.get("items", [])),
            root_bodies=list(data.get("root_bodies", [])),
            features=copy.deepcopy(data.get("features", [])),
            groups=copy.deepcopy(data.get("groups", [])),
            bounds=(tuple(bounds[0]), tuple(bounds[1])) if bounds else None,
        )


def copy_fragment(document: Document, items: list[str], bounds=None) -> ModelFragment:
    """Capture the history closure needed to reproduce *items*."""
    items = list(dict.fromkeys(items))
    roots = document.expand(items)
    needed_bodies = set(roots)
    needed_features: set[str] = set()
    chosen: list[Feature] = []

    # Walk backwards because the last writer of a body tells us what earlier
    # bodies and references it needs. Keeping the body name in ``needed`` also
    # includes its complete same-name modifier chain.
    for feature in reversed(document.features):
        refs = feature.shape_refs()
        if (
            set(feature.outputs) & needed_bodies
            or feature.id in needed_features
        ):
            chosen.append(feature)
            needed_bodies.update(feature.body_inputs())
            needed_bodies.update(ref.body for ref in refs if ref.body)
            needed_features.update(ref.feature_id for ref in refs if ref.feature_id)
    chosen.reverse()

    group_names: set[str] = set()

    def take_group(name: str) -> None:
        group = document.groups.get(name)
        if group is None or name in group_names:
            return
        group_names.add(name)
        for member in group.members:
            take_group(member)

    for item in items:
        take_group(item)

    return ModelFragment(
        items=items,
        root_bodies=roots,
        features=[copy.deepcopy(feature.to_dict()) for feature in chosen],
        groups=[document.groups[name].to_dict() for name in group_names],
        bounds=bounds,
    )


def paste_fragment(
    document: Document,
    fragment: ModelFragment,
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> tuple[list[Feature], list[str]]:
    """Clone *fragment* into *document* and return features and root items."""
    source_features = [Feature.from_dict(entry) for entry in fragment.features]
    nonce = uuid.uuid4().hex[:8]
    taken = (
        {feature.name for feature in document.features}
        | set(document.bodies)
        | set(document.groups)
    )

    def unique(base: str) -> str:
        base = base or "Copy"
        candidate = base
        suffix = 2
        while candidate in taken:
            candidate = f"{base}{suffix}"
            suffix += 1
        taken.add(candidate)
        return candidate

    all_outputs = {name for feature in source_features for name in feature.outputs}
    all_inputs = {
        name for feature in source_features for name in feature.body_inputs()
    }
    all_ref_bodies = {
        ref.body for feature in source_features for ref in feature.shape_refs()
        if ref.body
    }
    body_names = all_outputs | all_inputs | all_ref_bodies
    roots = set(fragment.root_bodies)
    body_map: dict[str, str] = {}
    # Root order is user-visible and must be deterministic. Dependencies are
    # internal, so their set order does not matter.
    for name in fragment.root_bodies:
        if name in body_names and name not in body_map:
            body_map[name] = unique(name)
    for name in body_names - roots:
        body_map[name] = f"__copy_{nonce}_{name}"
    feature_map = {
        feature.id: f"f{uuid.uuid4().hex[:10]}" for feature in source_features
    }

    clones: list[Feature] = []
    for source in source_features:
        clone = copy.deepcopy(source)
        clone.id = feature_map[source.id]
        clone.outputs = [body_map.get(name, name) for name in source.outputs]
        # Primitive features conventionally share their display name with
        # their body (Box -> Box). Preserve that convention in the clone. If
        # we instead spent another unique suffix on the feature, the first
        # paste would create body Box2/feature Box3 and the next body would
        # unexpectedly jump to Box4.
        if source.name in source.outputs and source.name in body_map:
            clone.name = body_map[source.name]
        else:
            clone.name = unique(source.name or source.label)
        clone.dropped = [body_map.get(name, name) for name in source.dropped]
        for key, value in list(clone.inputs.items()):
            clone.inputs[key] = _rewrite_value(value, body_map, feature_map)
        document.add_feature(clone)
        clones.append(clone)

    # One shared translation preserves the assembly layout. Separate Move
    # features keep every root independently editable after the paste.
    from ..kernel.operations import MoveFeature

    dx, dy, dz = offset
    for old_name in fragment.root_bodies:
        new_name = body_map.get(old_name)
        if not new_name:
            continue
        move = MoveFeature(
            inputs={"body": BodyRef(new_name), "dx": dx, "dy": dy, "dz": dz},
            outputs=[new_name],
        )
        move.name = unique("Move")
        document.add_feature(move)
        clones.append(move)

    group_map = {
        entry["name"]: unique(entry["name"]) for entry in fragment.groups
    }
    for entry in fragment.groups:
        name = group_map[entry["name"]]
        members = [
            group_map.get(member, body_map.get(member, member))
            for member in entry.get("members", [])
        ]
        document.groups[name] = Group(
            name=name,
            members=members,
            visible=entry.get("visible", True),
            expanded=entry.get("expanded", True),
        )

    root_items = [group_map.get(item, body_map.get(item, item)) for item in fragment.items]
    return clones, root_items


def _rewrite_value(value, body_map: dict[str, str], feature_map: dict[str, str]):
    if isinstance(value, BodyRef):
        return BodyRef(body_map.get(str(value), str(value)))
    if isinstance(value, SubShapeRef):
        return replace(
            value,
            body=body_map.get(value.body, value.body),
            feature_id=feature_map.get(value.feature_id, value.feature_id),
        )
    if isinstance(value, list):
        return [_rewrite_value(item, body_map, feature_map) for item in value]
    if isinstance(value, tuple):
        return tuple(_rewrite_value(item, body_map, feature_map) for item in value)
    return value
