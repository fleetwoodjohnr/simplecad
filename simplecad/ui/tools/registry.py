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


def load() -> dict[str, Callable]:
    """Import the tool modules, so ``TOOLS`` describes everything there is.

    Registration happens as a side effect of importing, which means asking
    ``TOOLS`` a question before this has run gets the wrong answer. That is not
    hypothetical: ``run_action`` decides between a tool and a command by
    looking the key up, so on a cold start the *first* thing launched from the
    contextual bar or from search fell through to "not available yet" -- and
    picking an edge and pressing Fillet is exactly that first thing.
    """
    from . import (  # noqa: F401 - registers the built-in tools
        clips, combining, matching, measuring, modeling, arranging, shapes, sketching,
        splitting, text,
    )
    from ..printws import fit_panel, panel  # noqa: F401

    return TOOLS


def is_tool(key: str) -> bool:
    """Whether *key* names a tool, loading the modules if need be."""
    return key in load()


def activate(window, key: str) -> None:
    """Activate the tool named *key* on *window*."""
    factory = load().get(key)
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
