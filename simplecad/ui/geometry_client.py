"""The parent's side of the geometry process.

Spawns the child, sends rebuild requests, and polls for replies on a timer. A
timer rather than a blocking read on a thread: ``poll(0)`` is cheap and costs the
UI nothing, and it keeps every Qt object on the main thread where it belongs.

If the child cannot be started -- or dies -- the client falls back to rebuilding
in-process. Slower and blocking, but the application keeps working, which is the
right trade for something that is fundamentally an optimisation.
"""

from __future__ import annotations

import logging
import multiprocessing
import time

from PySide6.QtCore import QObject, QTimer, Signal

from ..core.geometry_service import (
    FAILED, PREVIEW, PREVIEWED, READY, REBUILD, RESULT, SHUTDOWN, child_main,
    deserialise_shape,
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

#: How many times to bring the child back before giving up on it. A shape that
#: reliably kills OCCT would otherwise spawn children for as long as the user
#: keeps dragging.
MAX_RESTARTS = 3


class GeometryClient(QObject):
    """Runs rebuilds out of process and reports results back on the UI thread."""

    started = Signal()
    finished = Signal(object)          # the report dict
    failed = Signal(str)
    previewed = Signal(object)         # {"token": ..., "shape": bytes | None}

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._process = None
        self._pipe = None
        self._busy = False
        self._pending: dict | None = None
        # Previews travel on the same pipe but are tracked apart from rebuilds:
        # a dragged fillet radius must never displace a real edit, and a preview
        # reply must never be mistaken for one.
        self._preview_busy = False
        self._pending_preview: dict | None = None
        self._preview_sent_at: float | None = None
        self._sent_at: float | None = None
        self._restarts = 0
        self.available = False

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL)
        self._timer.timeout.connect(self._poll)

    # -- lifecycle -------------------------------------------------------
    def start(self, timeout: float = 20.0) -> bool:
        """Spawn the geometry process. Returns False if it could not start."""
        try:
            context = _process_context()
            parent_end, child_end = context.Pipe(duplex=True)
            process = context.Process(
                target=child_main, args=(child_end,), daemon=True,
                name="simplecad-geometry",
            )
            process.start()
            child_end.close()
            # The child imports OCCT before it answers, which takes a moment.
            if not parent_end.poll(timeout):
                raise TimeoutError("the geometry process did not start in time")
            hello = parent_end.recv()
            if hello.get("kind") != READY:
                raise RuntimeError(f"unexpected greeting: {hello!r}")
        except Exception as exc:  # noqa: BLE001 - fall back, never fail startup
            log.warning("geometry process unavailable (%s); rebuilding in-process", exc)
            self.available = False
            return False

        self._process = process
        self._pipe = parent_end
        self.available = True
        self._timer.start()
        log.info("geometry process ready (pid %s)", hello.get("pid"))
        return True

    def stop(self) -> None:
        self._timer.stop()
        # Cleared here, not just on a reply. A stopped client that still reports
        # busy answers every later rebuild by parking it in ``_pending`` and
        # returning True, so the caller believes it succeeded and the model
        # never changes again -- silently, for the rest of the session.
        self._busy = False
        self._pending = None
        self._preview_busy = False
        self._pending_preview = None
        self._preview_sent_at = None
        self._sent_at = None
        if self._pipe is not None:
            try:
                self._pipe.send({"kind": SHUTDOWN})
            except (OSError, BrokenPipeError, ValueError):
                pass
        if self._process is not None:
            self._process.join(timeout=5)
            if self._process.is_alive():
                self._process.terminate()
        self._process = None
        self._pipe = None
        self.available = False

    @property
    def busy(self) -> bool:
        return self._busy

    # -- requests --------------------------------------------------------
    def request(self, document, stale=None, force: bool = False) -> bool:
        """Ask for a rebuild. Returns False if the caller must do it in-process."""
        if not self.available or self._pipe is None:
            return False
        payload = {
            "kind": REBUILD,
            "document": document.to_dict(),
            "stale": sorted(stale or ()),
            "force": force,
        }
        if self._busy:
            # Coalesce: one more rebuild after the current one, not a queue of
            # them. Dragging a dimension settles instead of piling up.
            self._pending = payload
            return True
        return self._send(payload)

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
        payload = {"kind": PREVIEW, "feature": feature_state, "token": token}
        if self._preview_busy:
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
        self._preview_sent_at = time.monotonic()
        return True

    def _send(self, payload: dict) -> bool:
        try:
            self._pipe.send(payload)
        except (OSError, BrokenPipeError, ValueError) as exc:
            log.warning("geometry process went away (%s)", exc)
            self.available = False
            return False
        self._busy = True
        self._sent_at = time.monotonic()
        self.started.emit()
        return True

    # -- replies ---------------------------------------------------------
    def _poll(self) -> None:
        if self._pipe is None:
            return
        self._drop_stuck_preview()
        try:
            if not self._pipe.poll(0):
                if self._process is not None and not self._process.is_alive():
                    self._handle_death()
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
        if kind == PREVIEWED:
            # Deliberately does not clear ``_busy``: a preview can be answered
            # while a rebuild is still in flight, and crediting the rebuild with
            # this reply would let the next one overwrite it.
            self._preview_busy = False
            self._preview_sent_at = None
            self.previewed.emit(message)
            if self._pending_preview is not None:
                payload, self._pending_preview = self._pending_preview, None
                self._send_preview(payload)
            return

        self._busy = False
        self._sent_at = None
        if kind == RESULT:
            # A child that is answering has earned its restarts back; the cap is
            # there to stop a crash *loop*, not to ration a long session.
            self._restarts = 0
            self.finished.emit(message)
        elif kind == FAILED:
            log.error("geometry process error: %s", message.get("traceback"))
            self.failed.emit(message.get("error", "rebuild failed"))

        if self._pending is not None:
            payload, self._pending = self._pending, None
            self._send(payload)

    def _overdue(self) -> bool:
        return (
            self._busy
            and self._sent_at is not None
            and time.monotonic() - self._sent_at > REPLY_TIMEOUT
        )

    def _drop_stuck_preview(self) -> None:
        """Let go of a preview whose answer is never coming.

        ``_busy`` has ``_overdue`` to fall back on; ``_preview_busy`` had
        nothing. A single lost PREVIEWED reply -- the child crashing inside
        ``build_preview``, which is outside the rebuild path's own guard --
        latched this True for the rest of the session, and every later preview
        then parked silently in ``_pending_preview``. Nothing looked broken and
        no drag ever previewed again.
        """
        if not self._preview_busy or self._preview_sent_at is None:
            return
        if time.monotonic() - self._preview_sent_at <= REPLY_TIMEOUT:
            return
        log.warning("preview has not answered in %.0fs; dropping it", REPLY_TIMEOUT)
        self._preview_busy = False
        self._preview_sent_at = None
        if self._pending_preview is not None:
            payload, self._pending_preview = self._pending_preview, None
            self._send_preview(payload)

    def _handle_death(self) -> None:
        """The child is gone. Bring it back rather than inheriting its work.

        Falling straight back to in-process rebuilds -- which is what this used
        to do -- is the wrong reflex when the thing that killed the child was
        OCCT. The next attempt at the same shape then runs *here*, on the GUI
        thread, and takes the window with it. The child exists precisely so that
        cannot happen, so the answer to a dead child is another child.
        """
        self._timer.stop()
        self.available = False
        self._busy = False
        self._preview_busy = False
        self._pending_preview = None
        self._preview_sent_at = None
        self._sent_at = None
        self._pipe = None
        if self._process is not None and self._process.is_alive():
            self._process.terminate()
        self._process = None

        if self._restarts < MAX_RESTARTS:
            self._restarts += 1
            log.warning(
                "geometry process died; restarting it (%d of %d)",
                self._restarts, MAX_RESTARTS,
            )
            if self.start():
                self.failed.emit(
                    "The geometry engine stopped and has been restarted. "
                    "The last operation was not applied."
                )
                if self._pending is not None:
                    payload, self._pending = self._pending, None
                    self._send(payload)
                return

        log.error("geometry process died and could not be restarted")
        self._pending = None
        self.failed.emit(
            "The geometry engine stopped unexpectedly and could not be "
            "restarted. Rebuilds will continue in the main process, which is "
            "slower and less safe -- save your work."
        )

    def wait(self, timeout_ms: int = 300_000) -> bool:
        """Block until idle. For scripts and tests only."""
        from PySide6.QtCore import QDeadlineTimer, QEventLoop
        from PySide6.QtWidgets import QApplication

        deadline = QDeadlineTimer(timeout_ms)
        while (self._busy or self._pending) and not deadline.hasExpired():
            self._poll()
            QApplication.processEvents(QEventLoop.AllEvents, 5)
        return not (self._busy or self._pending)


def apply_result(document, message: dict) -> list[str]:
    """Put a child's reply back into the parent's document.

    Returns the names of bodies whose geometry changed.
    """
    from ..core.document import Body, FeatureState

    changed: list[str] = []
    for name, blob in (message.get("bodies") or {}).items():
        if blob is None:
            document.bodies.pop(name, None)
            changed.append(name)
            continue
        shape = deserialise_shape(blob)
        if shape is None:
            continue
        body = document.bodies.get(name)
        if body is None:
            body = Body(name=name)
            document.bodies[name] = body
        body.shape = shape
        changed.append(name)

    for feature in document.features:
        state = (message.get("report", {}).get("features") or {}).get(feature.id)
        if not state:
            continue
        feature.state = FeatureState(state["state"])
        feature.message = state["message"]
        if state.get("outputs"):
            feature.outputs = list(state["outputs"])
        for key, value in (state.get("inputs") or {}).items():
            # Only fill in what the feature decided for itself; never overwrite
            # a reference or an expression the parent already holds.
            if key not in feature.inputs:
                feature.inputs[key] = value
    return changed
