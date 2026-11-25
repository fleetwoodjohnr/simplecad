# Benchmarks: October 2026 overhaul

Original code (`ccf5475`) and the `overhaul` branch, run back to back on the
same machine (Fedora 44, Python 3.14). Wall and GUI-thread time in ms, medians.
Reproduce with `scripts/benchmark_session.py` (`--sizes`, `--long 300`,
`--project`) and `scripts/benchmark_texture.py`.

## Face texture previews (seconds, preview + mesh + serialise)

| Case | Before | After | Faces before → after |
|---|---|---|---|
| 30×30 honeycomb | 0.88 | 0.66 | 570 → 570 |
| 60×60 honeycomb | 5.2 | 3.7 | 2,770 → 2,770 |
| 60×60 recessed dots | 41.0 | 1.7 | 9,139 → 988 |
| r10 cylinder dots | 21.6 | 4.5 | 6,381 → 1,593 |

Previews over 8 s were also killed before, so the last two could never be applied.

## Per-edit cost (wall / GUI ms)

| Case | Move | Undo | Redo |
|---|---|---|---|
| 100 bodies | 15.3/11.2 → 11.2/8.1 | 14.7/11.3 → 9.2/7.2 | 17.7/13.7 → 9.1/7.1 |
| 500 bodies | 72.1/51.8 → 49.4/34.1 | 62.8/49.0 → 46.5/32.7 | 65.3/50.8 → 45.5/36.0 |
| UPS Atlas (394 features), 9 repeats ×2 | 69/50 → 44–49/32–36 | 65/50 → 38/29–31 | 61–66/48–52 → 36–38/29–30 |

Join on 500 bodies: 184 → 69 ms wall.

## Long session (300 mixed ops, 49 → 250 features), wall ms in the last 10% of ops

| Op | Before | After |
|---|---|---|
| move | 52.7 | 30.3 |
| rotate | 54.6 | 30.4 |
| delete | 101.5 | 54.6 |
| duplicate | 145.3 | 46.0 |
| undo/redo ×3 | 299.6 | 188.1 |
| autosave | 232.5 | 184.4 |

End-of-session counters: paste checkpoints held 25 → 0, pinned history keys 82 → 20,
result-store entries 330 → 270. Cost still grows with document size (5× features),
not with session length.

## Project size

A normal save of `UPS Atlas.scad3` is unchanged: 97.3 MB. Its size is one cached
result per current feature (many are booleans on a 12.9 MB imported mesh), not
stale history. The new *Smaller files* preference keeps only the results a
rebuild asks for. Measured in the real window under Xvfb, on a copy in scratch
space:

| UPS Atlas | Normal save | Smaller files |
|---|---|---|
| File size | 97.3 MB | 16.8 MB |
| Save | 12.1 s | 2.6 s |
| Reopen (window) | 7.7 s | 4.1 s |
| First move after reopening | 1.0 s | 1.0 s |
| Edit a step late in the timeline (Combine17) | 0.4 s | 0.7 s |
| Edit a step early in the timeline (Chamfer, 77–127 steps rebuilt) | 110 s | 114 s |

The early edit is slow either way: it reruns the booleans against the mesh.

## Compatibility with existing projects

Every `.scad3` on this machine was opened read-only by the original code
(ccf5475) and by this branch, and compared at each stage: as loaded, after the
opening rebuild, rebuilt from the recipe with the cache discarded, after undoing
every saved step, after redoing them all, and after a save-as and reopen
(volume, area, bounding box and face count of every body, plus every feature's
state). The originals were never written; their checksums and timestamps are
unchanged.

| Project | Features | Undo steps | Result |
|---|---|---|---|
| AtlasBox | 158 | 0 | identical |
| Recycling Bin | 5 | 7 | identical |
| Coffee Funnel | 6 | 22 | identical |
| Addi_Keychain | 49 | 0 | identical |
| AtlasBoxLower V1 | 388 | 55 | identical |
| Dish Soap Holder | 29 | 55 | identical |
| UPS Atlas (Copy) | 19 | 15 | identical |
| UPS Atlas (and `Atlas Box.scad3`, a byte-identical copy) | 394 | 80 | identical |
| Spondge Holder | 135 | 0 | bodies identical; differs only where the old code was wrong (below) |

Spondge Holder holds a split half pasted by the old code. Its copied Split
still named the original halves, so the old code built the copy's history only
partly and, on save and reopen, replaced `Box3 A` and `Box3 B` with the copy's
geometry. This branch builds the copy under its own names and keeps `Box3 A`/`B`
through save and reopen.

Shared by both versions, so not regressions:

- Rebuilding UPS Atlas from the recipe alone fails: its import still points at
  `~/Desktop/Atlas/UPS Atlas.step`, and that file now lives in
  `Atlas 3D Files/Atlas PSU/`. The project opens and edits normally because the
  saved results stand in for it.
- Addi_Keychain and Spondge Holder do not rebuild from the recipe alone to
  what they show: a fillet now too large and an align whose `Torus` was deleted
  in one, a combine whose `Cylinder2` was deleted in the other. In Spondge
  Holder an edit to `Box` therefore keeps its last good shape, in the old window
  and the new. AtlasBoxLower V1 rebuilds to the same volume with 51 faces
  instead of the saved 47.

Undo and redo through UPS Atlas's 80 saved steps (core, per step summed):

| | Original code | Branch before fix | Branch |
|---|---|---|---|
| Undo all 80 | 3.7 s | 3.7 s | 3.6 s |
| Redo all 80 | 0.0 s | 45.6 s | 0.0 s |
| Dish Soap Holder, redo all 55 | 2.0 s | 53.8 s | 2.0 s |

Rebuilding from the recipe takes the same time in both versions (three
alternating runs: Addi_Keychain 12.1–12.6 s vs 11.9–12.2 s, Dish Soap Holder
25.8–26.0 s vs 25.7–25.9 s).

## Gizmo drag cost: November 2026 pass

`scripts/benchmark_gizmo.py`, a real window under Xvfb. 40 boxes; Move on one;
the X arrow grabbed and dragged 3 × 40 steps. The figures are `gizmo.drag`
times: solve, snap filter and preview transform. The repaint is scheduled and
not counted. `main` and this branch were run back to back on the same machine,
while a test suite was also running, so treat absolute values loosely.

| | Press | Drag, median | Drag, 95th percentile |
|---|---|---|---|
| `main` (`cc83fb7`) | 1.26 ms | 4.97 ms | 10.5 ms |
| this branch | 1.87 ms | 6.25 ms | 14.1 ms |

About 1.3 ms more per step. Almost all of it is the new point snapping: the
moving axis is compared with the corners, midpoints, holes and face centres of
the eight nearest bodies, as well as their boxes. Both stay inside a 60 Hz
frame. The press is slower by the snap-geometry set-up done when a drag starts.

## v0.3.0: gizmo drag and re-attach

`scripts/benchmark_gizmo.py`: a real window under Xvfb, 40 boxes, Move on
one. New in v0.3.0, the script also times the *re-attach*: from letting go of
a drag, which commits a Move, until the gizmo is back and can be grabbed again.
`main` (`e2bb25e`, v0.2.0) and `v0.3` were run alternately on the same machine
with nothing else running.

| | Press | Drag, median | Drag, 95th percentile | Re-attach, median | Re-attach, max |
|---|---|---|---|---|---|
| `main`, run 1 | 1.87 ms | 3.05 ms | 7.46 ms | 250 ms | 266 ms |
| `v0.3`, run 1 | 1.73 ms | 2.64 ms | 6.54 ms | 238 ms | 285 ms |
| `main`, run 2 | 1.58 ms | 2.79 ms | 7.08 ms | 261 ms | 285 ms |
| `v0.3`, run 2 | 1.82 ms | 2.91 ms | 8.04 ms | 229 ms | 259 ms |

**The drag is unchanged** by the ground-plane solve, the plane squares and the
overlay, and stays inside a 60 Hz frame.

**Where the re-attach goes** (timestamps inside one commit):

| | |
|---|---|
| Waiting for the geometry worker's reply | about 215 ms (the client polls every 16 ms, so this is the worker) |
| `refresh_view`, browser, context bar, Properties | about 40 ms (Properties is 5 ms of it) |
| `on_model_rebuilt` and `attach` | about 15 ms |

**During that wait the part stays where it was let go.** This was measured on
both versions by reading the presentation's placement while the rebuild was
still pending: 12.00 mm on both, for an arrow drag and for a body drag. So
nothing jumps back. The only effect is that the next drag can start about a
quarter of a second later.

A drag released before then is queued and applied as its own step (defect H in
`specs/transforms.md`), so nothing is lost. The worker's round trip is the
place to cut, if this ever needs to be faster; it was left alone in v0.3.0.

## Packaged build (Linux, PyInstaller one-folder)

| | |
|---|---|
| Bundle size | 1.3 GB unpacked, before matplotlib and PIL were excluded from the spec. OCCT is 264 MB of it, Qt most of the rest |
| `simplecad --self-test` | Passes. Rebuild, boolean, data files, STEP/3MF out and a geometry worker process in about 6 s |
| Window under Xvfb | Starts, draws the viewport, grid and view cube, and starts the geometry process |

## v0.5.0 against v0.4.4 (November 2026)

The 14 projects on the development machine (Fedora 44, scratch copies),
v0.4.4 (`d15187d`, Python 3.14) against the 0.5.0 engine (`4462105`), one
after the other on an otherwise idle machine. Seconds, wall clock, from a cold
process: for v0.4.4 its import and open are added to each stage, since each
Rust `scad` command opens the file itself. Peak resident memory is the largest
of any stage.

| Project | Open + rebuild | Undo and redo all | Save | Rebuild from recipe | Peak memory, MB |
|---|---|---|---|---|---|
| Addi Glasses Holder | 1.10 → 0.11 | 2.64 → 1.44 | 1.15 → 0.11 | 2.71 → 1.68 | 356 → 79 |
| Addi_Keychain | 1.22 → 0.14 | 1.22 → 0.19 | 1.31 → 0.06 | 11.73 → 8.03 | 401 → 126 |
| Atlas Box (Copy) | 3.36 → 1.62 | 16.75 → 39.08 | 11.11 → 6.95 | 5.90 → 10.04 | 970 → 363 |
| Atlas Box | 3.54 → 2.03 | 30.02 → 59.36 | 11.58 → 7.60 | 17.64 → 16.20 | 1074 → 452 |
| AtlasBox | 1.56 → 0.46 | 1.02 → 0.48 | 1.04 → 0.46 | 1.54 → 0.84 | 322 → 60 |
| AtlasBoxLower V1 | 1.09 → 0.15 | 3.81 → 5.54 | 1.15 → 0.15 | 3.62 → 1.44 | 352 → 84 |
| Coffee Funnel | 1.01 → 0.04 | 1.04 → 0.07 | 1.02 → 0.05 | 1.03 → 0.05 | 318 → 50 |
| Dish Soap Holder | 1.23 → 0.13 | 24.24 → 6.20 | 1.41 → 0.32 | 24.59 → 5.48 | 386 → 86 |
| Poppy Travel Water Holder | 1.00 → 0.04 | 1.25 → 0.34 | 1.01 → 0.04 | 1.19 → 0.19 | 325 → 54 |
| Recycling Bin | 1.02 → 0.12 | 2.27 → 1.27 | 1.07 → 0.07 | 2.26 → 1.26 | 344 → 70 |
| Spice Rack | 1.01 → 0.04 | 1.06 → 0.12 | 1.01 → 0.03 | 1.02 → 0.04 | 314 → 42 |
| Spondge Holder | 1.86 → 0.55 | 1.52 → 0.64 | 1.75 → 0.11 | 4.71 → 2.62 | 423 → 139 |
| UPS Atlas (Copy) | 1.40 → 1.00 | 18.44 → 17.98 | 1.65 → 0.90 | 13.29 → 16.34 | 586 → 284 |
| UPS Atlas | 3.36 → 1.63 | 16.69 → 37.46 | 11.10 → 6.98 | 5.91 → 9.91 | 992 → 363 |

Still slower on the large Atlas files:

- **Undo and redo all** (every saved step undone, rebuilt, redone, rebuilt):
  up to 2.3×. About half is history bookkeeping, roughly 90 ms a step: the
  step signatures are worked out twice per move, and the project document,
  history included, is copied. The rest is rerunning Align and Move steps.
- **Rebuild from recipe:** up to 1.7× on three of the four, the same on Atlas Box.
- **One Undo or Redo in the packaged app:** Atlas Box 0.35–0.45 s (the first
  after opening 0.8 s), UPS Atlas about 0.4 s (first 2.2 s, since it brings
  back bodies the opened model didn't use). v0.4.4 does the same step in
  12–25 ms. Before `4462105` it took 1.6 s and 2.7 s: every rebuild parsed
  the saved results again, and the window re-meshed every body.

Move and Rotate drags on Atlas Box, packaged, Wayland (the default): p95
frame interval 16.7 ms in three runs each, at 60 Hz (gate 20 ms). Forced onto
X11 through XWayland, every frame arrives but in late pairs: p95 21.8–31.9 ms,
because the GPU process blocks 5–10 ms in `eglSwapBuffers`. This machine has
no native Xorg session to measure.

## v0.6.0 (November 2026)

The packaged app on Fedora 44 (Wayland), with scratch copies of the two large projects; three runs each.

**One Undo or Redo** (click to the first frame showing it; the first right after opening, then 20 Undo and 20
Redo; ms):

| Project | 0.5.1: first, p50, p95 | 0.6.0: first, p50, p95 | Heaviest step, 0.6.0 (limit: 1.1 × 0.4.4 with display) |
|---|---|---|---|
| Atlas Box | 717–997, 116–131, 343–376 | 665–732, 101–103, 262–269 | Undo 482–511 (1253); Redo 328–341 (707) |
| UPS Atlas | 570–650, 182–195, 686–957 | 683–720, 98–102, 614–739 | Move Undo 428–469 (560) |

On UPS Atlas the first Redos, of its heavy import steps, took 603–856 ms, varying from run to run (one run in
three steady at about 620 ms, as every run was at 6e1a0ed); they have no limit of their own, and they vary
with the automatic update check off as well.

What changed:

- **Undoing a Move of UPS Atlas's board** took 1.7–2.1 s: 0.4.4 saved each Move's result whole, so the step
  before brought back separate geometry, which the window meshed and measured again. A body an Undo or Redo
  brings back as the shown one moved now comes back as that one placed there, and the window moves its display.
- **Every step** sent the whole model to the window (15–30 ms to send, as much to receive); the engine now sends
  only the parts the window lacks. The view measured its size on every frame, laying the window out again (50 ms
  a step); History's hundreds of buttons were turned off and on (tens of ms of style and layout); and a large
  model's face list (3,000 options) was rebuilt.

**Drag frames** (`measure-atlas-transform.cjs`, Atlas Box, 3.6 s drags, with the move magnets on): Move p50 16.68,
p95 16.68, max 33.4 ms; Rotate p50 16.68, p95 16.68, max 33.4 ms.

**Main thread while the engine works** (longest task, limit 300 ms): Stack & Place on Atlas Box 70 ms
(`stack-probe.cjs`); live preview of Hole on Atlas Box, typed slowly and in a burst, none over 50 ms
(`preview-probe.cjs`).

**Clicks, keys and typed values for ten everyday tasks** (`tests/e2e/clicks.spec.ts`; a click or drag is 1, a
drop-down list 2, a typed value 1, Enter, Delete or Escape 1):

| Task | 0.5.1 | 0.6.0 | How, in 0.6.0 |
|---|---|---|---|
| Move a part 10 mm | 4 | 2 | Click the part, drag the X arrow (it snaps to 0.25 mm) |
| Rotate a part 90° | 4 | 2 | Click the part, drag the Z ring (15° steps) |
| Pull a face 5 mm | 3 | 2 | Click the face, drag its handle |
| Round an edge 2 mm | 4 | 2 | Click the edge, drag its handle |
| Delete a part | 2 | 2 | Click the part, Delete |
| Drill a 6 mm hole | 5 | 4 | Click where it goes, Hole, type 6, Enter |
| Cut one part with another | 6 | 4 | Click the part, Shift-click the cutter, Subtract, Enter |
| Arrange three parts on the plate | 9 | 4 | Right-drag a box round them, Arrange, click the plate, Enter |
| Join two parts with a clip | 7 | 4 | Click a face, Shift-click the other, Clip Joint, Enter |
| Change a part's width | 3 | 3 | Double-click the part, type 50, Enter |
| **All ten** | **47** | **29** | |

## v0.5.1 against v0.4.4 (November 2026)

Scratch copies of the two large projects, v0.4.4 (`d15187d`) against 0.5.1,
Fedora 44.

**One Undo or Redo in the packaged app** (Wayland; click to the first frame
showing it; the first right after opening, then 20 Undo and 20 Redo; ms):

| Project | 0.5.0: first, p50, p95 | 0.5.1: first, p50, p95 | Heaviest step: 0.4.4 engine (with display) → 0.5.1 |
|---|---|---|---|
| Atlas Box | 1368, 415, 787 | 717–997, 116–128, 343–376 | Undo 875 (1139) → 834–1107; Redo 537 (643) → 540–588 |
| UPS Atlas | ~2200, ~400, – | 570–650, 182–195, 686–957 | Align 1197 (1199) → 635–885; Move Undo 247 (509) → 1690–2000 |

Ranges are over five runs on Atlas Box and three on UPS Atlas. v0.4.4 is its
engine alone (`bench/undo044.py`), and in brackets with its display meshing
too for each body the step brought back changed (`undo044_display`), since
the 0.4.4 window has to show the step as well. What changed since 0.5.0:

- Each step's signatures are worked out once, from the steps it changes; the
  history is kept beside the document rather than copied with it; the rebuild
  takes its inputs rather than copying them; a step that failed is not tried
  again until something it reads changes.
- Finding a picked face read every face of the body once for each face it
  compared. On UPS Atlas's imported board (2944 faces) each Align step took
  3.5 s, against 1.2 s in 0.4.4; it now takes 0.5 s.
- A move is saved on its input's geometry with a new location, as 0.4.4
  saves one, instead of writing the body again (0.55 s on that board); the
  body is written whole when the project is saved. The window shows a moved
  body as its earlier display, moved, instead of measuring every face and its
  volume again (1.3 s).

Still slower than 0.4.4:

- **Undoing a Move of a large imported body:** the body comes back as the
  file saved it, so the window meshes and measures it afresh: 1.7–1.8 s,
  against about 0.5 s in 0.4.4, whose viewport does not measure faces.
- **UPS Atlas, any step:** the window takes 130–200 ms to show a step of a
  project with that many bodies and faces; 0.4.4's engine does the lighter
  steps in 10–30 ms.

**Whole-history and recipe rebuilds** (`bench/run.py`, seconds; v0.4.4 with
its import and open added to each stage, as above; 0.5.0 from the table
above, except UPS Atlas's recipe rebuild, measured again beside 0.5.1):

| Project | Undo and redo all: 0.4.4, 0.5.0, 0.5.1 | Rebuild from recipe: 0.4.4, 0.5.0, 0.5.1 |
|---|---|---|
| Atlas Box | 31.8, 59.4, 33.8 | 17.6, 16.2, 16.2 |
| UPS Atlas | 18.5, 37.5, 22.5 | 5.8, 10.7, 10.3 |

0.5.0's recipe rebuild of UPS Atlas measured 10.5–10.8 s in the same session
as 0.5.1's 10.2–10.3 s; the 9.9 s in the 0.5.0 table was a quieter run.
