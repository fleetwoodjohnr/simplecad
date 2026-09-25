"""Measure recomputation and save/reopen parity; timings are informational.

Run: PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B scripts/benchmark_incremental.py
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simplecad.core.document import Document
from simplecad.core.project import load, save
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.primitives import BoxFeature


def measured(function):
    start = time.perf_counter()
    result = function()
    return result, round((time.perf_counter() - start) * 1000, 3)


def benchmark(size, folder):
    document = Document()
    # Batch fixture construction, leaving interactive edits below unbatched.
    document.features = [BoxFeature(name=f"Box{i}", outputs=[f"Box{i}"],
        inputs={"width": 10, "depth": 10, "height": 10, "x": i * 20}) for i in range(size)]
    builder = Rebuilder(document)
    report, cold_ms = measured(builder.rebuild)
    assert report.ok and len(report.rebuilt) == size
    identities = {b.name: b.id for b in document.bodies.values()}
    results = {"objects": size, "cold_build_ms": cold_ms}
    path = str(folder / f"{size}.scad3")
    for mode in ("new", "reopened"):
        if mode == "reopened":
            _, results["save_ms"] = measured(lambda: save(document, path))
            (document, cached), results["load_ms"] = measured(lambda: load(path))
            assert cached and document._cache_complete
            assert identities == {b.name: b.id for b in document.bodies.values()}
            builder = Rebuilder(document)
        unchanged = document.body("Box0").shape
        document.features[-1].inputs["width"] = 11 if mode == "new" else 12
        report, edit_ms = measured(builder.rebuild)
        assert report.ok and len(report.rebuilt) == 1
        assert document.body("Box0").shape.IsSame(unchanged)
        noop, noop_ms = measured(builder.rebuild)
        assert noop.visited == 0
        results[mode] = {"edit_ms": edit_ms, "features_recomputed": len(report.rebuilt),
                         "noop_ms": noop_ms, "noop_features_visited": noop.visited}
        def append():
            feature = document.add_feature(BoxFeature(name="Additional", outputs=["Additional"]))
            report = builder.rebuild()
            assert report.ok and report.rebuilt == [feature.id]
            return feature
        feature, append_ms = measured(append)
        results[mode]["append_ms"] = append_ms
        results[mode]["append_features_recomputed"] = 1
        document.remove_feature(feature.id, splice=False)
        assert builder.rebuild().rebuilt == []
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", nargs="+", type=int, default=[100, 1000, 3000])
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="simplecad-benchmark-") as folder:
        for size in args.sizes:
            print(json.dumps(benchmark(size, Path(folder))), flush=True)
