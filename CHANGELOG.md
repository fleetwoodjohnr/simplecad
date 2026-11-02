# Changelog

Each version's section is the GitHub release's notes, and what the in-app
updater shows (`scripts/release_notes.py`).

## 0.3.0 (2026-11-02)

A new look, easier moving and rotating, one place to align, stack and arrange,
a new icon, and installers that are installed and run before they are released.

### Interface

- **New visual style, "Graphite".** Neutral graphite surfaces and a single amber
  accent that never looks like an X, Y or Z axis, in matching light and dark
  themes. Every button, field, list, menu, slider and tab has the same hover,
  pressed, focus and disabled states.
- **The same type on every system.** Inter and JetBrains Mono are now included,
  and all text uses one type scale. Numbers are in a monospaced font so columns
  line up.
- **Everyday tools one click away.** The left rail now holds Shape, Sketch,
  Pull, Move, Rotate, Scale, Align & Arrange and Measure. Tooltips give each
  tool's shortcut. On a short screen the tiles drop their labels instead of
  running off the bottom.
- **A menu for files and settings**, at the top left: New project (new, Ctrl+N),
  Open, Open recent, Save, Save as, Import, Export, Settings, Keyboard
  shortcuts, Check for updates and About.
- **Properties.** Under the model tree, the selected object's position and size,
  ready to type into. A new position is one Move. A new size is one Scale about
  the chosen point: bottom centre by default, so a part on the plate stays on
  the plate. With several objects selected, edits apply to all of them
  together.
- **A panel that fits.** The model panel can be dragged wider or narrower, and
  it never runs under the bottom bar. Its width and the window's size and
  place are remembered.
- **The theme follows the desktop** while SimpleCAD is running, not only when
  it starts.
- **Drop-down menus have their arrow back**, and "&" in a button name no longer
  turns into an underline.

### Selecting and organising

- **Select all** (Ctrl+A).
- **Right-click** now selects what is under the cursor before showing its menu.
- **Lock**: a locked object is still drawn but cannot be clicked, boxed, moved,
  nudged or deleted. Locking needs no new file format, so older versions open
  the file and simply ignore the lock.
- **The model tree** has eye and lock buttons on every row, and renames in
  place: double-click a name and type.
- **Move, Rotate and Scale are offered when a face or edge is picked.** Before,
  you had to discover how to select the whole object first.

### Moving and rotating

- **Dragging a part slides it along the ground.** It stays under the cursor and
  never rises or sinks. Height changes only with the Z arrow or a plane square.
  When the camera is almost level with the ground, the drag follows the mouse
  across the screen instead of racing away.
- **Plane squares** between the arrows move a part in XY, XZ or YZ. A plane
  seen edge-on is hidden, so it can never run away with the part.
- **Esc or right-click during a drag** puts the part back and keeps the tool
  open. Pressing another mouse button mid-drag no longer leaves the gizmo stuck.
- **Snapping can be switched off** with the new button beside the view controls.
  Hold Shift to do the opposite for one drag. Snaps now catch within 10 pixels
  however far out you have zoomed (they were capped at 1 mm). The Move panel
  has quick step sizes: 0.1, 0.5, 1, 5 and 10 mm.
- **A wedge shows how far a ring has turned**, with the angle beside it.
- **Esc while picking a pivot** ends the pick and keeps the tool open.

### Align & Arrange

One button, three tabs:

- **Align (new).** Line selected objects up by their left, centre or right
  side along X, Y or Z, relative to all of them together or to the first or
  last one selected. Spread three or more out with equal gaps, or two or more
  with an exact gap. Each click is one step you can undo.
- **Stack & Place.** One object onto a face of another. It now opens on the
  operation you clicked (Center no longer opens as Stack). The less-used
  anchor grid and U/V offsets sit behind "More placement options".
- **Arrange.** Grid, row, radial, staggered and packed layouts, now also **on the
  build plate** with no face to pick.

The bottom bar offers one way in for each job instead of five. Center, Align &
Stack and Section Replace no longer crowd it; Section Replace is under Modify.

- **Mirror (new).** Flip objects across a plane, or add a mirrored copy beside
  the original that you can edit on its own.
- **3D Print panel.** The buttons now read Drop all, Center all and Orient each.
  Orient turns each part to its own best flat lay. It used to give every part
  the turn worked out for the first one.

### Fixes

- A mirrored part's flat faces reported normals pointing inward, so Stack,
  Align and Arrange put mirrored copies upside down.
- The last row of a tool panel could sit under Cancel / Done when its
  description ran to several lines.
- The top-left logo was blank when the window was opened without the launcher.
- The updater could stop at "Downloading…" on a disk or permission error, left
  half-downloaded files behind, kept every old installer, and re-enabled Install
  for a release that was not newer. All fixed: every failure now says what
  happened and changes nothing.

### Icon

- A new icon matching the new look: a flat amber cube on a graphite tile,
  clear at 16 pixels. It now ships in every size each system asks for: seven
  in the Windows .ico, a full macOS .icns, and every Linux icon size plus the
  SVG. AppStream metadata ships with the Linux packages.

### Release process

- Every installer is installed and run before release: the AppImage, the .deb,
  the .rpm (on Fedora, with its desktop entry and metadata validated), the
  Windows installer and the macOS disk image.
- Workflow actions and build tools are pinned to exact versions, and the
  downloaded tools are checked against their checksums. A release is refused if
  its tag is not annotated, does not match the version, is not on main, or
  already has a release.
- Release notes come from this file, and the disk image opens with an
  Applications folder to drag the app into.

### Not included

- The Windows installer is not Authenticode-signed and the macOS app is not
  notarised; both need paid certificates. Updates are still verified against
  SimpleCAD's own signing key before they install.
- The 3D view on Windows and macOS is built and started by CI, but nobody has
  checked by eye that it draws correctly.

## 0.2.0 (2026-11-01)

- Move and Rotate: every drag solved by the gizmo, with the axis constraint
  applied last. Dragging X changes X only, from any camera angle. Adds
  World/Local axes, Move to, X/Y/Z locks and Reset rotation.
- Settings window: theme, display unit, modelling numbers, and every keyboard
  shortcut can be rebound.
- Edit parameters: change any step's dimensions after the fact.
- Joints: dovetail, T-rail and tongue-and-groove rails that slide in and stop.
- Hinges: pin, print-in-place, snap-on and living hinges, checked through their
  whole swing.
- Packages for Linux, Windows and macOS, signed updates, and CI.

## 0.1.0

- Auto-Arrange: grid, linear, radial, staggered, packed and custom patterns on
  flat and round faces.
- The overhaul: dependable rotation and movement, responsive face textures,
  bounded long-session growth, smaller project files, and existing projects
  kept working.
