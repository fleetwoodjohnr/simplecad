#!/usr/bin/env python
"""Open projects with two versions of SimpleCAD and compare what they build.

Every change to rebuilding, history or the file format has to leave existing
``.scad3`` files opening and editing exactly as before. For each file this runs
both source trees in their own process, never writing the file itself:

* the geometry saved in the file and the geometry displayed after opening
* a rebuild that adopts that cache, and one from the recipe alone
* after undoing everything, and after redoing it all again
* after Save As to a scratch file and reopening that

and compares each body's volume, bounding box and face count.

    .venv/bin/python scripts/compare_projects.py --old /path/to/old/checkout ~/Desktop/**/*.scad3

``--rust engine/target/release/scad`` compares loaded geometry and checks that
files Rust saves reopen here. ``--rust ... --rebuild`` also requires cached and
recipe rebuilds, undo and redo of the original file, and a native Save As
reopen. A missing stage or an unported operation fails the comparison.
``--rust ... --align`` compares each saved Align step by its feature ID.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DUMP_STAGES = {"loaded": dict, "displayed": dict, "signatures": dict, "fingerprints": dict, "document": dict}
_HISTORY_STAGES = {"undo_all": dict, "redo_all": dict, "undo_steps": int, "redo_steps": int,
                   "undo_diagnostics": dict, "redo_diagnostics": dict}
_REBUILD_STAGES = {"cached_rebuild": dict, "cached_failed": int, "cached_diagnostics": dict,
                   "recipe_rebuild": dict, "recipe_failed": int, "recipe_diagnostics": dict}


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


def _rounded(stage: dict) -> dict:
    """The Rust engine's raw numbers, rounded as ``_signature`` rounds Python's."""
    return {name: None if body is None else {
        "volume": round(abs(body["volume"]), 3),
        "box": [round(v, 3) for v in body["box"]],
        "faces": body["faces"],
    } for name, body in stage.items()}


def _failure_states(report) -> dict:
    from simplecad.core.errors import ReferenceLost

    return {fid: "needs_attention" if isinstance(error, ReferenceLost) else "failed"
            for fid, error in report.failures.items()}


def dump(path: str, loaded_only: bool = False) -> dict:
    """Run inside one tree: every stage's bodies, as JSON-able data."""
    import simplecad.kernel  # noqa: F401 - registers every feature type
    from simplecad.core.history import History
    from simplecad.core.project import load, save
    from simplecad.core.rebuild import Rebuilder

    stages = {}
    document, cached = load(path)
    stages["loaded"] = _signature(document)
    from simplecad.core.naming import fingerprint, sub_shapes
    from simplecad.core.result_store import signatures
    stages["signatures"] = signatures(document)
    stages["fingerprints"] = {
        name: [fingerprint(face, "face").to_dict() for face in sub_shapes(body.shape, "face")]
        for name, body in document.bodies.items() if body.shape is not None}
    stages["document"] = document.to_dict()
    if loaded_only:
        return stages

    opens_from_cache = cached and document._cache_complete
    rebuilder = Rebuilder(document)
    rebuilder.adopt_baseline()
    report = rebuilder.rebuild()
    stages["cached_rebuild"] = _signature(document)
    stages["displayed"] = stages["loaded"] if opens_from_cache else stages["cached_rebuild"]
    stages["cached_failed"] = len(report.failures)
    stages["cached_diagnostics"] = _failure_states(report)

    stored_history = getattr(document, "_history_data", {})
    history = History(document, limit=max(1, len(stored_history.get("undo", [])),
                                          len(stored_history.get("redo", []))))
    undone = 0
    while history.can_undo:
        stale = history.undo()
        rebuilder.invalidate(stale)
        undone += 1
    stages["undo_diagnostics"] = _failure_states(rebuilder.rebuild())
    stages["undo_all"] = _signature(document)
    redone = 0
    while history.can_redo:
        stale = history.redo()
        rebuilder.invalidate(stale)
        redone += 1
    stages["redo_diagnostics"] = _failure_states(rebuilder.rebuild())
    stages["redo_all"] = _signature(document)
    stages["undo_steps"] = undone
    stages["redo_steps"] = redone

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
    stages["recipe_diagnostics"] = _failure_states(report)
    return stages


def reopen(path: str, redo: bool = False) -> dict:
    """Run inside one tree: a saved file as reopened, and optionally after one redo."""
    import simplecad.kernel  # noqa: F401 - registers every feature type
    from simplecad.core.history import History
    from simplecad.core.project import load
    from simplecad.core.rebuild import Rebuilder
    from simplecad.core.result_store import signatures

    document, cached = load(path)
    found = {"bodies": _signature(document), "cached": cached, "complete": document._cache_complete,
             "signatures": signatures(document)}
    if redo:
        rebuilder = Rebuilder(document)
        rebuilder.adopt_baseline()
        found["rebuild_failed"] = len(rebuilder.rebuild().failures)
        history = History(document)
        history.reset(document)
        rebuilder.invalidate(history.redo())
        report = rebuilder.rebuild()
        found["redone"] = _signature(document)
        found["redo_failed"] = len(report.failures)
    return found


def align_results(path: str) -> dict:
    """Capture the Python solver's result for each saved Align step."""
    from unittest.mock import patch

    import simplecad.kernel  # noqa: F401 - registers every feature type
    from simplecad.core.errors import CadError
    from simplecad.core.project import load
    from simplecad.core.rebuild import Rebuilder
    from simplecad.kernel import align

    document, _ = load(path)
    rebuilder = Rebuilder(document)
    rebuilder.adopt_baseline()
    rebuilder._prepare()
    solve = align.solve
    placement = None
    results = {}

    def record(*args, **kwargs):
        nonlocal placement
        placement = solve(*args, **kwargs)
        return placement

    with patch.object(align, "solve", record):
        for feature in document.features:
            if feature.type_name != "align":
                continue
            if feature.id in results:
                raise ValueError(f"duplicate Align feature ID {feature.id}")
            entry = {"name": feature.name}
            try:
                context = rebuilder._input_context(feature)
                placement = None
                feature.execute(context)
            except CadError as exc:
                entry["error"] = str(exc)
            else:
                if placement is None:
                    raise ValueError(f"{feature.name}: Align did not call the placement solver")
                entry["result"] = {
                    "transform": [placement.transform.Value(row, column)
                                  for row in range(1, 4) for column in range(1, 5)],
                    "description": placement.description,
                }
            results[feature.id] = entry
    return results


def _resaved(path: str, target: str) -> str:
    """Run inside one tree: *path* saved here to *target*, as Save As does."""
    import simplecad.kernel  # noqa: F401 - registers every feature type
    from simplecad.core.project import load, save
    from simplecad.core.rebuild import Rebuilder

    document, cached = load(path)
    if not (cached and document._cache_complete):
        rebuilder = Rebuilder(document)
        rebuilder.adopt_baseline()
        rebuilder.rebuild()
    return save(document, target)


def _rust_saves(rust: str, path: str, loaded: dict, *, native: bool, reference) -> list[str]:
    """What is wrong with the files Rust saves from *path*, reopened here."""
    problems = []
    with tempfile.TemporaryDirectory() as scratch:
        plain = os.path.join(scratch, "plain.scad3")
        result = subprocess.run([rust, "save", path, plain], capture_output=True, text=True, timeout=3600)
        if result.returncode:
            return [f"save: {result.stderr.strip()}"]
        ours = reference(plain, flag="--reopen")
        python_saved = os.path.join(scratch, "python.scad3")
        result = reference(path, flag="--resave", extra=[python_saved])
        theirs = result if "error" in result else reference(python_saved, flag="--reopen")
        required = {"bodies": dict, "cached": bool, "complete": bool, "signatures": dict}
        _require_stages(ours, required, "Python reopening Rust Save As")
        _require_stages(theirs, required, "Python reopening Python Save As")
        for key in required:
            if ours.get(key) != theirs.get(key):
                problems.append(f"saved as is, {key}: rust {str(ours.get(key))[:200]} python {str(theirs.get(key))[:200]}")
        if native:
            reopened = _rust_json(rust, "dump", plain)
            _require_stages(reopened, _DUMP_STAGES, "Rust reopening Rust Save As")
            if _body_stage(reopened, "loaded") != theirs["bodies"]:
                problems.append("Rust Save As reopens with different bodies in Rust than Python Save As does in Python")
        # Two 1 mm pulls on the first planar face of the first body Rust shows, the second undone.
        body = next((name for name, fingerprints in loaded.get("fingerprints", {}).items()
                     if any(f["geometry"] == "plane" for f in fingerprints)), None)
        if body is None:
            return problems
        face = next(i for i, f in enumerate(loaded["fingerprints"][body]) if f["geometry"] == "plane")
        pulled = os.path.join(scratch, "pulled.scad3")
        result = _rust_json(rust, "pull", path, body, str(face), "1,1", pulled, "1")
        _require_stages(result, {"volumes": list}, "Rust pull")
        volumes = result["volumes"]
        if len(volumes) != 2 or any(type(volume) not in (float, int) for volume in volumes):
            raise ValueError("Rust pull: expected two volumes")
        reopened = reference(pulled, flag="--reopen-redo")
        _require_stages(reopened, {**required, "redone": dict, "rebuild_failed": int, "redo_failed": int},
                        "Python reopening Rust edit")
        if not (reopened["cached"] and reopened["complete"]) and theirs.get("complete"):
            problems.append("pulled: not reopened from its cache")
        got = [reopened["bodies"].get(body, {}).get("volume"), reopened["redone"].get(body, {}).get("volume")]
        if reopened["rebuild_failed"]:
            problems.append(
                "reference could not verify Redo after Rust Save As: "
                f"{reopened['rebuild_failed']} steps failed to rebuild before Redo, "
                f"{reopened['redo_failed']} after Redo"
            )
        elif got != [round(abs(v), 3) for v in volumes] or reopened["redo_failed"]:
            problems.append(f"pulled {body} face {face}: rust volumes {volumes}, python reopened then redone {got}")
    return problems


def _run(tree: str, path: str, python: str = sys.executable, flag: str = "--dump", extra=()) -> dict:
    env = dict(os.environ, PYTHONPATH=tree, PYTHONDONTWRITEBYTECODE="1",
               QT_QPA_PLATFORM="offscreen")
    result = subprocess.run(
        [python, os.path.abspath(__file__), flag, path, *extra],
        cwd=tree, env=env, capture_output=True, text=True, timeout=3600,
    )
    if result.returncode != 0:
        return {"error": result.stderr.strip().splitlines()[-1:] or ["failed"]}
    lines = result.stdout.strip().splitlines()
    if not lines:
        return {"error": f"{flag} produced no JSON"}
    return json.loads(lines[-1])


def _require_stages(stages: dict, required: dict, source: str) -> None:
    if not isinstance(stages, dict):
        raise ValueError(f"{source}: expected a JSON object")
    if "error" in stages:
        raise ValueError(f"{source}: {stages['error']}")
    for name, kind in required.items():
        value = stages.get(name)
        if type(value) is not kind or kind is int and value < 0:
            raise ValueError(f"{source}: missing or invalid {name}")


def _body_stage(stages: dict, name: str) -> dict:
    try:
        return _rounded(stages[name])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name}: invalid Rust body data") from exc


def _rust_json(rust: str, command: str, path: str, *extra: str, expected=dict) -> dict | list:
    result = subprocess.run([rust, command, path, *extra], capture_output=True, text=True, timeout=3600)
    label = " ".join((command, *extra))
    if result.returncode:
        diagnostic = result.stderr.strip() or f"exit status {result.returncode}"
        raise ValueError(f"{label}: {diagnostic}")
    try:
        stages = json.loads(result.stdout)
    except ValueError as exc:
        raise ValueError(f"{label}: invalid JSON") from exc
    if not isinstance(stages, expected):
        raise ValueError(f"{label}: expected a JSON {'object' if expected is dict else 'array'}")
    return stages


def _compare_rust(rust: str, path: str, rebuild: bool, reference) -> tuple[dict, dict]:
    """Compare the same file at every requested stage, then exercise Save As."""
    try:
        original = _rust_json(rust, "dump", path)
        _require_stages(original, _DUMP_STAGES if rebuild else {"loaded": dict}, "Rust dump")
        new = {**original, "loaded": _body_stage(original, "loaded")}
        if rebuild:
            new["displayed"] = _body_stage(original, "displayed")
            for stage, extra in (("cached", ()), ("recipe", ("--from-recipe",))):
                bodies, failures = f"{stage}_rebuild", f"{stage}_failed"
                result = _rust_json(rust, "rebuild", path, *extra)
                diagnostics = f"{stage}_diagnostics"
                _require_stages(result, {bodies: dict, failures: int, diagnostics: dict, "unported": list}, bodies)
                if result["unported"]:
                    raise ValueError(f"{bodies}: unported operations {result['unported']}")
                new[bodies], new[failures] = _body_stage(result, bodies), result[failures]
                new[diagnostics] = result[diagnostics]
            history = _rust_json(rust, "history", path)
            _require_stages(history, {**_HISTORY_STAGES, "unported": list}, "Rust history")
            if history["unported"]:
                raise ValueError(f"history: unported operations {history['unported']}")
            new.update({name: _body_stage(history, name) if name in ("undo_all", "redo_all") else history[name]
                        for name in _HISTORY_STAGES})
            required = {**_DUMP_STAGES, **_REBUILD_STAGES, **_HISTORY_STAGES}
            old = reference(path, flag="--dump")
            _require_stages(old, required, "Python dump")
            old = {name: old[name] for name in required}
        else:
            new.pop("displayed", None)
            old = reference(path, flag="--dump-loaded")
            _require_stages(old, {"loaded": dict}, "Python dump")
            old = {stage: value for stage, value in old.items() if stage in new}
        new["reopened"], old["reopened"] = _rust_saves(
            rust, path, new, native=rebuild, reference=reference,
        ), []
        return old, new
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        return {}, {"error": str(exc)}


def _align_outcome(entry: dict) -> tuple[str, str | tuple[list, str]]:
    if "error" in entry and "result" not in entry and isinstance(entry["error"], str):
        return "error", entry["error"]
    result = entry.get("result")
    if "error" in entry or not isinstance(result, dict):
        raise ValueError("expected exactly one Align result or CAD error")
    transform, description = result.get("transform"), result.get("description")
    if (not isinstance(transform, list) or len(transform) != 12
            or any(type(value) not in (float, int) or not math.isfinite(value) for value in transform)
            or not isinstance(description, str)):
        raise ValueError("invalid Align transform or description")
    return "result", (transform, description)


def _compare_align(rust: str, path: str, reference) -> tuple[int, float, list[str]]:
    entries = _rust_json(rust, "steps", path, "align", expected=list)
    actual = {}
    for entry in entries:
        if not isinstance(entry, dict) or not all(isinstance(entry.get(key), str) for key in ("id", "name")):
            raise ValueError("Rust Align step is missing a feature ID or name")
        feature_id = entry["id"]
        if not feature_id or feature_id in actual:
            raise ValueError(f"invalid or duplicate Rust Align feature ID {feature_id!r}")
        actual[feature_id] = entry
    expected = reference(path, flag="--dump-align")
    _require_stages(expected, {}, "Python Align")
    problems = []
    worst = 0.0
    ids = actual.keys() | expected.keys()
    for feature_id in sorted(ids):
        python, native = expected.get(feature_id), actual.get(feature_id)
        name = (python if python is not None else native)["name"]
        label = f"{name} [{feature_id}]"
        if python is None or native is None:
            problems.append(f"{label}: step missing from {'python' if python is None else 'rust'}")
            continue
        old_kind, old = _align_outcome(python)
        new_kind, new = _align_outcome(native)
        if old_kind == new_kind == "result":
            old_transform, old_description = old
            new_transform, new_description = new
            delta = max(abs(a - b) for a, b in zip(old_transform, new_transform, strict=True))
            worst = max(worst, delta)
            if delta <= 1e-9 and old_description == new_description:
                continue
            problems.append(f"{label}: transform delta {delta:g}, python {old_description!r}, rust {new_description!r}")
        elif old_kind != new_kind or old != new:
            problems.append(f"{label}: python {old_kind} {old!r}, rust {new_kind} {new!r}")
    return len(ids), worst, problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--old", help="checkout of the version to compare against")
    parser.add_argument("--old-python", default=sys.executable,
                        help="interpreter for --old, e.g. a venv with the previous OCCT")
    parser.add_argument("--rust", help="the Rust engine's scad tool, compared against --old or this tree")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--rebuild", action="store_true",
                      help="with --rust, require rebuild and history parity and native Save As reopen")
    mode.add_argument("--align", action="store_true", help="with --rust, compare every saved Align step by ID")
    parser.add_argument("--dump", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dump-loaded", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dump-align", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--reopen", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--reopen-redo", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--resave", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("files", nargs="+")
    args = parser.parse_args()
    if args.dump or args.dump_loaded:
        print(json.dumps(dump(args.files[0], loaded_only=args.dump_loaded)))
        return 0
    if args.dump_align:
        print(json.dumps(align_results(args.files[0])))
        return 0
    if args.reopen or args.reopen_redo:
        print(json.dumps(reopen(args.files[0], redo=args.reopen_redo)))
        return 0
    if args.resave:
        print(json.dumps({"saved": _resaved(*args.files)}))
        return 0
    if not args.old and not args.rust:
        parser.error("--old or --rust is required")
    if args.align and not args.rust:
        parser.error("--align requires --rust")
    reference_tree = os.path.abspath(args.old) if args.old else ROOT

    def reference(path, **options):
        return _run(reference_tree, path, python=args.old_python, **options)

    same = True
    matches = total = 0
    for file in args.files:
        path = os.path.abspath(file)
        name = os.path.basename(path)
        if args.align:
            try:
                steps, worst, problems = _compare_align(args.rust, path, reference)
            except (ValueError, OSError, OverflowError, subprocess.TimeoutExpired) as exc:
                same = False
                print(f"ERROR      {name}: {exc}")
                continue
            total += steps
            matches += steps - len(problems)
            if problems:
                same = False
                print(f"DIFFERENT  {name}: Align")
                for problem in problems:
                    print(f"    {problem}")
            else:
                print(f"same       {name}  ({steps} Align steps, worst transform delta {worst:g})")
            continue
        if args.rust:
            old, new = _compare_rust(args.rust, path, args.rebuild, reference)
        else:
            old, new = reference(path), _run(ROOT, path)
        differences = [stage for stage in sorted(set(old) | set(new))
                       if old.get(stage) != new.get(stage)]
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
    if args.align:
        print(f"{matches}/{total} Align outcomes matched across the projects compared")
    print("VERDICT:", "PASS" if same else "FAIL")
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())
