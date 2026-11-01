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

## Packaged build (Linux, PyInstaller one-folder)

| | |
|---|---|
| Bundle size | 1.3 GB unpacked, before matplotlib and PIL were excluded from the spec. OCCT is 264 MB of it, Qt most of the rest |
| `simplecad --self-test` | Passes. Rebuild, boolean, data files, STEP/3MF out and a geometry worker process in about 6 s |
| Window under Xvfb | Starts, draws the viewport, grid and view cube, and starts the geometry process |
