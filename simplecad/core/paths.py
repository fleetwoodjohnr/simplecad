"""Where SimpleCAD keeps its settings and its data, on each platform.

Linux keeps the XDG locations it always used, so an upgrade finds the settings,
recovery files and printer calibration it already had. Windows uses %APPDATA%
and macOS ~/Library/Application Support, as every other desktop app does there.
Program files never hold any of it, which is what lets an update replace them.
"""

from __future__ import annotations

import os
import sys


def config_dir() -> str:
    """Settings: preferences, shortcuts, recent files."""
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "SimpleCAD")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/SimpleCAD")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                        "simplecad")


def data_dir() -> str:
    """Data: crash recovery, printer calibration, update downloads."""
    if sys.platform in ("win32", "darwin"):
        return config_dir()
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
                        "simplecad")


def system_env() -> dict[str, str]:
    """The environment to start the system's own programs with.

    A frozen build runs with its bundled libraries first on ``LD_LIBRARY_PATH``
    (PyInstaller keeps the user's own value in ``LD_LIBRARY_PATH_ORIG``). A
    system program started with that loads SimpleCAD's libssl and libpcre2
    instead of its own: ``xdg-open`` then found no way to open a downloaded
    update, and the updater said it had handed the package over when nothing
    had happened.
    """
    env = dict(os.environ)
    if getattr(sys, "frozen", False):
        original = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if original:
            env["LD_LIBRARY_PATH"] = original
        else:
            env.pop("LD_LIBRARY_PATH", None)
    return env
