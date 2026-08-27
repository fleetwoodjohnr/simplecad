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
4. An `Aspect_NeutralWindow` is created with `SetVirtual(True)` and
   **`SetNativeHandle(int(self.winId()))`**.
5. `view.SetWindow(window, context_capsule)`.
6. In `paintGL`, `OpenGl_FrameBuffer.InitWrapper(gl_ctx)` adopts the FBO Qt has currently
   bound, and the neutral window is resized to match it. This runs every frame because Qt
   recreates its FBO on resize.

### The non-obvious part

`SetNativeHandle` **must** be given `winId()` — a real X11 `Window`. OCCT does not read the
visual from the GL context; `OpenGl_Window::CreateWindow` calls `XGetWindowAttributes` on the
native handle and looks the visual up with `XGetVisualInfo`:

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
body. A body is a named output slot of whichever feature last wrote it. This is required by
two headline features and cannot be retrofitted onto a per-body linear history:

- **Stack/Align** makes one body's placement depend on another body's *geometry*. The
  transform is re-solved from live face references on every rebuild, so moving or resizing the
  target moves the aligned body with it.
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
document's serialised state; the child diffs it against what it already has,
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

**If the child cannot start, or dies, rebuilds fall back to the UI thread.**
Slower and blocking, but the session survives — the right trade for something
that is fundamentally an optimisation. `MainWindow._rebuild_in_process` is that
path, and it reports progress between features so a multi-feature rebuild does
not look frozen throughout.

Also helping, independently of where the work runs: booleans use OCCT's own C++
parallelism via `SetRunParallel(True)` in `occ.built_shape` (C++ threads are not
subject to the GIL — a thread-groove cut plus fuse, 1.17 s → 0.63 s),
`thread_solid` is memoised, and opening a project restores geometry from the
saved B-Rep instead of re-running features.

## Project format

`.scad3` is a zip holding `model.json` (the feature recipe: features, parameters, sub-shape
references, thread and alignment relationships), one `bodies/<name>.brep` per body, and an
optional `thumbnail.png`.

The recipe is the file; the B-Rep is only ever a cache. Delete `bodies/` and the project still
opens — it is simply rebuilt from its features, which is what
`test_the_model_rebuilds_when_the_cache_is_absent` checks. Saves are written to a `.part` file and
moved into place, so an interrupted save cannot destroy the previous version.

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
