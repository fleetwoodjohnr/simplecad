# Roadmap

Milestones M1–M5 form the acceptance spine and are complete: the workflow
`Box → Pull → Second Part → Stack → Hole → Thread Pair → Fillet → Export` runs
end to end through the UI.

## Done

**M1 — Foundation.** Project venv, launcher, OCCT-in-QOpenGLWidget viewport,
theme tokens with light/dark, the feature graph, dependency-driven rebuild with
last-valid retention, named parameters and expressions, the topological naming
layer, friendly error translation.

**M2 — Primitives and direct manipulation.** Eight primitives created without a
sketch, hover highlight and multi-selection across bodies/faces/edges/vertices,
Press/Pull, Move, the model browser and feature history, the contextual action
bar.

**M3 — Align / Stack / Mate.** Stack, Center, Concentric, Align with Flip and
Offset. Smart suggestion picks the operation from the selection. Alignment is a
persistent relationship: the transform is re-solved from live face references
every rebuild, so moving the target carries the aligned body with it.

**M4 — Holes and threads.** Hole tool with simple / counterbore / countersink /
threaded and through / blind. Thread tool detecting internal vs external and
diameter, recommending sizes across five standards. **Create Threaded
Connection** produces a matched printable pair from one node with two outputs,
with clearance applied once, to the female side.

**Quality of life.** Undo and redo with named actions, restoring geometry rather
than just the feature list and marking only the changed features for rebuild;
autosave with crash recovery on next launch; command search on `S` with fuzzy
matching over every command.

**Direct manipulation.** Selecting a flat face and dragging it pushes or pulls
it, with a translucent preview of the material being added or removed and the
distance shown live. The drag only starts on the face that is already selected,
so ordinary selection and orbiting are untouched.

**M5 — Finish and export.** Fillet, Chamfer, Shell, booleans. STEP, STL, OBJ and
3MF in both directions. The native `.scad3` project format: a zip holding the
feature recipe plus a B-Rep cache, so a reopened file is still fully parametric
and the cache is only ever an optimisation.

## Next

## Done (continued)

**M6 — Sketcher.** Points, lines, circles and arcs over one flat parameter
vector. Sixteen constraint types as residual equations — coincident, horizontal,
vertical, parallel, perpendicular, tangent, equal, concentric, midpoint,
symmetric, point-on-line, fix, distance, angle, radius, diameter. Solved with
Levenberg–Marquardt via scipy, with the sketch's state read from the Jacobian's
**rank** rather than a constraint tally, so a duplicated dimension is reported as
redundant instead of being miscounted as fully constrained. Profiles become OCCT
faces with inner loops cut as holes; Extrude (including symmetric) and Revolve
build solids from them, and sketch dimensions accept document parameters.

## Next

**Done: the interactive sketch canvas.** Clicking in the viewport places
geometry on the sketch plane — line, polyline, rectangle, centre-rectangle,
circle, arc, polygon, slot and point — with rubber-band preview following the
cursor. The cursor snaps to existing sketch points first and the grid second,
which is what makes shapes actually connect into a closed profile. Horizontal
and Vertical constraints are inferred for lines drawn within four degrees of
either, so a hand-drawn rectangle survives being edited. The solver runs on
every change and the toolbar reports the state live. Leaving the sketch restores
the camera to where it was.

**Done: on-canvas dimensions.** `D → click → type → Enter`. Dimensions are Qt
labels positioned over the viewport by projecting their anchor point — clickable,
and editing one re-solves the sketch. Editing an existing dimension changes its
value rather than adding a second constraint, and a value the sketch cannot
satisfy is rolled back where it is typed rather than left to be hunted down.
(OCCT's own `PrsDim` annotations are not wrapped in this OCP build, and a widget
can be typed into where an annotation cannot.)

**Done: the transform gizmo.** X/Y/Z arrows, plane handles and rotation rings
over `AIS_Manipulator`. Dragging previews live and releasing commits a
parametric Move, so a drag is as editable afterwards as a typed number — and the
numeric fields fill in with what was dragged.

**Done: Create Matching Part.** Bolt, nut, threaded hole, or apply the matching
thread to a selected face. The thread is read off the feature history, so the
size, hand and clearance are already known. Head and nut dimensions follow ISO
4014 and ISO 4032 from `data/fasteners.json`. A generated bolt is checked
against a generated nut by intersecting them, with a control proving the check
can detect a bad fit.

**Done: Fit & Clearance calibration.** A calibration model — a pin strip at
nominal and a labelled plate of holes from 0.05 to 0.50 mm — plus a panel to
enter what you measured. Values are saved per printer to
`~/.local/share/simplecad/printers/` and override the shipped defaults
everywhere, including thread generation.

**M7 — Advanced modelling.** Sweep along a path and Loft between profiles, both
from sketches. Draft for mould release and print angles. Mirror, and
rectangular, circular and along-path patterns — all parametric, and the circular
one steps by 360/N on a full turn so the last copy does not land on the first.
Still outstanding from M7: construction geometry, the measurement tools and the
parameters panel — see the list at the end.

**M8 — 3D Print workspace.** Elegoo Centauri Carbon 2 profile (256³ mm). Place
on plate, Center, and Orient for printing (which tries the six flat lays and
picks the one with least overhang). Printability analysis covering build volume,
watertightness, disconnected pieces, overhangs, thin walls and tiny features —
every one a warning, never a refusal. Export for Printing produces 3MF, and
Export and open in slicer hands it to whichever slicer is installed (OrcaSlicer,
ElegooSlicer, PrusaSlicer or Cura, native or Flatpak).

Fit & Clearance calibration is built: a pin strip and a labelled plate of holes
from 0.05 to 0.50 mm, and a panel to record what you measured. Values are stored
per printer and override the shipped defaults everywhere, thread generation
included.

**Done: geometry in a separate process.** Rebuilds run in a persistent child
process holding its own document and rebuild cache, returning only the bodies
that changed. The window stays live throughout — 77 ms worst stall against
3141 ms in-process. A worker thread was implemented first, measured ten times
slower because the OCP bindings hold the GIL, and reverted; architecture.md has
the numbers.

**Done: the last ten.**

- **Measure** — length, distance, angle, area, volume, radius, diameter, centre
  distance and minimum distance, dispatched from the selection rather than from
  a sub-mode: pick two holes and it reports the pitch, because that is what two
  holes means. No tile in the tool rail is dead any more.
- **Ellipse and Spline** in the sketcher. A spline's control points are ordinary
  sketch points, so they can be constrained, snapped to and dimensioned like
  anything else, and the spline itself adds no parameters of its own.
- **Sketching on a planar face.** Selecting a flat face and pressing Sketch
  starts on it directly, with the face's own frame as the sketch's.
- **Construction geometry** — offset plane, midplane, plane at an angle, plane
  through three points, tangent plane, axis and point. All features, so a
  construction plane follows the geometry it was derived from.
- **Model browser actions** — rename, isolate, show all, duplicate, delete, and
  suppress/unsuppress/delete/rename on features. Renaming a body rewrites every
  reference to it, so the feature graph does not lose track.
- **Hole "To Object"** — cast a ray down the hole's axis and stop at the first
  body it meets.
- **Align & Thread**, and **Align & Stack** — the spec's combined commands,
  chaining the align solver and the thread pair generator into one step.
- **Constraint inference now covers parallel, perpendicular and equal length**
  as well as horizontal and vertical, one per line, backed out if it would break
  the sketch.
- **Recent files and configurable shortcuts**, stored in
  `~/.config/simplecad/settings.json`.
- **Components and assemblies** — the spec asks only that the architecture allow
  them later. The N-in/N-out feature graph does: a Stack is already a
  relationship between two bodies, and Create Threaded Connection is already one
  node writing into two.

## Still outstanding

Nothing from the specification. What is left is depth:

- **A settings window.** Shortcuts and recent files are stored and read, and can
  be edited by hand or set programmatically, but there is no UI for rebinding a
  key.
- **Sketch dimensions cover distance and diameter.** Angle and radius constraints
  exist and solve; they are not yet offered by clicking on the canvas.
- **The sketch canvas has no drag-to-edit.** Geometry is placed by clicking and
  changed by dimensioning; dragging a point to move it would be the next step.
- **Patterns operate on whole bodies, not on features.** Patterning a hole
  copies the body it is in rather than the hole alone.
- **A face can be sketched on, but a construction plane cannot yet** — the
  planes are built and stored, and choosing one as a sketch plane is the missing
  connection.

## Regression coverage

Three bugs shipped and were caught by scripts driving real input rather than by
the test suite. All three now have headless tests, each verified by reverting
the fix and confirming the suite goes red.

- The transform gizmo committed a zero transform, because it re-read its value
  at the drag's *start* position.
- Editing a dimension added a second constraint instead of changing the first,
  so every edit conflicted with itself.
- A window-wide Return shortcut made inline dimension editing impossible. This
  one is worth remembering: guarding the *handler* is not enough, because Qt's
  shortcut map consumes the key before the focused widget sees it. Plain
  characters are exempt — Qt gives those to a focused text field first — but
  Return and Delete are not, so they are scoped to the viewport instead.

The pattern generalises: behaviour that only appears when input is actually
delivered needs a test that delivers it, or a stub standing in for the part that
cannot run headless.
