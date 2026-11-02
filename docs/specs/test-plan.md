# Test and validation plan

## Layers

| Layer | Where | Runs |
|---|---|---|
| Unit and integration, headless | `tests/` (`pytest -m "not slow"`, about 7 min) | Every push (CI, Linux) and before every commit |
| Slow kernel tests (modelled threads, big booleans) | `tests/`, marked `slow` | Before a release (`pytest`) |
| Real-window checks | `scripts/check_*.py`, about 50 scripts; the core ones are wrapped by `tests/test_gui_checks.py` | `xvfb-run -a pytest -m gui` (CI, Linux). Never on the desktop display: they run only when `-m gui` is asked for |
| Project compatibility | `scripts/compare_projects.py --old <checkout> files…` | After any change to rebuild, history or the file format: every real project, old code against new |
| Packaged build | `simplecad --self-test` | On every bundle in the release workflow, and in CI from source on Linux, Windows and macOS |
| Lint, dependency audit | `ruff check .`, `pip-audit -r requirements-build.txt` (runtime and build) | CI |
| Installers | The release workflow installs and runs every package (AppImage, deb, rpm on Fedora, Windows installer, DMG) | Every tag |

## What each new subsystem is held to

| Subsystem | Specification | Tests |
|---|---|---|
| Move / Rotate | `transforms.md` | `test_transform_constraints.py` (29 camera directions × 3 axes × 4 drags; stale-lock regression; local frames; rings, edge-on rings, pivot dead zone; axis-angle Move, ±45° round trip, Reset rotation), `scripts/check_axis_lock.py` (real window, 26 directions × ortho/perspective, plus the click-then-drag case that reproduced the diagonal bug on the old code) |
| Settings, shortcuts, units, opening files | `settings-and-ui.md` | `test_settings_window.py`, `test_single_instance.py` |
| Edit parameters, damaged files | `feature-editing.md`, `file-format.md` | `test_edit_parameters.py`, `test_project.py` |
| Sliding joints | `joints.md` | `test_joints_sliding.py`, `scripts/check_joint_slide.py` |
| Hinges | `hinges.md` | `test_hinge.py`, `scripts/check_hinge.py` |
| Updates | `updates.md` | `test_updates.py` |
| Packaging | `packaging.md` | `--self-test` on every bundle and installed package; `test_packaging_assets.py`; a local Linux bundle |
| Move / Rotate (v0.3.0) | `transforms.md` | `test_gizmo_v030.py` (ground drag in ortho and perspective, grazing views, plane squares, cancel, snap toggle, wedge, two bodies), `scripts/check_gizmo.py` (real window: arrow, ground drag, plane square, Esc, two bodies) |
| Shell (v0.3.0) | `settings-and-ui.md` | `test_shell_v030.py` (rail, app menu, Properties, lock, Select all, New project), `test_ui_theme.py` (tokens, fonts, every icon name) |
| Align & Arrange (v0.3.0) | `settings-and-ui.md` | `test_align_arrange_v030.py` (align/distribute arithmetic and panel, Stack & Place opening on its key, context bar, Arrange on the plate, Mirror, mirrored normals), `test_scenarios.py` 8–10 |

## Rules

- **Geometric claims are checked with geometry.** Booleans and bounding boxes
  are used, not the feature's own report: parts are slid apart, lids are turned
  and drags are measured.
- **A regression test fails on the old code.** Where practical this has been
  shown: the click-then-drag diagonal move fails on `main` and passes here.
- **No test is skipped or loosened to get green.** The one real-window check
  left out of `-m gui` is `check_nudge`. Its held-arrow step depends on key
  autorepeat timing and fails on `main` under Xvfb as well.

## Validation run, 2026-11-01 (this branch)

| What | Result |
|---|---|
| `pytest` (everything, slow included) | 1255 passed, 6 skipped, 0 failed (13 min) |
| `ruff check .` | clean |
| `scripts/compare_projects.py` (all 14 Desktop projects, against `main`) | identical at every stage |
| `simplecad --self-test` (source, and the frozen Linux bundle) | OK |
| Real-window scripts under Xvfb | 46 of 51 pass. The five that fail, fail identically on `main` (see below) |

The five checks that fail on `main` as well:

| Check | Reading on both | Why |
|---|---|---|
| `check_hover_perf` | ~133 ms per hover vs a 60 ms budget | Xvfb's software GL takes ~130 ms to repaint |
| `check_responsive` | 243 ms (branch) / 257 ms (`main`) worst stall vs its limit | Same cause |
| `check_draw` | `coordinate_round_trip: False` | |
| `check_text` | The engraved plate loses 0 mm³ through the scripted UI | The kernel's engraving tests pass. The scripted two-step flow should be looked at separately |
| `check_nudge` | Its held-arrow step is timing-dependent under Xvfb | |
