"""The parent's side of the geometry process.

Spawns the child, sends rebuild requests, and polls for replies on a timer. A
timer rather than a blocking read on a thread: ``poll(0)`` is cheap and costs the
UI nothing, and it keeps every Qt object on the main thread where it belongs.

Startup and recovery are asynchronous. A failed worker leaves the last valid
model visible; heavy kernel work never falls back onto the GUI thread.
"""

from __future__ import annotations

import logging
import copy
import multiprocessing
import os
import time

from PySide6.QtCore import QObject, QTimer, Signal

from ..core.geometry_service import (
    FAILED, PREVIEW, PREVIEWED, PROGRESS, READY, REBUILD, RESULT, SHUTDOWN,
    SYNC, SYNCED, body_from_wire, child_main, location_from_wire, serialise_shape,
)

log = logging.getLogger("simplecad.geometry")


def _process_context():
    """Pick a start method that works however SimpleCAD was launched.

    ``forkserver`` is preferred: its server is a fresh interpreter, so children
    are forked from something clean rather than from a process that already has
    Qt and an OpenGL context open. ``spawn`` re-executes ``__main__``, which
    breaks when the app is run from stdin or an embedded interpreter. Plain
    ``fork`` is the last resort -- it duplicates the GL context, which is asking
    for trouble, but a working fallback beats no geometry process at all.
    """
    for method in ("forkserver", "spawn", "fork"):
        try:
            context = multiprocessing.get_context(method)
        except ValueError:
            continue
        if method == "forkserver":
            try:
                context.set_forkserver_preload(
                    ["simplecad.core.geometry_service"]
                )
            except Exception:  # noqa: BLE001 - preloading is only a speed-up
                pass
        return context
    return multiprocessing.get_context()

#: How often to check the pipe, in milliseconds.
POLL_INTERVAL = 16

#: How long a rebuild may go unanswered before the child is treated as gone.
#: A modelled thread is seconds of OCCT and an imported mesh can be more, so
#: this is generous; the point is that a child which will *never* answer stops
#: the model dead for ever, with nothing said, and that is worse than a
#: false alarm. See :meth:`GeometryClient._poll`.
REPLY_TIMEOUT = 120.0
PREVIEW_TIMEOUT = 8.0
#: Booleans over imported or densely perforated bodies can legitimately take
#: longer than an interactive fillet/drag preview. They perform the same kernel
#: work as a rebuild, so give them the rebuild deadline.
BOOLEAN_PREVIEW_TIMEOUT = REPLY_TIMEOUT

#: How many times to bring the child back before giving up on it. A shape that
#: reliably kills OCCT would otherwise spawn children for as long as the user
#: keeps dragging.
MAX_RESTARTS = 3

#: How often the child's CPU time may be sampled, in seconds. A last-resort
#: liveness check for a worker that is plainly working but has not reached the
#: next feature boundary yet -- one OCCT call can run for minutes.
LIVENESS_INTERVAL = 2.0

#: How many answered-in-absentia preview tokens to remember, so a reply that
#: arrives after we gave up on it is discarded rather than believed.
MAX_ABANDONED_TOKENS = 64


class GeometryClient(QObject):
    """Runs rebuilds out of process and reports results back on the UI thread."""

    started = Signal()
    finished = Signal(object)          # the report dict
    failed = Signal(str)
    previewed = Signal(object)         # {"token": ..., "shape": bytes | None}
    saved = Signal(object)
    progress = Signal(object)          # {"feature": ..., "index": ..., "total": ...}

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._process = None
        self._pipe = None
        self._busy = False
        self._pending: dict | None = None
        self._pending_saves = []
        self._active_save = None
        # Previews travel on the same pipe but are tracked apart from rebuilds:
        # a dragged fillet radius must never displace a real edit, and a preview
        # reply must never be mistaken for one.
        self._preview_busy = False
        self._pending_preview: dict | None = None
        self._active_preview: dict | None = None
        self._preview_sent_at: float | None = None
        self._sent_at: float | None = None
        self._progress_at: float | None = None
        self._abandoned_previews: list = []
        self._cpu_sampled_at: float | None = None
        self._cpu_seconds: float | None = None
        self._revision = 0
        self._latest_revision = 0
        self._latest_document: dict | None = None
        self._restarts = 0
        self._starting = False
        self._startup_at = 0.0
        self._startup_timeout = 20.0
        self._generation = 0
        self._document_identity = None
        self._checkpoint = None
        self._published_bodies = {}
        self._restore = None
        self._active_document = None
        self._retired = []
        self._known_fragments = set()
        self._wire_state = None
        self._wire_revision = 0
        self._worker_blobs = set()
        self._worker_entries = set()
        self._worker_assets = set()
        self.available = False

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL)
        self._timer.timeout.connect(self._poll)

    # -- lifecycle -------------------------------------------------------
    def start(self, timeout: float = 20.0) -> bool:
        """Spawn the geometry process. Returns False if it could not start."""
        # A fresh user request may recover after the automatic restart budget
        # was spent. _handle_death preserves its own count across this call.
        self._restarts = 0
        try:
            context = _process_context()
            parent_end, child_end = context.Pipe(duplex=True)
            process = context.Process(
                target=child_main, args=(child_end,), daemon=True,
                name="simplecad-geometry",
            )
            process.start()
            child_end.close()
        except Exception as exc:  # noqa: BLE001 - fall back, never fail startup
            log.warning("geometry process unavailable (%s)", exc)
            self.available = False
            return False

        self._process = process
        self._pipe = parent_end
        self.available = True
        self._starting = True
        self._startup_at = time.monotonic()
        self._startup_timeout = timeout
        self._known_fragments.clear()
        self._wire_state = None
        self._worker_blobs.clear()
        self._worker_entries.clear()
        self._worker_assets.clear()
        if self._restore is None and self._checkpoint is not None:
            self._restore = self._checkpoint_payload()
        self._abandoned_previews.clear()
        self._cpu_sampled_at = None
        self._cpu_seconds = None
        self._timer.start()
        log.info("geometry process starting (pid %s)", process.pid)
        return True

    def stop(self) -> None:
        self._timer.stop()
        self._pending_saves.clear()
        self._active_save = None
        # Cleared here, not just on a reply. A stopped client that still reports
        # busy answers every later rebuild by parking it in ``_pending`` and
        # returning True, so the caller believes it succeeded and the model
        # never changes again -- silently, for the rest of the session.
        self._busy = False
        self._starting = False
        self._restore = None
        self._pending = None
        # Released without announcing it: the window is on its way out, and a
        # signal into a panel that is already being destroyed helps nobody.
        self._release_previews()
        self._sent_at = None
        self._progress_at = None
        if self._pipe is not None:
            try:
                self._pipe.send({"kind": SHUTDOWN})
            except (OSError, BrokenPipeError, ValueError):
                pass
        if self._process is not None:
            if self._process.is_alive():
                self._process.terminate()
            self._process.join(timeout=0.1)
        for process in self._retired:
            process.join(timeout=0.1)
        self._retired.clear()
        self._process = None
        self._pipe = None
        self.available = False

    @property
    def busy(self) -> bool:
        return self._busy or self._pending is not None or self._starting

    @property
    def latest_revision(self) -> int:
        return self._latest_revision

    # -- requests --------------------------------------------------------
    def reset_document(self, document) -> None:
        """Detach old work immediately, including opening an empty document."""
        self._use_document(document)
        self._revision += 1
        self._latest_revision = self._revision
        self._latest_document = None
        self._pending = None
        if self._busy or self._preview_busy:
            self._handle_death(cancelled=True)

    def _use_document(self, document) -> None:
        if document is self._document_identity:
            return
        self._document_identity = document
        self._known_fragments.clear()
        self._wire_state = None
        self._worker_blobs.clear()
        self._worker_entries.clear()
        self._worker_assets.clear()
        self._generation += 1
        self._checkpoint = None
        self._published_bodies = {}
        self._restore = None

    def sync_cached(self, document) -> bool:
        """Give a fresh worker the shapes already loaded from a project file.

        This is queued like a rebuild, so a preview cannot overtake it. The
        worker acknowledges it without rebuilding, leaving the instant-open
        cache useful while making Texture and Join previews possible.
        """
        if not self.available or self._pipe is None:
            return False
        self._use_document(document)
        try:
            bodies = {
                name: serialise_shape(body.shape)
                for name, body in document.bodies.items()
                if body.shape is not None
            }
        except Exception as exc:  # noqa: BLE001 - rebuild in the worker instead
            log.warning("could not transfer cached geometry (%s); rebuilding", exc)
            return self.request(document, force=True)
        if len(bodies) != len(document.bodies):
            return self.request(document, force=True)
        self._revision += 1
        payload = {
            "kind": SYNC, "document": document.to_dict(), "bodies": bodies,
            "cache": document._geometry_store.export(self._worker_blobs, self._worker_entries)
                     if document._geometry_store else None,
            "assets": {sha: document._all_assets[sha] for sha in document.active_asset_keys()
                       if sha in document._all_assets},
            "revision": self._revision,
            "generation": self._generation,
            "fragments": copy.deepcopy(getattr(document, "_geometry_fragments", [])),
        }
        self._latest_revision = self._revision
        self._latest_document = payload
        self._checkpoint = self._lightweight_checkpoint(payload)
        self._published_bodies = dict(bodies)
        self._fail_pending_previews(
            "The project was reloaded before this preview finished.",
            retryable=False, active=False,
        )
        if self._starting or self._busy or self._preview_busy or self._pending is not None:
            self._pending = payload
            return True
        if self._send(payload):
            return True
        self._handle_death()
        return self.available and self._busy

    def request(self, document, stale=None, force: bool = False) -> bool:
        """Ask for a rebuild. Returns False if the worker is unavailable."""
        if not self.available or self._pipe is None:
            return False
        self._restarts = 0
        self._use_document(document)
        payload = {
            "kind": REBUILD,
            "document": document.to_dict(),
            "cache": document._geometry_store.export(self._worker_blobs, self._worker_entries)
                     if document._geometry_store else None,
            "assets": {sha: document._all_assets[sha] for sha in document.active_asset_keys()
                       if sha in document._all_assets},
            "stale": sorted(stale or ()),
            # What the window actually has on screen. History can remove a
            # body whose producer survives -- undoing a step recorded while an
            # earlier rebuild was still in flight -- and the child must then
            # send it again rather than assume the window still holds it.
            "held": sorted(name for name, body in document.bodies.items()
                           if body.shape is not None),
            "retained_cache_keys": sorted(document._history.cache_keys()) if getattr(document, "_history", None) else [],
            "force": force,
            "revision": self._revision + 1,
            "generation": self._generation,
            "fragments": copy.deepcopy(getattr(document, "_geometry_fragments", [])),
        }
        self._revision += 1
        self._latest_revision = self._revision
        self._latest_document = payload
        if self._starting or self._busy or self._preview_busy or self._pending is not None:
            # Coalesce: one more rebuild after the current one, not a queue of
            # them. Dragging a dimension settles instead of piling up.
            self._pending = payload
            # Said here as well as in ``_send``: an edit queued behind a long
            # boolean preview used to look like it had done nothing at all.
            self.started.emit()
            return True
        return self._send(payload)

    @property
    def saving(self):
        return self._active_save is not None or bool(self._pending_saves)

    def save(self, document, path, *, thumbnail=None, token=None):
        """Queue an immutable recipe and history snapshot behind current work."""
        from ..core.geometry_service import SAVE
        if not self.available and not self.start():
            return False
        self._use_document(document)
        history = getattr(document, "_history", None)
        payload = {"kind": SAVE, "document": document.to_dict(), "path": path,
                   "history": history.to_dict() if history else document._history_data,
                   "thumbnail": thumbnail, "token": token, "bodies": dict(self._published_bodies),
                   "generation": self._generation, "revision": self._latest_revision,
                   "cache": document._geometry_store.export() if document._geometry_store else None}
        needed_assets = set(payload["document"].get("assets", []))
        for stack in ("undo", "redo"):
            for action in payload["history"].get(stack, []):
                needed_assets.update(action.get("patch", {}).get("values", {}).get("assets", []))
        payload["assets"] = {sha: document._all_assets[sha] for sha in needed_assets
                             if sha in document._all_assets}
        self._pending_saves.append(copy.deepcopy(payload))
        self._send_next()
        return True

    def preview(self, feature_state: dict, token: object = None) -> bool:
        """Ask what *feature_state* would build, without changing anything.

        This exists so OCCT's fillet solver stops running on the GUI thread.
        ``BRepFilletAPI_MakeFillet::Build`` does not always raise on geometry it
        cannot blend -- twice on this machine it dereferenced a null curve
        adaptor and took the whole window with it, from inside a Qt signal
        handler, where no ``except`` can help. Out here a crash costs the child
        and a restart.

        Coalesced separately from rebuilds: a dragged handle asks many times a
        second and only the latest answer is wanted, but a real edit queued
        behind it must not be dropped.
        """
        if not self.available or self._pipe is None:
            return False
        self._restarts = 0
        payload = {"kind": PREVIEW, "feature": copy.deepcopy(feature_state), "token": token,
                   "revision": self._latest_revision, "generation": self._generation}
        if self._starting or self._preview_busy or self._busy or self._pending is not None:
            if self._pending_preview is not None:
                self._announce_lost_previews([self._pending_preview.get("token")], "Preview superseded.", retryable=False)
            self._pending_preview = payload
            return True
        return self._send_preview(payload)

    def _send_preview(self, payload: dict) -> bool:
        try:
            self._pipe.send(payload)
        except (OSError, BrokenPipeError, ValueError) as exc:
            log.warning("geometry process went away (%s)", exc)
            self.available = False
            return False
        self._preview_busy = True
        self._active_preview = payload
        self._preview_sent_at = time.monotonic()
        self._progress_at = None
        self._cpu_sampled_at = self._cpu_seconds = None
        return True

    def _send(self, payload: dict) -> bool:
        from ..core.changes import make_patch
        wire = dict(payload)
        if payload["kind"] == REBUILD and self._wire_state is not None:
            wire["patch"] = make_patch(self._wire_state, payload["document"])
            wire["base_revision"] = self._wire_revision
            wire.pop("document", None)
        if payload.get("cache"):
            cache = payload["cache"]
            wire["cache"] = {"version": cache["version"],
                "entries": {k: v for k, v in cache["entries"].items() if k not in self._worker_entries},
                "blobs": {k: v for k, v in cache["blobs"].items() if k not in self._worker_blobs}}
        wire["assets"] = {k: v for k, v in payload.get("assets", {}).items()
                          if k not in self._worker_assets}
        wire["fragments"] = [
            fragment for fragment in payload.get("fragments", [])
            if payload["kind"] == SYNC or fragment["features"][-1]["id"] not in self._known_fragments
        ]
        try:
            self._pipe.send(wire)
        except (OSError, BrokenPipeError, ValueError) as exc:
            log.warning("geometry process went away (%s)", exc)
            self.available = False
            return False
        self._known_fragments.update(f["features"][-1]["id"] for f in wire["fragments"])
        self._wire_state = payload["document"]
        self._wire_revision = payload["revision"]
        if wire.get("cache"):
            self._worker_entries.update(wire["cache"]["entries"])
            self._worker_blobs.update(wire["cache"]["blobs"])
        self._worker_assets.update(wire.get("assets", {}))
        self._busy = True
        self._active_document = payload
        self._sent_at = time.monotonic()
        self._progress_at = None
        self._cpu_sampled_at = None
        self._cpu_seconds = None
        if payload["kind"] == REBUILD:
            self.started.emit()
        return True

    # -- replies ---------------------------------------------------------
    def _poll(self) -> None:
        for process in list(self._retired):
            if not process.is_alive():
                process.join(timeout=0)
                self._retired.remove(process)
        if self._pipe is None:
            return
        try:
            if not self._pipe.poll(0):
                if self._process is not None and not self._process.is_alive():
                    self._handle_death()
                elif self._starting and time.monotonic() - self._startup_at > self._startup_timeout:
                    self._handle_death()
                elif self._drop_stuck_preview():
                    return
                elif self._overdue():
                    log.error(
                        "geometry process has not answered in %.0fs; "
                        "treating it as gone", REPLY_TIMEOUT,
                    )
                    self._handle_death()
                return
            message = self._pipe.recv()
        except (EOFError, OSError, ValueError):
            self._handle_death()
            return

        kind = message.get("kind")
        if kind == "saved":
            self._busy = False
            self._active_save = None
            self._sent_at = self._progress_at = None
            self.saved.emit(message)
            self._send_next()
            return
        if kind == READY:
            self._starting = False
            self._send_next()
            return
        if kind == PROGRESS:
            # Proof of life, not an answer: ``_busy`` stays set, but the clock
            # the death check reads starts again.
            self._progress_at = time.monotonic()
            self.progress.emit(message)
            return

        if kind == PREVIEWED:
            # Deliberately does not clear ``_busy``: a preview can be answered
            # while a rebuild is still in flight, and crediting the rebuild with
            # this reply would let the next one overwrite it.
            token = message.get("token")
            if token in self._abandoned_previews:
                self._abandoned_previews.remove(token)
                self._send_next()
                return
            if self._active_preview and token != self._active_preview.get("token"):
                return
            self._preview_busy = False
            self._active_preview = None
            self._preview_sent_at = None
            self._restarts = 0
            if (message.get("revision", self._latest_revision) == self._latest_revision
                    and message.get("generation", self._generation) == self._generation):
                self.previewed.emit(message)
            else:
                self._announce_lost_previews([token], "The model changed; building a current preview.", retryable=True)
            self._send_next()
            return

        if kind == SYNCED:
            self._busy = False
            self._sent_at = None
            self._progress_at = None
            self._send_next()
            return

        self._busy = False
        self._sent_at = None
        self._progress_at = None
        if kind == RESULT:
            self._worker_entries.update(message.get("cache", {}).get("entries", {}))
            self._worker_blobs.update(message.get("cache", {}).get("blobs", {}))
            # A child that is answering has earned its restarts back; the cap is
            # there to stop a crash *loop*, not to ration a long session.
            self._restarts = 0
            if (message.get("revision", 0) == self._latest_revision
                    and message.get("generation", self._generation) == self._generation):
                self._remember_result(message)
                if self._checkpoint and message.get("report", {}).get("ok", True):
                    # The worker prunes to the same retained set, so anything
                    # outside it must be sent again if it is ever needed.
                    entries, blobs = self._checkpoint_cache_keys()
                    self._worker_entries.intersection_update(entries)
                    self._worker_blobs.intersection_update(blobs)
                self.finished.emit(message)
            elif self._pending is not None and self._pending["kind"] == REBUILD:
                # The parent never applied this result. The child rolls back
                # its record of what the parent holds to before this reply, so
                # the next reply carries exactly what is missing. Resending
                # every body and the whole cache here made rapid undo on a
                # large project slower the faster it was pressed.
                self._pending["rollback"] = message.get("revision", 0)
        elif kind == FAILED:
            log.error("geometry process error: %s", message.get("traceback"))
            if message.get("revision", 0) == self._latest_revision:
                self.failed.emit(message.get("error", "rebuild failed"))

        self._send_next()

    def _send_next(self) -> None:
        # Real edits take priority over a stream of hover or drag previews.
        if self._starting or self._busy or self._preview_busy:
            return
        if self._restore is not None:
            payload, self._restore = self._restore, None
            if not self._send(payload):
                self._handle_death()
        elif self._pending is not None:
            payload, self._pending = self._pending, None
            if not self._send(payload):
                self._handle_death()

        elif self._pending_saves:
            self._active_save = self._pending_saves.pop(0)
            try:
                self._pipe.send(self._active_save)
                self._busy = True
                self._sent_at = self._progress_at = time.monotonic()
            except (OSError, ValueError):
                self._handle_death()
        elif self._pending_preview is not None:
            payload, self._pending_preview = self._pending_preview, None
            if not self._send_preview(payload):
                # The payload is gone and nothing will ever answer it. Say so,
                # or the panel that asked waits for a reply for ever.
                self._fail_pending_previews(
                    "The geometry engine is unavailable.",
                    tokens=[payload.get("token")], retryable=False,
                )
                self._handle_death()

    def _remember_result(self, message):
        active = self._active_document
        if active is None:
            return
        bodies = dict(self._published_bodies)
        for name, blob in (message.get("bodies") or {}).items():
            if blob is None:
                bodies.pop(name, None)
            else:
                bodies[name] = blob
        for name, matrix in (message.get("located") or {}).items():
            base = bodies.get(name)
            if isinstance(base, (tuple, list)):
                base = base[1]
            if base is not None:
                bodies[name] = ("located", base, list(matrix))
        self._published_bodies = bodies
        # The request snapshot is never mutated after it is sent, so the
        # checkpoint shares it and copies only the features the worker changed.
        # A whole-document deepcopy here was paid on every accepted reply.
        state = dict(active["document"])
        outcomes = message.get("report", {}).get("features", {})
        features = []
        for feature in state["features"]:
            outcome = outcomes.get(feature["id"])
            if outcome:
                feature = dict(feature)
                for key in ("outputs", "output_ids", "bindings"):
                    if key in outcome:
                        feature[key] = copy.deepcopy(outcome[key])
                if outcome.get("inputs") or outcome.get("solved_sketch") is not None:
                    feature["inputs"] = dict(feature["inputs"])
                    for key, value in outcome.get("inputs", {}).items():
                        feature["inputs"].setdefault(key, copy.deepcopy(value))
                    if outcome.get("solved_sketch") is not None:
                        feature["inputs"]["sketch"] = copy.deepcopy(outcome["solved_sketch"])
            features.append(feature)
        state["features"] = features
        table = {b["name"]: b for b in state["bodies"]}
        table.update(message.get("report", {}).get("body_metadata", {}))
        state["bodies"] = [table.get(name, {"name": name}) for name in bodies]
        # Failed histories must not become a trusted successful baseline.
        if message.get("report", {}).get("ok", True):
            retained = (set(message.get("report", {}).get("signatures", {}).values())
                        | set(active.get("retained_cache_keys", [])))
            self._checkpoint = {
                "kind": SYNC, "revision": message["revision"], "generation": self._generation,
                "document": state, "bodies": bodies, "fragments": active.get("fragments", []),
                "assets": active.get("assets", {}),
                "cache": None, "cache_keys": retained,
            }

    def _lightweight_checkpoint(self, payload):
        checkpoint = dict(payload)
        cache = checkpoint.pop("cache", None) or {}
        checkpoint["cache"] = None
        checkpoint["cache_keys"] = set(cache.get("entries", {}))
        return copy.deepcopy(checkpoint)

    def _checkpoint_cache_keys(self):
        """Entry keys and blob digests the current checkpoint depends on."""
        keys = set(self._checkpoint.get("cache_keys") or ()) if self._checkpoint else set()
        store = getattr(self._document_identity, "_geometry_store", None)
        blobs = set()
        if store is not None:
            for key in keys:
                entry = store.entries.get(key)
                if entry:
                    blobs.update(v["sha256"] for v in entry["outputs"].values() if "sha256" in v)
        return keys, blobs

    def _checkpoint_payload(self):
        """A restorable copy of the checkpoint, with its cache materialized.

        The cache is resolved only here -- on worker recovery, which is rare --
        from the document's own result store. Keeping a second exported copy of
        every blob, re-merged and re-hashed on every reply, cost more than a
        second per edit on a large project.
        """
        if self._checkpoint is None:
            return None
        checkpoint = dict(self._checkpoint)
        keys = checkpoint.pop("cache_keys", None) or set()
        restore = copy.deepcopy(checkpoint)
        store = getattr(self._document_identity, "_geometry_store", None)
        if store is not None and keys:
            entries = {k: store.entries[k] for k in keys if k in store.entries}
            shas = {v["sha256"] for e in entries.values() for v in e["outputs"].values() if "sha256" in v}
            blobs = {}
            for sha in shas:
                try:
                    blobs[sha] = store.blobs[sha]
                except KeyError:
                    pass
            from ..core.result_store import CACHE_VERSION
            restore["cache"] = {"version": CACHE_VERSION, "entries": copy.deepcopy(entries), "blobs": blobs}
        return restore

    def cancel_preview(self, token) -> None:
        if token is None:
            return
        if self._pending_preview and self._pending_preview.get("token") == token:
            self._pending_preview = None
        if self._active_preview and self._active_preview.get("token") == token:
            self._handle_death(cancelled=True)

    def _overdue(self) -> bool:
        """Has the child gone quiet for longer than a rebuild may take?

        Measured from the last sign of life, not from when the request was
        sent. A 98-feature model takes minutes of honest work; the flat
        send-to-reply deadline killed it at two, resent it, killed it again,
        and then fell back to rebuilding on the GUI thread -- so a slow model
        read as a frozen application. A child that is still reporting features,
        or still burning CPU, is working, and is left alone.
        """
        if not self._busy or self._sent_at is None:
            return False
        since = time.monotonic() - (self._progress_at or self._sent_at)
        if since <= REPLY_TIMEOUT:
            return False
        return not self._worker_progressing()

    def _worker_progressing(self) -> bool:
        """Whether the child has burned CPU since the last look.

        The last resort behind the progress messages: one OCCT call can run for
        minutes without reaching the next feature boundary, and killing it then
        loses exactly the work that was nearly done. Linux-only; elsewhere the
        progress clock stands on its own.
        """
        process = self._process
        if process is None or process.pid is None:
            return False
        now = time.monotonic()
        if self._cpu_sampled_at is not None and now - self._cpu_sampled_at < LIVENESS_INTERVAL:
            return True
        try:
            with open(f"/proc/{process.pid}/stat", "rb") as handle:
                fields = handle.read().rsplit(b")", 1)[1].split()
            seconds = (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
        except (OSError, IndexError, ValueError):
            return False
        moved = self._cpu_seconds is None or seconds > self._cpu_seconds
        self._cpu_sampled_at = now
        self._cpu_seconds = seconds
        if moved:
            self._progress_at = now
        return moved

    def _drop_stuck_preview(self) -> bool:
        """Let go of a preview whose answer is never coming.

        ``_busy`` has ``_overdue`` to fall back on; ``_preview_busy`` had
        nothing. A single lost PREVIEWED reply -- the child crashing inside
        ``build_preview``, which is outside the rebuild path's own guard --
        latched this True for the rest of the session, and every later preview
        then parked silently in ``_pending_preview``. Nothing looked broken and
        no drag ever previewed again.
        """
        if not self._preview_busy or self._preview_sent_at is None:
            return False
        feature = (self._active_preview or {}).get("feature") or {}
        timeout = (
            BOOLEAN_PREVIEW_TIMEOUT if feature.get("type") == "boolean" or "__inspect_edge" in feature
            else PREVIEW_TIMEOUT
        )
        last_activity = self._preview_sent_at
        if feature.get("type") == "boolean":
            last_activity = max(last_activity, self._progress_at or last_activity)
        if time.monotonic() - last_activity <= timeout:
            return False
        if feature.get("type") == "boolean" and self._worker_progressing():
            return False
        log.warning("preview has not answered in %.0fs; restarting geometry worker", timeout)
        self._handle_death(
            preview_timeout=True,
            preview_error="The preview took too long. The geometry engine is restarting.",
        )
        return True

    def _fail_pending_previews(
        self, error: str, *, tokens=(), timeout: bool = False,
        retryable: bool = True, active: bool = True,
    ) -> None:
        """Answer every preview that will never get a real reply.

        A panel that asked for a preview shows "Building the preview…" until
        one comes back, and listens for nothing else. Letting a request go
        quietly -- because the child died, because the pipe broke, because a
        newly opened project superseded it -- therefore left the tool wedged on
        that line with its Apply button disabled for the rest of the session.
        Every path that lets go of a preview comes through here, so the panel
        always learns what happened.
        """
        self._announce_lost_previews(
            self._release_previews(tokens=tokens, active=active),
            error, timeout=timeout, retryable=retryable,
        )

    def _release_previews(self, *, tokens=(), active: bool = True) -> list:
        """Let go of the outstanding previews and return their tokens."""
        waiting = [token for token in tokens if token is not None]
        if active and self._active_preview is not None:
            waiting.append(self._active_preview.get("token"))
        if self._pending_preview is not None:
            waiting.append(self._pending_preview.get("token"))
        if active:
            self._preview_busy = False
            self._active_preview = None
            self._preview_sent_at = None
        self._pending_preview = None
        waiting = [token for token in waiting if token is not None]
        # A child that is merely slow may still answer one of these later.
        # Remembered, so that answer is discarded rather than acted on.
        self._abandoned_previews.extend(waiting)
        del self._abandoned_previews[:-MAX_ABANDONED_TOKENS]
        return waiting

    def _announce_lost_previews(
        self, tokens: list, error: str, *, timeout: bool = False,
        retryable: bool = True,
    ) -> None:
        for token in tokens:
            self.previewed.emit({
                "kind": PREVIEWED, "token": token, "shape": None, "parts": {},
                "error": error, "warnings": [], "inputs": {}, "message": "",
                "failed": True, "timeout": timeout, "retryable": retryable,
            })

    def _handle_death(
        self, *, preview_timeout: bool = False, preview_error: str | None = None,
        cancelled: bool = False,
    ) -> None:
        """Restart without blocking Qt or replaying an accepted history."""
        active = self._active_document if self._busy else None
        failed_rebuild = (
            not cancelled and active is not None
            and active.get("kind") == REBUILD
        )
        if self._active_save is not None:
            self._pending_saves.insert(0, self._active_save)
            self._active_save = None
        latest = self._latest_document
        self._pending = dict(latest) if latest is not None else None
        self._restore = self._checkpoint_payload()
        if self._restore is not None and latest is not None:
            if latest["revision"] == self._restore["revision"]:
                self._pending = None
        elif self._pending is not None and self._pending["kind"] == REBUILD:
            self._pending["full"] = True
        if (failed_rebuild and self._pending is not None
                and self._pending.get("revision") == active.get("revision")):
            # Retrying a native crash with the same feature just burns through
            # workers. Keep a newer queued edit, if any, but stop this one.
            self._pending = None
        self.available = False
        self._busy = False
        self._starting = False
        lost = self._release_previews()
        self._sent_at = self._progress_at = None
        self._cpu_sampled_at = self._cpu_seconds = None
        if self._pipe is not None:
            try:
                self._pipe.close()
            except (OSError, AttributeError):
                pass
        self._pipe = None
        if self._process is not None:
            if self._process.is_alive():
                self._process.terminate()
            self._retired.append(self._process)
        self._process = None
        self._active_document = None

        if cancelled or self._restarts < MAX_RESTARTS:
            if not cancelled:
                self._restarts += 1
            restart_count = self._restarts
            started = self.start()
            self._restarts = restart_count
            if started:
                self._send_next()
                self._announce_lost_previews(
                    lost, preview_error or ("Preview cancelled." if cancelled else
                        "The geometry engine restarted before this preview finished."),
                    timeout=preview_timeout, retryable=False,
                )
                if failed_rebuild:
                    self.failed.emit(
                        "The geometry engine stopped while rebuilding. "
                        "The last valid model is preserved; edit or undo the failed operation."
                    )
                return

        self._timer.stop()
        self._pending = self._restore = None
        saves, self._pending_saves = self._pending_saves, []
        for request in saves:
            self.saved.emit({"token": request.get("token"),
                             "generation": request.get("generation"),
                             "error": "The geometry engine could not complete the save. Please retry."})
        self._announce_lost_previews(
            lost, preview_error or "The geometry engine could not be restarted.",
            timeout=preview_timeout, retryable=False,
        )
        self.failed.emit("The geometry engine is unavailable. Your last valid model is preserved. Retry the operation to restart it.")

    def wait(self, timeout_ms: int = 300_000) -> bool:
        """Block until idle. For scripts and tests only."""
        from PySide6.QtCore import QDeadlineTimer, QEventLoop
        from PySide6.QtWidgets import QApplication

        deadline = QDeadlineTimer(timeout_ms)
        while (self.busy or self._restore) and not deadline.hasExpired():
            self._poll()
            QApplication.processEvents(QEventLoop.AllEvents, 5)
        return not (self.busy or self._restore)


def apply_result(document, message: dict) -> list[str]:
    """Put a child's reply back into the parent's document.

    Returns the names of bodies whose geometry changed.
    """
    from ..core.document import Body, FeatureState
    from ..core.graph import OutputRef
    from ..core.result_store import ResultStore

    if document._geometry_store is None:
        document._geometry_store = ResultStore()
    document._geometry_store.merge(message.get("cache"))
    previous_signatures = getattr(document, "_geometry_signatures", {})
    document._geometry_signatures = dict(message.get("report", {}).get("signatures", {}))

    changed: list[str] = []
    for name, blob in (message.get("bodies") or {}).items():
        if blob is None:
            document.bodies.pop(name, None)
            changed.append(name)
            continue
        metadata = message.get("report", {}).get("body_metadata", {}).get(name, {})
        body = document.bodies.get(name)
        producer = metadata.get("producer")
        # Skipped only when the shape on screen is known to be this producer's
        # result at this signature. Undoing a join, boolean or fillet hands
        # the body back to an earlier producer whose signature never changed
        # -- and undo restores the body's producer field before this reply
        # lands -- so neither of those alone says the geometry is current.
        current = (producer, document._geometry_signatures.get(producer))
        shown = getattr(body, "_shape_key", None) if body is not None else None
        if (body is not None and body.shape is not None and producer
                and body.id == metadata.get("id") and current[1]
                and (shown == current if shown is not None else (
                    body.producer == producer
                    and previous_signatures.get(producer) == current[1]))):
            continue
        shape = body_from_wire(blob)
        if shape is None:
            continue
        body = document.bodies.get(name)
        if body is None:
            body = Body(name=name)
            document.bodies[name] = body
        body.shape = shape
        metadata = message.get("report", {}).get("body_metadata", {}).get(name, {})
        body.id = metadata.get("id", body.id)
        body.producer = metadata.get("producer", body.producer)
        body._shape_key = (body.producer, document._geometry_signatures.get(body.producer))
        changed.append(name)

    for name, matrix in (message.get("located") or {}).items():
        # The worker moved geometry this window already holds; only its
        # placement crosses the pipe.
        body = document.bodies.get(name)
        if body is None or body.shape is None:
            continue
        body.shape = body.shape.Located(location_from_wire(matrix))
        metadata = message.get("report", {}).get("body_metadata", {}).get(name, {})
        body.id = metadata.get("id", body.id)
        body.producer = metadata.get("producer", body.producer)
        body._shape_key = (body.producer, document._geometry_signatures.get(body.producer))
        changed.append(name)

    for feature in document.features:
        state = (message.get("report", {}).get("features") or {}).get(feature.id)
        if not state:
            continue
        feature.state = FeatureState(state["state"])
        feature.message = state["message"]
        if feature.type_name == "sketch" and state.get("solved_sketch") is not None:
            feature.inputs["sketch"] = state["solved_sketch"]
        if "outputs" in state:
            feature.outputs = list(state["outputs"])
        if "output_ids" in state:
            feature.output_ids = dict(state["output_ids"])
        if "bindings" in state:
            feature.bindings = {n: OutputRef.from_dict(r) for n, r in state["bindings"].items()}
        for key, value in (state.get("inputs") or {}).items():
            # Only fill in what the feature decided for itself; never overwrite
            # a reference or an expression the parent already holds.
            if key not in feature.inputs:
                feature.inputs[key] = value
    graph = document.prepare_graph()
    for name in changed:
        body = document.bodies.get(name)
        ref = graph.latest.get(name)
        if body is not None and ref is not None:
            body.id, body.producer = ref.object_id, ref.feature_id
    document._geometry_store.adopt_display(document)
    history = getattr(document, "_history", None)
    retained = set(document._geometry_signatures.values())
    if history is not None:
        retained.update(history.cache_keys())
    else:
        for stack in ("undo", "redo"):
            for action in document._history_data.get(stack, []):
                retained.update(action.get("cache_keys", []))
    document._geometry_store.prune(retained)
    return changed
