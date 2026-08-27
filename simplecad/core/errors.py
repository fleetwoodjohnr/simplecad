"""Turning kernel failures into things a person can act on.

OCCT reports problems in its own vocabulary -- ``BRep_API: command not done``,
``StdFail_NotDone``, ``Standard_ConstructionError``. None of that tells a user
what to change. Everything geometric therefore runs through :func:`guard`, which
catches the kernel exception and re-raises a :class:`CadError` carrying a plain
sentence and, wherever possible, a concrete suggestion.

The raw text is kept on the exception for the log, never shown by default.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

log = logging.getLogger("simplecad")


class CadError(Exception):
    """A failure that can be explained to the user."""

    def __init__(
        self,
        message: str,
        *,
        suggestion: str = "",
        detail: str = "",
        operation: str = "",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.suggestion = suggestion
        self.detail = detail
        self.operation = operation

    def __str__(self) -> str:
        return (
            f"{self.message} {self.suggestion}".strip()
            if self.suggestion
            else self.message
        )


class ReferenceLost(CadError):
    """A stored reference to a face/edge/vertex no longer resolves.

    Raised by the naming layer rather than guessing at a replacement, because a
    fillet silently moving to the wrong edge is far worse than one that stops
    and asks.
    """


class RebuildError(CadError):
    """A feature failed to rebuild. The last valid model is preserved."""


#: Substrings of OCCT messages mapped to (message, suggestion).
#: Ordered: the first match wins, so put specific patterns before general ones.
_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (
        "bopalgo",
        "These shapes could not be combined.",
        "They may not overlap, or may touch along only an edge or point. "
        "Try moving one slightly so the parts genuinely intersect.",
    ),
    (
        "boolean",
        "The boolean operation failed.",
        "Check that the bodies actually overlap and neither is self-intersecting.",
    ),
    (
        "chfi3d",
        "The fillet or chamfer is too large for the surrounding geometry.",
        "Try a smaller radius, or apply it to fewer edges at once.",
    ),
    (
        "fillet",
        "The fillet is too large for the surrounding geometry.",
        "Try a smaller radius.",
    ),
    (
        "thickness",
        "The shell thickness does not fit this shape.",
        "Use a thinner wall, or remove faces with tight internal corners.",
    ),
    (
        "pipe",
        "The swept shape intersects itself.",
        "Reduce the profile size or increase the radius of the path.",
    ),
    (
        "notdone",
        "The operation could not be completed on this geometry.",
        "Try adjusting the size or position of the inputs slightly.",
    ),
    (
        "construction",
        "Those values do not describe a valid shape.",
        "Check for zero or negative dimensions.",
    ),
    (
        "nullobject",
        "One of the inputs is missing.",
        "Re-select the geometry for this feature.",
    ),
    (
        "domain",
        "A value is outside the range this operation accepts.",
        "Check for zero or negative dimensions.",
    ),
)


def translate(exc: BaseException, operation: str = "") -> CadError:
    """Map any exception onto a :class:`CadError` with a friendly message."""
    if isinstance(exc, CadError):
        return exc
    raw = f"{type(exc).__name__}: {exc}"
    haystack = raw.lower()
    for needle, message, suggestion in _PATTERNS:
        if needle in haystack:
            return CadError(
                message, suggestion=suggestion, detail=raw, operation=operation
            )
    label = f"The {operation} operation" if operation else "The operation"
    return CadError(
        f"{label} did not succeed on this geometry.",
        suggestion="Try adjusting the inputs slightly, or undo and take a different approach.",
        detail=raw,
        operation=operation,
    )


@contextmanager
def guard(operation: str):
    """Run a kernel call, re-raising failures as :class:`CadError`.

    >>> with guard("fillet"):
    ...     builder.Build()
    """
    try:
        yield
    except CadError:
        raise
    except BaseException as exc:  # noqa: BLE001 - kernel throws non-Exception types
        error = translate(exc, operation)
        log.debug("%s failed: %s", operation or "operation", error.detail)
        raise error from exc


def check_done(builder, operation: str) -> None:
    """Assert an OCCT builder finished, with a friendly failure if not."""
    try:
        done = builder.IsDone()
    except Exception:  # noqa: BLE001 - not every builder exposes IsDone
        return
    if not done:
        raise translate(
            RuntimeError(f"{operation}: StdFail_NotDone"), operation
        )
