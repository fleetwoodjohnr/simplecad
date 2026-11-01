#!/usr/bin/env python
"""Open projects with two versions of SimpleCAD and compare what they build.

Every change to rebuilding, history or the file format has to leave existing
``.scad3`` files opening and editing exactly as before. For each file this runs
both source trees in their own process, never writing the file itself:

* the geometry as loaded from the file's cache
* a rebuild that adopts that cache, and one from the recipe alone
* after undoing everything, and after redoing it all again
* after Save As to a scratch file and reopening that

and compares each body's volume, bounding box and face count.

    .venv/bin/python scripts/compare_projects.py --old /path/to/old/checkout ~/Desktop/**/*.scad3
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _signature(document) -> dict:
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer

    from simplecad.kernel.occ import bounding_box, volume

    found = {}
    for name, body in sorted(document.bodies.items()):
        if body.shape is None or body.shape.IsNull():
            found[name] = None
            continue
        faces, explorer = 0, TopExp_Explorer(body.shape, TopAbs_FACE)
        while explorer.More():
            faces += 1
            explorer.Next()
        low, high = bounding_box(body.shape)
        found[name] = {
            "volume": round(abs(volume(body.shape)), 3),
            "box": [round(v, 3) for v in (*low, *high)],
            "faces": faces,
        }
    return found


def dump(path: str) -> dict:
    """Run inside one tree: every stage's bodies, as JSON-able data."""
    import simplecad.kernel  # noqa: F401 - registers every feature type
    from simplecad.core.history import History
    from simplecad.core.project import load, save
    from simplecad.core.rebuild import Rebuilder

    stages = {}
    document, _cached = load(path)
    stages["loaded"] = _signature(document)

    rebuilder = Rebuilder(document)
    rebuilder.adopt_baseline()
    report = rebuilder.rebuild()
    stages["cached_rebuild"] = _signature(document)
    stages["cached_failed"] = len(report.failures)

    history = History(document)
    history.reset(document)
    undone = 0
    while history.can_undo:
        stale = history.undo()
        rebuilder.invalidate(stale)
        undone += 1
    rebuilder.rebuild()
    stages["undo_all"] = _signature(document)
    while history.can_redo:
        stale = history.redo()
        rebuilder.invalidate(stale)
    rebuilder.rebuild()
    stages["redo_all"] = _signature(document)
    stages["undo_steps"] = undone

    with tempfile.TemporaryDirectory() as scratch:
        copy = os.path.join(scratch, "copy.scad3")
        save(document, copy)
        reopened, _ = load(copy)
        stages["reopened"] = _signature(reopened)

    fresh, _ = load(path)
    for body in fresh.bodies.values():
        body.shape = None
    recipe = Rebuilder(fresh)
    report = recipe.rebuild(force=True)
    stages["recipe_rebuild"] = _signature(fresh)
    stages["recipe_failed"] = len(report.failures)
    return stages


def _run(tree: str, path: str) -> dict:
    env = dict(os.environ, PYTHONPATH=tree, PYTHONDONTWRITEBYTECODE="1",
               QT_QPA_PLATFORM="offscreen")
    result = subprocess.run(
        [sys.executable, os.path.abspath(__file__), "--dump", path],
        cwd=tree, env=env, capture_output=True, text=True, timeout=3600,
    )
    if result.returncode != 0:
        return {"error": result.stderr.strip().splitlines()[-1:] or ["failed"]}
    return json.loads(result.stdout.strip().splitlines()[-1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--old", help="checkout of the version to compare against")
    parser.add_argument("--dump", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("files", nargs="+")
    args = parser.parse_args()
    if args.dump:
        print(json.dumps(dump(args.files[0])))
        return 0
    if not args.old:
        parser.error("--old is required")
    same = True
    for path in args.files:
        old, new = _run(os.path.abspath(args.old), path), _run(ROOT, path)
        differences = [stage for stage in sorted(set(old) | set(new))
                       if old.get(stage) != new.get(stage)]
        name = os.path.basename(path)
        if "error" in old or "error" in new:
            same = False
            print(f"ERROR      {name}: old {old.get('error')} new {new.get('error')}")
            continue
        if differences:
            same = False
            print(f"DIFFERENT  {name}: {', '.join(differences)}")
            for stage in differences:
                print(f"    old {stage}: {json.dumps(old.get(stage))[:400]}")
                print(f"    new {stage}: {json.dumps(new.get(stage))[:400]}")
        else:
            print(f"same       {name}  ({len(new.get('loaded', {}))} bodies, "
                  f"{new.get('undo_steps', 0)} undo steps)")
    print("VERDICT:", "PASS" if same else "FAIL")
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())
