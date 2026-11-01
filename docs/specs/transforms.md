# Move and Rotate

## Current behaviour (before this change)

- The gizmo wraps OCCT's `AIS_Manipulator`. Arrow drags were solved by OCCT's
  `ObjectTransformation`, then passed through the Move panel's snapping filter
  (`_snap_translation`).
- Dragging the part away from the handles slides it in a plane chosen from the
  camera. Rotate turns it about the axis that faces the camera.
- A drag commits one parametric `MoveFeature` per body, storing `dx/dy/dz` and
  `rx/ry/rz` (world axes, X then Y then Z) about a pivot.

## Defects found

| # | Defect | Where |
|---|---|---|
| A | A snap lock caught by an earlier click (one that never became a drag) was applied to the untouched axes of the next drag. A Z-arrow drag then moved X/Y too: the reported "diagonal" movement. Reproduced in the real window on `main`: Z drag after a click moved Y by 0.3 mm. | `_snap_translation` honoured locks before skipping a zero axis; locks were cleared only on commit |
| B | The part-drag plane preferred the ground whenever the camera's Z component was > 0.3: a plane seen at 17°, where motion is amplified 3.4×. Hits behind a perspective camera were not rejected. | `_facing_axis`, `_ray_plane` |
| C | A near-miss press took the first handle scanned clockwise from screen-right, not the nearest. A press on the part near the hub grabbed the short Z arrow. | `_nearby_handle` |
| D | A nearly edge-on ring was intersected at a grazing angle and jumped or flipped. | ring drag |
| E | Turning the part from a press near the pivot measured the angle about a point under the cursor. | free rotation |
| F | The pivot was re-centred after each turn, so +45° then −45° did not return an uneven part. | `_pivot_point` |
| G | The Move panel's arrow keys always changed X/Y, whatever the camera. A drag overwrote typed but unapplied values. | `handle_arrow_key`, `_on_gizmo_drag` |

## Design

**One solver, one constraint, applied last.** OCCT still draws the handles and
reports which one is under the cursor. Everything else is solved in
`ui/viewport/gizmo.py`:

- **Axis drag**: the closest approach of the cursor ray to the axis line through
  the gizmo. When the axis is within `AXIS_EDGE_ON` (|axis·view| > 0.97) of the
  line of sight, screen motion along the axis's projection at the true mm/pixel
  scale is used instead.
- **Plane drag**: the ray's intersection with the plane, forward hits only in
  perspective.
- **Ring drag**: the signed angle between pivot-to-cursor vectors in the ring
  plane. A dead zone of `ROTATE_DEAD_ZONE_PX` around the pivot is ignored, and
  rings with |normal·view| < `RING_EDGE_ON` (0.2) use screen motion along the
  ring's line.

The constraint (`("axis", i)`, `("plane", normal)`, `("ring", i)`) is fixed at
press time. `constrain()` zeroes the disallowed components **before and after**
the panel's snapping filter, so no filter can add motion off the dragged axis.
Untouched components are exact zeros (no `-0`).

**Frames.** The gizmo works in a frame of three orthonormal axes:

- **World**
- **Local**: the part's accumulated Move rotations (`accumulated_rotation`)
- **Picked axis**: Z along an edge, a hole or a face normal

Fields hold components along that frame. In world frame everything is
identical to before.

**Gestures.**

- `started` clears all snap state and remembers what was typed.
- `cancelled` (a click, not a drag) puts the typed values back and discards the
  tremor.
- A drag adds to the typed values; the ghost shows the sum, which is what
  letting go applies.

## Interactions

| Action | Result |
|---|---|
| Drag an arrow | Moves along that axis only |
| Drag the part | Slides it in the world plane most face-on to the camera (ground preferred unless a vertical plane is 1.5× more face-on) |
| X / Y / Z during a drag | Lock to that axis; the motion so far is projected onto it. Same key again releases |
| Shift+X / Y / Z during a drag | Lock to the plane across that axis |
| Shift | No snapping |
| Ctrl | Fine snapping (a tenth of the move step) |
| Axes: World / Local | Arrows, rings and fields follow the part's own turned axes |
| Move by / Move to | Offset, or world coordinates of the reference point |
| Reference point (Centre / Min / Max) | What Move to places and what the grid snaps |
| Arrow keys in Move | Step 0.25 mm along the panel axis nearest screen right/up; Ctrl+Up/Down steps in depth |
| Snap angle 5 / 15 / 45 / 90° | Ring detent step (saved as the `rotation_snap` preference) |
| Turn about | Centre of selection (fixed for the session), World origin, Custom point, or Edge or face axis (Z ring turns about it) |
| Reset rotation | Adds one Move that undoes every Rotate the part has had, about its current centre |
| After a move | Warns if the part now overlaps another body (bounding-box prefilter, then a boolean common; bodies over 600 faces are skipped) |

**Snapping while moving (world frame).** Each moving axis is pulled toward:

- other bodies' bounding-box edges and centres
- corners, edge midpoints, hole centres and face centres of the 8 nearest bodies

within 10 px. Otherwise the reference point lands on the move-step grid. Axes
not being moved are never touched.

## File format

`MoveFeature` gains optional `axis` (vector) and `angle` (expression) inputs:
a turn about an arbitrary axis through `pivot`, applied after `rx/ry/rz` and
before `dx/dy/dz`. Files without them build exactly as before (tested
bit-for-bit). `FORMAT_VERSION` is 4, so an older SimpleCAD refuses a newer
file instead of silently dropping the axis turn.

## Acceptance criteria

The first three criteria are tested by `tests/test_transform_constraints.py`
(stub camera, 29 directions × 3 axes × 4 drags) and `scripts/check_axis_lock.py`
(real window).

- Dragging the X arrow changes X only; the same for Y and Z. This holds from 26
  camera directions, in orthographic and perspective, with the other components
  exactly 0.
- A ring drag turns about its own axis only.
- A Z-arrow drag after a click near another body moves Z only.
- Typed 90° about an arbitrary axis gives the exact expected bounding box.
- +45° then −45° about one pivot returns the bounding box to within 1e-6.
- Reset rotation restores the part's unrotated size.

## Known limits

- **Local axes** follow Move/Rotate features only. A turn made by Align or
  Arrange is not part of the local frame.
- **Collision** is a warning after the move, not "stop at contact".
- **Plane handles** stay off: OCCT's invisible plane handles made the Y arrow
  hard to grab. The keyboard locks and the part drag cover plane moves.
