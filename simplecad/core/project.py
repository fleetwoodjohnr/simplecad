"""The native project format: ``.scad3``.

A zip container holding the *recipe*, not the result:

```
model.json         identities, dependencies, recipes, undo/redo, cache manifest
geometry/<sha>.brep deduplicated intermediate and final geometry
thumbnail.png      optional preview
```

The distinction matters. STL is an export target; it is never what SimpleCAD
saves, because a mesh cannot be edited back into a parametric model. Everything
needed to re-derive the geometry -- the feature graph, the expressions, the
sub-shape references, the thread and alignment relationships -- lives in
``model.json``, and the cached B-Rep is only ever an optimisation. Delete it and
the file still opens; the model is simply rebuilt from its features.
"""

from __future__ import annotations

import io
import copy
import hashlib
import json
import os
import zipfile

from .document import Document
from .errors import CadError

EXTENSION = ".scad3"
FORMAT_VERSION = 3
MODEL_ENTRY = "model.json"
BODY_PREFIX = "bodies/"
THUMBNAIL_ENTRY = "thumbnail.png"


def _safe_entry(name: str) -> str:
    """A zip-safe filename for a body, so odd names cannot escape the archive."""
    keep = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    return f"{BODY_PREFIX}{keep or 'body'}.brep"


def save(document: Document, path: str, thumbnail: bytes | None = None) -> str:
    """Write *document* to *path*."""
    from OCP.BRepTools import BRepTools

    if not path.lower().endswith(EXTENSION):
        path += EXTENSION

    from .result_store import signatures, ResultStore
    from .geometry_service import serialise_shape

    payload = document.to_dict()
    payload["format"] = FORMAT_VERSION
    payload["application"] = "SimpleCAD"
    history = getattr(document, "_history", None)
    payload["history"] = history.to_dict() if history else copy.deepcopy(document._history_data)
    retained_assets = set(payload.get("assets", []))
    for stack in ("undo", "redo"):
        for action in payload["history"].get(stack, []):
            retained_assets.update(action.get("patch", {}).get("values", {}).get("assets", []))
    current = signatures(document)
    accepted = getattr(document, "_geometry_signatures", {})
    store = document._geometry_store or ResultStore()
    checkpoint = store.export(include_blobs=False)
    payload["cache"] = {"version": checkpoint["version"], "entries": copy.deepcopy(checkpoint["entries"])}
    payload["geometry"] = {}
    payload["diagnostics"] = {f.id: {"state": f.state.value, "message": f.message} for f in document.features}
    entries = {}
    for body in document.bodies.values():
        if body.shape is None:
            continue
        try:
            blob = serialise_shape(body.shape)
            sha = hashlib.sha256(blob).hexdigest()
            entries[f"geometry/{sha}.brep"] = blob
            producer = document.producer_of(body.name)
            fid = producer.id if producer else body.producer
            valid = bool(fid and accepted.get(fid) == current.get(fid)
                         and producer.state.value == "ok")
            payload["geometry"][body.id] = {"sha256": sha, "feature_id": fid,
                                           "signature": accepted.get(fid), "current": valid}
        except Exception:
            continue

    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    # Write beside the target and move into place, so an interrupted save never
    # destroys the previous version of the user's work.
    temporary = path + ".part"
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MODEL_ENTRY, json.dumps(payload, indent=1))
            for sha in retained_assets:
                blob = document._all_assets.get(sha)
                if blob is not None:
                    archive.writestr(f"assets/{sha}.brep", blob)
            for sha in list(store.blobs):
                entry = f"geometry/{sha}.brep"
                if entry not in entries:
                    archive.writestr(entry, store.blobs[sha])
            for name, blob in entries.items():
                archive.writestr(name, blob)
            if thumbnail:
                archive.writestr(THUMBNAIL_ENTRY, thumbnail)
        os.replace(temporary, path)
    except OSError as exc:
        if os.path.exists(temporary):
            os.remove(temporary)
        raise CadError(
            f"'{os.path.basename(path)}' could not be saved.",
            suggestion="Check that the folder exists and there is space free.",
            detail=str(exc),
        ) from exc
    return path


def load(path: str) -> tuple[Document, bool]:
    """Read a project. Returns ``(document, geometry_was_cached)``.

    When the cache is missing or unreadable the document still loads; the caller
    rebuilds it from its features.
    """
    from OCP.BRep import BRep_Builder
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape

    if not os.path.exists(path):
        raise CadError(f"'{os.path.basename(path)}' could not be found.")
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise CadError(
            f"'{os.path.basename(path)}' is not a SimpleCAD project.",
            suggestion="To bring in geometry from another program, use Import.",
        ) from exc

    with archive:
        names = set(archive.namelist())
        if MODEL_ENTRY not in names:
            raise CadError(
                f"'{os.path.basename(path)}' is missing its model data.",
                suggestion="The file may be damaged.",
            )
        try:
            payload = json.loads(archive.read(MODEL_ENTRY))
        except json.JSONDecodeError as exc:
            raise CadError(
                f"'{os.path.basename(path)}' could not be read.",
                suggestion="The file may be damaged.",
                detail=str(exc),
            ) from exc

        version = int(payload.get("format", 1))
        if version > FORMAT_VERSION:
            raise CadError(
                f"'{os.path.basename(path)}' was saved by a newer SimpleCAD.",
                suggestion="Update SimpleCAD to open it.",
            )

        document = Document.from_dict(payload)
        for entry in names:
            if not entry.startswith("assets/") or not entry.endswith(".brep"):
                continue
            sha = entry[len("assets/"):-len(".brep")]
            try:
                blob = archive.read(entry)
                if hashlib.sha256(blob).hexdigest() == sha:
                    document._all_assets[sha] = blob
            except (OSError, zipfile.BadZipFile):
                continue
        document.assets = {sha: document._all_assets[sha]
                           for sha in payload.get("assets", []) if sha in document._all_assets}

        from .document import FeatureState
        from .result_store import ResultStore, signatures
        from .geometry_service import deserialise_shape
        document._history_data = copy.deepcopy(payload.get("history", {}))
        document._geometry_store = ResultStore()
        cached = False
        accepted = {}
        if version >= 2:
            blobs = {}
            for entry in names:
                if entry.startswith("geometry/") and entry.endswith(".brep"):
                    sha = entry[len("geometry/"):-len(".brep")]
                    try:
                        blob = archive.read(entry)
                        if hashlib.sha256(blob).hexdigest() == sha:
                            blobs[sha] = blob
                    except (OSError, zipfile.BadZipFile):
                        continue
            document._geometry_store.merge({**payload.get("cache", {}), "blobs": blobs})
            current = signatures(document)
            for body in document.bodies.values():
                entry = payload.get("geometry", {}).get(body.id, {})
                try:
                    shape = deserialise_shape(blobs[entry["sha256"]])
                    if shape is None:
                        continue
                    body.shape = shape
                    valid = entry.get("current") and entry.get("signature") == current.get(entry.get("feature_id"))
                    if valid:
                        accepted[entry["feature_id"]] = entry["signature"]
                        cached = True
                    else:
                        document._pending_changes.add(entry.get("feature_id", ""))
                except (KeyError, ValueError, RuntimeError):
                    continue
            for feature in document.features:
                outcome = payload.get("diagnostics", {}).get(feature.id, {})
                feature.state = FeatureState(outcome.get("state", "ok"))
                feature.message = outcome.get("message", "")
        else:
            # Detect legacy filename collisions instead of giving two objects
            # the same geometry accidentally.
            from collections import Counter
            locations = Counter(_safe_entry(b.name) for b in document.bodies.values())
            for body in document.bodies.values():
                entry = _safe_entry(body.name)
                if entry not in names or locations[entry] != 1:
                    continue
                try:
                    shape = deserialise_shape(archive.read(entry))
                    if shape is not None:
                        body.shape = shape
                        cached = True
                except Exception:
                    continue
            if cached:
                from .rebuild import Rebuilder
                builder = Rebuilder(document)
                if builder.adopt_baseline():
                    accepted = signatures(document)
        document._geometry_signatures = accepted
        document._geometry_store.adopt_display(document)
        document._cache_complete = all(
            body.shape is not None and body.producer in accepted
            for body in document.bodies.values()
        )
    return document, cached


def inspect_bodies(path: str) -> list[tuple[str, str]]:
    """List native project objects without decoding any geometry."""
    try:
        with zipfile.ZipFile(path) as archive:
            payload = json.loads(archive.read(MODEL_ENTRY))
    except (OSError, zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
        raise CadError(f"'{os.path.basename(path)}' could not be read as a SimpleCAD project.") from exc
    if int(payload.get("format", 1)) > FORMAT_VERSION:
        raise CadError("This project was saved by a newer SimpleCAD.")
    return [(body.get("id") or body["name"], body["name"])
            for body in payload.get("bodies", [])]


def read_thumbnail(path: str) -> bytes | None:
    try:
        with zipfile.ZipFile(path) as archive:
            if THUMBNAIL_ENTRY in archive.namelist():
                return archive.read(THUMBNAIL_ENTRY)
    except (OSError, zipfile.BadZipFile):
        return None
    return None
