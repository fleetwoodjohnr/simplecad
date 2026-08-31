"""Shared helpers for scripted screenshots of SimpleCAD widgets.

UI work needs to be looked at, not just asserted, so every visual check in this
project goes through here: build a widget, let it settle, save a PNG.
"""

from __future__ import annotations

import os
import sys
import tempfile

# OCCT in the cadquery-ocp wheel is a GLX build, so Qt must speak X11.
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
# Scripted runs must never stop on the recovery dialog.
os.environ.setdefault("SIMPLECAD_NO_RECOVERY", "1")
os.environ.pop("QT_XCB_GL_INTEGRATION", None)

# Scripted runs must not write to the real profile. ``settings`` and
# ``autosave`` resolve their directories from these at import time, so this has
# to happen before the package is imported -- which is the whole reason it lives
# at module scope here. Without it a screenshot script that toggles the theme
# silently rewrites the user's saved preference, and a recovery check plants
# files in the recovery folder the user's own session reads.
_SANDBOX = os.path.join(tempfile.gettempdir(), f"simplecad-scripts-{os.getuid()}")
os.environ.setdefault("XDG_CONFIG_HOME", os.path.join(_SANDBOX, "config"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_SANDBOX, "data"))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from simplecad.ui.qt_runtime import configure_qt_application  # noqa: E402

# Match the real bootstrap. Without this, stage children may acquire separate
# X11 windows, so scripted direct events can pass while physical clicks are
# routed to the wrong native child in the installed application.
configure_qt_application()


def grab_composited(widget):
    """Safely capture the top-level widget, including its OpenGL viewport."""
    from PySide6.QtWidgets import QApplication

    # Qt 6's QWidget.grab() includes QOpenGLWidget content, but a grab between a
    # display change and its paint can crash.  Drain events and force that paint
    # first, exactly as the final screenshot path does.
    viewport = getattr(getattr(widget, "stage", None), "viewport", None)
    if viewport is not None and viewport.is_ready:
        viewport.repaint()
    QApplication.processEvents()
    return widget.grab().toImage()


def run_and_capture(build, out_path: str, settle_ms: int = 1400, size=(1280, 820)):
    """Build a widget, show it, capture it to *out_path*, and quit.

    ``build(app)`` must return the top-level widget. Returns a dict of results.
    """
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication

    from simplecad.ui.viewport.occt_view import default_surface_format

    QSurfaceFormat.setDefaultFormat(default_surface_format())
    app = QApplication.instance() or QApplication(sys.argv[:1])
    widget = build(app)
    widget.setGeometry(40, 40, size[0], size[1])
    widget.show()
    widget.raise_()
    widget.activateWindow()

    result: dict[str, object] = {}

    def capture() -> None:
        try:
            # Screen grabs are useless here: XWayland returns a blank root
            # window. Capture through Qt's compositor instead.
            image = grab_composited(widget)
            colors = {
                image.pixel(x, y)
                for x in range(0, image.width(), 11)
                for y in range(0, image.height(), 11)
            }
            os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
            image.save(out_path)
            result.update(
                size=(image.width(), image.height()),
                distinct_colors=len(colors),
                path=os.path.abspath(out_path),
            )
        except Exception as exc:  # noqa: BLE001
            result["error"] = f"{type(exc).__name__}: {exc}"
        app.quit()

    QTimer.singleShot(settle_ms, capture)
    app.exec()
    return result
