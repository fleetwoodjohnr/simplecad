"""Content-addressed feature results shared by rebuild, previews and projects."""

from __future__ import annotations

import base64
from collections import OrderedDict, deque
from collections.abc import MutableMapping
from dataclasses import asdict, is_dataclass
import hashlib
import json
from pathlib import Path
import tempfile


CACHE_VERSION = "simplecad-feature-cache-2-occt-7.9"
DECODED_BUDGET = 512 * 1024 * 1024
BLOB_BUDGET = 64 * 1024 * 1024


class BlobStore(MutableMapping):
    """Bound serialized cache memory; spill cold blobs to private scratch files.

    Files belong to this cache instance and are deleted on eviction or cleanup.
    Archives and worker messages still contain ordinary portable bytes.
    """

    def __init__(self):
        self.memory = OrderedDict()
        self.disk = {}
        self.memory_bytes = 0
        self._directory = None

    def __len__(self):
        return len(self.memory) + len(self.disk)

    def __iter__(self):
        yield from tuple(self.memory)
        yield from tuple(self.disk)

    def __contains__(self, key):
        return key in self.memory or key in self.disk

    def __getitem__(self, key):
        if key in self.memory:
            self.memory.move_to_end(key)
            return self.memory[key]
        return self.disk[key].read_bytes()

    def __setitem__(self, key, value):
        if key in self:
            del self[key]
        self.memory[key] = value
        self.memory_bytes += len(value)
        while self.memory_bytes > BLOB_BUDGET:
            cold, blob = next(iter(self.memory.items()))
            try:
                if self._directory is None:
                    self._directory = tempfile.TemporaryDirectory(prefix="simplecad-cache-")
                path = Path(self._directory.name) / hashlib.sha256(cold.encode()).hexdigest()
                path.write_bytes(blob)
            except OSError:
                # Cache storage is optional; a full scratch drive must not
                # prevent a valid modeling operation or project save.
                break
            self.disk[cold] = path
            del self.memory[cold]
            self.memory_bytes -= len(blob)

    def __delitem__(self, key):
        if key in self.memory:
            self.memory_bytes -= len(self.memory.pop(key))
        else:
            path = self.disk.pop(key)
            path.unlink(missing_ok=True)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def signatures(document, graph=None):
    """Fingerprint the recipe, including transitive inputs and parameters."""
    from .document import OUTPUT_NAME_INPUTS
    from .params import dependencies
    graph = graph or document.prepare_graph()
    parameters = {p.name: p.to_dict() for p in document.parameters}
    result = document._signature_cache
    dirty = set(document._signature_dirty) | (graph.features.keys() - result.keys())
    previous_parameters = getattr(document, "_signature_parameters", {})
    for name in parameters.keys() | previous_parameters.keys():
        if parameters.get(name) != previous_parameters.get(name):
            dirty.update(graph.parameter_dependents(name, document.parameters))
    dirty = graph.dependents(dirty) & graph.features.keys()
    for fid in result.keys() - graph.features.keys():
        result.pop(fid)
    for fid in sorted(dirty, key=graph.ranks.get):
        feature = graph.features[fid]
        names = set(feature.parameter_names())
        todo = list(names)
        while todo:
            entry = parameters.get(todo.pop())
            if entry:
                for name in dependencies(entry["expression"]) - names:
                    names.add(name)
                    todo.append(name)
        state = feature.to_dict()
        state.pop("name", None)
        def canonical(value):
            if isinstance(value, list):
                return [canonical(v) for v in value]
            if isinstance(value, dict):
                if "__body__" in value:
                    ref = feature.bindings.get(value["__body__"])
                    return {"__body__": ref.to_dict() if ref else value["__body__"]}
                if "__ref__" in value:
                    ref = dict(value["__ref__"])
                    binding = feature.bindings.get(ref.get("body"))
                    ref["body"] = binding.object_id if binding else ref.get("body_id", "")
                    ref.pop("body_id", None)
                    return {"__ref__": ref}
                return {k: canonical(v) for k, v in value.items()}
            return value
        state["inputs"] = canonical(state["inputs"])
        for key in OUTPUT_NAME_INPUTS:
            if key in state["inputs"]:
                state["inputs"][key] = [feature.output_ids.get(n, n) for n in state["inputs"][key]]
        state["outputs"] = [feature.output_ids[n] for n in feature.outputs]
        state["dropped"] = [feature.output_ids.get(n, n) for n in feature.dropped]
        state["output_ids"] = sorted(feature.output_ids.values())
        state["bindings"] = sorted((r.feature_id, r.object_id) for r in feature.bindings.values())
        result[feature.id] = digest({
            "version": CACHE_VERSION, "feature": state,
            "parameters": {n: parameters.get(n) for n in sorted(names)},
            "upstream": {fid: result.get(fid, "missing") for fid in sorted(graph.forward[feature.id])},
        })
    document._signature_dirty.clear()
    document._signature_parameters = parameters
    document._last_signature_changes = dirty
    return dict(result)


class ResultStore:
    def __init__(self):
        self.entries = {}
        self.blobs = BlobStore()
        self.decoded = OrderedDict()
        self.decoded_bytes = 0
        self.hits = 0
        self.serialized = 0
        #: Recently stored or decoded shapes and the blob holding each, so a
        #: rigidly moved copy can be recorded as that blob plus a placement.
        self._recent = deque(maxlen=64)

    def _remember(self, key, outputs, size):
        old = self.decoded.pop(key, None)
        if old is not None:
            self.decoded_bytes -= old[1]
        self.decoded[key] = (outputs, size)
        self.decoded_bytes += size
        while self.decoded_bytes > DECODED_BUDGET and len(self.decoded) > 1:
            _, (_, amount) = self.decoded.popitem(last=False)
            self.decoded_bytes -= amount

    def adopt_display(self, document):
        """Reuse the exact displayed objects when hydrating a saved cache."""
        grouped = {}
        accepted = getattr(document, "_geometry_signatures", {})
        for body in document.bodies.values():
            key = accepted.get(body.producer)
            if key and body.shape is not None:
                grouped.setdefault(key, {})[body.id] = body.shape
        for key, outputs in grouped.items():
            entry = self.entries.get(key)
            if entry and set(entry["outputs"]) == set(outputs):
                size = sum(len(self.blobs.get(v.get("sha256"), b"")) for v in entry["outputs"].values())
                self._remember(key, outputs, size)

    def put(self, feature, signature, outputs):
        from .geometry_service import serialise_shape
        encoded, size = {}, 0
        for name, value in outputs.items():
            oid = feature.output_ids[name]
            base = self._located_base(value) if hasattr(value, "IsNull") else None
            if base is not None:
                # A move shares its input's geometry under a new location.
                # Serialising it again cost ~0.8 s and 13 MB for a large
                # imported mesh, on every move and every undo of one.
                from .geometry_service import location_to_wire
                encoded[oid] = {"kind": "located", "sha256": base,
                                "location": location_to_wire(value.Location())}
                size += len(self.blobs.get(base, b""))
                continue
            if hasattr(value, "IsNull"):
                blob = serialise_shape(value)
                sha = hashlib.sha256(blob).hexdigest()
                if sha not in self.blobs:
                    self.blobs[sha] = blob
                    self.serialized += 1
                encoded[oid] = {"kind": "brep", "sha256": sha}
                size += len(blob)
                self._recent.append((value, sha))
            elif hasattr(value, "to_dict"):
                encoded[oid] = {"kind": "sketch", "data": value.to_dict()}
            elif is_dataclass(value):
                encoded[oid] = {"kind": type(value).__name__, "data": asdict(value)}
            else:
                # Diagnostic/test kernels may return nonportable values.
                encoded[oid] = {"kind": "memory"}
        self.entries[signature] = {"feature_id": feature.id, "outputs": encoded,
                                   "message": feature.message, "version": CACHE_VERSION}
        # A solved sketch is mutable editor data. Never let future point edits
        # alter a cached result belonging to an older history revision.
        from ..sketch.sketch import Sketch
        self._remember(signature, {feature.output_ids[n]: Sketch.from_dict(v.to_dict())
                                  if isinstance(v, Sketch) else v for n, v in outputs.items()}, size)

    def get(self, feature, signature):
        entry = self.entries.get(signature)
        if not entry or entry.get("version") != CACHE_VERSION or entry["feature_id"] != feature.id:
            return None
        if signature in self.decoded:
            decoded, size = self.decoded.pop(signature)
            self.decoded[signature] = (decoded, size)
        else:
            from .geometry_service import deserialise_shape
            decoded, size = {}, 0
            try:
                for oid, value in entry["outputs"].items():
                    kind = value["kind"]
                    if kind in ("brep", "located"):
                        blob = self.blobs[value["sha256"]]
                        if hashlib.sha256(blob).hexdigest() != value["sha256"]:
                            return None
                        shape = self._decoded_blob(value["sha256"], blob)
                        if shape is None:
                            return None
                        if kind == "located":
                            from .geometry_service import location_from_wire
                            shape = shape.Located(location_from_wire(value["location"]))
                        decoded[oid] = shape
                        size += len(blob)
                    elif kind == "sketch":
                        from ..sketch.sketch import Sketch
                        decoded[oid] = Sketch.from_dict(value["data"])
                    elif kind in {"ConstructionPlane", "ConstructionAxis", "ConstructionPoint"}:
                        from ..kernel import construction
                        decoded[oid] = getattr(construction, kind)(**value["data"])
                    else:
                        return None
            except Exception:
                return None
            self._remember(signature, decoded, size)
        if not set(decoded) <= set(feature.output_ids.values()):
            return None
        self.hits += 1
        feature.message = entry.get("message", "")
        return {name: decoded[oid] for name, oid in feature.output_ids.items() if oid in decoded}

    def _located_base(self, shape):
        """The blob of a stored shape that *shape* is a moved copy of."""
        try:
            if shape.IsNull():
                return None
            for candidate, sha in reversed(self._recent):
                if (sha in self.blobs and candidate.IsPartner(shape)
                        and candidate.Orientation() == shape.Orientation()):
                    return sha
        except Exception:  # noqa: BLE001 - fall back to serialising
            return None
        return None

    def _decoded_blob(self, sha, blob):
        """Decode *blob*, sharing the result between entries that use it."""
        for candidate, known in reversed(self._recent):
            if known == sha:
                return candidate
        from .geometry_service import deserialise_shape
        shape = deserialise_shape(blob)
        if shape is not None:
            self._recent.append((shape, sha))
        return shape

    def export(self, known=(), known_entries=(), *, include_blobs=True):
        return {"version": CACHE_VERSION,
                "entries": {key: value for key, value in self.entries.items() if key not in known_entries},
                "blobs": {key: self.blobs[key] for key in list(self.blobs) if key not in known} if include_blobs else {}}

    def merge(self, payload):
        if not payload or payload.get("version") != CACHE_VERSION:
            return
        self.entries.update(payload.get("entries", {}))
        for sha, blob in payload.get("blobs", {}).items():
            if sha in self.blobs:
                # Content-addressed: an existing key already holds these bytes.
                continue
            if isinstance(blob, str):
                blob = base64.b64decode(blob)
            if hashlib.sha256(blob).hexdigest() == sha:
                self.blobs[sha] = blob

    def prune(self, retained):
        self.entries = {key: entry for key, entry in self.entries.items() if key in retained}
        used = {v["sha256"] for e in self.entries.values() for v in e["outputs"].values() if "sha256" in v}
        for key in list(self.blobs):
            if key not in used:
                del self.blobs[key]
        for key in list(self.decoded):
            if key not in retained:
                self.decoded_bytes -= self.decoded.pop(key)[1]
