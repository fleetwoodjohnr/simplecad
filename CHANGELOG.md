# Changelog

Each version's section is the GitHub release's notes, and what the in-app
updater shows (`scripts/release_notes.py`).

## 0.6.0 (2026-11-25)

A simpler, more graphical window, as 0.2.0 worked: click what you want to
change, then drag it, or pick a tool from the bar beside it. The ten everyday
tasks measured for this release take 29 clicks and keys in all, against 47 in
0.5.1. Projects saved in 0.6.0 still open in 0.4.4 and 0.5.x.

- **Click a part and drag it.** A click takes the part and its face, and the
  move arrows and turn rings are already on it. Letting go is one step; type a
  number while dragging for an exact one, and Esc puts it back. Several chosen
  parts move together.
- **Box select with the right button.** Right-drag a box anywhere, over the
  model too: left to right takes what it surrounds, right to left what it
  touches, Shift adds. A left drag turns the view anywhere; a short right
  click still opens the menu. Ctrl+A, or Select all in the menus, takes every
  part, and a right click on a part of a several-part selection keeps them
  all, so the menu offers Subtract and Join.
- **Double-click** a part to change the step that made it; in a group, the
  first double-click reaches inside for the one part.
- **A bar beside what you selected**, clear of its arrows and rings, with the
  likeliest tool first: the pull distance and Hole for a face, Fillet for an
  edge, Move and Rotate for a part, Subtract for two parts, Arrange for three
  or more, Ungroup for a group. Everything else is behind its ⋯, and the
  right-click menu offers the same.
- **Handles on the model.** Drag a face's handle to pull it, an edge's to round
  it, or Split's plane through a part, with the size at the cursor. Drags snap
  to 0.25 mm; hold Shift for free, Ctrl for 0.05 mm.
- **Edges: click them.** The edge under the pointer lights up. A clicked edge
  (Shift-click for more) gets Round or Bevel on the bar: drag its handle, or
  type the size and press Enter. There is no face list to pick from any more;
  it stays for the keyboard, shown when it has the focus.
- **Join and Subtract you can see.** The kept part is tinted and the cutters
  drawn see-through red (green to join, blue to intersect), over the preview
  too; the panel says "Removing Pin from Block", with Swap to turn it round,
  and a click on another part adds it. Three or more parts get Subtract and
  Join on the bar as well.
- **Threads as 0.2.0 made them.** Click a shaft and Shift-click the hole on
  the other part: Thread together leads the bar and threads both, sized from
  the shaft with the clearance on the hole. Once there is a thread, Matching
  Part is on the bar and opens on what you clicked: it threads a round face
  (previewed first), or drills a threaded hole that fits in a flat one.
- **Magnets and guides.** A dragged part catches flush on other parts' edges,
  middles, corners and centres within 10 pixels and says what it met; the
  rings hold at each step and turn freely between. Stack & Place shows the
  part's distances from the face's edges on the face, and the part drags on
  it, catching flush or centred. Measure with nothing selected snaps to
  corners, centres and midpoints and gives the distance between two points.
  Shift drags freely, Ctrl finely.
- **Updates by themselves**, as in the rest of the Simple suite: SimpleCAD
  looks for a new release once a day and offers to install it (Linux
  packages through your package manager, which asks for your password) and
  restart. Settings → Updates turns it off. From 0.5.1, update once with
  File → Check for updates.
- **Live preview in every tool.** There is no Preview button: the result shows
  as you type, the last one stays while the next builds, and Enter applies.
- **Short panels** open beside what you clicked, with More… for the rest of a
  tool's settings. The side panel keeps the Bodies list and History.
- **A short ribbon**: Shape, Sketch, Import, Measure and Print. Every tool is in
  the bar, the right-click menu and command search (Ctrl+K), which lists them all.

### Fixed

- **Large projects are quicker.** A step on UPS Atlas takes about 100 ms
  (180–195 ms in 0.5.1), and on Atlas Box about 105 ms (125–130 ms). The
  window no longer sends the whole model again for every step, lays itself
  out again on every frame, or turns History's buttons off and on (what you
  click in History while a step runs opens when it is done).
- **Undoing a Move of a large imported part** takes under half a second (UPS
  Atlas's board: 0.42–0.47 s, against 1.7–2.0 s in 0.5.1 and about 0.5 s in
  0.4.4). The part it brings back is recognised as the one shown, moved, so it
  is not meshed and measured again.
- Installers are checked upgrading from 0.5.1, as well as from 0.4.4, on Linux,
  Windows and macOS.

### Still slower than 0.4.4

- A light step on a project as large as UPS Atlas takes about 100 ms from
  click to screen; 0.4.4's engine does it in 10–30 ms (its window's share was
  not measured).

## 0.5.1 (2026-11-23)

Undo and Redo are quick again on large projects, and what 0.5.0 could not
build yet is here: Arrange in a pattern, sliding Clip Joints, clips made with
the older clip tool, and a Draft tool. Projects saved in 0.5.1 still open in
0.4.4.

- **Arrange in a pattern.** Lay parts out in a grid, a line, a ring,
  staggered rows, packed tight, or rows and columns of your own, on a flat
  face, round a cylinder or inside a bore, or on the printer's build plate.
  The preview outlines the space they can use, marks what doesn't fit and
  why, and offers what would, such as "Try Packed" or a smaller spacing. Drag
  it to move the whole pattern. Every setting 0.4.4 had is there, including a
  face for each part to stand on and edges to keep a margin from, and
  patterns made in 0.4.4 rebuild exactly where it put them.
- **Sliding Clip Joints.** Dovetail, T-rail and tongue & groove rails, with a
  stop at the end and a detent that clicks home, and a preview with the first
  part slid out.
- **Draft.** Taper picked faces of a part so it comes out of a mould or
  prints cleanly, and edit the angle from History.
- Clips made with the older clip tool, before Join Parts, rebuild.

### Fixed

- **Undo and Redo on large projects.** One step on a project like a
  450-step enclosure takes about a tenth of a second, three to four times
  faster than in 0.5.0, and the first after opening under a second. Undoing
  or redoing an Align or a Move of a large imported part, which took up to
  4.7 s, takes under a second.
- **Delete is back.** Delete a body from its row in the Bodies list, a whole
  group from the group's row, or the chosen bodies with the Delete key. As in
  0.4.4, the steps that made a body go with it, and a step that used other
  bodies, such as a Combine's tools, takes them too. Undo brings them back.
- **The wheel button moves the view again.** Drag with the wheel pressed to
  pan, and hold Shift, or drag with the right button, to turn the view, as in
  0.4.4. In 0.5.0 the wheel button zoomed and the right button panned.
- **Right click is back.** As in 0.4.4, a right click without dragging
  selects the face under the pointer and opens a menu there: the tools for
  that face, then Edit the step that made the part, Hide, Group or Ungroup,
  and Delete. Right click a body in the Bodies list or a step in History for
  the same kind of menu. The menu key or Shift+F10 opens it from the
  keyboard.
- **A part that won't change now says why.** When a step lost something it
  was built from, such as a deleted part an Align lined up against, its part
  keeps its last good shape and can't be changed. 0.4.4 behaved the same, but
  0.5.0 gave no sign. Selecting the part now shows which step blocks it,
  with:
  - **Keep where it is**, for an Align: the step becomes a Move to the same
    place, so the part moves and aligns freely again;
  - **Keep this shape**, for other steps: the part keeps its last good shape,
    and steps before it no longer change it;
  - **Repair input**, to choose a replacement for what was lost.

  Undo reverses each one, and 0.4.4 opens the result.
- **Quitting no longer hangs after the geometry engine restarted.** If the
  engine stops responding when you quit, it is closed after 5 seconds.

### Still slower than 0.4.4

- Undoing a Move of a large imported part (thousands of faces) takes about
  2 s, where 0.4.4 takes about half a second: the part comes back from the
  project as saved, and the window measures every face again. Redoing it is
  quick.

## 0.5.0 (2026-11-23)

A new desktop app: every tool is a button, and what a tool will do shows in
the model while you set it up. Projects from 0.4 open and rebuild the same,
and projects saved in 0.5.0 still open in 0.4.4.

- **Tools are buttons.** The ribbon groups the tools by task (Create, Modify,
  Combine, Place, Construct, Inspect), and a bar beside the selected face
  offers what fits it. Hover a button to see what it does, or what it needs
  before it can be used.
- **See it before you apply it.** Dragging Move or Rotate moves the part
  itself and leaves a faint copy where it started, with the distance or angle
  beside it; type a number to set it exactly. Push/Pull shows the new face as
  you drag. Escape puts everything back, and each finished drag is one undo.
  Arrow keys nudge the selection.
- **More modeling tools.** Mirror, Pattern, Scale, Combine, Shell, Split,
  Fillet and Chamfer on clicked edges, Section Replace, Arrange in a row,
  construction planes, axes and points, groups, Vent, and Measure on edges
  and corners. Every step can be edited from History, and Repair reconnects a
  missing input while the model keeps its last good shape.
- **Joints.** Hinges (pin, print-in-place, snap-on and living), Clip Joints
  (snap tabs, printed connectors and keyed tab & slot), threaded connections,
  and a matching thread on any round face.
- **Printing.** A print check for overhangs, thin walls, tiny features and
  open edges; drop, centre and orient parts on the build plate; Fit &
  Clearance with a calibration test print and thread gauges; and Send to
  slicer.
- Sketches, Extrude, Revolve, Sweep, Loft, Pattern Along Path, matching bolts
  and nuts, and imports from STEP, IGES, B-Rep, STL, OBJ, 3MF and SimpleCAD
  projects all carry over. Modeling runs in a separate geometry engine, so
  the window stays responsive while it works.

### Not yet in 0.5.0

These open from older projects with their saved shapes. A step that has to be
rebuilt says so and keeps its last good result; edit it in 0.4.4 until 0.5.1
brings it over.

- Sliding Clip Joints (dovetail, T-rail, tongue & groove), and clips made
  with the older clip tool that came before Join Parts.
- Arrange face patterns (grid, linear, radial, staggered, packed, custom).
  Arrange in a row works.
- Draft has no control yet: saved drafts rebuild, but can't be added or
  edited.

### Slower than 0.4.4

- On very large projects, Undo and Redo take a moment: about 0.4 s a step on
  a project like a 450-step enclosure (the first after opening up to 2 s),
  where 0.4.4 took a few hundredths of a second. Undoing or redoing many
  steps in a row is up to 2.3 times as slow, and rebuilding such a project
  from scratch up to 1.7 times. Opening and saving are faster than in 0.4.4
  on every project measured, with far less memory. 0.5.1 makes Undo quick
  again.

## 0.4.4 (2026-11-20)

Installing updates works again on Linux, parts no longer go missing when a
project opens, and Stack & Place keeps up on large projects.

### Fixed

- **"Downloaded", then nothing happened.** After downloading and checking an
  update, SimpleCAD hands the package to your system's software installer. It
  started that hand-over with its own bundled libraries in the way, so the
  system could not tell what the file was and silently gave up, while the
  window said it had been handed over. System programs now start with the
  system's own libraries, and if the system still won't open the package you
  are told where it is and the one command that installs it. **0.4.1 to 0.4.3
  can't install this update themselves:** let the app download it, then run
  `sudo dnf install ~/.local/share/simplecad/updates/SimpleCAD-0.4.4-linux-x86_64.rpm`
  (or `sudo apt install …deb` on Ubuntu) once. Later updates install from
  inside the app.
- **Stack & Place froze on detailed parts.** Every change in the panel
  measured the whole moving part again, which took about five seconds for a
  part with a few thousand faces. It now measures only the faces that can reach
  the edges, with exactly the same result.
- **Some parts didn't show after opening a project.** Deleting a body that a
  later step still used (for example the tool of a Cut kept with "Keep tool
  bodies") left that step needing attention, and the parts it made could be
  saved with no shape at all: the project then opened with them listed but
  invisible. A step that needs attention now shows its last good result again
  -- its own, or failing that the newest one before it -- whether the project
  was saved that way or not. Re-select the missing input to bring the step up
  to date.
- **Large projects slowly filled memory between sessions.** On a big project
  the geometry engine keeps part of its working cache in a temporary folder,
  which on most Linux systems is held in memory. The engine is stopped rather
  than closed, so the folder was never deleted: a few hundred megabytes left
  behind each session. Folders left by a stopped engine are now cleared the
  next time one is needed.
- **Stack & Place placed a part from the wrong position after Undo.** In a
  project just opened from a file, undoing past a change to a part could leave
  the window showing the part as it was before the undo. The panel then
  previewed and placed it from where it was drawn, not where it was. The window
  now always shows the result undo returns to.

## 0.4.3 (2026-11-19)

Closing SimpleCAD works the first time, and never loses unsaved work. (0.4.2
was tagged but never released: its automated checks stopped on the new
question below. 0.4.3 is 0.4.2 with the checks fixed.)

### Fixed

- **Clicking × did nothing.** While a project was being autosaved (every 90
  seconds, a large file on a detailed model), closing the window was refused
  without a word until the autosave finished. The window now closes at once:
  an autosave is only a safety copy and is deleted on a clean exit anyway. A
  save you started yourself is still finished first, and the hint says so.
- **Closing asks about unsaved changes.** It used to close and discard them.
  Now it asks Save / Don't Save / Cancel, like New and Open do. If the save
  fails, the window stays open with the reason.

## 0.4.1 (2026-11-18)

Checking for updates works on Fedora and other Linux distributions again.

### Fixed

- **"Could not reach GitHub … CERTIFICATE_VERIFY_FAILED".** The Linux
  packages carry the OpenSSL of the Ubuntu machine that builds them, which
  looks for trusted certificates only where Ubuntu keeps them. On Fedora,
  openSUSE and others it found none, so every update check and download
  failed. SimpleCAD now also trusts the system's own certificate bundle.
  Copies older than 0.4.1 can't update themselves on those systems: install
  0.4.1 by hand once (or start the old copy with
  `SSL_CERT_FILE=/etc/ssl/cert.pem simplecad` and update from Settings →
  Updates), and later updates work from inside the app.

## 0.4.0 (2026-11-18)

Pushing and pulling faces on a detailed part works again, on a newer geometry
kernel. Every saved project opens and rebuilds exactly as before.

### Fixed

- **Pushing and pulling no longer fails on a busy part.** Pushing the inner
  wall of a hollowed box with holes and a vent in it crashed the geometry
  engine inside OCCT 7.9.3's face merging, on a result that was itself valid.
  SimpleCAD now runs on **OCCT 8.0.1**, which merges the same shape cleanly.
  Every saved project opens and rebuilds exactly as before.
- **A push or pull takes what is on the face with it.** A recess or a raised
  boss on the face now moves with the face, and through-holes stay through.
  Before, pushing a wall with a recessed vent left a solid pillar the full
  depth of the push behind the vent.
- **A step that fails is not kept.** A Pull (or any new step) that failed used
  to stay in the history as the body's newest step, so every later push or
  pull on that body failed behind it with "An earlier feature failed". It is
  now taken back at once, nothing is left to redo, and the hint says why:
  "Pull failed: … Nothing was changed."
- **A push that would cut a part in two says so** instead of leaving loose
  pieces behind.
- **Engraved text cuts into the face.** With OCCT 7.9.3 an engraved label
  could leave the face untouched; on 8.0.1 it removes the material it should.
- **The "⋯" menu on the action bar opens upward**, inside the window. It used
  to drop off the bottom of the screen and show only its first rows.

## 0.3.1 (2026-11-02)

The first published 0.3 release. 0.3.0 was tagged but never released, because
its Linux packages could not open a window on current distributions. 0.3.1 is
0.3.0 with that fixed, so everything listed under 0.3.0 is new in it too.

### Fixed

- **Linux: the window opens on current distributions.** The AppImage, .deb and
  .rpm carried an older C++ runtime than the system's graphics driver needs, so
  on Fedora 44 SimpleCAD stopped at start with "Could not initialize GLX". They
  now use the system's. 0.2.0 had the same problem.
- **The .deb and .rpm declare what they need from the system**, so installing
  one also installs the few X11 libraries a minimal system may lack.
- **Release checks open the real window** of every Linux package, on Ubuntu
  22.04 and 24.04 and on Fedora 44, rather than only running it headless.

## 0.3.0 (2026-11-02, not released)

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
