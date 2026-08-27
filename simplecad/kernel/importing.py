"""Bringing outside geometry in, as a feature.

An import is stored as the *path* it came from plus which body of that file it
is, and the file is re-read on rebuild. That keeps an imported part exactly as
parametric as a modelled one -- it has a node in the timeline, it can be
suppressed, reordered, moved and undone -- and it keeps the project file small,
since a 40 MB STEP assembly is not copied into every save.

The cost of that choice is that the link can break, and it is handled rather
than hidden: a missing file raises a plain error naming the path, the rebuild
engine keeps the last good geometry on screen, and the ``.scad3`` body cache
means reopening a saved project still works even when the original has gone.

Reads are memoised on the file's path, size and modification time, because one
STEP file holding ten parts becomes ten features and would otherwise be parsed
ten times for every rebuild.
"""

from __future__ import annotations

import os

from ..core.document import BuildContext, Feature, register
from ..core.errors import CadError
from .io_formats import import_bodies
from .occ import make_transform, transformed

#: How many files to keep parsed. Small: this exists to collapse the ten reads
#: of one rebuild into one, not to hold a library in memory.
CACHE_LIMIT = 4

_cache: dict[tuple, list] = {}


def _stamp(path: str) -> tuple:
    stat = os.stat(path)
    return (os.path.abspath(path), stat.st_mtime_ns, stat.st_size)


def read_bodies(path: str) -> list:
    """The bodies in *path*, parsed at most once per version of the file."""
    if not os.path.exists(path):
        raise CadError(
            f"The imported file '{os.path.basename(path)}' is no longer there.",
            suggestion=f"It was read from {path}. Move it back, or re-import it.",
        )
    key = _stamp(path)
    found = _cache.get(key)
    if found is None:
        found = import_bodies(path)
        if len(_cache) >= CACHE_LIMIT:
            _cache.pop(next(iter(_cache)))
        _cache[key] = found
    return found


def forget(path: str | None = None) -> None:
    """Drop cached reads, so the next rebuild picks the file up again."""
    if path is None:
        _cache.clear()
        return
    target = os.path.abspath(path)
    for key in [k for k in _cache if k[0] == target]:
        _cache.pop(key, None)


@register("import")
class ImportFeature(Feature):
    """One body read out of a 3D file, placed in the scene."""

    label = "Import"

    def execute(self, ctx: BuildContext) -> dict:
        path = str(self.inputs.get("path") or "")
        if not path:
            raise CadError("This import has no file to read.")
        index = int(self.inputs.get("index", 0) or 0)

        bodies = read_bodies(path)
        if index >= len(bodies):
            raise CadError(
                f"'{os.path.basename(path)}' no longer has a body "
                f"{index + 1} — it now holds {len(bodies)}.",
                suggestion="Delete this import and bring the file in again.",
            )
        imported = bodies[index]
        if imported.note:
            ctx.warn(f"{self.name or imported.name}: {imported.note}")
            self.message = imported.note

        shape = imported.shape
        offset = (
            ctx.value(self, "dx", 0.0),
            ctx.value(self, "dy", 0.0),
            ctx.value(self, "dz", 0.0),
        )
        if any(abs(v) > 1e-12 for v in offset):
            shape = transformed(shape, make_transform(translate=offset))

        name = self.outputs[0] if self.outputs else imported.name
        self.outputs = [name]
        return {name: shape}
