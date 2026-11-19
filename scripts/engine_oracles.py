#!/usr/bin/env python
"""Write the Python app's answers that the Rust engine's tests hold it to.

    .venv/bin/python scripts/engine_oracles.py [project.scad3 ...]

engine/scad/tests/data/dependencies.json: every string input and parameter
expression of the given projects (the Desktop ones when this was written), plus
edge cases, with the parameter names ``params.dependencies`` finds in each.
engine/scad/tests/data/dependencies_fuzz.json: the same for seeded random
expressions, most of them not valid Python.
engine/scad/tests/data/pyjson.json: values and how ``result_store.digest``
writes them (``json.dumps`` with sorted keys and no spaces).
engine/scad/tests/corpus/: the documents tests/test_scenarios.py builds, saved
compact, each with the signatures the Python app computes for it.
engine/scad/tests/data/normalised.json: each corpus document, and a copy cut
back to format 1 (no ids, bindings or counters), with what ``Document.from_dict``
then ``to_dict`` make of it and the signatures of the result.
engine/scad/tests/data/new_box.json: the model.json of a new project with one
box, made as the Shape panel makes it (``ShapePanel.commit``).
engine/scad/tests/data/evaluate.json: expressions with the names they may use,
evaluated as ``params.evaluate`` does in each dimension: the value, or the
error's type and text. The given projects' inputs with their parameters, edge
cases, and seeded random arithmetic.
engine/scad/tests/data/parameters.json: parameter sets resolved as
``ParameterSet`` resolves them: each value, or its error.
engine/scad/tests/data/pulls_undone.json: that box with its top pulled 5 mm
then -3 mm, the second pull undone, as saved: the history with its redo step.
"""

from __future__ import annotations

import json
import os
import random
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "engine", "scad", "tests", "data")

EDGE_CASES = {
    "15 mm", "2*pi*r", "sqrt(a**2 + b**2)", "living", "Hello World", "M3", "M3x0.5", "None", "True",
    "a.b", "x if y else z", "-w/2 + 3 mm", "10 deg", "1e-3 in", "", "   ", "f(x)(y)", "a[0]", "lambda: 1",
    "width + height", "Arial Bold", "_hidden", "2(3)", "3 mm 4", "1_000", "0x10", "é", "a and b", "not a",
    "[a, b]", "{a: 1}", "M3mm", "lambda x: x + y", "[x for x in y]", "f(*a, **b, c=d)", "f(c=d, e)",
    "(a := 1)", "a := 1", "await a", "a,", " a", "a\n", "a #c", "f\"{a}\"", "r'x'", "b'\\x'", "1.e5",
    "01", "00", "1__0", "5j", "a not in b", "a is not b", "...", "a[1:2, ::3]", "{**a}", "(*a,)",
    "sans-serif", "builtin.v1", "12.5", "0.4 mm", 'hole + 0.4"', "45°", "2 rad + x",
}

TOKENS = [
    "a", "b", "width", "top", "pi", "sin", "e", "mm", "_x", "x1", "None", "True", "not", "and", "or",
    "if", "else", "lambda", "in", "is", "for", "await", "yield", "1", "2.5", "1e3", "0x1F", "00", "01",
    "1_0", ".5", "5j", "3 mm", "2in", "10 deg", "1.e5", "'x'", '"y"', "f'{a}'", "b'A'", "r'd'", "'u",
    "+", "-", "*", "/", "//", "%", "**", "@", "<<", ">>", "&", "|", "^", "~", "<", ">", "<=", "==", "!=",
    ":=", ",", ":", ".", ";", "=", "(", ")", "[", "]", "{", "}", " ", "  ", "#c", "\n", "$", "!",
]


def project_texts(paths):
    texts = set()
    for path in paths:
        model = json.loads(zipfile.ZipFile(path).read("model.json"))
        for feature in model.get("features", []):
            texts.update(v for v in feature.get("inputs", {}).values() if isinstance(v, str))
        texts.update(p.get("expression", "") for p in model.get("parameters", []))
    return texts


def fuzz(count=4000, seed=20261119):
    rng = random.Random(seed)
    found = set()
    while len(found) < count:
        found.add("".join(rng.choice(TOKENS) + rng.choice(["", " "]) for _ in range(rng.randint(1, 7))).rstrip(" "))
    return found


def write(name, texts):
    from simplecad.core.params import dependencies

    cases = [[text, sorted(dependencies(text))] for text in sorted(texts)]
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, name), "w", encoding="utf-8") as handle:
        json.dump(cases, handle, indent=0, ensure_ascii=False)
        handle.write("\n")
    print(f"{name}: {len(cases)} texts, {sum(1 for _, d in cases if d)} name parameters")


def json_cases(seed=20261119):
    """Floats across the whole range, the edges of repr's fixed notation, and strings to escape."""
    rng = random.Random(seed)
    floats = [0.0, -0.0, 0.1, 1 / 3, 100.0, 1e16, 1e15, 1234567890123456.0, 12345678901234567.0, 0.0001,
              0.00001, 5e-324, 1.7976931348623157e308, -2.5, 25.4, 0.0254, 57.29577951308232, 1e22, 1e-7]
    floats += [rng.uniform(1, 10) * 10.0 ** rng.randint(-30, 30) * rng.choice([1, -1]) for _ in range(3000)]
    floats += [round(rng.uniform(-500, 500), rng.randint(0, 6)) for _ in range(1000)]
    floats += [rng.randint(-10**6, 10**6) / 8.0 for _ in range(200)]
    strings = ["", "plain", 'quote " and \\ back', "tab\tnew\nline\r", "\x00\x1f\x7f", "é", "日本",
               "emoji 🙂", "\u2028", "Ω≈ç√", "/slash", "\b\f"]
    nested = {"b": [1, 2.5, None, True, False], "a": {"z": "é", "é": 1, "A": -0.0}, "": "empty key"}
    values = floats + strings + [nested, [], {}, 10**15, -7, [[1.0, [2.0e-9]], {"x": 1e300}]]
    return [[v, json.dumps(v, sort_keys=True, separators=(",", ":"))] for v in values]


SCENARIOS = ("test_1_cube_slice_duplicate_rotate_align", "test_2_cylinder_halves_move_independently_and_stack",
             "test_3_fillet_move_edit_duplicate_reopen", "test_4_boolean_then_rotate_duplicate_texture_undo",
             "test_5_copy_several_delete_one_undo_independent", "test_9_slice_duplicate_and_edit_both_halves_independently",
             "test_10_mirror_align_and_plate_survive_undo_redo_and_reopen")


def corpus():
    """Run the scenario tests, keep every document they build, and save each one."""
    import inspect
    import pathlib
    import tempfile

    from simplecad.core.project import save
    from simplecad.core.result_store import signatures
    from tests import test_scenarios

    built = []
    original = test_scenarios.Model.__init__

    def remember(self):
        original(self)
        built.append(self)

    test_scenarios.Model.__init__ = remember
    folder = os.path.join(ROOT, "engine", "scad", "tests", "corpus")
    os.makedirs(folder, exist_ok=True)
    expected = {}
    for name in SCENARIOS:
        built.clear()
        test = getattr(test_scenarios, name)
        with tempfile.TemporaryDirectory() as scratch:
            # The scenarios that reopen a file take pytest's tmp_path.
            test(*([pathlib.Path(scratch)] if inspect.signature(test).parameters else []))
        for i, model in enumerate(built):
            file = f"{name[5:]}-{i}.scad3"
            save(model.d, os.path.join(folder, file), compact=True)
            expected[file] = signatures(model.d)
    with open(os.path.join(folder, "signatures.json"), "w", encoding="utf-8") as handle:
        json.dump(expected, handle, indent=1, sort_keys=True)
        handle.write("\n")
    print(f"corpus: {len(expected)} documents, {sum(len(s) for s in expected.values())} signatures")


def as_format_1(model):
    """*model* as a format 1 file has it: no output ids, bindings, ids or counters."""
    def strip(value):
        if isinstance(value, dict) and "__ref__" in value:
            return {"__ref__": {k: v for k, v in value["__ref__"].items() if k != "body_id"}}
        if isinstance(value, dict) and "__body__" in value:
            return {"__body__": value["__body__"]}
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items()}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value

    old = {"format": 1, "title": model.get("title", "Untitled"), "parameters": model.get("parameters", []),
           "metadata": model.get("metadata", {}), "application": "SimpleCAD"}
    old["features"] = [{k: strip(v) for k, v in f.items() if k not in ("output_ids", "bindings")}
                       for f in model.get("features", [])]
    old["bodies"] = [{k: v for k, v in b.items() if k != "id"} for b in model.get("bodies", [])]
    old["groups"] = [{k: v for k, v in g.items() if k not in ("id", "member_ids")} for g in model.get("groups", [])]
    return old


def normalised():
    from simplecad.core.document import Document
    from simplecad.core.result_store import signatures

    folder = os.path.join(ROOT, "engine", "scad", "tests", "corpus")
    cases = []
    for file in sorted(os.listdir(folder)):
        if not file.endswith(".scad3"):
            continue
        model = json.loads(zipfile.ZipFile(os.path.join(folder, file)).read("model.json"))
        for name, source in ((file, model), (file + " as format 1", as_format_1(model))):
            document = Document.from_dict(json.loads(json.dumps(source)))
            cases.append({"name": name, "model": source, "document": document.to_dict(),
                          "signatures": signatures(document)})
    with open(os.path.join(DATA, "normalised.json"), "w", encoding="utf-8") as handle:
        json.dump(cases, handle, indent=0, sort_keys=True)
        handle.write("\n")
    print(f"normalised.json: {len(cases)} documents")


def new_box(pulls=(), undo=0, out="new_box.json"):
    import tempfile

    from simplecad.core.document import Document
    from simplecad.core.history import History
    from simplecad.core.project import save
    from simplecad.core.rebuild import Rebuilder
    from simplecad.core.units import Dimension, format_quantity
    from simplecad.kernel.primitives import BoxFeature

    document = Document("Untitled")
    history = History(document, limit=100)
    inputs = {key: format_quantity(value, Dimension.LENGTH)
              for key, value in (("width", 40.0), ("depth", 30.0), ("height", 20.0))}
    feature = BoxFeature(inputs=inputs)
    feature.name = document.unique_name("Box")
    feature.outputs = [feature.name]
    history.record(feature.label)
    document.add_feature(feature)
    rebuilder = Rebuilder(document)
    rebuilder.rebuild()
    for distance in pulls:
        from simplecad.core.document import BodyRef
        from simplecad.core.naming import fingerprint, make_ref, sub_shapes
        from simplecad.kernel.operations import PushPullFeature

        shape = document.bodies["Box"].shape
        top = max(sub_shapes(shape, "face"), key=lambda f: fingerprint(f, "face").center[2])
        reference = make_ref(shape, top, document.producer_of("Box").id, body="Box")
        pull = PushPullFeature(inputs={"body": BodyRef("Box"), "face": reference, "distance": distance},
                               outputs=["Box"])
        history.record(pull.label)
        document.add_feature(pull)
        rebuilder.rebuild()
    for _ in range(undo):
        rebuilder.invalidate(history.undo())
        rebuilder.rebuild()
    with tempfile.TemporaryDirectory() as scratch:
        path = save(document, os.path.join(scratch, "new.scad3"))
        model = json.loads(zipfile.ZipFile(path).read("model.json"))
    with open(os.path.join(DATA, out), "w", encoding="utf-8") as handle:
        json.dump(model, handle, indent=1, sort_keys=True)
        handle.write("\n")
    print(out, "written")


EVAL_EDGE_CASES = [
    "40 mm", "-5 mm", "+-5", "--5", "5 MM", "5 deg", "10°", '0.5"', "2 in + 3 mm", "1e3", "1E-3 m", "5.", ".5",
    "0005", "00", "01", "1_000", "1_000 mm", "0x1F", "0o17", "0b101", "5j", "1.5J", "1e3j", "e", "E", "pi", "tau",
    "inf", "nan", "1inf", "5e", "5em", "1e400", "-1e400", "1e-400", "10**400", "10.0**400", "2**0.5", "(-8)**(1/3)",
    "0**-1", "0.0**-1", "1/0", "1//0", "1%0", "7//2", "-7//2", "7//-2", "-7.5//2", "7%3", "-7%3", "7%-3", "-0.0%5",
    "0%-5", "5.5%-2.25", "1e308*10", "-0", "-0.0", "0*-1", "2**-1074", "x + 1", "x*2 - y/3", "-x", "+x", "~x",
    "not x", "x and y", "x or y", "x if y else z", "x < y", "x @ y", "x << 1", "x & 1", "x | y", "x ^ y", "x >> 1",
    "lambda: 1", "(1, 2)", "1, 2", "[1]", "{1}", "{1: 2}", "x[0]", "x.y", "f(x)", "sin(30)", "cos(60 deg)",
    "tan(45)", "asin(0.5)", "acos(2)", "atan(1)", "atan2(1, 1)", "atan2(1)", "sqrt(2)", "sqrt(-1)", "abs(-3)",
    "abs()", "min(3, 1, 2)", "min()", "min(1)", "max(1, 2)", "floor(2.5)", "floor(-2.5)", "ceil(2.1)", "round(2.5)",
    "round(3.5)", "round(-2.5)", "round(2.5, 1)", "hypot(3, 4)", "hypot()", "hypot(1, 2, 3)", "log(10)", "log(8, 2)",
    "log(0)", "log(-1)", "exp(1)", "exp(1000)", "floor(1e400)", "floor(nan)", "sin(x=1)", "sin(*x)", "sin(1, 2)",
    "sin()", "sqrt(x for x in y)", "'a'", "'a' 'b'", '"it\'s"', "b'A'", "f'{x}'", "True", "None", "...", "x := 1",
    "(x := 1)", "await x", "", "   ", "5 mm 4", "1.2.3", ".", "-", "x +", "(", "()", "w / 2 + 3 mm", "hole + 0.4 mm",
    "2(3)", "sin", "x(1)", "sin(1)(2)", "(sin)(1)", "-2**2", "2**-1", "2**3**2", "(1)", "((x))", "1 - -1", "3 % 2 ** 2",
    "45°", "2 rad", "1 radian + 1 deg", "100 thou", "1 ft", "1 m", "x mm", "2 * pi * x", "round(1e300)",
    "round(nan)", "round(inf)", "abs(1, 2)", "max(nan, 1)", "max(1, nan)", "1e16 + 1", "0.1 + 0.2",
]


def _eval_case(text, names):
    from simplecad.core.params import ExpressionError, evaluate
    from simplecad.core.units import Dimension

    out = []
    for dimension in Dimension:
        try:
            value = evaluate(text, dict(names), dimension)
            out.append({"value": repr(value)})
        except ExpressionError as exc:
            out.append({"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - recorded as Python raises it
            out.append({"raised": type(exc).__name__, "error": str(exc)})
    return out


def _random_expression(rng, depth=0):
    if depth > 3 or rng.random() < 0.3:
        return rng.choice(["x", "y", "z", "1", "2.5", "0", "3 mm", "10 deg", "0.5 in", "1e3", ".5", "pi", "7"])
    kind = rng.random()
    if kind < 0.55:
        op = rng.choice(["+", "-", "*", "/", "//", "%", "**"])
        return f"({_random_expression(rng, depth + 1)} {op} {_random_expression(rng, depth + 1)})"
    if kind < 0.7:
        return f"-{_random_expression(rng, depth + 1)}"
    func = rng.choice(["sin", "cos", "tan", "asin", "acos", "atan", "atan2", "sqrt", "abs", "min", "max", "floor",
                       "ceil", "round", "hypot", "log", "exp"])
    count = rng.choice([1, 1, 1, 2, 2, 3])
    return f"{func}({', '.join(_random_expression(rng, depth + 1) for _ in range(count))})"


PARAMETER_SETS = [
    [{"name": "w", "expression": "40 mm"}, {"name": "h", "expression": "w / 2"}, {"name": "a", "expression": "30",
     "dimension": "angle"}, {"name": "n", "expression": "3", "dimension": "scalar"}],
    [{"name": "b", "expression": "a + 1"}, {"name": "a", "expression": "2"}, {"name": "c", "expression": "b * a"}],
    [{"name": "x", "expression": "y"}, {"name": "y", "expression": "x"}, {"name": "z", "expression": "1"}],
    [{"name": "p", "expression": "q + 1"}, {"name": "r", "expression": "1/0"}, {"name": "s", "expression": "r + 1"},
     {"name": "t", "expression": "10 deg", "dimension": "length"}, {"name": "u", "expression": "1 mm", "dimension": "angle"}],
    [{"name": "d", "expression": "1"}, {"name": "d", "expression": "2"}, {"name": "e2", "expression": "d * 10"}],
]


def parameter_sets(paths):
    from simplecad.core.params import ParameterSet

    sets = list(PARAMETER_SETS)
    for path in paths:
        sets.append(json.loads(zipfile.ZipFile(path).read("model.json")).get("parameters", []))
    cases = []
    for parameters in sets:
        resolved = ParameterSet.from_list(parameters)
        cases.append([parameters, {k: repr(v) for k, v in resolved.values.items()}, resolved.errors])
    with open(os.path.join(DATA, "parameters.json"), "w", encoding="utf-8") as handle:
        json.dump(cases, handle, indent=0, ensure_ascii=False)
        handle.write("\n")
    print(f"parameters.json: {len(cases)} parameter sets")


def evaluations(paths, seed=20261119):
    from simplecad.core.params import ParameterSet

    cases = []
    for path in paths:
        model = json.loads(zipfile.ZipFile(path).read("model.json"))
        values = ParameterSet.from_list(model.get("parameters", [])).values
        texts = {v for f in model.get("features", []) for v in f.get("inputs", {}).values() if isinstance(v, str)}
        texts |= {p.get("expression", "") for p in model.get("parameters", [])}
        cases += [[text, values, _eval_case(text, values)] for text in sorted(texts)]
    names = {"x": 3.0, "y": -2.5, "z": 0.0}
    cases += [[text, names, _eval_case(text, names)] for text in EVAL_EDGE_CASES]
    rng = random.Random(seed)
    for _ in range(3000):
        values = {"x": rng.uniform(-100, 100), "y": rng.choice([0.0, -1.0, 2.0, rng.uniform(-1, 1)]),
                  "z": rng.uniform(0, 1e6)}
        text = _random_expression(rng)
        cases.append([text, values, _eval_case(text, values)])
    with open(os.path.join(DATA, "evaluate.json"), "w", encoding="utf-8") as handle:
        json.dump(cases, handle, indent=0, ensure_ascii=False)
        handle.write("\n")
    print(f"evaluate.json: {len(cases)} expressions")


def main() -> int:
    import simplecad.kernel  # noqa: F401 - registers every feature type
    corpus()
    normalised()
    new_box()
    new_box(pulls=("5", "-3"), undo=1, out="pulls_undone.json")
    write("dependencies.json", project_texts(sys.argv[1:]) | EDGE_CASES)
    evaluations(sys.argv[1:])
    parameter_sets(sys.argv[1:])
    write("dependencies_fuzz.json", fuzz())
    with open(os.path.join(DATA, "pyjson.json"), "w", encoding="utf-8") as handle:
        json.dump(json_cases(), handle, indent=0)
        handle.write("\n")
    print("pyjson.json written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
