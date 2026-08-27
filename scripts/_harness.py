"""Shared helpers for scripted screenshots of SimpleCAD widgets.

UI work needs to be looked at, not just asserted, so every visual check in this
project goes through here: build a widget, let it settle, save a PNG.
"""

from __future__ import annotations

import os
import sys

# OCCT in the cadquery-ocp wheel is a GLX build, so Qt must speak X11.
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
# Scripted runs must never stop on the recovery dialog.
os.environ.setdefault("SIMPLECAD_NO_RECOVERY", "1")
os.environ.pop("QT_XCB_GL_INTEGRATION", None)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


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
            # Qt 6's QWidget.grab() does include QOpenGLWidget content, so this
            # captures chrome and viewport together. (Screen grabs are useless
            # here: XWayland returns a blank root window.)
            #
            # grab() crashes if it runs between a display change and the paint
            # that follows it, so drain pending events and force the viewport to
            # paint synchronously first.
            viewport = getattr(getattr(widget, "stage", None), "viewport", None)
            if viewport is not None and viewport.is_ready:
                viewport.repaint()
            QApplication.processEvents()
            image = widget.grab().toImage()
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
