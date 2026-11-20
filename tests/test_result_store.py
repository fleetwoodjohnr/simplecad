"""Geometry caches remain portable and bounded during long sessions."""
import sys

import pytest

from simplecad.core.document import Document
from simplecad.core.project import load, save
from simplecad.core.rebuild import Rebuilder
from simplecad.core import result_store
from simplecad.kernel.occ import volume
from simplecad.kernel.primitives import BoxFeature


def test_disk_spill_round_trip_and_eviction(monkeypatch, tmp_path):
    monkeypatch.setattr(result_store, "BLOB_BUDGET", 1)
    monkeypatch.setattr(result_store, "DECODED_BUDGET", 1)
    doc = Document()
    for i in range(3):
        doc.add_feature(BoxFeature(outputs=[f"Box{i}"], inputs={"width": i + 1, "depth": 2, "height": 3}))
    builder = Rebuilder(doc)
    assert builder.rebuild().ok
    blobs = builder.store.blobs
    assert not blobs.memory and len(blobs.disk) == 3
    paths = list(blobs.disk.values())
    assert all(path.exists() for path in paths)
    assert all(isinstance(value, bytes) for value in builder.store.export()["blobs"].values())
    reopened, cached = load(save(doc, str(tmp_path / "spilled.scad3")))
    assert cached and reopened._cache_complete
    assert Rebuilder(reopened).rebuild().rebuilt == []
    assert volume(reopened.body("Box2").shape) == 18
    for feature in list(doc.features):
        doc.remove_feature(feature.id, splice=False)
    assert builder.rebuild().ok
    assert not blobs and not builder.store.decoded
    assert all(not path.exists() for path in paths)


def test_blob_mapping_iteration_and_replacement(monkeypatch):
    monkeypatch.setattr(result_store, "BLOB_BUDGET", 4)
    blobs = result_store.BlobStore()
    blobs["a"] = b"aaa"
    blobs["b"] = b"bb"
    assert dict(blobs.items()) == {"a": b"aaa", "b": b"bb"}
    old = blobs.disk["a"]
    blobs["a"] = b"a"
    assert not old.exists()
    assert blobs.memory_bytes <= 4
    assert dict(blobs) == {"a": b"a", "b": b"bb"}


@pytest.mark.skipif(sys.platform == "win32", reason="not swept on Windows")
def test_spill_folders_a_gone_process_left_are_removed(tmp_path):
    """A killed geometry worker never deleted its spill folder: ~340 MB of a
    /tmp held in memory, every session on the Atlas Box."""
    import os
    import subprocess

    from simplecad.core.result_store import SPILL_PREFIX, sweep_spills

    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    paths = {name: tmp_path / f"{SPILL_PREFIX}{name}" for name in (
        f"{gone.pid}-abc", f"{os.getpid()}-def", f"{os.getppid()}-ghi", "legacy_old", "legacy_new")}
    for path in paths.values():
        path.mkdir()
        (path / "blob").write_bytes(b"x")
    os.utime(paths["legacy_old"], (0, 0))
    unrelated = tmp_path / "something-else"
    unrelated.mkdir()

    removed = sweep_spills(str(tmp_path), now=10 * 86400)
    assert sorted(removed) == sorted([str(paths[f"{gone.pid}-abc"]), str(paths["legacy_old"])])
    assert all(p.exists() for n, p in paths.items() if n not in (f"{gone.pid}-abc", "legacy_old"))
    assert unrelated.exists()
