"""Keep desktop launches attached to the one SimpleCAD window.

GNOME normally activates an application's existing window when its dock icon is
clicked.  A launcher can still be invoked directly, though (from the app grid,
a file association, or a dock implementation which chooses "launch"), and a
plain ``QApplication`` treats that as a request for a second process.  The
second process has no useful work to do: tell the first one to come forward and
exit before starting OCCT or the geometry service.
"""

from __future__ import annotations

import getpass
import logging
import os

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger("simplecad.instance")


def _server_name() -> str:
    """A per-user name; two logged-in users must never share an instance."""
    user = os.getuid() if hasattr(os, "getuid") else getpass.getuser()
    return f"simplecad-{user}"


class SingleInstance(QObject):
    """Own the application socket, or notify the process which already does."""

    #: Carries the launcher's activation token, or "" when there was none.
    activate_requested = Signal(str)
    #: A file the second launch was asked to open (a double-click in Files).
    open_requested = Signal(str)

    def __init__(self, name: str | None = None, parent=None, paths=()) -> None:
        super().__init__(parent)
        self.name = name or _server_name()
        #: Files this launch was given, forwarded if another process owns the name.
        self.paths = [os.path.abspath(p) for p in paths]
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._accept_connections)
        self._owns_server = False

    def claim(self) -> bool:
        """Return True for the primary process, False after notifying it."""
        if self._notify_existing():
            return False

        # A crashed process can leave its filesystem socket behind.  Only remove
        # it after a connection attempt failed, never while a live owner exists.
        QLocalServer.removeServer(self.name)
        if self._server.listen(self.name):
            self._owns_server = True
            return True

        # Two launches can race between the failed connection and listen.  The
        # winner owns the server; the loser gets one more chance to notify it.
        if self._notify_existing():
            return False

        # Activation is a convenience, not a reason to make the application
        # unlaunchable when the platform cannot provide local sockets.
        log.warning(
            "single-instance socket unavailable (%s); allowing this launch",
            self._server.errorString(),
        )
        return True

    def _notify_existing(self) -> bool:
        socket = QLocalSocket(self)
        socket.connectToServer(self.name)
        if not socket.waitForConnected(300):
            socket.abort()
            socket.deleteLater()
            return False
        # The token is the only thing this process has that the running one
        # needs. The compositor issued it to *us* because the user just clicked
        # the launcher; without it the other process is a background window
        # asking for focus on its own behalf, which is what focus-stealing
        # prevention exists to refuse.
        message = "activate\n" + _activation_token() + "\n"
        message += "".join(f"open\t{path}\n" for path in self.paths)
        socket.write(message.encode())
        socket.flush()
        socket.waitForBytesWritten(300)
        socket.disconnectFromServer()
        socket.deleteLater()
        return True

    def _accept_connections(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                continue
            # Connecting is itself the request, so the activation always
            # happens; the token is read only if it has already arrived. Making
            # the request *conditional* on a payload would reintroduce the race
            # where the peer disconnects before readyRead is delivered.
            token, paths = "", []
            if socket.waitForReadyRead(200):
                data = bytes(socket.readAll())
                while socket.waitForReadyRead(50):
                    data += bytes(socket.readAll())
                lines = data.decode(errors="ignore").split("\n")
                if len(lines) > 1:
                    token = lines[1].strip()
                paths = [line[5:] for line in lines[2:] if line.startswith("open\t")]
            self.activate_requested.emit(token)
            for path in paths:
                self.open_requested.emit(path)
            socket.disconnectFromServer()
            socket.deleteLater()

    def close(self) -> None:
        """Release the name without ever unlinking another process's socket."""
        if not self._owns_server:
            return
        self._server.close()
        QLocalServer.removeServer(self.name)
        self._owns_server = False


def _activation_token() -> str:
    """The compositor's permission slip for this launch, if we were given one."""
    return (
        os.environ.get("XDG_ACTIVATION_TOKEN")
        or os.environ.get("DESKTOP_STARTUP_ID")
        or ""
    )


def activate_window(window, token: str = "") -> None:
    """Restore and request focus for the existing main window.

    The token is put back into the environment before asking, because that is
    where both toolkit paths look for it -- ``XDG_ACTIVATION_TOKEN`` on Wayland,
    ``DESKTOP_STARTUP_ID`` for X11 startup notification -- and there is no API
    to hand one to ``requestActivate`` directly. A token is single-use, so it is
    removed again afterwards rather than left to be replayed on the next click.
    """
    if token:
        os.environ["XDG_ACTIVATION_TOKEN"] = token
        os.environ["DESKTOP_STARTUP_ID"] = token
    try:
        # Un-minimising is a client-side state change and works whatever the
        # compositor thinks of the focus request that follows it.
        if window.isMinimized():
            window.showNormal()
        else:
            window.show()
        window.raise_()
        window.activateWindow()
        handle = window.windowHandle()
        if handle is not None:
            handle.requestActivate()
    finally:
        if token:
            os.environ.pop("XDG_ACTIVATION_TOKEN", None)
            os.environ.pop("DESKTOP_STARTUP_ID", None)
