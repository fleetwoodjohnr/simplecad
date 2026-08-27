"""Autosave and crash recovery."""

from __future__ import annotations

import os
import time

import pytest

from simplecad.core import autosave
from simplecad.core.document import Document
from simplecad.core.rebuild import Rebuilder
from simplecad.kernel.primitives import BoxFeature


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Keep the tests out of the user's real recovery directory."""
    monkeypatch.setattr(autosave, "RECOVERY_DIR", str(tmp_path / "recovery"))
    yield


@pytest.fixture
def doc():
    document = Document("Work")
    document.add_feature(
        BoxFeature(inputs={"width": 30, "depth": 20, "height": 10}, outputs=["Box"])
    )
    Rebuilder(document).rebuild()
    return document


def test_nothing_pending_on_a_clean_machine():
    assert autosave.pending() == []


def test_autosave_writes_a_recoverable_project(doc):
    from simplecad.core.project import load

    path = autosave.write(doc, "1234")
    assert path and os.path.exists(path)

    recovered, cached = load(path)
    assert cached
    assert [f.name for f in recovered.features] == ["Box"]


def test_a_written_file_shows_up_as_pending(doc):
    autosave.write(doc, "1234")
    pending = autosave.pending()
    assert len(pending) == 1
    assert pending[0][0].endswith("session-1234.scad3")


def test_a_clean_exit_leaves_nothing_behind(doc):
    autosave.write(doc, "1234")
    autosave.clear("1234")
    assert autosave.pending() == []


def test_clearing_a_session_that_never_wrote_is_harmless():
    autosave.clear("nonexistent")     # must not raise


def test_the_newest_recovery_file_comes_first(doc):
    autosave.write(doc, "older")
    time.sleep(0.01)
    autosave.write(doc, "newer")
    paths = [path for path, _when in autosave.pending()]
    assert paths[0].endswith("session-newer.scad3")


def test_ages_are_described_in_words():
    now = time.time()
    assert "less than a minute" in autosave.describe_age(now)
    assert autosave.describe_age(now - 600) == "10 minutes ago"
    assert autosave.describe_age(now - 7200) == "2 hours ago"
    assert autosave.describe_age(now - 200000) == "2 days ago"


def test_autosave_never_raises_on_an_unwritable_location(doc, monkeypatch):
    """Autosave runs on a timer; it must never interrupt the user."""
    monkeypatch.setattr(autosave, "RECOVERY_DIR", "/proc/definitely/not/writable")
    assert autosave.write(doc, "1234") is None
