"""Application bootstrap."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def configure_environment() -> None:
    """Settings that must be in place before Qt starts.

    The OCCT in the cadquery-ocp wheel is an Xlib/GLX build with no EGL support,
    so Qt has to speak X11. Under Wayland that means XWayland, which is what
    FreeCAD does too. See docs/architecture.md.
    """
    if sys.platform.startswith("linux"):
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
        os.environ.pop("QT_XCB_GL_INTEGRATION", None)
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")


def _child_volume() -> float:
    """Runs in a geometry worker process: proves the kernel loads there too."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    from .kernel.occ import volume

    return volume(BRepPrimAPI_MakeBox(10.0, 10.0, 10.0).Shape())


def self_test() -> int:
    """Check, without a display, that this build can do real work.

    Used by the release workflow on every packaged build: the kernel and its
    data files load, a model rebuilds, a boolean works, STEP and 3MF are
    written, and a geometry worker process starts (the part a frozen build
    most often gets wrong).
    """
    import tempfile

    from . import __version__
    from .core.document import BodyRef, Document
    from .core.rebuild import Rebuilder
    from .kernel import thread_specs
    from .kernel.io_formats import export_3mf, export_step
    from .kernel.occ import volume
    from .kernel.operations import BooleanFeature
    from .kernel.primitives import BoxFeature, CylinderFeature
    from .ui.geometry_client import _process_context

    document = Document("self-test")
    document.add_feature(BoxFeature(inputs={"width": 20, "depth": 20, "height": 10},
                                    outputs=["Block"]))
    document.add_feature(CylinderFeature(inputs={"radius": 3, "height": 30, "x": 10, "y": 10,
                                                 "z": -5}, outputs=["Pin"]))
    document.add_feature(BooleanFeature(inputs={"body": BodyRef("Block"), "tools": [BodyRef("Pin")],
                                                "operation": "cut"}, outputs=["Block"]))
    report = Rebuilder(document).rebuild()
    if not report.ok:
        print("self-test: rebuild failed:", report.summary())
        return 1
    solid = volume(document.body("Block").shape)
    expected = 20 * 20 * 10 - 3.141592653589793 * 9 * 10
    if abs(solid - expected) > 0.5:
        print(f"self-test: wrong volume {solid:.2f}, expected {expected:.2f}")
        return 1
    if not thread_specs.fit_presets():
        print("self-test: the thread and fit data did not load")
        return 1
    with tempfile.TemporaryDirectory() as folder:
        for writer, name in ((export_step, "part.step"), (export_3mf, "part.3mf")):
            path = writer([document.body("Block").shape], os.path.join(folder, name))
            if not os.path.getsize(path):
                print(f"self-test: {name} is empty")
                return 1
    with _process_context().Pool(1) as pool:
        if abs(pool.apply(_child_volume) - 1000.0) > 1e-6:
            print("self-test: the geometry worker process gave a wrong answer")
            return 1
    print(f"self-test: SimpleCAD {__version__} OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    if "--self-test" in (argv if argv is not None else sys.argv)[1:]:
        return self_test()
    if "--version" in (argv if argv is not None else sys.argv)[1:]:
        from . import __version__

        print(__version__)
        return 0
    configure_environment()
    logging.basicConfig(
        level=os.environ.get("SIMPLECAD_LOG", "INFO"),
        format="%(levelname)s %(name)s: %(message)s",
    )

    # Armed before Qt, so a crash on the way up is as readable as one later.
    from .core.diagnostics import install as install_diagnostics

    install_diagnostics("app")

    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QGuiApplication, QIcon, QSurfaceFormat
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
    icon_path = Path(__file__).resolve().parent / "assets" / "simplecad.svg"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))

    paths = [a for a in arguments[1:] if not a.startswith("-") and os.path.isfile(a)]
    instance = SingleInstance(paths=paths)
    if not instance.claim():
        return 0
    try:
        window = MainWindow(Mode.SYSTEM)
        instance.activate_requested.connect(
            lambda token: activate_window(window, token)
        )
        instance.open_requested.connect(window.open_file)
        for path in paths:
            # After the event loop starts, so the viewport exists to show it.
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, lambda p=path: window.open_file(p))
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
