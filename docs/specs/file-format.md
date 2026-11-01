# Project file format (`.scad3`)

A zip archive containing:

| Entry | Contents |
|---|---|
| `model.json` | The recipe: features, parameters, bodies, groups, history and diagnostics. Its `format` key is the format version |
| `geometry/<sha256>.brep` | Cached results, keyed by content hash. Optional: a missing or damaged entry is rebuilt from the recipe |
| `assets/<sha256>.brep` | Imported geometry that has no recipe. A file that was imported cannot be rebuilt without these |
| `thumbnail.png` | Preview image |

## Versions

| `format` | Change |
|---|---|
| 1 | One B-Rep per body |
| 2 | Content-addressed geometry cache, per-feature signatures |
| 3 | Saved history patches, compact saves |
| 4 | `move` may carry `axis` + `angle`; hinge and sliding-joint features |

## Rules

- **Additive only.** A new feature input is optional, and its absence means the
  old behaviour. For example, a `move` without `axis` builds bit-for-bit as
  before (`tests/test_transform_constraints.py`).
- **Bump `FORMAT_VERSION`** whenever an older build would *silently* build a
  newer file differently: by ignoring a new input, or by failing on an unknown
  feature type. `load` refuses a newer format with "saved by a newer SimpleCAD"
  instead of opening it wrongly.
- **Migrations.** Reading v1–v3 needs no conversion step. If one is ever
  needed, convert the `model.json` payload in `project.load` before
  `Document.from_dict`, keyed on the old `format`, and add a fixture file of
  that version to the tests.
- **Damage.**
  - A file that is not a zip, or has unreadable `model.json`, raises a
    `CadError` saying so.
  - A damaged geometry or asset entry is skipped; the body rebuilds from its
    recipe.
- **Unknown feature types** are refused by name ("uses a feature type this
  version does not know: 'hinge'"), never dropped.

## Compatibility checking

```sh
.venv/bin/python scripts/compare_projects.py --old /path/to/previous/checkout ~/Desktop/**/*.scad3
```

This opens every file read-only in both trees and compares body volume, box and
face count at each stage:

- loaded
- cached rebuild
- undo all
- redo all
- Save As + reopen
- recipe-only rebuild
