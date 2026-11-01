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
