"""The real-window checks in ``scripts/``, as tests.

Each opens the actual SimpleCAD window, drives it with real mouse and key
events, and prints a VERDICT. They need an X display with GLX -- run them under
Xvfb:

    xvfb-run -a .venv/bin/python -m pytest -m gui

They run only when ``-m gui`` is asked for: windows opening on the desktop in
the middle of an ordinary test run would be unwelcome, and a desktop's own
windows can sit over the one being driven. Each runs in a subprocess: a
QApplication is one per process and the rest of the suite creates an offscreen
one, which OCCT cannot draw into.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CHECKS = (
    "check_axis_lock",
    "check_gizmo",
    "check_rotate",
    # check_nudge is left out: its held-arrow step depends on key autorepeat
    # timing and fails intermittently under Xvfb on main as well.
    "check_joint_slide",
    "check_hinge",
)


@pytest.mark.gui
@pytest.mark.parametrize("name", CHECKS)
def test_real_window_check(name, tmp_path, request):
    markers = request.config.getoption("markexpr", "") or ""
    if "gui" not in markers or "not gui" in markers:
        pytest.skip("real-window check: run with -m gui under xvfb-run")
    script = os.path.join(ROOT, "scripts", f"{name}.py")
    if not os.path.exists(script):
        pytest.skip(f"{name} is not written yet")
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        pytest.skip("needs an X display: run under xvfb-run")
    env = dict(os.environ)
    env.pop("QT_QPA_PLATFORM", None)
    # Each check gets a throwaway profile, and screenshots go nowhere tracked.
    env["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    env["XDG_DATA_HOME"] = str(tmp_path / "data")
    env["SIMPLECAD_SHOTS_DIR"] = str(tmp_path / "shots")
    result = subprocess.run(
        [sys.executable, script], cwd=ROOT, env=env, capture_output=True, text=True,
        timeout=900,
    )
    output = result.stdout + result.stderr
    assert "VERDICT: PASS" in output and result.returncode == 0, output[-4000:]
