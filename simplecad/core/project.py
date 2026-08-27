"""The native project format: ``.scad3``.

A zip container holding the *recipe*, not the result:

```
model.json         features, parameters, bodies, metadata
bodies/<name>.brep cached geometry, so opening is instant
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
import json
import os
import zipfile

from .document import Document
from .errors import CadError

EXTENSION = ".scad3"
FORMAT_VERSION = 1
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

    payload = document.to_dict()
    payload["format"] = FORMAT_VERSION
    payload["application"] = "SimpleCAD"

    entries: dict[str, bytes] = {}
    for body in document.bodies.values():
        if body.shape is None:
            continue
        try:
            stream = io.BytesIO()
            BRepTools.Write_s(body.shape, stream)
            entries[_safe_entry(body.name)] = stream.getvalue()
        except Exception:  # noqa: BLE001 - the cache is optional by design
            continue

    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    # Write beside the target and move into place, so an interrupted save never
    # destroys the previous version of the user's work.
    temporary = path + ".part"
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MODEL_ENTRY, json.dumps(payload, indent=1))
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

        cached = False
        for body in document.bodies.values():
            entry = _safe_entry(body.name)
            if entry not in names:
                continue
            try:
                shape = TopoDS_Shape()
                BRepTools.Read_s(shape, io.BytesIO(archive.read(entry)), BRep_Builder())
                if not shape.IsNull():
                    body.shape = shape
                    cached = True
            except Exception:  # noqa: BLE001 - fall back to rebuilding
                continue
    return document, cached


def read_thumbnail(path: str) -> bytes | None:
    try:
        with zipfile.ZipFile(path) as archive:
            if THUMBNAIL_ENTRY in archive.namelist():
                return archive.read(THUMBNAIL_ENTRY)
    except (OSError, zipfile.BadZipFile):
        return None
    return None
