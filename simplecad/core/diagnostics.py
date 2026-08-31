"""Make a crash or a hang say what it was doing.

SimpleCAD drives OCCT, and OCCT does not always throw when it is unhappy --
sometimes it dereferences a null curve adaptor and the process is gone
mid-instruction. Twice, on this machine, that took the whole window with it, and
the journal recorded nothing at all: no traceback, no last statement, just the
scope's "Consumed 9min 21s CPU" line and silence. A separate four-minute hang
left exactly as little.

None of that is diagnosable after the fact, so this arranges for the next one to
leave something behind. Everything here is stdlib and costs nothing while things
are working.

Qt-free by intent, so it can be armed before ``QApplication`` exists (a crash
during startup is a crash worth reading too) and so the geometry child can use
the same machinery.
"""

from __future__ import annotations

import logging
import os
import sys

log = logging.getLogger("simplecad.diagnostics")

#: How long the GUI thread may stop answering before its stack is dumped.
#: Longer than the slowest thing that legitimately blocks it -- an in-process
#: rebuild reports progress every feature -- and far shorter than the four
#: minutes a user will sit through before calling it frozen.
STALL_SECONDS = 5.0

#: How often the heartbeat re-arms the watchdog. Anything under STALL_SECONDS
#: works; this is comfortably under it without being chatty.
HEARTBEAT_MS = 1000

#: Set by SIGUSR2, acted on by the heartbeat. A signal handler cannot touch Qt
#: -- it runs between bytecodes, at an arbitrary point, possibly while the event
#: loop is inside C++ -- so it only raises a flag and the timer, which is
#: already running on the GUI thread, does the work.
_UI_DUMP_REQUESTED = False


def install(where: str = "app") -> None:
    """Arm crash and hang reporting for this process.

    *where* names the process in the log, so a parent and a geometry child are
    told apart in the journal.
    """
    _enable_faulthandler(where)
    _install_ui_dump()
    _install_excepthook(where)


def _install_ui_dump() -> None:
    """SIGUSR2 asks the window what state it is in.

    SIGUSR1 dumps Python stacks, and there is a whole class of "frozen" that it
    cannot see at all: an application whose event loop is perfectly healthy and
    idle, which repaints correctly, and which ignores every click because the
    input is going somewhere else -- a menu that grabbed the pointer and was
    drawn off-screen, a modal widget, a disabled window, a stale override
    cursor. A stack trace of that says ``app.exec()`` and nothing more, which is
    exactly what it said.

    So this asks the question that one cannot: who is holding the input?
    """
    try:
        import signal

        signal.signal(signal.SIGUSR2, _request_ui_dump)
    except (AttributeError, OSError, ValueError) as exc:
        log.debug("SIGUSR2 UI dump unavailable (%s)", exc)


def _request_ui_dump(_signum, _frame) -> None:
    global _UI_DUMP_REQUESTED
    _UI_DUMP_REQUESTED = True


def dump_ui_state(window=None) -> None:
    """Log who is holding the input, and what the window thinks it is doing."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QWidget

    app = QApplication.instance()
    if app is None:
        log.warning("UI dump: no QApplication")
        return

    def name(widget):
        # Defensive on purpose: this runs on a window that is misbehaving, and a
        # dump that raises tells you nothing at all.
        if widget is None:
            return None
        try:
            return (
                f"{type(widget).__name__}"
                f"(visible={widget.isVisible()}, geom={widget.geometry()})"
            )
        except Exception as exc:  # noqa: BLE001 - a half-dead widget still counts
            return f"<unreadable {type(widget).__name__}: {exc}>"

    log.warning("---- UI state dump ----")
    # The four ways input goes somewhere the user cannot see.
    log.warning("active popup    : %s", name(app.activePopupWidget()))
    log.warning("active modal    : %s", name(app.activeModalWidget()))
    log.warning("mouse grabber   : %s", name(QWidget.mouseGrabber()))
    log.warning("keyboard grabber: %s", name(QWidget.keyboardGrabber()))
    cursor = app.overrideCursor()
    log.warning("override cursor : %s", cursor.shape() if cursor else None)
    log.warning("active window   : %s", name(app.activeWindow()))

    if window is None:
        return
    log.warning(
        "window          : enabled=%s visible=%s active=%s state=%s",
        window.isEnabled(), window.isVisible(),
        window.isActiveWindow(), window.windowState(),
    )
    viewport = getattr(getattr(window, "stage", None), "viewport", None)
    if viewport is not None:
        log.warning(
            "viewport        : ready=%s failure=%s painted=%s enabled=%s "
            "native=%s effective_winid=%#x",
            viewport.is_ready, viewport.failure,
            getattr(viewport, "has_rendered", None), viewport.isEnabled(),
            viewport.testAttribute(Qt.WidgetAttribute.WA_NativeWindow),
            int(viewport.effectiveWinId()),
        )
        # Whether clicks are landing where the model is. An application that
        # renders, has no grab, and still "does nothing" is one where picking
        # is missing -- and picking is done in device pixels against a widget
        # measured in logical ones, so the ratio is the first thing to see.
        log.warning(
            "picking         : enabled=%s point_mode=%s ratio=%.2f "
            "viewport_geom=%s mouse_tracking=%s",
            getattr(viewport, "picking_enabled", None),
            getattr(viewport, "_picking_points", None),
            viewport.devicePixelRatioF(),
            viewport.geometry(),
            viewport.hasMouseTracking(),
        )
        log.warning(
            "mouse           : events=%s nav=%s dragged=%s press_pos=%s",
            getattr(viewport, "_events", None),
            getattr(viewport, "_nav", None),
            getattr(viewport, "_dragged", None),
            getattr(viewport, "_press_pos", None),
        )
        selection = getattr(window, "selection", None)
        if selection is not None:
            try:
                selection.refresh()
                log.warning(
                    "selection       : count=%s bodies=%s items=%s",
                    selection.count, selection.bodies,
                    [str(i) for i in selection.items][:4],
                )
            except Exception as exc:  # noqa: BLE001 - a dump must not throw
                log.warning("selection       : unreadable (%s)", exc)
        context = getattr(viewport, "context", None)
        if context is not None:
            try:
                from OCP.AIS import AIS_ListOfInteractive

                displayed = AIS_ListOfInteractive()
                context.DisplayedObjects(displayed)
                log.warning(
                    "AIS             : displayed=%d detected=%s",
                    sum(1 for _ in displayed), context.HasDetected(),
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("AIS             : unreadable (%s)", exc)
    stage = getattr(window, "stage", None)
    for widget, anchor in getattr(stage, "overlays", []):
        log.warning(
            "overlay         : %s @%s visible=%s enabled=%s geom=%s "
            "native=%s effective_winid=%#x",
            type(widget).__name__, anchor, widget.isVisible(),
            widget.isEnabled(), widget.geometry(),
            widget.testAttribute(Qt.WidgetAttribute.WA_NativeWindow),
            int(widget.effectiveWinId()),
        )
    log.warning("---- end UI state ----")


def _enable_faulthandler(where: str) -> None:
    import faulthandler

    try:
        faulthandler.enable(all_threads=True)
    except (RuntimeError, ValueError) as exc:  # a stderr that cannot be dup'd
        log.warning("faulthandler unavailable in %s (%s)", where, exc)
        return

    # SIGUSR1 dumps every thread's Python stack without stopping the process, so
    # a window that has stopped responding can be asked what it is doing:
    #     kill -USR1 $(pgrep -f -- '-m simplecad$')
    # Anchored deliberately: an unanchored pattern also matches the shell that
    # is running the pgrep, and signals it instead. This is the thing whose
    # absence made the last hang unreadable.
    register = getattr(faulthandler, "register", None)
    if register is None:  # not Unix
        return
    try:
        import signal

        register(signal.SIGUSR1, all_threads=True, chain=False)
    except (AttributeError, OSError, RuntimeError) as exc:
        log.debug("SIGUSR1 stack dumps unavailable (%s)", exc)


def _install_excepthook(where: str) -> None:
    """Log what would otherwise be a bare traceback on stderr.

    PySide prints an unhandled exception raised inside a slot and carries on, so
    these are easy to miss among Qt's own chatter. Routing them through logging
    gives them a level and a name to grep for.
    """
    previous = sys.excepthook

    def hook(kind, value, traceback) -> None:
        log.error("unhandled exception in %s", where,
                  exc_info=(kind, value, traceback))
        previous(kind, value, traceback)

    sys.excepthook = hook


class StallWatch:
    """Dump every thread's stack when the GUI thread stops answering.

    Deliberately built on ``faulthandler.dump_traceback_later`` rather than a
    Python watchdog thread. The OCP bindings hold the GIL for the whole of every
    kernel call, so a Python thread cannot run *while* OCCT is the thing that has
    stopped the window -- which is the case that matters. ``dump_traceback_later``
    is a C thread and does not need the GIL, so it fires exactly when a Python
    watchdog could not.

    Used as: heartbeat on a timer from the GUI thread; each beat re-arms the
    deadline. Miss enough beats and the stacks land in the journal.
    """

    def __init__(self, seconds: float = STALL_SECONDS) -> None:
        self.seconds = seconds
        self._armed = False

    @property
    def available(self) -> bool:
        import faulthandler

        return hasattr(faulthandler, "dump_traceback_later")

    def beat(self) -> None:
        """The GUI thread is alive; push the deadline out again."""
        import faulthandler

        if not self.available:
            return
        # exit=False: this is a report, not a kill. A stall is very often
        # survivable, and killing the window would destroy the user's model to
        # tell them about it.
        faulthandler.dump_traceback_later(self.seconds, repeat=False, exit=False)
        self._armed = True

    def stop(self) -> None:
        import faulthandler

        if not self._armed or not self.available:
            return
        faulthandler.cancel_dump_traceback_later()
        self._armed = False


def start_gui_watch(parent, seconds: float = STALL_SECONDS):
    """Heartbeat *parent*'s thread on a timer. Returns the timer, or None.

    Kept here rather than in the window so the import of Qt stays inside the one
    function that needs it.
    """
    from PySide6.QtCore import QTimer

    watch = StallWatch(seconds)
    if not watch.available:
        log.debug("stall watchdog unavailable on this interpreter")
        return None
    def beat() -> None:
        watch.beat()
        global _UI_DUMP_REQUESTED
        if _UI_DUMP_REQUESTED:
            _UI_DUMP_REQUESTED = False
            try:
                dump_ui_state(parent)
            except Exception as exc:  # noqa: BLE001 - a dump must not kill the app
                log.warning("UI dump failed (%s)", exc)

    timer = QTimer(parent)
    timer.setInterval(HEARTBEAT_MS)
    timer.timeout.connect(beat)
    timer.start()
    watch.beat()
    # Held on the timer so the watch lives exactly as long as the heartbeat.
    timer.stall_watch = watch
    log.info(
        "stall watchdog armed (%.0fs); kill -USR1 %d dumps stacks, "
        "kill -USR2 %d dumps UI state",
        seconds, os.getpid(), os.getpid(),
    )
    return timer
