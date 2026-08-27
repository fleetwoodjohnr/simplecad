"""The sketch constraint solver.

OCCT has nothing for this, so it is built here: the constraints are a system of
residual equations, solved by Levenberg–Marquardt, and the sketch's state is read
off the Jacobian.

That last part is what the UI needs. "Fully constrained" is not a bookkeeping
count of constraints — it is the statement that the Jacobian has full column
rank, so no motion of the geometry leaves every constraint satisfied. Counting
constraints would call a sketch with two identical dimensions fully constrained;
rank does not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from .constraints import _View

#: Residuals below this are considered satisfied (millimetres).
TOLERANCE = 1e-7
#: Singular values below this fraction of the largest count as zero.
RANK_TOLERANCE = 1e-9


class SketchState(str, Enum):
    UNCONSTRAINED = "unconstrained"
    PARTIAL = "partially constrained"
    FULLY = "fully constrained"
    CONFLICTING = "conflicting"
    OVER = "over-constrained"

    def label(self) -> str:
        return {
            SketchState.UNCONSTRAINED: "Unconstrained",
            SketchState.PARTIAL: "Partially constrained",
            SketchState.FULLY: "Fully constrained",
            SketchState.CONFLICTING: "Conflicting constraints",
            SketchState.OVER: "Redundant constraints",
        }[self]


@dataclass
class SolveResult:
    state: SketchState
    dof: int
    solved: bool
    residual: float
    message: str = ""
    conflicting: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.state is not SketchState.CONFLICTING


def residual_vector(sketch, values) -> np.ndarray:
    view = _View(sketch, values)
    out: list[float] = []
    for constraint in sketch.constraints:
        if not constraint.driving:
            continue
        out.extend(constraint.residuals(view))
    return np.asarray(out, dtype=float)


def jacobian(sketch, values, epsilon: float = 1e-7) -> np.ndarray:
    """Numerical Jacobian of the residuals with respect to the parameters."""
    values = np.asarray(values, dtype=float)
    base = residual_vector(sketch, values)
    matrix = np.zeros((base.size, values.size))
    for column in range(values.size):
        stepped = values.copy()
        step = epsilon * max(1.0, abs(stepped[column]))
        stepped[column] += step
        matrix[:, column] = (residual_vector(sketch, stepped) - base) / step
    return matrix


def degrees_of_freedom(sketch, values=None) -> tuple[int, int, int]:
    """Return ``(dof, rank, rows)`` for the current configuration."""
    values = sketch.to_vector() if values is None else values
    if not sketch.constraints:
        return (len(values), 0, 0)
    matrix = jacobian(sketch, values)
    if matrix.size == 0:
        return (len(values), 0, 0)
    singular = np.linalg.svd(matrix, compute_uv=False)
    largest = singular[0] if singular.size else 0.0
    rank = int((singular > max(largest * 1e-8, RANK_TOLERANCE)).sum())
    return (len(values) - rank, rank, matrix.shape[0])


def solve(sketch, max_iterations: int = 200) -> SolveResult:
    """Solve the sketch in place, then report what state it is in."""
    from scipy.optimize import least_squares

    values = np.asarray(sketch.to_vector(), dtype=float)
    if values.size == 0:
        return SolveResult(SketchState.FULLY, 0, True, 0.0, "Empty sketch.")

    driving = [c for c in sketch.constraints if c.driving]
    if not driving:
        dof = values.size
        return SolveResult(
            SketchState.UNCONSTRAINED, dof, True, 0.0,
            f"{dof} degrees of freedom.",
        )

    outcome = least_squares(
        lambda v: residual_vector(sketch, v),
        values,
        method="lm" if residual_vector(sketch, values).size >= values.size else "trf",
        xtol=1e-12, ftol=1e-12, gtol=1e-12,
        max_nfev=max_iterations * max(1, values.size),
    )
    sketch.from_vector(outcome.x)

    residuals = residual_vector(sketch, outcome.x)
    worst = float(np.max(np.abs(residuals))) if residuals.size else 0.0
    solved = worst <= 1e-5

    dof, rank, rows = degrees_of_freedom(sketch, outcome.x)

    if not solved:
        return SolveResult(
            SketchState.CONFLICTING, dof, False, worst,
            "These constraints cannot all be satisfied at once. "
            "Remove the most recent one to continue.",
            conflicting=_likely_culprits(sketch, residuals),
        )
    if rank < rows:
        # Satisfiable, but some constraints repeat information others already give.
        return SolveResult(
            SketchState.OVER, dof, True, worst,
            f"{rows - rank} constraint(s) repeat information already implied by "
            "the others. The sketch still solves.",
        )
    if dof == 0:
        return SolveResult(
            SketchState.FULLY, 0, True, worst, "Fully constrained."
        )
    return SolveResult(
        SketchState.PARTIAL, dof, True, worst,
        f"{dof} degree{'s' if dof != 1 else ''} of freedom remaining.",
    )


def _likely_culprits(sketch, residuals) -> list[str]:
    """Constraint ids whose residuals are furthest from satisfied."""
    ranked: list[tuple[float, str]] = []
    index = 0
    view = _View(sketch, sketch.to_vector())
    for constraint in sketch.constraints:
        if not constraint.driving:
            continue
        count = len(constraint.residuals(view))
        slice_ = residuals[index:index + count]
        index += count
        if slice_.size:
            ranked.append((float(np.max(np.abs(slice_))), constraint.id))
    ranked.sort(reverse=True)
    return [identifier for error, identifier in ranked if error > 1e-5][:3]
