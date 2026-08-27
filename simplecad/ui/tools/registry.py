"""Tool activation.

Every interactive operation is a tool with the same shape -- activate, preview,
commit, cancel -- so ``Select -> Drag -> Type -> Enter`` behaves identically
everywhere and Esc always gets you out.
"""

from __future__ import annotations

from typing import Callable

#: key -> factory. Populated by the tool modules.
TOOLS: dict[str, Callable] = {}


def register_tool(key: str):
    """Register a tool factory. Stackable, so one panel can serve several keys."""

    def decorate(factory):
        TOOLS[key] = factory
        return factory

    return decorate


def activate(window, key: str) -> None:
    """Activate the tool named *key* on *window*."""
    from . import (  # noqa: F401 - registers the built-in tools
        matching, measuring, modeling, shapes, sketching, splitting,
    )
    from ..printws import fit_panel, panel  # noqa: F401

    factory = TOOLS.get(key)
    if factory is None:
        window.set_hint(f"{key.replace('_', ' ').title()} is not available yet.")
        return

    window.close_tool_panels()
    panel = factory(window)
    # A plain function tool (like the shape picker) manages its own panel.
    if panel is not None and hasattr(panel, "is_tool_panel"):
        window.stage.add_overlay(panel, "top-left")
        panel.show()
        panel.setFocus()
