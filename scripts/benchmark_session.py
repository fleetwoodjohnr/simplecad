"""Time a modelling session through the real client -> worker -> apply path.

``benchmark_incremental.py`` measures the rebuild engine alone. This drives
what the window actually does per edit: record history, send the request,
wait for the geometry process, merge its reply into the document. It reports
wall time per operation, GUI-thread time (request + reply handling, which is
what freezes the window), and bytes crossing the pipe.

Run:
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B scripts/benchmark_session.py
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B scripts/benchmark_session.py \
        --project "~/Desktop/Atlas/UPS Atlas.scad3" --body Hexagon

Works against older checkouts too (moves are appended where folding is not
available), so the same script gives before/after numbers.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import pickle
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from simplecad import kernel  # noqa: E402,F401 - registers feature types
from simplecad.core.document import BodyRef, Document  # noqa: E402
from simplecad.core.history import History  # noqa: E402
from simplecad.core.project import load, save  # noqa: E402
from simplecad.core.result_store import ResultStore  # noqa: E402
from simplecad.kernel.operations import BooleanFeature, MoveFeature  # noqa: E402
from simplecad.kernel.primitives import BoxFeature, CylinderFeature  # noqa: E402
from simplecad.ui import geometry_client as client_module  # noqa: E402
from simplecad.ui.geometry_client import GeometryClient, apply_result  # noqa: E402


class Session:
    """A headless stand-in for MainWindow's rebuild loop, instrumented."""

    def __init__(self, document):
        self.document = document
        if document._geometry_store is None:
            document._geometry_store = ResultStore()
        self.history = History(document)
        self.client = GeometryClient()
        self.gui = 0.0
        self.reply_bytes = 0
        self.located = 0
        self.replies = []
        self.client.finished.connect(self._finished)
        poll = self.client._poll

        def timed_poll():
            start = time.perf_counter()
            try:
                poll()
            finally:
                self.gui += time.perf_counter() - start
        self.client._poll = timed_poll
        self.client._timer.timeout.disconnect()
        self.client._timer.timeout.connect(timed_poll)
        assert self.client.start(), "geometry process did not start"

    def _finished(self, message):
        self.reply_bytes += len(pickle.dumps(message, protocol=pickle.HIGHEST_PROTOCOL))
        self.located += len(message.get("located") or {})
        apply_result(self.document, message)
        self.replies.append(message)

    def rebuild(self, stale=()):
        start = time.perf_counter()
        self.client.request(self.document, stale=stale)
        self.gui += time.perf_counter() - start
        assert self.client.wait(600_000), "rebuild timed out"

    def measure(self, action):
        self.gui, self.reply_bytes, self.located = 0.0, 0, 0
        start = time.perf_counter()
        action()
        return {"wall_ms": (time.perf_counter() - start) * 1000,
                "gui_ms": self.gui * 1000, "reply_kb": self.reply_bytes / 1024,
                "located": self.located}

    def move(self, name, dx):
        self.history.record("Move")
        fold = getattr(self.document, "fold_translation", None)
        if fold is None or not fold([name], (dx, 0.0, 0.0)):
            self.document.add_feature(MoveFeature(
                inputs={"body": BodyRef(name), "dx": dx, "dy": 0, "dz": 0}, outputs=[name]))
        self.rebuild()

    def undo(self):
        self.rebuild(self.history.undo() or ())

    def redo(self):
        self.rebuild(self.history.redo() or ())

    def stop(self):
        self.client.stop()


def summarise(samples):
    keys = samples[0].keys()
    return {k: round(statistics.median(s[k] for s in samples), 1) for k in keys}


def synthetic(size):
    document = Document("Bench")
    features = []
    for i in range(size):
        features.append(BoxFeature(name=f"Box{i}", outputs=[f"Box{i}"],
                                   inputs={"width": 10, "depth": 10, "height": 10, "x": i * 20}))
    document.features = features
    # A boolean every tenth body, so the history holds real kernel work that
    # an unrelated edit must not repeat.
    for i in range(0, size, 10):
        document.add_feature(CylinderFeature(name=f"Pin{i}", outputs=[f"Pin{i}"],
                             inputs={"radius": 2, "height": 20, "x": i * 20 + 5, "y": 5, "z": -5}))
        document.add_feature(BooleanFeature(inputs={
            "body": BodyRef(f"Box{i}"), "tool": BodyRef(f"Pin{i}"), "operation": "cut"},
            outputs=[f"Box{i}"]))
    return document


def run_session(session, body, repeats, label):
    out = {"case": label}
    session.measure(lambda: session.rebuild())
    moves = [session.measure(lambda: session.move(body, 1.0)) for _ in range(repeats)]
    out["move"] = summarise(moves)
    out["features_after_moves"] = len(session.document.features)
    out["undo"] = summarise([session.measure(session.undo) for _ in range(repeats)])
    out["redo"] = summarise([session.measure(session.redo) for _ in range(repeats)])
    # Long history: many unrelated actions, then undo/redo cost again.
    for i in range(repeats * 5):
        session.move(body, 0.5 if i % 2 else -0.5)
    out["undo_after_long_history"] = summarise([session.measure(session.undo) for _ in range(repeats)])
    out["redo_after_long_history"] = summarise([session.measure(session.redo) for _ in range(repeats)])

    def select():
        document = session.document
        document.any_thread()
        for name in list(document.bodies)[:50]:
            document.threads_on(name)
    out["selection_predicates_ms"] = round(session.measure(select)["wall_ms"], 2)
    return out


def run_synthetic(size, repeats):
    document = synthetic(size)
    session = Session(document)
    try:
        out = run_session(session, "Box1", repeats, f"synthetic {size} bodies")

        def subtract():
            session.history.record("Subtract")
            session.document.add_feature(CylinderFeature(name="Cutter", outputs=["Cutter"],
                                         inputs={"radius": 2, "height": 20, "x": 25, "y": 5, "z": -5}))
            session.document.add_feature(BooleanFeature(inputs={
                "body": BodyRef("Box1"), "tool": BodyRef("Cutter"), "operation": "cut"},
                outputs=["Box1"]))
            session.rebuild()
        out["subtract"] = summarise([session.measure(subtract)])

        def join():
            session.history.record("Join")
            session.document.add_feature(BooleanFeature(inputs={
                "body": BodyRef("Box3"), "tool": BodyRef("Box4"), "operation": "fuse"},
                outputs=["Box3"]))
            session.rebuild()
        out["join"] = summarise([session.measure(join)])

        def edit_parameter():
            session.history.record("Edit")
            feature = next(f for f in session.document.features if f.name == "Box7")
            feature.inputs["height"] = 14
            session.rebuild()
        out["edit_one_feature"] = summarise([session.measure(edit_parameter)])
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "bench.scad3")
            start = time.perf_counter()
            save(session.document, path)
            out["save_ms"] = round((time.perf_counter() - start) * 1000, 1)
            start = time.perf_counter()
            load(path)
            out["open_ms"] = round((time.perf_counter() - start) * 1000, 1)
        return out
    finally:
        session.stop()


def run_project(path, body, repeats):
    start = time.perf_counter()
    document, _cached = load(path)
    load_ms = (time.perf_counter() - start) * 1000
    session = Session(document)
    try:
        session.gui = 0.0
        session.client.sync_cached(document)
        assert session.client.wait(600_000)
        body = body or next(iter(document.bodies))
        out = run_session(session, body, repeats, f"{Path(path).name} ({len(document.features)} features)")
        out["open_ms"] = round(load_ms, 1)
        return out
    finally:
        session.stop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", default="100,500")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--project")
    parser.add_argument("--body")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    QApplication.instance() or QApplication([])
    client_module.log.setLevel("ERROR")
    results = []
    if args.project:
        results.append(run_project(os.path.expanduser(args.project), args.body, args.repeats))
    else:
        for size in (int(s) for s in args.sizes.split(",") if s):
            results.append(run_synthetic(size, args.repeats))
    print(json.dumps(results, indent=1) if args.json else "\n".join(json.dumps(r) for r in results))


if __name__ == "__main__":
    main()
