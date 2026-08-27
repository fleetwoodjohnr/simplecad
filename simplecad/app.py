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

    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication

    from .ui.main_window import MainWindow
    from .ui.theme import Mode
    from .ui.viewport.occt_view import default_surface_format

    QSurfaceFormat.setDefaultFormat(default_surface_format())

    # Python rewrites argv[0] to the module path under ``-m``, and Qt derives the
    # X11 WM_CLASS instance name from it -- which is how the window ended up
    # announcing itself as "__main__.py" and failing to match the desktop entry.
    arguments = list(argv if argv is not None else sys.argv)
    arguments[:1] = ["simplecad"]
    app = QApplication(arguments)
    app.setApplicationName("SimpleCAD")
    app.setApplicationDisplayName("SimpleCAD")
    app.setOrganizationName("SimpleCAD")
    app.setDesktopFileName("simplecad")

    window = MainWindow(Mode.SYSTEM)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
