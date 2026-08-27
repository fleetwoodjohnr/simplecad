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

from PySide6.QtCore import QObject, QTimer, Signal

from ..core.geometry_service import (
    FAILED, READY, REBUILD, RESULT, SHUTDOWN, child_main, deserialise_shape,
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


class GeometryClient(QObject):
    """Runs rebuilds out of process and reports results back on the UI thread."""

    started = Signal()
    finished = Signal(object)          # the report dict
    failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._process = None
        self._pipe = None
        self._busy = False
        self._pending: dict | None = None
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

    def _send(self, payload: dict) -> bool:
        try:
            self._pipe.send(payload)
        except (OSError, BrokenPipeError, ValueError) as exc:
            log.warning("geometry process went away (%s)", exc)
            self.available = False
            return False
        self._busy = True
        self.started.emit()
        return True

    # -- replies ---------------------------------------------------------
    def _poll(self) -> None:
        if self._pipe is None:
            return
        try:
            if not self._pipe.poll(0):
                if self._process is not None and not self._process.is_alive():
                    self._handle_death()
                return
            message = self._pipe.recv()
        except (EOFError, OSError, ValueError):
            self._handle_death()
            return

        self._busy = False
        kind = message.get("kind")
        if kind == RESULT:
            self.finished.emit(message)
        elif kind == FAILED:
            log.error("geometry process error: %s", message.get("traceback"))
            self.failed.emit(message.get("error", "rebuild failed"))

        if self._pending is not None:
            payload, self._pending = self._pending, None
            self._send(payload)

    def _handle_death(self) -> None:
        log.warning("geometry process died; falling back to in-process rebuilds")
        self._timer.stop()
        self.available = False
        self._busy = False
        self._pipe = None
        self.failed.emit(
            "The geometry engine stopped unexpectedly. "
            "Rebuilds will continue in the main process."
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
