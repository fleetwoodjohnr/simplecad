"""Application bootstrap."""

from __future__ import annotations

import logging
import os
import sys


def configure_environment() -> None:
    """Settings that must be in place before Qt starts.

    The OCCT in the cadquery-ocp wheel is an Xlib/GLX build with no EGL support,
    so Qt has to speak X11. Under Wayland that means XWayland, which is what
    FreeCAD does too. See docs/architecture.md.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    os.environ.pop("QT_XCB_GL_INTEGRATION", None)
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")


def main(argv: list[str] | None = None) -> int:
    configure_environment()
    logging.basicConfig(
        level=os.environ.get("SIMPLECAD_LOG", "INFO"),
        format="%(levelname)s %(name)s: %(message)s",
    )

    # Armed before Qt, so a crash on the way up is as readable as one later.
    from .core.diagnostics import install as install_diagnostics

    install_diagnostics("app")

    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QGuiApplication, QSurfaceFormat
    from PySide6.QtWidgets import QApplication

    from .ui.main_window import MainWindow
    from .ui.qt_runtime import configure_qt_application
    from .ui.single_instance import SingleInstance, activate_window
    from .ui.theme import Mode
    from .ui.viewport.occt_view import default_surface_format

    # Keep the viewport and its overlays composited under one native top-level
    # window. This defensive attribute only has reliable effect before the
    # QApplication and any native widgets exist.
    configure_qt_application()
    QSurfaceFormat.setDefaultFormat(default_surface_format())

    # Python rewrites argv[0] to the module path under ``-m``, and Qt derives the
    # X11 WM_CLASS instance name from it -- which is how the window ended up
    # announcing itself as "__main__.py" and failing to match the desktop entry.
    arguments = list(argv if argv is not None else sys.argv)
    arguments[:1] = ["simplecad"]
    # Set the identity before QApplication lets the platform integration and
    # desktop portal observe it.  Changing it afterwards can leave the window
    # and the dock registered under different application ids.
    QCoreApplication.setApplicationName("SimpleCAD")
    QCoreApplication.setOrganizationName("SimpleCAD")
    QGuiApplication.setDesktopFileName("simplecad")
    app = QApplication(arguments)
    app.setApplicationDisplayName("SimpleCAD")

    instance = SingleInstance()
    if not instance.claim():
        return 0
    try:
        window = MainWindow(Mode.SYSTEM)
        instance.activate_requested.connect(
            lambda token: activate_window(window, token)
        )
        _log_qt_messages()
        from .core.diagnostics import start_gui_watch

        start_gui_watch(window)
        window.show()
        return app.exec()
    finally:
        instance.close()


def _log_qt_messages() -> None:
    """Send Qt's own warnings through logging.

    Qt writes to stderr with no level and no source, so "must be a top level
    window" and a real failure look identical in the journal. Attributing them
    costs one handler.
    """
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    levels = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }
    qt_log = logging.getLogger("simplecad.qt")

    def handler(kind, context, message: str) -> None:
        qt_log.log(levels.get(kind, logging.INFO), "%s", message)

    qInstallMessageHandler(handler)


if __name__ == "__main__":
    raise SystemExit(main())
