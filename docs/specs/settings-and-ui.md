# Settings, shortcuts, theme and units

## Before

- **Preferences** was a floating tool panel with four fields: rotation snap,
  move step, undo depth and smaller files.
- **Shortcuts** were stored in `settings.json` and read at startup, but there
  was no UI to change them.
- `group` / `ungroup` had handlers but no default binding, so they were never
  bound.
- **Theme**: the top-bar button toggled light/dark only. Once toggled, "follow
  the desktop" could not be chosen again.
- **Units**: lengths always displayed in millimetres. Every unit could be typed.
- **Opening files**: `simplecad.desktop` declares `%f` and model MIME types,
  but a file passed on the command line was ignored. A second launch only
  raised the existing window.

## Now

**Settings** (Command search → *Settings…*, or the `preferences` / `settings`
actions) is one non-modal window. Every change applies and saves at once.

| Tab | Contents |
|---|---|
| General | Theme: Follow the desktop / Light / Dark. Show lengths in: mm, cm, m, in |
| Modelling | Rotation snap, move step, undo steps kept (clamped to their ranges), smaller project files |
| Shortcuts | Every action, its key and a Default button; Restore all defaults |
| Updates | Added by the update system (see `updates.md`) |

**Shortcut rules.**

- A key already used by another action is refused, and the message names that
  action.
- Default fails if another action now holds the default key.
- Bindings apply through the existing `MainWindow.reload_shortcuts()`, so the
  old key stops working immediately.
- Command search reads bindings each time it opens.

**Display unit.**

- `core.units.format_quantity` shows lengths in the chosen unit, adding
  decimals so precision stays at about 0.01 mm (cm +1, m +3, in +2 decimals).
- The text carries its unit, so a value shown as `0.4724 in` evaluates back to
  11.999 mm.
- Stored values and the file format stay in millimetres.
- Free-text hints (for example "Moving 2.00 mm") still say mm.

**Opening files from outside.**

- `simplecad part.scad3` opens the project.
- Any other existing file is imported into the current model.
- If the window is already running, the second launch forwards its paths over
  the single-instance socket as `open\t<path>` lines and exits.
- Opening a project over unsaved work asks first.

## Acceptance (tests/test_settings_window.py, tests/test_single_instance.py)

- A rebound action fires on its new key, and its old key is unbound.
- A clashing key is refused, naming the action that holds it.
- Default and Restore all defaults restore the shipped bindings.
- Ctrl+G / Ctrl+Shift+G group and ungroup, with no conflicts in the defaults.
- "Follow the desktop" persists as `system`.
- Inches display and read back to within 0.003 mm.
- Modelling numbers are clamped, and the undo depth applies at once.
- A forwarded path reaches the running instance.
- A `.scad3` opens, while a `.step` imports.
