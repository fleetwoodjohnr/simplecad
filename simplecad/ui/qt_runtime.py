"""Qt application settings that must precede ``QApplication``.

The OCCT viewport needs access to a real X11 window id, but it uses the
effective handle of the top-level native ancestor rather than becoming a
native child itself.  If a stage widget is accidentally promoted, this guard
also prevents Qt from promoting all of its siblings: a full-size paint-only
overlay with its own X11 input region can otherwise receive a button before Qt
can honour ``WA_TransparentForMouseEvents``.
"""

from __future__ import annotations


def configure_qt_application() -> None:
    """Keep stage widgets in Qt's composited, Qt-hit-tested hierarchy."""
    from PySide6.QtCore import QCoreApplication, Qt

    QCoreApplication.setAttribute(
        Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings, True
    )
