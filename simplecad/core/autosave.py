"""Autosave and crash recovery.

Work is written to a recovery file on a timer. On a clean exit the file is
removed; if it is still there next launch, the session did not end cleanly and
the work can be recovered.

The recovery file is a normal ``.scad3``, so recovery is just opening it -- there
is no second format to keep working, and a user can open one by hand if they
ever need to.
"""

from __future__ import annotations

import os
import time

APP_DIR = os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
    "simplecad",
)
RECOVERY_DIR = os.path.join(APP_DIR, "recovery")
#: Seconds between autosaves.
INTERVAL = 90


def recovery_path(session_id: str) -> str:
    os.makedirs(RECOVERY_DIR, exist_ok=True)
    return os.path.join(RECOVERY_DIR, f"session-{session_id}.scad3")


def write(document, session_id: str) -> str | None:
    """Autosave *document*. Returns the path written, or None on failure."""
    from .project import save

    try:
        return save(document, recovery_path(session_id))
    except Exception:  # noqa: BLE001 - autosave must never interrupt the user
        return None


def clear(session_id: str) -> None:
    """Remove this session's recovery file, on a clean exit."""
    try:
        path = os.path.join(RECOVERY_DIR, f"session-{session_id}.scad3")
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def pending() -> list[tuple[str, float]]:
    """Recovery files left behind by sessions that did not exit cleanly.

    Returns ``(path, modified_time)``, newest first.
    """
    if not os.path.isdir(RECOVERY_DIR):
        return []
    found = []
    for name in os.listdir(RECOVERY_DIR):
        if not name.endswith(".scad3"):
            continue
        path = os.path.join(RECOVERY_DIR, name)
        try:
            found.append((path, os.path.getmtime(path)))
        except OSError:
            continue
    return sorted(found, key=lambda item: item[1], reverse=True)


def describe_age(modified: float) -> str:
    seconds = max(0, time.time() - modified)
    if seconds < 90:
        return "less than a minute ago"
    if seconds < 3600:
        return f"{int(seconds // 60)} minutes ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} hours ago"
    return f"{int(seconds // 86400)} days ago"
