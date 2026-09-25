# Incremental modeling validation

The regression coverage exercises the same project before and after `.scad3`
save/reopen. It checks identities and geometry, not just whether operations return
success.

## Root causes addressed

- Name-based dependency inference could bind a later operation to the wrong
  producer. Features now persist explicit producer/object IDs, including internal
  sketch outputs and each output of multi-body operations.
- Whole-history saved baselines and cold preview replay invalidated unrelated
  geometry. Per-feature signatures and result caches now serve both workflows.
- Modifier deletion could reuse downstream geometry containing the deleted
  feature. Removal explicitly splices its consumers and invalidates the old and
  new dependency closures.
- Nested snapshot aliases and finalizing a pending action against a replaced
  stack could corrupt undo/redo. History now retains immutable entity patches.
- Saved recipes could be paired with older displayed shapes. Worker saves capture
  an immutable recipe/history snapshot and validate its geometry before writing.
- Loading features one at a time repeatedly scanned the growing list. Loading now
  installs the feature list in one structural change.
- Eager Boolean constructors built before settings were applied and could modify
  cached operands. Builders now receive non-destructive settings before execution.

## Work-count benchmark

Run from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B scripts/benchmark_incremental.py
```

Representative local run on 2026-09-20, in milliseconds:

| Independent objects | Edit, new | Edit, reopened | Save | Open |
| ---: | ---: | ---: | ---: | ---: |
| 100 | 1.6 | 2.3 | 31 | 27 |
| 1,000 | 11.2 | 12.2 | 324 | 299 |
| 3,000 | 35.2 | 38.3 | 1,105 | 953 |

Every isolated edit and appended primitive recomputed **one** feature. Every
no-op rebuild visited **zero** features. Unchanged shapes retained their geometry
identity, and object IDs matched after reopening. Times vary by machine and
model complexity; dependency bookkeeping and viewport publication still scale
with project size. The tests enforce work counts rather than timing thresholds.

## Regression entry points

- `tests/test_incremental_projects.py`: texture changes, replacement/removal,
  undo/redo, deleted/reused names, Boolean chains, indirect parameters, legacy
  projects, deep history, long sessions and growing projects.
- `tests/test_geometry_service.py` and `tests/test_geometry_client_revision.py`:
  process isolation, stale replies, snapshots, cache cleanup and worker recovery.
- `tests/test_result_store.py`: memory limits, disk spill, archive round trips and
  eviction of deleted geometry.
- `tests/test_shape_panel_layout.py` and `tests/test_browser_incremental.py`:
  repeated creation/switching, narrow layouts, theme changes and retained rows.
- `scripts/check_face_texture.py`: real-window picking, exact preview, editing,
  reopening, removal and undo/redo.
- `scripts/check_geometry_recovery.py`: real-window Boolean previews, cancellation
  and recovery. The checked run's worst UI heartbeat interval was 89 ms.

Version 1 files remain readable. They did not contain saved undo/redo stacks or
intermediate caches; the loader preserves final geometry and hydrates a legacy
branch only when required. Missing inputs remain flagged rather than being
redirected to another object with the same name. Version 2 files preserve the
explicit graph, both history stacks and intermediate results.
