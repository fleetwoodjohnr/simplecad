"""Named parameters and expressions.

Every dimension in a SimpleCAD document is stored as an *expression* rather than
a bare number, so ``wall``, ``width / 2`` and ``hole + 0.4 mm`` are first-class
inputs. This module owns evaluation and, just as importantly, the dependency
extraction the rebuild engine uses to decide what a parameter edit dirties.

Evaluation is a whitelisted AST walk, not ``eval`` -- documents are files that
get shared, so an expression must never be able to execute arbitrary code.
"""

from __future__ import annotations

import ast
import math
from dataclasses import dataclass, field

from .units import Dimension, known_unit, parse_quantity, unit_factor

#: Functions an expression may call.
FUNCTIONS: dict[str, object] = {
    "sin": lambda d: math.sin(math.radians(d)),
    "cos": lambda d: math.cos(math.radians(d)),
    "tan": lambda d: math.tan(math.radians(d)),
    "asin": lambda x: math.degrees(math.asin(x)),
    "acos": lambda x: math.degrees(math.acos(x)),
    "atan": lambda x: math.degrees(math.atan(x)),
    "atan2": lambda y, x: math.degrees(math.atan2(y, x)),
    "sqrt": math.sqrt,
    "abs": abs,
    "min": min,
    "max": max,
    "floor": math.floor,
    "ceil": math.ceil,
    "round": round,
    "hypot": math.hypot,
    "log": math.log,
    "exp": math.exp,
}

#: Constants an expression may reference.
CONSTANTS: dict[str, float] = {"pi": math.pi, "e": math.e, "tau": math.tau}

_BIN_OPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a**b,
}


class ExpressionError(ValueError):
    """Raised when an expression cannot be parsed or evaluated."""


def _tokenize_units(text: str, dimension: Dimension) -> str:
    """Rewrite unit suffixes into multiplications Python's parser accepts.

    ``"hole + 0.4 mm"`` becomes ``"hole + 0.4 * 25.4/25.4"`` -- more precisely,
    ``0.4 * <factor>`` -- so the rest of the pipeline is plain arithmetic.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        char = text[i]
        # A number, possibly followed by a unit suffix.
        if char.isdigit() or (
            char == "." and i + 1 < n and text[i + 1].isdigit()
        ):
            start = i
            while i < n and (text[i].isdigit() or text[i] == "."):
                i += 1
            # Scientific notation: 1e-3
            if i < n and text[i] in "eE":
                probe = i + 1
                if probe < n and text[probe] in "+-":
                    probe += 1
                if probe < n and text[probe].isdigit():
                    i = probe
                    while i < n and text[i].isdigit():
                        i += 1
            number = text[start:i]
            gap = i
            while gap < n and text[gap] == " ":
                gap += 1
            unit_start = gap
            while gap < n and (text[gap].isalpha() or text[gap] in '°"'):
                gap += 1
            unit = text[unit_start:gap]
            dim = known_unit(unit) if unit else None
            if dim is not None:
                out.append(f"({number} * {unit_factor(unit, dim)!r})")
                i = gap
            else:
                out.append(number)
            continue
        out.append(char)
        i += 1
    return "".join(out)


def _walk(node: ast.AST, names: dict[str, float]) -> float:
    if isinstance(node, ast.Expression):
        return _walk(node.body, names)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(
            node.value, (int, float)
        ):
            raise ExpressionError(f"{node.value!r} is not a number")
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id in names:
            return float(names[node.id])
        if node.id in CONSTANTS:
            return CONSTANTS[node.id]
        raise ExpressionError(f"Unknown name {node.id!r}")
    if isinstance(node, ast.BinOp):
        handler = _BIN_OPS.get(type(node.op))
        if handler is None:
            raise ExpressionError("Unsupported operator")
        left, right = _walk(node.left, names), _walk(node.right, names)
        try:
            return float(handler(left, right))
        except ZeroDivisionError:
            raise ExpressionError("Division by zero") from None
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.USub):
            return -_walk(node.operand, names)
        if isinstance(node.op, ast.UAdd):
            return _walk(node.operand, names)
        raise ExpressionError("Unsupported unary operator")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
            raise ExpressionError("Unknown function")
        if node.keywords:
            raise ExpressionError("Functions take positional arguments only")
        args = [_walk(a, names) for a in node.args]
        try:
            return float(FUNCTIONS[node.func.id](*args))  # type: ignore[operator]
        except (TypeError, ValueError) as exc:
            raise ExpressionError(f"{node.func.id}: {exc}") from None
    raise ExpressionError("Expression contains something that is not allowed")


def dependencies(expression: str) -> set[str]:
    """Return the parameter names *expression* references.

    Used by the rebuild engine to build parameter -> feature edges. Unparseable
    text yields an empty set rather than raising: the caller reports the error
    when it evaluates.
    """
    try:
        tree = ast.parse(_tokenize_units(expression, Dimension.LENGTH), mode="eval")
    except SyntaxError:
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id not in CONSTANTS and node.id not in FUNCTIONS:
                found.add(node.id)
    return found


def evaluate(
    expression: str,
    names: dict[str, float] | None = None,
    dimension: Dimension = Dimension.LENGTH,
) -> float:
    """Evaluate *expression* to a value in internal units."""
    text = str(expression).strip()
    if not text:
        raise ExpressionError("Empty expression")
    # Fast path: a plain literal, with or without a unit.
    try:
        return parse_quantity(text, dimension)
    except ValueError:
        pass
    try:
        tree = ast.parse(_tokenize_units(text, dimension), mode="eval")
    except SyntaxError:
        raise ExpressionError(f"{text!r} is not a valid expression") from None
    return _walk(tree, names or {})


@dataclass
class Parameter:
    """A user-visible named value."""

    name: str
    expression: str
    dimension: Dimension = Dimension.LENGTH
    comment: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "expression": self.expression,
            "dimension": self.dimension.value,
            "comment": self.comment,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Parameter":
        return cls(
            name=data["name"],
            expression=data["expression"],
            dimension=Dimension(data.get("dimension", "length")),
            comment=data.get("comment", ""),
        )


class ParameterSet:
    """The document's named parameters, resolved together.

    Parameters may reference each other; evaluation resolves in dependency order
    and reports cycles rather than recursing forever.
    """

    def __init__(self) -> None:
        self._params: dict[str, Parameter] = {}
        self._values: dict[str, float] = {}
        self._errors: dict[str, str] = {}
        self._dirty = True

    # -- mutation -------------------------------------------------------
    def set(
        self,
        name: str,
        expression: str,
        dimension: Dimension = Dimension.LENGTH,
        comment: str = "",
    ) -> None:
        if not name.isidentifier():
            raise ExpressionError(f"{name!r} is not a valid parameter name")
        if name in CONSTANTS or name in FUNCTIONS:
            raise ExpressionError(f"{name!r} is reserved")
        self._params[name] = Parameter(name, str(expression), dimension, comment)
        self._dirty = True

    def remove(self, name: str) -> None:
        self._params.pop(name, None)
        self._dirty = True

    def rename(self, old: str, new: str) -> None:
        """Rename a parameter and rewrite references to it in other expressions."""
        if old not in self._params:
            raise KeyError(old)
        if not new.isidentifier():
            raise ExpressionError(f"{new!r} is not a valid parameter name")
        param = self._params.pop(old)
        param.name = new
        self._params[new] = param
        for other in self._params.values():
            if other.name != new and old in dependencies(other.expression):
                other.expression = _rename_in(other.expression, old, new)
        self._dirty = True

    # -- access ---------------------------------------------------------
    def __contains__(self, name: object) -> bool:
        return name in self._params

    def __iter__(self):
        return iter(self._params.values())

    def __len__(self) -> int:
        return len(self._params)

    def get(self, name: str) -> Parameter | None:
        return self._params.get(name)

    @property
    def values(self) -> dict[str, float]:
        """Every parameter's resolved value, recomputing if needed."""
        if self._dirty:
            self._resolve()
        return dict(self._values)

    @property
    def errors(self) -> dict[str, str]:
        """Parameter name -> friendly message, for those that failed."""
        if self._dirty:
            self._resolve()
        return dict(self._errors)

    def evaluate(
        self, expression: str, dimension: Dimension = Dimension.LENGTH
    ) -> float:
        """Evaluate an arbitrary expression against the current parameters."""
        return evaluate(expression, self.values, dimension)

    # -- internals ------------------------------------------------------
    def _resolve(self) -> None:
        self._values = {}
        self._errors = {}
        self._dirty = False

        order, cyclic = _topological_order(
            {n: dependencies(p.expression) & self._params.keys()
             for n, p in self._params.items()}
        )
        for name in cyclic:
            self._errors[name] = (
                f"{name!r} refers back to itself through another parameter."
            )
        for name in order:
            param = self._params[name]
            try:
                self._values[name] = evaluate(
                    param.expression, self._values, param.dimension
                )
            except ExpressionError as exc:
                self._errors[name] = str(exc)

    # -- serialisation --------------------------------------------------
    def to_list(self) -> list[dict]:
        return [p.to_dict() for p in self._params.values()]

    @classmethod
    def from_list(cls, data: list[dict]) -> "ParameterSet":
        out = cls()
        for entry in data:
            param = Parameter.from_dict(entry)
            out._params[param.name] = param
        out._dirty = True
        return out


def _rename_in(expression: str, old: str, new: str) -> str:
    """Replace whole-word references to *old* with *new*."""
    out: list[str] = []
    token = ""
    for char in expression + "\0":
        if char.isalnum() or char == "_":
            token += char
            continue
        if token:
            out.append(new if token == old else token)
            token = ""
        if char != "\0":
            out.append(char)
    return "".join(out)


def _topological_order(
    graph: dict[str, set[str]],
) -> tuple[list[str], set[str]]:
    """Kahn's algorithm. Returns (resolvable order, names caught in cycles)."""
    remaining = {n: set(deps) for n, deps in graph.items()}
    order: list[str] = []
    while True:
        ready = sorted(n for n, deps in remaining.items() if not deps)
        if not ready:
            break
        for name in ready:
            order.append(name)
            del remaining[name]
        for deps in remaining.values():
            deps.difference_update(ready)
    return order, set(remaining)
