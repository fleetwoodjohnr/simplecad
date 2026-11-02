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

## v0.3.0: the shell, selection and Align & Arrange

**Layout.**

- The rail pins Shape, Sketch, Pull, Move, Rotate, Scale, Align & Arrange and
  Measure; Create, Modify and Inspect are flyouts. Tiles drop their labels
  when the window is too short for all of them, and tooltips name the tool
  and its shortcut.
- The menu button (top left) holds New project, Open, Open recent, Save,
  Save as, Import, Export, Settings, Keyboard shortcuts, Check for updates and
  About.
- The model panel can be dragged wider or narrower by its left edge
  (`ui.browser_width`), never runs under the bottom bar, and the window's
  geometry is remembered (`ui.window_geometry`). Offscreen and screenshot
  windows do not save geometry.

**Properties** (under the Bodies tree) show the selection's position and size.

- The reference point is Bottom (centre of the bottom face, the default),
  Centre or Corner.
- A typed position is one Move: folded into the object's last move where it
  can be.
- A typed size is one Scale about the reference point, with an optional
  lock that keeps proportions.
- Rotation shows the turns made by Move/Rotate.
- With several objects selected, edits apply to all of them together.

**Shortcuts added.** New project `Ctrl+N`, Select all `Ctrl+A` (only visible,
unlocked objects), Rotate `R`.

**Lock** is `Body.metadata["locked"]`, so the file format does not change.

- A locked body is drawn, but `OcctViewport.set_pickable` removes its pick
  zones.
- The context bar offers only Unlock, Measure, Duplicate, Colour, Hide, Group
  and Ungroup for it.
- Delete, nudge, the quarter turns, Drop to plate and every tool that moves or
  reshapes refuse it and say why.

**Right-click** selects what is under the cursor unless that is already part of
the selection. The model tree has eye and lock buttons on each row and renames
in place.

**Align & Arrange** is one rail tile and one search entry with three tabs
(`tools/base.py ToolTabs`). It opens the tab that fits the selection.

| Tab | Tool key | What it does |
|---|---|---|
| Align | `align_bodies` | Min / centre / max along X, Y or Z, relative to the whole selection or the first or last selected; spread out with equal or fixed gaps. One undo step per click |
| Stack & Place | `align` (also `stack`, `center`, `concentric`, `place_on_face`, `align_stack`) | Opens on the operation clicked: the registry passes the key as `window.requested_tool` |
| Arrange | `arrange` | Patterns on a face, or on the build plate (`target: "plate"`, built from the printer profile) |

**Context bar, by selection.**

| Selection | Actions |
|---|---|
| Two flat faces | Stack (first), Align & Arrange, Joint, Measure |
| Two round faces | Concentric |
| Shaft and hole | Align & Thread, Threaded pair |
| Two or more objects | Booleans, Align & Arrange, Group, Move, Rotate, Scale, Mirror and more |
| One object and a face | Place on Face |
| Two or more objects and a face | Arrange |

**Mirror** flips across a plane through the selection's centre, or adds a
mirrored copy across its far side, so the copy sits against the original.
`MirrorFeature` gained `keep: False`; files without it still fuse both halves.
