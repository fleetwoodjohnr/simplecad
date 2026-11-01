"""User settings: recent files and keyboard shortcuts.

Stored as JSON in the XDG config directory, so it survives reinstalls and can be
edited by hand. Every read tolerates a missing or damaged file by falling back
to the defaults -- settings are a convenience, and a corrupted one must not stop
the application starting.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "simplecad",
)
SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.json")
MAX_RECENT = 10

#: The shortcuts SimpleCAD ships with. Anything here can be overridden.
DEFAULT_SHORTCUTS: dict[str, str] = {
    "search": "S",
    "dimension": "D",
    "extrude": "E",
    "move": "M",
    "hole": "H",
    "xray": "X",
    "fit_view": "F",
    "zoom_selection": "Ctrl+Shift+F",
    "undo": "Ctrl+Z",
    "redo": "Ctrl+Shift+Z",
    "redo_alt": "Ctrl+Y",
    "save": "Ctrl+S",
    "save_as": "Ctrl+Shift+S",
    "open": "Ctrl+O",
    "export": "Ctrl+E",
    "import": "Ctrl+I",
    "copy": "Ctrl+C",
    "paste": "Ctrl+V",
    "duplicate": "Ctrl+D",
    "group": "Ctrl+G",
    "ungroup": "Ctrl+Shift+G",
    "cancel": "Escape",
    "confirm": "Return",
    "confirm_alt": "Enter",
    "delete": "Delete",
    "delete_alt": "Backspace",
    "view_front": "1",
    "view_back": "2",
    "view_left": "3",
    "view_right": "4",
    "view_top": "5",
    "view_bottom": "6",
    "view_iso": "7",
}

#: What each shortcut does, as the Settings window lists it.
SHORTCUT_LABELS: dict[str, str] = {
    "search": "Command search", "dimension": "Dimension (sketch)", "extrude": "Pull",
    "move": "Move", "hole": "Hole", "xray": "X-ray", "fit_view": "Zoom to fit",
    "zoom_selection": "Zoom to selection", "undo": "Undo", "redo": "Redo",
    "redo_alt": "Redo (alternative)", "save": "Save", "save_as": "Save as",
    "open": "Open", "export": "Export", "import": "Import", "copy": "Copy",
    "paste": "Paste", "duplicate": "Duplicate", "group": "Group", "ungroup": "Ungroup",
    "cancel": "Cancel / close tool", "confirm": "Confirm", "confirm_alt": "Confirm (keypad)",
    "delete": "Delete", "delete_alt": "Delete (alternative)", "view_front": "Front view",
    "view_back": "Back view", "view_left": "Left view", "view_right": "Right view",
    "view_top": "Top view", "view_bottom": "Bottom view", "view_iso": "Isometric view",
}

#: Actions whose shortcut must yield while the user is typing.
#:
#: Single characters are the obvious case -- a bare "S" would swallow the letter
#: out of an expression. But Return and Delete need it just as much: Return has
#: to commit the field being typed into rather than finish a spline, and Delete
#: has to remove a character rather than a body. Ctrl+D is forward-delete in
#: an X11 text field. Other modified keys (Ctrl+S) are safe: they cannot be typed.
YIELDS_TO_TYPING = {
    key for key, binding in DEFAULT_SHORTCUTS.items() if len(binding) == 1
} | {"confirm", "confirm_alt", "delete", "delete_alt", "duplicate"}

#: Kept for callers that only care about the literal single-character bindings.
SINGLE_KEY = {
    key for key, binding in DEFAULT_SHORTCUTS.items() if len(binding) == 1
}

#: Actions whose shortcut must only fire when the 3D view has focus.
#:
#: Guarding the handler is not enough for these. Qt's shortcut map *consumes*
#: the key before the focused widget ever sees it, so a guard that declines to
#: act still swallows the keystroke. Plain characters are exempt -- Qt gives
#: those to a focused text field first -- but Return and Delete are not, so a
#: window-wide Return shortcut makes it impossible to press Enter in any inline
#: editor. Scoping them to the viewport is the only thing that actually works.
VIEWPORT_ONLY = {"confirm", "confirm_alt", "delete", "delete_alt"}


def _read() -> dict:
    try:
        with open(SETTINGS_PATH) as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write(data: dict) -> bool:
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        temporary = SETTINGS_PATH + ".part"
        with open(temporary, "w") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, SETTINGS_PATH)
        return True
    except OSError:
        return False


# ----------------------------------------------------------------------
# Recent files
# ----------------------------------------------------------------------
def recent_files() -> list[str]:
    """Recently opened projects, newest first, skipping any that have gone."""
    stored = _read().get("recent", [])
    return [path for path in stored if isinstance(path, str) and os.path.exists(path)]


def remember_file(path: str) -> None:
    """Put a project at the top of the recent list."""
    path = os.path.abspath(path)
    data = _read()
    recent = [p for p in data.get("recent", []) if p != path]
    recent.insert(0, path)
    data["recent"] = recent[:MAX_RECENT]
    _write(data)


def forget_files() -> None:
    data = _read()
    data["recent"] = []
    _write(data)


# ----------------------------------------------------------------------
# Shortcuts
# ----------------------------------------------------------------------
def shortcuts() -> dict[str, str]:
    """The active bindings: defaults, with any user overrides applied."""
    bindings = dict(DEFAULT_SHORTCUTS)
    stored = _read().get("shortcuts", {})
    if isinstance(stored, dict):
        for action, binding in stored.items():
            if action in bindings and isinstance(binding, str) and binding:
                bindings[action] = binding
    return bindings


def set_shortcut(action: str, binding: str) -> bool:
    """Override one binding. Returns False if the action is not known."""
    if action not in DEFAULT_SHORTCUTS:
        return False
    data = _read()
    overrides = data.get("shortcuts")
    if not isinstance(overrides, dict):
        overrides = {}
    overrides[action] = binding
    data["shortcuts"] = overrides
    return _write(data)


def reset_shortcuts() -> None:
    data = _read()
    data.pop("shortcuts", None)
    _write(data)


def reset_shortcut(action: str) -> bool:
    """Put one action back on its default binding."""
    data = _read()
    overrides = data.get("shortcuts")
    if isinstance(overrides, dict) and action in overrides:
        del overrides[action]
        return _write(data)
    return True


def conflicts(bindings: dict[str, str] | None = None) -> dict[str, list[str]]:
    """Bindings claimed by more than one action."""
    bindings = bindings or shortcuts()
    seen: dict[str, list[str]] = {}
    for action, binding in bindings.items():
        seen.setdefault(binding, []).append(action)
    return {b: actions for b, actions in seen.items() if len(actions) > 1}


# ----------------------------------------------------------------------
# View preferences
# ----------------------------------------------------------------------
def view_preference(name: str, default: bool = True) -> bool:
    """A remembered on/off view setting, such as the ground grid."""
    stored = _read().get("view", {})
    value = stored.get(name) if isinstance(stored, dict) else None
    return default if not isinstance(value, bool) else value


def set_view_preference(name: str, value: bool) -> bool:
    data = _read()
    view = data.get("view")
    if not isinstance(view, dict):
        view = {}
    view[name] = bool(value)
    data["view"] = view
    return _write(data)


# ----------------------------------------------------------------------
# Interface preferences
# ----------------------------------------------------------------------
def ui_preference(name: str, default: bool = True) -> bool:
    """A remembered shell setting, such as the model-browser expansion."""
    stored = _read().get("ui", {})
    value = stored.get(name) if isinstance(stored, dict) else None
    return default if not isinstance(value, bool) else value


def set_ui_preference(name: str, value: bool) -> bool:
    """Store a boolean shell preference without disturbing other settings."""
    data = _read()
    ui = data.get("ui")
    if not isinstance(ui, dict):
        ui = {}
    ui[name] = bool(value)
    data["ui"] = ui
    return _write(data)


def theme_choice() -> str | None:
    """The theme the user picked, or None if they have never said.

    Kept apart from :func:`view_preference`, which is bool-only. None is a
    meaningful third answer here and not the same as either mode: it means
    "follow the desktop", which is what a fresh install should do.
    """
    value = _read().get("theme")
    return value if value in ("light", "dark", "system") else None


def set_theme_choice(value: str) -> bool:
    data = _read()
    data["theme"] = value
    return _write(data)


#: Length units a value can be shown in. Any unit can always be typed.
DISPLAY_UNITS = ("mm", "cm", "m", "in")


def display_unit() -> str:
    """The length unit fields and readouts show values in."""
    value = _read().get("display_unit")
    return value if value in DISPLAY_UNITS else "mm"


def set_display_unit(value: str) -> bool:
    if value not in DISPLAY_UNITS:
        return False
    data = _read()
    data["display_unit"] = value
    from .units import set_display_unit as apply

    apply(value)
    return _write(data)


# ----------------------------------------------------------------------
# Modelling preferences
# ----------------------------------------------------------------------
#: Numeric preferences, their defaults and the range a stored value must lie in.
NUMBER_PREFERENCES: dict[str, tuple[float, float, float]] = {
    "rotation_snap": (90.0, 1.0, 180.0),   # degrees between magnetic detents
    "move_step": (1.0, 0.01, 100.0),       # mm per gizmo translation step
    "history_depth": (200, 10, 2000),      # undo steps kept
}


def number_preference(name: str) -> float:
    """A numeric modelling preference, falling back to its default if unset or out of range."""
    default, low, high = NUMBER_PREFERENCES[name]
    stored = _read().get("modelling")
    value = stored.get(name) if isinstance(stored, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        return default
    return type(default)(value)


def set_number_preference(name: str, value: float) -> bool:
    default, low, high = NUMBER_PREFERENCES[name]
    data = _read()
    modelling = data.get("modelling")
    if not isinstance(modelling, dict):
        modelling = {}
    modelling[name] = type(default)(min(high, max(low, value)))
    data["modelling"] = modelling
    return _write(data)
