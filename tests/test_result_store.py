"""Geometry caches remain portable and bounded during long sessions."""
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
