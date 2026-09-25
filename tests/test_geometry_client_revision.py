"""Late geometry replies cannot roll a newer edit back."""

import time
from types import SimpleNamespace

import pytest

from simplecad.core.document import Document
from simplecad.core.geometry_service import (
    PREVIEW, PREVIEWED, PROGRESS, REBUILD, RESULT, SYNC, SYNCED,
)
from simplecad.ui.geometry_client import (
    BOOLEAN_PREVIEW_TIMEOUT, GeometryClient, PREVIEW_TIMEOUT, REPLY_TIMEOUT,
)


class Pipe:
    def __init__(self):
        self.sent = []
        self.incoming = []

    def send(self, message):
        self.sent.append(message)

    def poll(self, _timeout):
        return bool(self.incoming)

    def recv(self):
        return self.incoming.pop(0)


def test_save_captures_immutable_recipe_and_reports_worker_failure(monkeypatch, tmp_path):
    from simplecad.kernel.primitives import BoxFeature
    from simplecad.ui.geometry_client import MAX_RESTARTS
    client = GeometryClient()
    client._pipe = Pipe()
    client.available = True
    document = Document()
    feature = document.add_feature(BoxFeature(inputs={"width": 10}))
    replies = []
    client.saved.connect(replies.append)
    assert client.save(document, str(tmp_path / "snapshot.scad3"), token=42)
    feature.inputs["width"] = 99
    assert client._active_save["document"]["features"][0]["inputs"]["width"] == 10
    client._restarts = MAX_RESTARTS
    monkeypatch.setattr(client, "start", lambda: False)
    client._handle_death()
    assert not client.saving
    assert replies[0]["token"] == 42
    assert replies[0]["error"]


def test_newer_rebuild_wins_and_runs_before_queued_preview():
    client = GeometryClient()
    pipe = Pipe()
    client._pipe = pipe
    client.available = True
    completed = []
    client.finished.connect(completed.append)
    document = Document("History")

    assert client.request(document)
    assert client.preview({"type": "fillet"}, token=7)
    assert client.request(document)
    pipe.incoming.append({"kind": RESULT, "revision": 1, "bodies": {"old": b""}})
    client._poll()

    assert completed == []
    assert pipe.sent[-1]["revision"] == 2
    assert pipe.sent[-1]["full"] is True
    assert pipe.sent[-1]["kind"] == REBUILD

    pipe.incoming.append({"kind": RESULT, "revision": 2, "bodies": {}})
    client._poll()
    assert len(completed) == 1
    assert pipe.sent[-1]["kind"] == PREVIEW
    assert pipe.sent[-1]["token"] == 7


def test_stuck_preview_reports_timeout_instead_of_waiting_forever(monkeypatch):
    client = GeometryClient()
    client._pipe = Pipe()
    client.available = True
    client._preview_busy = True
    client._active_preview = {"token": 19}
    client._preview_sent_at = time.monotonic() - PREVIEW_TIMEOUT - 1
    replies = []
    client.previewed.connect(replies.append)
    monkeypatch.setattr(client, "start", lambda: False)

    client._poll()

    assert replies[0]["kind"] == PREVIEWED
    assert replies[0]["token"] == 19
    assert replies[0]["timeout"] is True
    assert "took too long" in replies[0]["error"]
    assert not client._preview_busy


def test_join_preview_gets_the_boolean_geometry_deadline(monkeypatch):
    client = GeometryClient()
    client._pipe = Pipe()
    client.available = True
    assert client.preview({"type": "boolean", "inputs": {"operation": "join"}}, token=20)
    restarted = []
    monkeypatch.setattr(client, "_handle_death", lambda **kwargs: restarted.append(kwargs))

    client._preview_sent_at = time.monotonic() - PREVIEW_TIMEOUT - 1
    client._poll()
    assert restarted == []
    assert client._preview_busy

    client._preview_sent_at = time.monotonic() - BOOLEAN_PREVIEW_TIMEOUT - 1
    client._poll()
    assert [entry["preview_timeout"] for entry in restarted] == [True]


def test_active_boolean_preview_is_not_killed_at_the_deadline(monkeypatch):
    client = GeometryClient()
    client._pipe = Pipe()
    client.available = True
    assert client.preview({"type": "boolean", "inputs": {"operation": "join"}}, token=22)
    client._preview_sent_at = time.monotonic() - BOOLEAN_PREVIEW_TIMEOUT - 1
    monkeypatch.setattr(client, "_worker_progressing", lambda: True)
    restarted = []
    monkeypatch.setattr(client, "_handle_death", lambda **kwargs: restarted.append(kwargs))

    client._poll()

    assert restarted == []
    assert client._preview_busy


@pytest.mark.parametrize("operation", ["join", "cut"])
def test_crashing_preview_is_reported_once_and_new_request_can_run(monkeypatch, operation):
    client = GeometryClient()
    client._pipe = Pipe()
    client.available = True
    assert client.preview({"type": "boolean", "inputs": {"operation": operation}}, token=23)
    replacement = Pipe()
    replies = []
    client.previewed.connect(replies.append)

    def restart():
        client._pipe = replacement
        client.available = True
        return True

    monkeypatch.setattr(client, "start", restart)
    client._handle_death()

    assert len(replies) == 1
    assert replies[0]["token"] == 23
    assert replies[0]["retryable"] is False
    assert replacement.sent == []
    assert client.preview({"type": "boolean", "inputs": {"operation": operation}}, token=24)
    assert replacement.sent[-1]["token"] == 24
    assert client._restarts == 0


def test_crashing_rebuild_is_not_replayed_after_worker_restart(monkeypatch):
    client = GeometryClient()
    client._pipe = Pipe()
    client.available = True
    document = Document("Failed edit")
    assert client.request(document)
    replacement = Pipe()
    failures = []
    client.failed.connect(failures.append)

    def restart():
        client._pipe = replacement
        client.available = True
        return True

    monkeypatch.setattr(client, "start", restart)
    client._handle_death()

    assert replacement.sent == []
    assert len(failures) == 1
    assert "last valid model" in failures[0]
    assert client.request(document)
    assert replacement.sent[-1]["kind"] == REBUILD


def test_explicit_start_clears_exhausted_automatic_restart_budget(monkeypatch):
    from simplecad.ui import geometry_client

    client = GeometryClient()
    client._restarts = geometry_client.MAX_RESTARTS
    def unavailable():
        raise RuntimeError("unavailable")

    monkeypatch.setattr(geometry_client, "_process_context", unavailable)

    assert not client.start()
    assert client._restarts == 0


def test_ready_preview_reply_wins_over_elapsed_timeout(monkeypatch):
    client = GeometryClient()
    pipe = Pipe()
    client._pipe = pipe
    client.available = True
    assert client.preview({"type": "fillet"}, token=21)
    client._preview_sent_at = time.monotonic() - PREVIEW_TIMEOUT - 1
    pipe.incoming.append({"kind": PREVIEWED, "token": 21, "shape": b"ready"})
    restarted = []
    replies = []
    monkeypatch.setattr(client, "_handle_death", lambda **kwargs: restarted.append(kwargs))
    client.previewed.connect(replies.append)

    client._poll()
    assert restarted == []
    assert replies == [{"kind": PREVIEWED, "token": 21, "shape": b"ready"}]


def test_cached_sync_finishes_before_a_texture_or_join_preview():
    client = GeometryClient()
    pipe = Pipe()
    client._pipe = pipe
    client.available = True

    assert client.sync_cached(Document("Opened"))
    assert pipe.sent[0]["kind"] == SYNC
    assert client.preview({"type": "face_texture"}, token=14)
    assert len(pipe.sent) == 1

    pipe.incoming.append({"kind": SYNCED, "revision": 1})
    client._poll()
    assert pipe.sent[-1]["kind"] == PREVIEW
    assert pipe.sent[-1]["token"] == 14


def test_opening_another_project_supersedes_old_rebuild():
    client = GeometryClient()
    pipe = Pipe()
    client._pipe = pipe
    client.available = True
    completed = []
    client.finished.connect(completed.append)

    assert client.request(Document("Old"))
    assert client.sync_cached(Document("New"))
    pipe.incoming.append({"kind": RESULT, "revision": 1, "bodies": {}})
    client._poll()
    assert completed == []
    assert pipe.sent[-1]["kind"] == SYNC
    assert pipe.sent[-1]["document"]["title"] == "New"


def test_restart_replays_cached_document_before_new_preview(monkeypatch):
    client = GeometryClient()
    client._pipe = Pipe()
    client.available = True
    assert client.sync_cached(Document("Opened"))
    replacement = Pipe()

    def restart():
        client._pipe = replacement
        client.available = True
        return True

    monkeypatch.setattr(client, "start", restart)
    client._handle_death(preview_timeout=True)
    assert replacement.sent[0]["kind"] == SYNC
    assert client.preview({"type": "join"}, token=18)
    assert len(replacement.sent) == 1

    replacement.incoming.append({"kind": SYNCED, "revision": 1})
    client._poll()
    assert replacement.sent[-1]["kind"] == PREVIEW
    assert replacement.sent[-1]["token"] == 18


def test_failed_sync_rebuilds_once_without_retry_loop():
    from simplecad.ui.main_window import MainWindow

    geometry = SimpleNamespace(available=True, busy=False, latest_revision=1)
    attempts = []
    window = SimpleNamespace(
        geometry=geometry, _geometry_recovery_revision=0,
        _cancel_nudge=lambda: None, set_hint=lambda _text: None,
    )

    def rebuild(*, force):
        attempts.append(force)
        geometry.latest_revision += 1

    window.rebuild = rebuild
    MainWindow._on_geometry_failed(window, "sync failed")
    MainWindow._on_geometry_failed(window, "rebuild failed")
    assert attempts == [True]


def _client(available=True):
    client = GeometryClient()
    client._pipe = Pipe()
    client.available = available
    return client


def test_a_dead_worker_answers_every_preview_it_was_holding(monkeypatch):
    """A panel waits on one signal. Dropping its request silently wedges it.

    The status line is written when the request goes out and rewritten only
    when a reply comes back. A worker that died mid-boolean used to take the
    request with it, leaving the Join panel on "Building the preview…" with
    Apply disabled for the rest of the session.
    """
    client = _client()
    client._preview_busy = True
    client._active_preview = {"token": 31}
    client._pending_preview = {"token": 32}
    replies = []
    client.previewed.connect(replies.append)
    monkeypatch.setattr(client, "start", lambda: False)

    client._handle_death()

    assert [reply["token"] for reply in replies] == [31, 32]
    assert all(reply["shape"] is None and reply["error"] for reply in replies)
    assert not client._preview_busy and client._pending_preview is None


def test_a_preview_that_could_not_be_sent_is_answered_not_dropped(monkeypatch):
    client = _client()
    client._pending_preview = {"kind": PREVIEW, "feature": {}, "token": 41}
    replies = []
    client.previewed.connect(replies.append)
    monkeypatch.setattr(client, "_send_preview", lambda _payload: False)
    monkeypatch.setattr(client, "_handle_death", lambda **_kwargs: None)

    client._send_next()

    assert [reply["token"] for reply in replies] == [41]
    assert replies[0]["retryable"] is False


def test_opening_a_project_answers_the_preview_it_discards():
    client = _client()
    client._pending_preview = {"kind": PREVIEW, "feature": {}, "token": 51}
    replies = []
    client.previewed.connect(replies.append)

    assert client.sync_cached(Document("Reopened"))

    assert [reply["token"] for reply in replies] == [51]
    assert "reloaded" in replies[0]["error"]


def test_a_late_reply_for_an_abandoned_preview_is_ignored():
    """Once a panel has been told its preview failed, do not tell it otherwise.

    Believing a reply that arrives after we gave up on it would enable Apply
    for a preview the user was just told could not be built.
    """
    client = _client()
    client._preview_busy = True
    client._active_preview = {"token": 61}
    failures = []
    client.previewed.connect(failures.append)
    client._fail_pending_previews("The engine let go of this one.")
    assert [reply["token"] for reply in failures] == [61]

    client._pipe.incoming.append({"kind": PREVIEWED, "token": 61, "shape": b"late"})
    client._poll()

    assert len(failures) == 1
    assert 61 not in client._abandoned_previews


def test_progress_keeps_a_long_rebuild_alive_and_silence_still_kills_it(monkeypatch):
    """The deadline measures silence, not duration.

    A 98-feature model measured 222 s of honest work here against a 120 s
    deadline, so it was killed, resent, killed again, and finally rebuilt on
    the GUI thread.
    """
    client = _client()
    monkeypatch.setattr(client, "_worker_progressing", lambda: False)
    assert client.request(Document("Long"))
    now = [1000.0]
    monkeypatch.setattr("simplecad.ui.geometry_client.time.monotonic", lambda: now[0])
    client._sent_at = now[0]

    now[0] += REPLY_TIMEOUT + 10
    assert client._overdue()

    client._pipe.incoming.append(
        {"kind": PROGRESS, "revision": 1, "feature": "Fillet", "index": 3, "total": 98}
    )
    seen = []
    client.progress.connect(seen.append)
    client._poll()
    assert seen and seen[0]["feature"] == "Fillet"
    assert client._busy, "progress is proof of life, not an answer"
    assert not client._overdue()

    now[0] += REPLY_TIMEOUT + 1
    assert client._overdue()


def test_a_rebuild_queued_behind_a_preview_still_says_it_started():
    client = _client()
    assert client.preview({"type": "boolean"}, token=71)
    started = []
    client.started.connect(lambda: started.append(True))

    assert client.request(Document("Edit"))

    assert started == [True]


def test_cancel_active_preview_restores_checkpoint_before_latest_edit(monkeypatch):
    client = _client()
    document = Document("Current")
    assert client.sync_cached(document)
    client._pipe.incoming.append({"kind": SYNCED, "revision": 1})
    client._poll()
    assert client.preview({"type": "boolean"}, token=81)
    assert client.request(document)
    replacement = Pipe()

    def restart():
        client._pipe = replacement
        client.available = True
        return True

    monkeypatch.setattr(client, "start", restart)
    failures = []
    client.previewed.connect(failures.append)
    client.cancel_preview(81)
    assert client._restarts == 0
    assert failures[0]["retryable"] is False
    assert replacement.sent[0]["kind"] == SYNC
    replacement.incoming.append({"kind": SYNCED, "revision": 1})
    client._poll()
    assert replacement.sent[-1]["kind"] == REBUILD
    assert replacement.sent[-1]["revision"] == 2


def test_timeout_does_not_automatically_retry_after_successful_restart(monkeypatch):
    client = _client()
    assert client.preview({"type": "boolean"}, token=82)

    def restart():
        client._pipe = Pipe()
        client.available = True
        return True

    monkeypatch.setattr(client, "start", restart)
    failures = []
    client.previewed.connect(failures.append)
    client._handle_death(preview_timeout=True)
    assert failures[0]["timeout"] and not failures[0]["retryable"]
    assert client._pending_preview is None


def test_preview_from_an_older_document_revision_is_not_published():
    client = _client()
    assert client.preview({"type": "boolean"}, token=83)
    assert client.request(Document("Edited"))
    replies = []
    client.previewed.connect(replies.append)
    client._pipe.incoming.append({"kind": PREVIEWED, "token": 83, "revision": 0, "shape": b"obsolete"})
    client._poll()
    assert len(replies) == 1
    assert replies[0]["shape"] is None and replies[0]["retryable"]
    assert client._pipe.sent[-1]["kind"] == REBUILD


def test_startup_queues_edits_without_blocking_until_ready():
    from simplecad.core.geometry_service import READY
    client = _client()
    client._starting = True
    assert client.request(Document("Startup"))
    assert client._pipe.sent == []
    client._pipe.incoming.append({"kind": READY})
    client._poll()
    assert not client._starting
    assert client._pipe.sent[-1]["kind"] == REBUILD
