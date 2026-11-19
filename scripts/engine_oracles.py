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


def main() -> int:
    corpus()
    write("dependencies.json", project_texts(sys.argv[1:]) | EDGE_CASES)
    write("dependencies_fuzz.json", fuzz())
    with open(os.path.join(DATA, "pyjson.json"), "w", encoding="utf-8") as handle:
        json.dump(json_cases(), handle, indent=0)
        handle.write("\n")
    print("pyjson.json written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
