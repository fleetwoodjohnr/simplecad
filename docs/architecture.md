# SimpleCAD Architecture

## Layers

```
simplecad/
  core/      document, feature DAG, rebuild engine, parameters, naming, errors, file format
  kernel/    all OCP/OCCT geometry — primitives, ops, threads, align solver, tessellation, IO
  sketch/    2D entities, constraints, LM solver, DOF analysis, sketch→wire
  ui/        theme, viewport, panels, contextual tools
```

Dependencies run one way: `ui → core → kernel`. The kernel never imports UI code, so
every geometric operation is testable headless.

## Geometry kernel

OCCT 7.9.3 through the `cadquery-ocp` 7.9.3.1.1 wheel (`OCP.*`). The wheel bundles its own
OCCT shared libraries. **Never link or load the system `opencascade` libraries alongside it** —
two copies of OCCT's memory manager and transient type registry in one process is an ABI
hazard. The system `opencascade-devel` headers are installed on this machine for source
reference only.

## Viewport — RESOLVED: OCCT renders into a `QOpenGLWidget` FBO

Decision gate for M1, resolved by `scripts/spike_viewport.py`. **Outcome: PASS.**

OCCT's AIS visualization layer draws into the framebuffer object that `QOpenGLWidget`
already owns, which means ordinary Qt widgets composite *on top of* the 3D viewport. This is
what makes SimpleCAD's floating contextual panels, overlays and transitions possible; a
native-window viewport would have forced panels into separate top-level windows.

### How it is wired

1. `QOpenGLWidget` requests a 3.3 core-profile surface with depth, stencil and 4x MSAA.
2. In `initializeGL`, while Qt's context is current, the native `GLXContext` is read with
   `glXGetCurrentContext()` via `ctypes` and boxed in a `PyCapsule` — OCP binds
   `Aspect_RenderingContext` (a `void*`) as a capsule.
3. `OpenGl_GraphicDriver(display, False)` is created and told Qt owns presentation:
   `buffersNoSwap = True`, `buffersOpaqueAlpha = True`, `useSystemBuffer = False`.
   `ChangeOptions()` returns a live mutable `OpenGl_Caps`, so these stick.
4. An `Aspect_NeutralWindow` is created with `SetVirtual(True)` and receives
   **`int(self.effectiveWinId())`** — the real handle of the nearest native ancestor,
   without making the viewport a native child.
5. `view.SetWindow(window, context_capsule)`.
6. In `paintGL`, `OpenGl_FrameBuffer.InitWrapper(gl_ctx)` adopts the FBO Qt has currently
   bound, and the neutral window is resized to match it. This runs every frame because Qt
   recreates its FBO on resize.

### The non-obvious part

`SetNativeHandle` **must** be given a real X11 `Window`. It does not have to be the viewport's
own window: because OCCT renders into Qt's current FBO, the effective handle of the top-level
native ancestor supplies the same X11 visual without turning the `QOpenGLWidget` into a native
child. OCCT does not read the visual from the GL context; `OpenGl_Window::CreateWindow` calls
`XGetWindowAttributes` on the native handle and looks the visual up with `XGetVisualInfo`:

```c
Window aWindow = (Window)myPlatformWindow->NativeHandle();
XGetWindowAttributes(aDisp, aWindow, &aWinAttribs);
aVisInfo.visualid = aWinAttribs.visual->visualid;
```

Passing `glXGetCurrentDrawable()` instead fails, because that is a GLX drawable rather than an
X `Window`, so `XGetWindowAttributes` yields nothing and the lookup throws
`Aspect_GraphicDeviceDefinitionError: XGetVisualInfo is unable to choose needed configuration
in existing OpenGL context`. That message is misleading — it is a *window* problem, not a
context problem, and it is the single trap on this path.

Calling `viewport.winId()` makes the viewport a native X11 child. That native child covers the
stage, so a physical click over a visually composited Shape panel is first delivered to the
viewport's X window; Qt never gets a chance to route it to the panel. Making the overlay native
instead does not solve the general problem: a full-size measurement overlay has
`WA_TransparentForMouseEvents`, but that flag only affects Qt's own hit testing, after XWayland
has already chosen a native target.

SimpleCAD therefore never calls `winId()` on the viewport. The viewport, floating panels and
paint-only overlays stay ordinary children in one Qt-composited, Qt-hit-tested hierarchy, and
OCCT receives `effectiveWinId()` solely for its visual lookup. The top-level window remains the
only native window for the stage. `AA_DontCreateNativeWidgetSiblings` is also set before
constructing `QApplication` as a defensive guard against accidental promotion elsewhere. Do
not remove that application attribute or set it after widget creation. The GL rebind handling
below is still required for genuine context, window and screen changes.

### Neither handle is permanent

Steps 2 and 4 hand OCCT two raw handles, and Qt is free to replace both. It destroys and
recreates a `QOpenGLWidget`'s GL context for ordinary reasons — the compositor taking the
surface back, a reparent, a move between screens — and OCCT is never
told. It goes on drawing through the pointer it was given, logs
`TKOpenGl.WinSystem ... glXMakeCurrent() has failed!` once, and then simply stops changing.
Nothing else looks wrong: the event loop is idle and healthy, the geometry process is fine,
Qt chrome keeps repainting. The window is left showing a still picture, which is what "I can
add a shape but then nothing changes" and "the sidebar goes dark and the model stays white"
both were.

So `initializeGL` is not one-shot. `OcctViewport._binding_is_stale` compares **both** handles
against what OCCT was given, and `paintGL` repeats the check because Qt does not always route a
replacement through `initializeGL`.

Checking only the context is not enough, and that mistake has already been made here once: a
failing session showed `glXMakeCurrent() has failed!` with no context-lost line anywhere near
it, because Qt had replaced the *window* and kept the context. Watch both.

**The two failures need different repairs.** `_recover_binding` picks:

| what went stale | GPU resources | repair |
| --- | --- | --- |
| the `QOpenGLContext` | gone with it | `_rebind_gl` — full teardown and `_init_occt` again |
| only the X window | still live | `_reseat_window` — `SetNativeHandle` + `SetWindow` |

Re-seating after a *context* loss is the obvious repair and silently does not work: the viewer
keeps the shaders, vertex buffers and textures it uploaded to the context that has gone, and
every draw then fails with `GL_INVALID_OPERATION` against a blank screen. The converse is just
as wrong in the other direction — rebuilding for a mere window change would discard a live
scene and re-tessellate every body each time the compositor handed us a new surface.

Nothing the user cares about is lost either way, because none of it lives on the GPU: the
camera, the grid and the selection modes are saved and restored around a rebuild, and the
bodies are re-displayed by the window when it sees `rebound`. `_reseat_window` does not emit
`rebound` at all — the presentations were never invalidated.

One thing that looks like the perfect health check and is not: `OpenGl_Context::IsCurrent()`.
OCCT implements it as `glXGetCurrentContext() == myGContext && glXGetCurrentDrawable() ==
myWindow`, i.e. precisely the test whose failure prints `glXMakeCurrent() has failed!`. It was
measured rather than assumed: True on a bare viewport, but False on *every healthy frame* once
the widget is a child inside the real window, because Qt's current drawable is then the
top-level's and OCCT switches to its own inside `Redraw`. Wired in as a health signal it
condemns a working view on its first frame. Compare the handles instead.

Anything holding an `AIS_InteractiveObject` has to drop it at that point rather than erase it:
the new context rejects presentations built in the old one with `object has been displayed in
another context`. `MainWindow._on_viewport_rebound` clears `_presentations` (in place — the
`SelectionModel` was handed the same dict) and displays the document again.

Guarded by `scripts/check_gl_rebind.py`, which destroys the context by reparenting the widget,
then swaps in a second valid X11 ancestor handle without replacing the context. It checks that
the view still draws, bodies come back after the expensive rebuild, bodies remain in place
after the cheap window re-seat, and theme switches still reach the 3D background.

### Platform

Qt runs under **`QT_QPA_PLATFORM=xcb`** (XWayland), pinned by the `simplecad` launcher.
Reasons:

- The bundled OCCT is an **Xlib/GLX build with no EGL support** —
  `OpenGl_GraphicDriver::InitEglContext()` raises `Standard_NotImplemented`. Under native
  Wayland Qt supplies an EGL context that this OCCT build cannot consume.
- Verified: OCCT's separate `Aspect_DisplayConnection` *can* resolve Qt's GLX context and
  visual, so a second X connection is not a problem in itself.

This matches what FreeCAD does on Wayland and costs nothing perceptible.

## Feature graph

Features have **N inputs and N outputs**, and the DAG spans features **globally**, not per
body. A body has a persistent UUID; each feature output is addressed by its producing
feature UUID and object UUID. Names are editable aliases at the UI/kernel boundary,
never a fallback for a missing identity. Bindings and output IDs are allocated before
execution and saved explicitly. This is required by
two headline features and cannot be retrofitted onto a per-body linear history:

- **Stack/Align** makes one body's placement depend on another body's *geometry*. The
  transform is re-solved from live face references on every rebuild, so moving or resizing the
  target moves the aligned body with it.
  - Because it is re-solved, the frame those distances are measured in has to be
    reproducible. Edge-gap placement reads the camera **once**, when the target
    face is picked, and only to decide which of the face's four signed axes
    reads right and up on screen — never to contribute an angle of its own
    (`kernel/align.view_frame`). Anything else would place from a diagonal: one
    project here stores a frame 13.3° off the face's own axes, where "the right
    edge" is a corner. The panel latches that frame per picked face, so stepping
    out to the anchor grid, swapping the pair, or refreshing a shape cannot
    re-derive it from wherever the camera has since moved to. Committed, it is
    four exact `0`/`±1` numbers plus the world direction they meant, so a
    rebuild that re-authors the target plane's parameterisation does not rotate
    the placement with it. Bases saved before this are restored exactly as they
    were written, and are not migrated: snapping one would move the part.
- **Create Threaded Connection** is one node with **two** outputs, writing geometry into two
  bodies and owning the shared clearance parameter — which is what makes "changing clearance
  updates both mating parts" fall out for free.

## Rebuild engine

Dirty-propagation over the feature DAG: editing a parameter or feature marks only downstream
nodes dirty and rebuilds in topological order, so unrelated bodies are never recomputed. A failing
feature is marked and skipped, the last valid geometry for its outputs is retained, and the
rebuild carries on — one bad fillet radius never empties the viewport or crashes the app.
`ReferenceLost` is handled separately from a hard failure: the geometry is fine, we have only lost
track of which face the user picked, so the feature is flagged "needs attention" rather than
"failed".

`core/graph.py` indexes dependencies, reverse dependencies, output heads and parameter
users. Observable recipe containers invalidate transitive signatures; parameter
fingerprints include indirect expressions. `core/result_store.py` caches results by
recipe, upstream signatures and kernel/cache version. Evaluation requests final heads
and hydrates only the uncached ancestors they need, using an iterative dependency walk.
An unchanged rebuild visits zero features. Cache reclamation retains current results and
the results referenced by the bounded undo/redo history; decoded results use an LRU budget.
Serialized B-Rep blobs are content-addressed and deduplicated. A separate 64 MiB
serialized-memory budget spills cold blobs into private temporary files; pruning
removes those files, and project saving streams them into the compressed archive.

Removing a modifier explicitly splices its consumers to its input version. Deleting an
object leaves its dependents flagged with missing IDs and preserves their last valid
geometry; a new object with the same name cannot repair that reference accidentally.
Undo/redo stores immutable reversible entity patches, with atomic texture actions.

### Rebuilds run in a separate process

Modelling a thread takes seconds of OCCT work. That runs in a child process, so
the window stays live: measured at **184 event-loop turns and a 77 ms worst
stall** across a 3.3 s threaded hole, against **one uninterrupted 3141 ms
freeze** when the same work runs in-process.

**A worker thread cannot do this, and it is worth recording why.** The OCP
bindings hold the GIL for the entire duration of every kernel call. Measured
directly: a pure-Python counting loop managed **7 iterations during 4.3 s** of
OCCT work, against roughly **4000** when idle — a 500x reduction. No Python
thread can make progress while the kernel is working, and `paintGL` is a Python
override, so the viewport cannot repaint either. This was implemented and
measured before being reverted: on a `QThread` the same work took **34 s per
round instead of 3 s**, because the UI thread and the worker fought over the GIL
on every repaint. A separate process has no GIL to share.

The child (`core/geometry_service.py`, Qt-free) is **persistent**: it holds its
own `Document` and `Rebuilder`, so the rebuild cache survives between requests
and an incremental edit still only re-runs what changed. Each request carries the
document's entity patch against the last sent revision (a complete snapshot on first
contact or recovery); the child preserves unchanged feature instances,
invalidates only what differs, and returns **only the bodies whose geometry
actually changed**, as B-Rep bytes. An unchanged body costs nothing on the wire.

The parent (`ui/geometry_client.py`) polls the pipe on a 16 ms timer rather than
blocking a thread on it — `poll(0)` is cheap, and it keeps every Qt object on the
main thread. Requests coalesce: one more rebuild after the current one, never a
queue, so dragging a dimension settles instead of piling up.

Start method is `forkserver`, falling back to `spawn` then `fork`. The
forkserver's server process is a fresh interpreter, so children are forked from
something clean rather than from a process holding Qt and an open GL context;
`spawn` re-executes `__main__`, which breaks when the app is launched from stdin
or an embedded interpreter.

**Worker startup and recovery never block Qt.** The client polls the READY
handshake, restores the last accepted geometry checkpoint, and then processes
newer edits. Three consecutive worker failures exhaust automatic recovery;
interactive tools show an error and allow explicit retry. They never switch
heavy kernel work onto the UI thread. Cancelling an active preview terminates
its worker without consuming the crash retry budget. Boolean previews have a
120-second deadline; a timeout is terminal until the user requests Retry.

A rebuild that is never answered is treated as a death too. Without that, a
child that hangs leaves the client permanently "busy", every later rebuild is
parked behind it, and the model silently stops changing for the rest of the
session.

**The deadline measures silence, not duration.** It used to run from the moment
a request was sent, which is a statement about how long a model is allowed to
take — and a 98-feature model measured here takes **222 s** against a 120 s
deadline. It was killed at two minutes, resent, killed again, and after three
rounds the rebuild fell back to the GUI thread, where it blocked. A slow model
read, exactly, as a hung application. So the child now sends a `PROGRESS`
message as it enters each feature (rate-limited to 5/s, and shown in the hint as
"Building Fillet (37 of 98)…"), the parent's clock restarts on each one, and a
child that is plainly still burning CPU is given the benefit of the doubt on top
of that. A child that has genuinely stopped still dies at 120 s of silence.

**No preview request is ever dropped in silence.** A tool panel writes "Building
the preview…" when it asks and rewrites it when the answer comes back, and it
listens for nothing else — so a request abandoned quietly leaves that tool
wedged on that line, Apply disabled, for the rest of the session. Everything
that lets go of a preview — a dead child, a broken pipe, a newly opened project
superseding it — goes through `GeometryClient._release_previews`, and every
released token is answered with an explicit failure. Retryable ones are
queued behind asynchronous startup and checkpoint restoration. Request tokens,
document generations, and revisions prevent obsolete replies from reaching a
new preview. Parameter changes coalesce without restarting a healthy worker.

### When it hangs, ask it what it is doing

`core/diagnostics.py` arms `faulthandler` in both processes before Qt starts, so
a native crash writes the Python stack of every thread to stderr — which, under
the desktop entry, is the journal. A four-minute "nothing works" session and two
SIGSEGVs left *nothing* before this existed.

`SIGUSR1` dumps stacks without stopping the process:

```
kill -USR1 $(pgrep -f -- '-m simplecad$')
journalctl --user -t simplecad.desktop --since "-5 min"
```

The pattern is anchored on purpose: unanchored, it also matches the shell
running the `pgrep`. A stall watchdog re-arms
`faulthandler.dump_traceback_later` from a 1 s GUI-thread heartbeat, so a GUI
thread that stops answering for 5 s dumps its own stack unprompted. It has to be
that call rather than a Python watchdog thread: OCP holds the GIL for the whole
of every kernel call, so when OCCT *is* the thing that stopped the window, no
Python thread can run to report it.

### Previews run there too

Not only committed features. The fillet and chamfer panels show the **real**
kernel result while you drag the handle, and that build used to run in the
parent, on the GUI thread, from inside a Qt signal handler. OCCT does not
reliably raise on a blend it cannot compute: two core dumps on the development
machine share one stack — `ChFi3d_Builder` walking off a null curve adaptor
inside `BRepFilletAPI_MakeFillet::Build` — and a SIGSEGV is not something
`except BaseException` can catch. The window died mid-drag.

So a preview is a `PREVIEW` message to the child, which builds the feature with
its own `execute` and returns the shape plus warnings, resolved inputs and a
specific error when the kernel refuses it. Fillet, chamfer, Thread, and all
matching-thread modes use this path; physical-thread tools keep Create disabled
until an actual helical result comes back. Preview contexts resolve explicit input versions
from the common result store and clone any recipe that needs hydration. They cannot publish
document geometry or mutate the committed recipe. Previews are coalesced separately from
rebuilds, so a dragged handle settles without ever displacing a real edit.

Clipboard version 2 captures editable feature history, nested groups, appearance,
the transitive closure of named parameters, and copy-time B-Rep geometry. Paste
remaps identities and parameter expressions, adopts a checkpoint for the cloned
history, and appends a placement move. Editing copied construction steps
invalidates their checkpoint. Version 1 clipboard data remains readable, and
project version 1 remains readable. A copy made during a pending rebuild
captures the requested history and parameters without adopting older geometry.

Rebuild publication preserves the last valid geometry and body membership of
failed dependency chains, blocks their downstream consumers, and still permits
independent successful changes. It never substitutes an earlier primitive for
a failed later modifier.

Combine constructs empty boolean builders, applies non-destructive, parallel,
and tolerance settings, then builds once per attempt. The worker retains one
successful Boolean or texture preview and promotes its outputs into the rebuild cache only
when both document state and operation inputs match. Display meshing uses the
same absolute 0.05 mm deflection and 0.35 rad angle in the worker and viewport;
serialized B-Rep includes triangulation. Unchanged shapes reuse serialized bytes.

Also helping, independently of where the work runs: booleans use OCCT's own C++
parallelism via `SetRunParallel(True)` in `occ.built_shape` (C++ threads are not
subject to the GIL — a thread-groove cut plus fuse, 1.17 s → 0.63 s),
`thread_solid` is memoised, and opening a project restores geometry from the
saved B-Rep instead of re-running features.

Opened and newly created documents use the same evaluator and cache. Version 2
restores intermediate feature results as well as final geometry. Legacy version 1
files adopt each complete final feature result independently; editing a legacy
branch may hydrate its ancestors once, without dropping unrelated results. Explicit
**Rebuild All** remains available and bypasses cached execution results.

## Project format

Version 2 `.scad3` is a compressed zip holding `model.json` (persistent identities,
explicit bindings, feature order, parameters, sub-shape references, groups, appearance,
both undo/redo stacks and a cache manifest), content-addressed `geometry/<sha256>.brep`
blobs for intermediate/final results, and an optional `thumbnail.png`. Sketch and
construction results are stored in the manifest. Blob checksums and recipe signatures
are verified before cache reuse. Old version 1 files with `bodies/<name>.brep` still load;
their missing identities are allocated deterministically and retained on the next save.
Version 1 did not save undo stacks, so those cannot be recovered.

The recipe is the file; the B-Rep is only ever a cache. Delete cached geometry and the project still
opens — it is simply rebuilt from its features, which is what
`test_the_model_rebuilds_when_the_cache_is_absent` checks. Saves are written to a `.part` file and
moved into place, so an interrupted save cannot destroy the previous version.

UI saves queue immutable recipe/history snapshots in the worker. Pending geometry is
evaluated for that snapshot before writing; an old displayed shape is never labeled as
the result of a newer recipe. Completion marks the window clean only if its edit epoch
still matches. Worker replies also carry document generations and request revisions,
so late results cannot overwrite edits or a newly opened project.

`scripts/benchmark_incremental.py` measures cold build, save/open, isolated edits and
no-op rebuilds at 100/1000/3000 independent objects, asserting identity and work-count
parity before and after reopening. Timings are informational; regression tests assert
exact recomputation counts rather than machine-dependent time limits.

Thumbnails are rendered with OCCT's `V3d_View::ToPixMap`, not Qt. `grabFramebuffer()` and
`QWidget.grab()` both **segfault** when called after a display change without an intervening paint
pass, because OCCT owns the GL context state and Qt's compositing path does not survive it.
Rendering offscreen through OCCT avoids Qt entirely.

## Two traps worth knowing about

**Single-letter shortcuts and text entry.** Qt's shortcut map consumes matching
keystrokes *before* the focused widget sees them, so a bare `S` or `5`
accelerator swallows those characters mid-expression — typing `50` into a field
would jump the camera. Single-character shortcuts are wrapped in a guard that
yields when a text widget has focus (`MainWindow._is_typing`). No test that sets
field text with `setText()` can catch this; `scripts/check_typing.py` sends real
key events.

**Sketch ids across sessions.** Sketch entity ids come from a module-global
counter that restarts at 1 in each process, while a reopened sketch brings its
old ids with it. `Sketch.from_dict` therefore calls `entities.reserve_id` for
every restored id; without it the first point added after opening a file reuses
an existing id and silently replaces that geometry, constraints and all. The
test for this resets the counter deliberately, because within one test process
it is already advanced past the collision.

## Topological naming

The highest-risk component. Sub-shapes are never referenced by index. See
`simplecad/core/naming.py`.
