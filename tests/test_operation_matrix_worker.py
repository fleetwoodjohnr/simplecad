"""The operation matrix again, through the window's real client -> worker path.

The core matrix (test_operation_matrix.py) builds in-process. The window never
does: it sends patches to the geometry process and merges replies back, and the
split-half duplicate failure lived in exactly that kind of seam. These cases
drive the same subjects and operations through ``GeometryClient``.
"""

from __future__ import annotations

import os
import sys

import pytest

pytestmark = pytest.mark.slow

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))


@pytest.fixture(scope="module")
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("operation", ["duplicate", "rotate_y", "move", "delete_undo", "duplicate_twice"])
@pytest.mark.parametrize("subject", ["box", "split_half", "boolean", "textured", "group"])
def test_operation_through_the_worker(qt_app, subject, operation, tmp_path):
    from benchmark_session import Session, _retire

    from simplecad.core.document import BodyRef
    from simplecad.core.model_clipboard import copy_fragment, paste_fragment
    from simplecad.kernel.occ import is_valid
    from simplecad.kernel.operations import MoveFeature
    from tests.test_operation_matrix import SUBJECTS, _bodies, _low, _state

    document, item = SUBJECTS[subject](tmp_path)
    session = Session(document)
    try:
        session.rebuild()
        assert session.replies[-1].get("report", {}).get("ok", True)
        before = _state(document, item)

        def edit(label, change):
            session.history.record(label)
            change()
            session.rebuild()
            report = session.replies[-1].get("report", {})
            assert report.get("ok", True), report.get("summary")

        if operation in ("duplicate", "duplicate_twice"):
            source = item
            for round_ in range(2 if operation == "duplicate_twice" else 1):
                made = []
                edit("Duplicate", lambda: made.extend(
                    paste_fragment(document, copy_fragment(document, [source]), (40, 0, 0))[1]))
                copy = made[0]
                copied = _state(document, copy)
                assert copied[0] == pytest.approx(before[0], rel=1e-6)
                source = copy
            assert _state(document, item) == pytest.approx(before, rel=1e-6, abs=1e-5)
        elif operation == "rotate_y":
            centre = [(a + b) / 2 for a, b in zip(before[1:4], before[4:7])]
            for _ in range(3):
                edit("Rotate", lambda: [document.add_feature(MoveFeature(inputs={
                    "body": BodyRef(n), "dx": 0, "dy": 0, "dz": 0, "ry": 120,
                    "pivot": centre, "order": "xyz"}, outputs=[n])) for n in _bodies(document, item)])
            assert _state(document, item) == pytest.approx(before, rel=1e-6, abs=1e-4)
        elif operation == "move":
            edit("Move", lambda: [document.add_feature(MoveFeature(inputs={
                "body": BodyRef(n), "dx": 5, "dy": 0, "dz": 0}, outputs=[n]))
                for n in _bodies(document, item)])
            assert _low(_state(document, item))[0] == pytest.approx(before[1] + 5, abs=1e-5)
        else:
            names = list(_bodies(document, item))
            edit("Delete", lambda: [_retire(document, n) for n in names])
            assert all(document.body(n) is None or document.body(n).shape is None for n in names)
            session.undo()
            assert _state(document, item) == pytest.approx(before, rel=1e-6, abs=1e-5)
        for name in _bodies(document, item):
            assert is_valid(document.body(name).shape)
    finally:
        session.stop()
