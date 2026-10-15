"""Independent parametric fragments for model copy and paste.

The clipboard stores editable feature history, frozen parameter definitions,
and a B-Rep checkpoint of the copy-time result. Paste remaps identities and
references; the checkpoint avoids replaying unchanged construction history.
"""

from __future__ import annotations

import copy
import base64
import re
import uuid
from dataclasses import dataclass, field, replace

from .document import Body, BodyRef, Document, Feature, Group
from .naming import SubShapeRef


@dataclass
class ModelFragment:
    items: list[str]
    root_bodies: list[str]
    features: list[dict]
    groups: list[dict] = field(default_factory=list)
    bounds: tuple[tuple[float, float, float], tuple[float, float, float]] | None = None
    parameters: list[dict] = field(default_factory=list)
    shapes: dict[str, str] = field(default_factory=dict)
    appearance: dict[str, dict] = field(default_factory=dict)
    #: Embedded import sources (sha256 -> base64 B-Rep) the history needs, so
    #: a copy pasted into another project brings its imported meshes along.
    assets: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "version": 2,
            "items": list(self.items),
            "root_bodies": list(self.root_bodies),
            "features": copy.deepcopy(self.features),
            "groups": copy.deepcopy(self.groups),
            "bounds": self.bounds,
            "parameters": copy.deepcopy(self.parameters),
            "shapes": dict(self.shapes),
            "appearance": copy.deepcopy(self.appearance),
            "assets": dict(self.assets),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ModelFragment":
        if int(data.get("version", 0)) not in (1, 2):
            raise ValueError("Unsupported SimpleCAD clipboard version")
        bounds = data.get("bounds")
        return cls(
            items=list(data.get("items", [])),
            root_bodies=list(data.get("root_bodies", [])),
            features=copy.deepcopy(data.get("features", [])),
            groups=copy.deepcopy(data.get("groups", [])),
            bounds=(tuple(bounds[0]), tuple(bounds[1])) if bounds else None,
            parameters=copy.deepcopy(data.get("parameters", [])),
            shapes=dict(data.get("shapes", {})),
            appearance=copy.deepcopy(data.get("appearance", {})),
            assets=dict(data.get("assets", {})),
        )


def copy_fragment(document: Document, items: list[str], bounds=None, *, capture_geometry=True) -> ModelFragment:
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

    from .params import dependencies
    from .geometry_service import serialise_shape

    parameters = {p["name"]: p for p in document.parameters.to_list()}
    needed_parameters = set().union(*(f.parameter_names() for f in chosen)) & parameters.keys()
    pending = list(needed_parameters)
    while pending:
        name = pending.pop()
        for dependency in dependencies(parameters[name]["expression"]) & parameters.keys():
            if dependency not in needed_parameters:
                needed_parameters.add(dependency)
                pending.append(dependency)

    return ModelFragment(
        items=items,
        root_bodies=roots,
        features=[copy.deepcopy(feature.to_dict()) for feature in chosen],
        groups=[document.groups[name].to_dict() for name in group_names],
        bounds=bounds,
        parameters=[copy.deepcopy(p) for name, p in parameters.items() if name in needed_parameters],
        shapes={
            name: base64.b64encode(serialise_shape(document.body(name).shape)).decode("ascii")
            for name in roots if capture_geometry and document.body(name).shape is not None
        },
        assets={
            sha: base64.b64encode(document._all_assets[sha]).decode("ascii")
            for sha in {str(f.inputs.get("asset")) for f in chosen
                        if f.type_name == "embedded_import"}
            if sha in document._all_assets
        },
        appearance={name: copy.deepcopy(document.body(name).to_dict()) for name in roots},
    )


def paste_fragment(
    document: Document,
    fragment: ModelFragment,
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0),
    *,
    keep_parameter_names: bool = False,
) -> tuple[list[Feature], list[str]]:
    """Clone *fragment* into *document* and return features and root items.

    With *keep_parameter_names* -- importing from another project -- a named
    parameter keeps its name unless this project already uses it. A paste
    within one project always renames, so the copy can diverge.
    """
    source_features = [Feature.from_dict(entry) for entry in fragment.features]
    from .geometry_service import deserialise_shape
    from .params import Parameter, dependencies

    # Validate all encoded geometry before adding any live features or parameters.
    for name, blob in fragment.shapes.items():
        if name not in fragment.root_bodies or deserialise_shape(base64.b64decode(blob, validate=True)) is None:
            raise ValueError("The copied geometry is invalid.")
    if fragment.shapes and set(fragment.shapes) != set(fragment.root_bodies):
        raise ValueError("The copied geometry is incomplete.")
    for sha, encoded in fragment.assets.items():
        if sha not in document._all_assets:
            document.add_asset(base64.b64decode(encoded, validate=True))
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
    from .graph import input_names
    needed = roots.union(*(input_names(feature) for feature in source_features))
    body_map: dict[str, str] = {}
    # Root order is user-visible and must be deterministic. Dependencies are
    # internal, so their set order does not matter.
    for name in fragment.root_bodies:
        if name in body_names and name not in body_map:
            body_map[name] = unique(name)
    for name in body_names - roots:
        body_map[name] = f"__copy_{nonce}_{name}"
    feature_map = {
        feature.id: f"f{uuid.uuid4().hex}" for feature in source_features
    }

    object_map = {oid: uuid.uuid4().hex for f in source_features for oid in f.output_ids.values()}
    from .graph import OutputRef

    parameter_names = {p["name"] for p in document.parameters.to_list()}
    parameter_map = {}
    for entry in fragment.parameters:
        name = entry["name"]
        if keep_parameter_names and name not in parameter_names:
            parameter_names.add(name)
            parameter_map[name] = name
            continue
        candidate, suffix = name + "_copy", 2
        while candidate in parameter_names:
            candidate = name + "_copy" + str(suffix)
            suffix += 1
        parameter_names.add(candidate)
        parameter_map[name] = candidate

    def expression(value):
        if not isinstance(value, str) or isinstance(value, BodyRef):
            return value
        if not dependencies(value) & parameter_map.keys():
            return value
        return re.sub(r"\b[^\W\d]\w*\b", lambda m: parameter_map.get(m[0], m[0]), value)

    new_parameters = [Parameter.from_dict({
        **entry, "name": parameter_map[entry["name"]],
        "expression": expression(entry["expression"]),
    }) for entry in fragment.parameters]

    clones: list[Feature] = []
    for source in source_features:
        clone = Feature.from_dict(source.to_dict())
        clone.id = feature_map[source.id]
        clone.outputs = [body_map.get(name, name) for name in source.outputs]
        clone.output_ids = {body_map.get(name, name): object_map[oid] for name, oid in source.output_ids.items()}
        clone.bindings = {body_map.get(name, name): OutputRef(feature_map.get(ref.feature_id, ref.feature_id),
                                                          object_map.get(ref.object_id, ref.object_id))
                          for name, ref in source.bindings.items()}
        # Primitive features conventionally share their display name with
        # their body (Box -> Box). Preserve that convention in the clone. If
        # we instead spent another unique suffix on the feature, the first
        # paste would create body Box2/feature Box3 and the next body would
        # unexpectedly jump to Box4.
        if source.name in source.outputs and source.name in body_map:
            clone.name = body_map[source.name]
        else:
            clone.name = unique(source.name or source.label)
        # A multi-output feature (Split, a joint) brings every output along,
        # but a sibling nothing in the fragment reads would only linger as a
        # hidden body. Drop it the way deleting one half does.
        unused = [name for name in source.outputs
                  if len(source.outputs) > 1 and name not in needed]
        clone.dropped = [body_map.get(name, name) for name in [*source.dropped, *unused]]
        for key, value in list(clone.inputs.items()):
            clone.inputs[key] = expression(_rewrite_value(value, body_map, feature_map, object_map))
        clone.rename_output_inputs(body_map)
        clones.append(clone)

    checkpoint = {
        "features": [copy.deepcopy(f.to_dict()) for f in clones],
        "parameters": [p.to_dict() for p in new_parameters],
        "shapes": {body_map[name]: blob for name, blob in fragment.shapes.items()},
    }

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
        clones.append(move)

    group_map = {
        entry["name"]: unique(entry["name"]) for entry in fragment.groups
    }
    new_groups = {}
    for entry in fragment.groups:
        name = group_map[entry["name"]]
        members = [
            group_map.get(member, body_map.get(member, member))
            for member in entry.get("members", [])
        ]
        new_groups[name] = Group(
            name=name,
            members=members,
            visible=entry.get("visible", True),
            expanded=entry.get("expanded", True),
        )

    root_items = [group_map.get(item, body_map.get(item, item)) for item in fragment.items]
    # Build and validate the entire fragment before touching the live model.
    # A malformed clipboard entry must never leave half a pasted history.
    for parameter in new_parameters:
        document.parameters.set(parameter.name, parameter.expression, parameter.dimension, parameter.comment)
    for clone in clones:
        document.add_feature(clone)
    document.groups.update(new_groups)
    for old_name, appearance in fragment.appearance.items():
        if old_name not in roots or old_name not in body_map:
            continue
        name = body_map[old_name]
        document.bodies[name] = Body(
            name=name, visible=appearance.get("visible", True),
            color=appearance.get("color"), metadata=copy.deepcopy(appearance.get("metadata", {})),
            id=object_map.get(appearance.get("id"), document.ref(name).object_id),
        )
    if checkpoint["shapes"]:
        if not hasattr(document, "_geometry_fragments"):
            document._geometry_fragments = []
        document._geometry_fragments.append(checkpoint)
    return clones, root_items


def _rewrite_value(value, body_map: dict[str, str], feature_map: dict[str, str], object_map=None):
    object_map = object_map or {}
    if isinstance(value, BodyRef):
        return BodyRef(body_map.get(str(value), str(value)), object_map.get(value.object_id, value.object_id),
                       feature_map.get(value.feature_id, value.feature_id))
    if isinstance(value, SubShapeRef):
        return replace(
            value,
            body=body_map.get(value.body, value.body),
            feature_id=feature_map.get(value.feature_id, value.feature_id),
            body_id=object_map.get(value.body_id, value.body_id),
        )
    if isinstance(value, list):
        return [_rewrite_value(item, body_map, feature_map, object_map) for item in value]
    if isinstance(value, tuple):
        return tuple(_rewrite_value(item, body_map, feature_map, object_map) for item in value)
    if isinstance(value, dict):
        return {key: _rewrite_value(item, body_map, feature_map, object_map) for key, item in value.items()}
    return value
