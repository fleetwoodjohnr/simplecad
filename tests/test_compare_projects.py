"""A Rust parity result must account for all required stages."""

import json
import os
import sys
from subprocess import CompletedProcess

import pytest

from scripts import compare_projects


@pytest.fixture
def comparison(monkeypatch):
    original = os.path.abspath("original.scad3")
    dump = {"loaded": {}, "signatures": {}, "fingerprints": {}, "document": {}}
    outcomes = {
        ("dump",): dump.copy(),
        ("rebuild",): {"cached_rebuild": {}, "cached_failed": 0, "cached_diagnostics": {}, "unported": []},
        ("rebuild", "--from-recipe"): {
            "recipe_rebuild": {}, "recipe_failed": 0, "recipe_diagnostics": {}, "unported": [],
        },
        ("history",): {"undo_all": {}, "redo_all": {}, "undo_steps": 1, "redo_steps": 2,
                       "undo_diagnostics": {}, "redo_diagnostics": {},
                       "unported": []},
    }
    python = {
        **dump, "cached_rebuild": {}, "cached_failed": 0, "recipe_rebuild": {},
        "recipe_failed": 0, "undo_all": {}, "redo_all": {}, "undo_steps": 1,
        "redo_steps": 2, "reopened": {}, "cached_diagnostics": {}, "recipe_diagnostics": {},
        "undo_diagnostics": {}, "redo_diagnostics": {},
    }
    calls = []

    def rust(args, **kwargs):
        executable, command, path, *extra = args
        assert executable == "scad"
        assert path == original
        calls.append((command, *extra))
        return CompletedProcess(args, 0, json.dumps(outcomes[command, *extra]), "")

    def reference(tree, path, *, flag):
        assert path == original
        return python if flag == "--dump" else dump

    monkeypatch.setattr(compare_projects.subprocess, "run", rust)
    monkeypatch.setattr(compare_projects, "_run", reference)
    monkeypatch.setattr(compare_projects, "_rust_saves", lambda *args, **kwargs: [])

    def run(*extra):
        monkeypatch.setattr(sys, "argv", ["compare_projects.py", "--rust", "scad", *extra,
                                        "original.scad3"])
        return compare_projects.main()

    return run, outcomes, calls


@pytest.mark.parametrize(("command", "replacement", "diagnostic"), [
    (("rebuild",), {"unported": ["sketch"]}, "sketch"),
    (("rebuild", "--from-recipe"), {"recipe_rebuild": None}, "recipe_rebuild"),
    (("rebuild",), {"unported": None}, "unported"),
    (("rebuild",), {"cached_diagnostics": {"unexpected": "failed"}}, "cached_diagnostics"),
    (("history",), {"redo_steps": None}, "redo_steps"),
    (("history",), {"redo_steps": 3}, "redo_steps"),
    (("history",), {"unported": None}, "unported"),
    (("history",), {"unported": ["face_texture"]}, "face_texture"),
])
def test_rust_rebuild_fails_when_evidence_is_missing_or_differs(comparison, capsys,
                                                           command, replacement, diagnostic):
    run, outcomes, _ = comparison
    outcomes[command].update(replacement)
    assert run("--rebuild") == 1
    output = capsys.readouterr().out
    assert "VERDICT: FAIL" in output
    assert diagnostic in output


def test_rust_rebuild_compares_every_stage_on_the_original_input(comparison, capsys):
    run, _, calls = comparison
    assert run("--rebuild") == 0
    assert calls == [("dump",), ("rebuild",), ("rebuild", "--from-recipe"), ("history",)]
    assert "VERDICT: PASS" in capsys.readouterr().out


def test_rust_loaded_only_comparison_requires_no_rebuild_or_history(comparison):
    run, _, calls = comparison
    assert run() == 0
    assert calls == [("dump",)]


@pytest.fixture
def align_comparison(monkeypatch):
    placed = {"name": "Align", "result": {"transform": [0.0] * 12, "description": "Placed"}}
    missing = {"name": "Align", "error": "The target was deleted."}
    python = {"one": placed, "two": missing}
    rust = [{"id": "two", **missing}, {"id": "one", **placed}]
    monkeypatch.setattr(compare_projects, "_run", lambda *args, **kwargs: python)
    monkeypatch.setattr(compare_projects, "_rust_json", lambda *args, **kwargs: rust)
    monkeypatch.setattr(sys, "argv", ["compare_projects.py", "--rust", "scad", "--align", "original.scad3"])
    return rust


def test_align_uses_stable_ids_and_accepts_the_same_cad_error(align_comparison, capsys):
    assert compare_projects.main() == 0
    assert "2/2 Align outcomes matched" in capsys.readouterr().out


@pytest.mark.parametrize(("replacement", "diagnostic"), [
    ({"id": "one", "name": "Align", "error": "Target missing"}, "python result"),
    ({"id": "two", "name": "Align", "error": "Target missing"}, "duplicate Rust Align feature ID"),
    ({"id": "other", "name": "Align", "error": "Target missing"}, "step missing from rust"),
    ({"name": "Align", "error": "Target missing"}, "missing a feature ID"),
])
def test_align_fails_on_unexpected_errors_or_missing_ids(align_comparison, capsys, replacement, diagnostic):
    align_comparison[1] = replacement
    assert compare_projects.main() == 1
    output = capsys.readouterr().out
    assert "VERDICT: FAIL" in output
    assert diagnostic in output
