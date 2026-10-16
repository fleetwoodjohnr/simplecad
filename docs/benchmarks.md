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

## Not improved

Re-saving `UPS Atlas.scad3`: 97.3 MB before, 97.2 MB after. Its size is one cached
result per current feature (many are booleans on a 12.9 MB imported mesh), not
stale history. Save/open time unchanged (~8.5 s / ~2 s).
