"""The 3D viewport: OCCT's AIS renderer drawing into a QOpenGLWidget.

Because OCCT draws into the framebuffer Qt already owns, ordinary Qt widgets
composite on top of the viewport -- which is what lets SimpleCAD put floating
contextual panels over the model instead of docking them around it.

See docs/architecture.md for the wiring and the one non-obvious trap: OCCT
needs a real X11 window handle, but making the viewport itself native prevents
Qt's composited floating panels from receiving physical pointer events.
"""

from __future__ import annotations

import ctypes
import logging
import math
from contextlib import contextmanager
from enum import Enum, IntEnum

import time

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from ..theme import Palette, rgb
from .camera import (
    CameraAnimator, CameraController, read_state, state_for_direction,
    view_direction, write_state,
)
from .fonts import init_fonts
from .ground_grid import GroundGrid
from .handles import HandleSet

log = logging.getLogger("simplecad.viewport")

#: Re-seats that failed to produce a drawable frame before the viewport stops
#: trying to be gentle and rebuilds the whole OCCT stack instead.
RESEAT_ATTEMPTS = 2
#: And the point past which it stops trying at all and says so.
MAX_GL_RECOVERIES = 6
#: How often to look at a view that has stopped drawing. Recovery runs in
#: ``paintGL``, and the failure being recovered from is one that stops paints
#: arriving, so something has to keep asking. Cheap: it does nothing at all
#: unless a repair is already known to be outstanding.
HEALTH_INTERVAL_MS = 1000


#: Drag increments, in mm. Landing on a round number should be the default and
#: not something the user has to hunt for -- but a modifier has to be able to
#: turn it off, because some dimensions are not round.
DRAG_SNAP = 0.25
DRAG_SNAP_FINE = 0.05


def _snap_distance(value: float, modifiers=None) -> float:
    """Quantise a dragged distance. Shift is free, Ctrl is fine, else 0.25 mm."""
    if modifiers is None:
        return value
    if modifiers & Qt.ShiftModifier:
        return value
    step = DRAG_SNAP_FINE if modifiers & Qt.ControlModifier else DRAG_SNAP
    return round(value / step) * step


#: A trackpad swipe of this many pixels counts as one wheel notch.
TRACKPAD_PX_PER_NOTCH = 60.0
#: Pinch is reported as a proportional scale change; this converts it to notches.
PINCH_UNITS = 12.0

#: Face-boundary line width. Wide enough to read as a drawn outline rather than
#: an anti-aliasing artefact, narrow enough not to swallow small features.
EDGE_WIDTH = 1.8

#: How near the cursor has to be to pick something, in *logical* pixels.
#:
#: OCCT's own default is 2, and it counts device pixels -- so on a HiDPI screen
#: an edge had to be hit within a single logical pixel. That is what made edges
#: and corners feel unhittable. The drawn line is the affordance; the hit box
#: is allowed to be bigger than what it outlines, and every direct-manipulation
#: tool in the app already works that way (see handles.GRAB_PX, which is 15).
PICK_TOLERANCE_PX = 8
#: Sensitivity of the edge and vertex selection zones. OCCT's defaults are 3
#: and 12 in its own units; these widen the thin targets without touching
#: faces, which are large already and would start winning ties if they grew.
EDGE_SENSITIVITY = 8
VERTEX_SENSITIVITY = 20
#: How many shapes' snap candidates to keep. A handful: this exists to collapse
#: the many mouse-moves over one face into one computation, not to hold a model.
SNAP_CACHE_LIMIT = 16
#: How far the cursor may travel between press and release and still count as
#: a click rather than a drag, in logical pixels. Two was tight enough that an
#: ordinary hand tremor discarded the selection outright.
CLICK_SLOP_PX = 5


class SelectionMode(IntEnum):
    """OCCT ``AIS_Shape`` selection modes, named for readability."""

    BODY = 0
    VERTEX = 1
    EDGE = 2
    WIRE = 3
    FACE = 4
    SHELL = 5
    SOLID = 6


class StandardView(Enum):
    """Named camera orientations. Values are (projection, up)."""

    FRONT = ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0))
    BACK = ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    LEFT = ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    RIGHT = ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    TOP = ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0))
    BOTTOM = ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0))
    ISO = ((1.0, -1.0, 1.0), (0.0, 0.0, 1.0))


class _Nav(Enum):
    NONE = 0
    ORBIT = 1
    PAN = 2
    ZOOM = 3
    DRAG_FACE = 4
    GIZMO = 5
    DRAG_HANDLE = 6
    RUBBER_BAND = 7


#: How long to leave between snap computations, in milliseconds.
#:
#: This used to be zero, which meant "once per turn of the event loop" -- so on
#: any model where a snap took longer than a frame, snapping consumed every turn
#: the loop had and the window stopped repainting. That is what "it freezes"
#: was. One frame's worth of interval guarantees the loop a turn between snaps
#: whatever they cost, and is far below what a hand can perceive. It does mean
#: the indicator answers a fraction of a frame late, which is why anything
#: driving the viewport in a script wants ``flush_snap``.
SNAP_INTERVAL_MS = 16
#: How many named snap candidates to project per mouse-move. Each costs a
#: camera projection, and past a certain number they are all within a few
#: pixels of one another anyway -- nobody can aim at the two hundredth.
SNAP_CANDIDATE_LIMIT = 240
#: A snap slower than this (seconds) means the body is too heavy for named
#: snapping, and it drops to the cheap ray-only path from then on.
SNAP_BUDGET = 0.030
#: Slower than this and even the whole-body fallback is dropped, and the user is
#: told once. Degrading in front of someone beats hanging behind them.
SNAP_CEILING = 0.250

#: How far the cursor may move between hovering a snap and clicking it before
#: the snap is recomputed, in widget pixels. Reusing the hovered answer is what
#: guarantees the point you get is the point the indicator was drawn on: the
#: two used to be computed independently, from different cursor positions and
#: from independent detection passes, and could legitimately disagree.
SNAP_REUSE_PX = 3


def default_surface_format() -> QSurfaceFormat:
    """The GL surface SimpleCAD needs. Must be set before the first widget."""
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.CoreProfile)
    fmt.setDepthBufferSize(24)
    fmt.setStencilBufferSize(8)
    fmt.setSamples(4)
    fmt.setSwapBehavior(QSurfaceFormat.DoubleBuffer)
    return fmt


def _current_gl_context_capsule():
    """Box the current native GLX context in a PyCapsule for OCCT.

    ``Aspect_RenderingContext`` is a ``void*`` and OCP binds it as a capsule, so
    we read the pointer with ctypes while Qt's context is current.
    """
    try:
        lib = ctypes.CDLL("libGL.so.1")
        get_context = lib.glXGetCurrentContext
    except (OSError, AttributeError):
        return None
    get_context.restype = ctypes.c_void_p
    get_context.argtypes = []
    pointer = get_context()
    if not pointer:
        return None
    new_capsule = ctypes.pythonapi.PyCapsule_New
    new_capsule.restype = ctypes.py_object
    new_capsule.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p]
    return new_capsule(pointer, None, None)


class OcctViewport(QOpenGLWidget):
    """Interactive 3D view over an ``AIS_InteractiveContext``."""

    #: Emitted with a description of what is under the cursor, or None.
    hover_changed = Signal(object)
    #: Emitted whenever the confirmed selection changes.
    selection_changed = Signal()
    #: Emitted after any camera change, so overlays can reposition.
    view_changed = Signal()
    #: Emitted on right-click with the widget-space position.
    context_menu_requested = Signal(QPoint)
    #: Emitted once OCCT is live and shapes can be displayed.
    ready = Signal()
    #: Dragging a selected planar face: (distance_mm, finished).
    face_dragged = Signal(float, bool)
    #: Dragging any handle: (key, distance_mm, finished). The generic form --
    #: Fillet and Split ride on this rather than each growing their own signal.
    handle_dragged = Signal(str, float, bool)
    #: A handle was grabbed, by key. Tools latch their starting value on this,
    #: because a drag reports distance from the press and needs somewhere to
    #: add it to.
    handle_pressed = Signal(str)
    #: While sketching: the cursor's position in sketch coordinates (u, v).
    sketch_moved = Signal(float, float)
    #: A click on the sketch plane, in sketch coordinates.
    sketch_clicked = Signal(float, float)
    #: While picking points: the snap under the cursor, or None.
    snap_hovered = Signal(object)
    #: A snap point was clicked.
    snap_picked = Signal(object)
    #: The selection rectangle being dragged, as a QRect, or None when there is
    #: none. Drawn by a Qt overlay rather than here -- painting into a
    #: QOpenGLWidget is the one thing this viewport must not do.
    band_changed = Signal(object)
    #: Something the user should know that has no better home, e.g. that
    #: snapping has been turned down on a model too heavy for it.
    notice = Signal(str)
    #: OCCT was re-attached to a new GL context or native window. Everything
    #: that was displayed is still displayed, but the window has to hand the
    #: bodies back -- see ``MainWindow._on_viewport_rebound``.
    rebound = Signal()

    def __init__(self, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._initialised = False
        self._failure: str | None = None

        self._display = None
        self._driver = None
        self._viewer = None
        self._context = None
        self._view = None
        self._window = None
        self._view_cube = None
        self._fbo = None

        #: The ``QOpenGLContext`` and X11 window id OCCT was handed. Qt is free
        #: to replace either one underneath us, and when it does OCCT keeps
        #: using the old pointers -- so they are remembered and compared rather
        #: than assumed to be the ones from ``_init_occt``.
        self._bound_context = None
        self._bound_winid = None
        #: Set when the context is known to be gone but Qt has not yet given us
        #: a current one to rebind to. Acted on in ``paintGL``.
        self._rebind_pending = False
        #: Consecutive repairs that have not yet produced a healthy frame.
        #: Reset by the first frame that draws.
        self._recoveries = 0

        self._nav = _Nav.NONE
        self._press_pos = QPoint()
        self._last_pos = QPoint()
        self._dragged = False
        #: Raw event counts, for the UI dump. "The logic rejected the click" and
        #: "the click never arrived" look identical from every other piece of
        #: state, and guessing between them has cost enough already.
        self._events = {"press": 0, "release": 0, "move": 0, "wheel": 0}
        self._selection_modes: tuple[SelectionMode, ...] = (SelectionMode.BODY,)
        self._batch_depth = 0
        #: Set while a face is being dragged: (origin_px, screen_axis, mm_per_px).
        self._drag: tuple[QPoint, tuple[float, float], float] | None = None
        #: The sketch plane currently being drawn on, if any.
        self._sketch_plane = None
        #: True while the measure tool is collecting points rather than entities.
        self._picking_points = False
        #: What kind of thing the current axis drag is moving.
        self._drag_kind = "face"
        #: The transform gizmo, when one is attached.
        self.gizmo = None
        #: Grabbable handles currently on screen (fillet radius, split plane).
        self.handles = HandleSet()
        self._active_handle = None
        #: The ground plane grid.
        self._grid = GroundGrid()
        self._painted = False
        self._pending_fit = False
        #: The last snap the cursor hovered, with where it was computed.
        self._last_snap: tuple[QPoint, object] | None = None
        #: Snap candidates, keyed on the shapes they were derived from. The
        #: cursor stays over one face for dozens of consecutive events and the
        #: answer is identical every time; computing it again is what made
        #: point-to-point measuring unusable.
        self._snap_candidates: dict = {}
        #: Where the cursor last was while point-picking, waiting to be snapped.
        self._pending_snap: QPoint | None = None
        #: Set while a double-click selection is being reported, so the window
        #: knows not to widen it to the whole group.
        self._pick_inside_group = False
        #: The selection rectangle being dragged, if any.
        self._band: QRect | None = None
        #: False while a tool owns the view and a selection highlight would only
        #: be in the way -- Split, where the point is to see the cutting plane.
        self._picking_enabled = True
        #: True while a snap is being computed, so a slow one cannot be entered
        #: twice by an event loop OCCT pumped underneath us.
        self._snapping = False
        #: What each shape can afford, counted before any work is done:
        #: TShape -> "full"|"ray"|"none". See ``_effort_for``.
        self._snap_tiers: dict = {}
        #: How snapping is performing per body: TShape -> "full"|"ray"|"none".
        self._snap_effort: dict = {}
        #: Bodies we have already complained about, so we say it once.
        self._snap_warned: set = set()

        self.camera = CameraController(self)
        self.animator = CameraAnimator(self)
        # ``view_changed`` relays out every floating overlay, so during an orbit
        # emitting it per mouse-move would relayout the whole window dozens of
        # times a second. Coalesced to one emission per event-loop turn.
        self._view_changed_timer = QTimer(self)
        self._view_changed_timer.setSingleShot(True)
        self._view_changed_timer.setInterval(0)
        self._view_changed_timer.timeout.connect(self.view_changed.emit)
        # Snapping is kernel work and X11 delivers motion every few
        # milliseconds, so doing it per event means the queue never drains and
        # the window stops repainting -- which is what "the dot never appears
        # and it freezes" was. Same trick as above: keep the latest position
        # and answer it once per turn of the event loop.
        self._snap_timer = QTimer(self)
        self._snap_timer.setSingleShot(True)
        self._snap_timer.setInterval(SNAP_INTERVAL_MS)
        self._snap_timer.timeout.connect(self._emit_pending_snap)
        # Nothing to do while the view is healthy; see HEALTH_INTERVAL_MS.
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(HEALTH_INTERVAL_MS)
        self._health_timer.timeout.connect(self._check_health)
        self._health_timer.start()

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)
        self.grabGesture(Qt.PinchGesture)
        self.setMinimumSize(320, 240)

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------
    @property
    def context(self):
        """The ``AIS_InteractiveContext``, or None before initialisation."""
        return self._context

    @property
    def view(self):
        return self._view

    @property
    def is_ready(self) -> bool:
        return self._initialised and self._failure is None

    @property
    def has_rendered(self) -> bool:
        """True once a real frame has been drawn.

        ``grabFramebuffer()`` before Qt has created the FBO does not merely warn
        -- it can take the process down -- so anything that captures the view
        must check this first.
        """
        return self._painted

    @property
    def failure(self) -> str | None:
        """Why the viewport could not start, if it could not."""
        return self._failure

    def apply_palette(self, palette: Palette) -> None:
        """Re-theme the 3D scene without rebuilding it."""
        self._palette = palette
        if not self.is_ready:
            return
        self._apply_view_colors()
        self._apply_highlight_styles()
        self._apply_edge_color()
        self._apply_cube_colors()
        self._apply_grid_colors()
        # Bodies keep their per-body tint, so they are deliberately *not*
        # recoloured here. The window re-displays them after a theme change
        # instead -- see MainWindow._apply_theme.
        self.refresh()

    def capture_png(self, width: int = 640, height: int = 400) -> bytes | None:
        """Render the view offscreen and return it as PNG bytes.

        Uses OCCT's own ``ToPixMap``, which redraws into an offscreen buffer.
        Qt's ``grab()``/``grabFramebuffer()`` cannot be used here: OCCT owns the
        GL context state, and grabbing through Qt's compositing path after a
        display change segfaults. Rendering offscreen sidesteps Qt entirely and
        also lets the thumbnail be a clean shot of the model rather than a
        picture of the window.
        """
        if self._view is None or not self._painted:
            return None
        import os
        import tempfile

        from OCP.Image import Image_AlienPixMap
        from OCP.TCollection import TCollection_AsciiString

        handle, path = tempfile.mkstemp(suffix=".png")
        os.close(handle)
        try:
            image = Image_AlienPixMap()
            if not self._view.ToPixMap(image, int(width), int(height)):
                return None
            if not image.Save(TCollection_AsciiString(path)):
                return None
            with open(path, "rb") as stream:
                return stream.read()
        except Exception:  # noqa: BLE001 - a preview is never worth failing over
            return None
        finally:
            if os.path.exists(path):
                os.remove(path)

    def refresh(self) -> None:
        """Ask for a redraw, unless a batch is in progress."""
        if self._batch_depth:
            return
        if self._view is not None:
            self._view.Invalidate()
        self.update()

    @contextmanager
    def batch(self):
        """Group many display changes into a single redraw.

        Rebuilding a document re-displays every body; without this each one
        forces its own repaint, which is both slow and makes Qt complain that
        the framebuffer is not ready yet.
        """
        self._batch_depth += 1
        try:
            yield
        finally:
            self._batch_depth -= 1
            if not self._batch_depth:
                self.refresh()

    # -- camera ---------------------------------------------------------
    def camera_moved(self) -> None:
        """The one funnel every camera change reports through.

        Handles are rescaled synchronously, because they have to track the
        camera exactly or they visibly lag behind the geometry they are stuck
        to. ``view_changed`` is deliberately *not* synchronous -- see the timer
        set up in ``__init__``.
        """
        if self._view is not None:
            self._view.Invalidate()
        self.handles.rescale(self)
        self.update()
        # start() on an already-running single-shot restarts it, which during a
        # continuous orbit would postpone the emission forever.
        if not self._view_changed_timer.isActive():
            self._view_changed_timer.start()

    def scene_center(self):
        """Centre of everything the user has modelled, or None if nothing is.

        Scenery -- the ground grid, the ViewCube, previews, handles -- is
        excluded. A grid reaching out to 500 mm would otherwise drag every
        default orbit pivot back toward the world origin.
        """
        from OCP.AIS import AIS_ListOfInteractive, AIS_Shape

        from .camera import bbox_center

        if self._context is None:
            return None
        scenery = {id(obj) for obj in self._grid.presentations}
        scenery.add(id(self._view_cube))
        scenery.add(id(getattr(self, "_ghost", None)))
        scenery.update(id(h.presentation) for h in self.handles)

        displayed = AIS_ListOfInteractive()
        self._context.DisplayedObjects(displayed)
        shapes = []
        for obj in displayed:
            if id(obj) in scenery or not isinstance(obj, AIS_Shape):
                continue
            try:
                shapes.append(obj.Shape())
            except Exception:  # noqa: BLE001
                continue
        return bbox_center(shapes)

    def mm_per_pixel(self, point) -> float:
        """How many millimetres one widget pixel spans at *point*.

        Measured along whichever world axis projects longest, so the answer is
        the true screen scale rather than the foreshortened one an axis pointing
        at the camera would give.
        """
        if self._view is None:
            return 0.0
        try:
            start = self._view.Convert(point[0], point[1], point[2])
            longest = 0.0
            for axis in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)):
                ahead = self._view.Convert(
                    point[0] + axis[0], point[1] + axis[1], point[2] + axis[2]
                )
                longest = max(
                    longest,
                    math.hypot(ahead[0] - start[0], ahead[1] - start[1]),
                )
        except Exception:  # noqa: BLE001 - behind the camera has no answer
            return 0.0
        if longest < 1e-9:
            return 0.0
        return self.devicePixelRatioF() / longest

    def cursor_ray(self, pos: QPoint):
        """The world-space ray under *pos*, as ``(origin, direction)``."""
        if self._view is None:
            return None
        x, y = self._device_pos(pos)
        try:
            px, py, pz, vx, vy, vz = self._view.ConvertWithProj(x, y)
        except Exception:  # noqa: BLE001
            return None
        return ((px, py, pz), (vx, vy, vz))

    def _frame(self, apply_fit, animate: bool) -> None:
        """Run an OCCT framing call, then ease into the result.

        OCCT offers no "what would FitAll give me" query, so the framing is
        actually performed, the camera it produced read off, and the camera put
        back before the sweep starts. Cheaper than reimplementing FitAll, and it
        stays exactly in step with OCCT's own idea of a margin.
        """
        before = read_state(self._view)
        apply_fit()
        target = read_state(self._view)
        if not animate or before is None or target is None:
            self.camera_moved()
            return
        write_state(self._view, before)
        self.animator.to(target)

    def fit_all(self, margin: float = 0.15, animate: bool = False) -> None:
        """Frame everything.

        *animate* defaults to off, and deliberately. A sweep leaves the camera
        somewhere other than where it will end up for the next few frames, and
        callers reasonably expect that once this returns they can project a
        point and get the framed answer -- tools do exactly that when they place
        a handle. So framing settles at once, and the entry points a *person*
        reaches for (the Fit button, F, the view commands) ask for the sweep.
        """
        if self._view is None:
            return
        if not self._painted:
            # Framing before the first paint asks OCCT to size itself against a
            # framebuffer Qt has not created yet. Defer to the first real frame.
            self._pending_fit = True
            self.update()
            return
        self._frame(lambda: self._view.FitAll(margin, False), animate)

    def zoom_to_selection(self, margin: float = 0.25, animate: bool = False) -> None:
        """Frame the current selection, falling back to the whole model.

        Settles immediately unless asked otherwise -- see :meth:`fit_all`.
        """
        if self._context is None or self._view is None:
            return
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib

        box = Bnd_Box()
        found = False
        self._context.InitSelected()
        while self._context.MoreSelected():
            owner = self._context.SelectedOwner()
            shape = self._selected_shape(owner)
            if shape is not None and not shape.IsNull():
                BRepBndLib.Add_s(shape, box, True)
                found = True
            self._context.NextSelected()
        if not found or box.IsVoid():
            self.fit_all(animate=animate)
            return
        corner_min = box.CornerMin()
        corner_max = box.CornerMax()
        self._frame(
            lambda: self._view.FitAll(
                corner_min.X(), corner_min.Y(), corner_min.Z(),
                corner_max.X(), corner_max.Y(), corner_max.Z(),
                margin, False,
            ),
            animate,
        )

    def set_standard_view(self, which: StandardView, animate: bool = True) -> None:
        """Re-aim the camera along a named axis.

        Deliberately does **not** refit. Re-orienting and re-framing are two
        different requests, and doing both means pressing 5 for Top after
        zooming into a detail throws the zoom away -- so the part you were
        looking at is gone and you have to find it again.
        """
        if self._view is None:
            return
        (px, py, pz), _up = which.value
        state = read_state(self._view)
        if state is None:
            return
        # The enum stores where the camera sits; the view direction is its
        # negation. Up is not read from the enum: constrained_up derives the
        # roll-free up for any direction, and agrees with the enum everywhere.
        target = state_for_direction(state, (-px, -py, -pz))
        if not self._painted or self.scene_center() is None:
            write_state(self._view, target)
            self._view.FitAll(0.15, False)
            self.camera_moved()
            return
        self.animator.to(target) if animate else self.animator.jump(target)

    def set_perspective(self, enabled: bool) -> None:
        if self._view is None:
            return
        from OCP.Graphic3d import Graphic3d_Camera

        camera = self._view.Camera()
        camera.SetProjectionType(
            Graphic3d_Camera.Projection_e.Projection_Perspective
            if enabled
            else Graphic3d_Camera.Projection_e.Projection_Orthographic
        )
        self._view.Redraw()
        self.camera_moved()

    def is_perspective(self) -> bool:
        if self._view is None:
            return False
        from OCP.Graphic3d import Graphic3d_Camera

        return (
            self._view.Camera().ProjectionType()
            == Graphic3d_Camera.Projection_e.Projection_Perspective
        )

    # -- selection ------------------------------------------------------
    def apply_pick_tolerance(self) -> None:
        """Widen the pick radius to something a hand can actually hit.

        Re-applied whenever the device pixel ratio can have changed, because
        OCCT counts device pixels and the ratio is what turns a comfortable
        target into an impossible one.
        """
        if self._context is None:
            return
        self._context.SetPixelTolerance(
            max(1, round(PICK_TOLERANCE_PX * self.devicePixelRatioF()))
        )

    def _apply_sensitivity(self, presentation) -> None:
        """Give this body's edges and corners bigger sensitive zones.

        Faces are left alone. They are large targets already, and OCCT breaks a
        tie by priority -- vertex over edge over face -- so the only thing that
        stops a corner winning is the cursor never reaching its zone at all.
        """
        if self._context is None or presentation is None:
            return
        for mode, sensitivity in (
            (SelectionMode.EDGE, EDGE_SENSITIVITY),
            (SelectionMode.VERTEX, VERTEX_SENSITIVITY),
        ):
            if mode not in self._selection_modes:
                continue
            try:
                self._context.SetSelectionSensitivity(
                    presentation, int(mode), int(sensitivity)
                )
            except Exception:  # noqa: BLE001 - a mode this object has no zones for
                pass

    def set_selection_modes(self, modes) -> None:
        """Restrict what the user can pick (bodies, faces, edges, vertices)."""
        modes = tuple(modes) or (SelectionMode.BODY,)
        self._selection_modes = modes
        if self._context is None:
            return
        from OCP.AIS import AIS_ListOfInteractive

        displayed = AIS_ListOfInteractive()
        self._context.DisplayedObjects(displayed)
        for obj in displayed:
            if self._view_cube is not None and obj == self._view_cube:
                continue
            self._context.Deactivate(obj)
            for mode in modes:
                self._context.Activate(obj, int(mode))
            self._apply_sensitivity(obj)
        self.refresh()

    def set_picking_enabled(self, enabled: bool) -> None:
        """Turn clicking and hovering on the model on or off.

        For tools that own the view and whose subject is *not* the body -- Split
        is the one, where the thing to look at is the cutting plane and a
        selection highlight glowing through it is pure noise. Handles are
        unaffected: they are tested before this in the press handler, so the
        plane stays draggable while nothing else responds.
        """
        self._picking_enabled = bool(enabled)
        if not self._picking_enabled and self._context is not None:
            self._context.ClearDetected(False)
            # Nothing is under the cursor any more as far as anyone watching is
            # concerned -- said out loud, because no further move event will say
            # it and a highlight left on screen is exactly what this turns off.
            self.hover_changed.emit(None)
            self.refresh()

    @property
    def picking_enabled(self) -> bool:
        return self._picking_enabled

    def clear_selection(self) -> None:
        if self._context is None:
            return
        self._context.ClearSelected(False)
        self.refresh()
        self.selection_changed.emit()

    # -- rectangle (marquee) selection ----------------------------------
    def _band_rect(self, pos: QPoint) -> QRect:
        """The rectangle from where the button went down to *pos*."""
        return QRect(self._press_pos, pos).normalized()

    def _set_band(self, rect) -> None:
        self._band = rect
        self.band_changed.emit(rect)

    @staticmethod
    def _band_is_a_drag(rect) -> bool:
        """Big enough to have been meant, rather than a click with a tremor."""
        return rect is not None and (
            rect.width() > CLICK_SLOP_PX or rect.height() > CLICK_SLOP_PX
        )

    def _set_overlap_detection(self, enabled: bool) -> None:
        """Whether a rectangle catches what it touches or only what it encloses."""
        try:
            self._context.MainSelector().AllowOverlapDetection(bool(enabled))
        except Exception:  # noqa: BLE001 - an older selector simply encloses
            pass

    def select_in_rect(
        self, rect: QRect, additive: bool = False, crossing: bool = False
    ) -> None:
        """Select every whole object the rectangle catches.

        **Whole objects, not faces.** Dragging a box across the model says "these
        things", and a marquee that came back with forty faces would offer none
        of the operations -- Group, Move, Subtract, Delete -- that wanting
        several things at once is usually the point of.

        *crossing* is the difference between a box that catches only what it
        completely surrounds and one that catches anything it touches. Both are
        wanted: surrounding is precise, touching is forgiving, and every CAD
        program worth copying picks between them by the direction of the drag.

        OCCT can only return what its active selection modes allow, so the modes
        are narrowed for the rectangle pass and put back afterwards, and the
        answer is read out before they are restored. Reading it out is also what
        lets the additive case be a genuine toggle rather than a second pass.
        """
        if self._context is None or self._view is None:
            return
        top_left = self._device_pos(rect.topLeft())
        bottom_right = self._device_pos(rect.bottomRight())
        previous = self._selection_modes

        self.set_selection_modes((SelectionMode.BODY, SelectionMode.SOLID))
        self._set_overlap_detection(crossing)
        try:
            # The rectangular overload takes an update flag, not a scheme, and
            # always replaces -- which is why the additive case is applied by
            # hand below rather than asked for here.
            self._context.Select(
                top_left[0], top_left[1], bottom_right[0], bottom_right[1],
                self._view, False,
            )
            found = []
            self._context.InitSelected()
            while self._context.MoreSelected():
                presentation = self._context.SelectedInteractive()
                if presentation is not None and not any(
                    p is presentation for p in found
                ):
                    found.append(presentation)
                self._context.NextSelected()
        finally:
            self._set_overlap_detection(False)
            self.set_selection_modes(previous)

        if not additive:
            self._context.ClearSelected(False)
        for presentation in found:
            self._context.AddOrRemoveSelected(presentation, False)
        self.refresh()
        self.selection_changed.emit()
        self.update()

    def selected_shapes(self) -> list:
        """Every selected sub-shape, as ``TopoDS_Shape``."""
        return [entry["shape"] for entry in self.selected_entries()]

    def selected_entries(self) -> list[dict]:
        """Selections with the presentation that owns each one.

        The owner matters: it is what lets the document turn a picked face back
        into "the top face of Lid" and store a durable reference to it.
        """
        if self._context is None:
            return []
        from OCP.TopAbs import (
            TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID, TopAbs_VERTEX, TopAbs_WIRE,
        )

        kinds = {
            TopAbs_VERTEX: "vertex", TopAbs_EDGE: "edge", TopAbs_WIRE: "wire",
            TopAbs_FACE: "face", TopAbs_SOLID: "solid",
        }
        out: list[dict] = []
        self._context.InitSelected()
        while self._context.MoreSelected():
            owner = self._context.SelectedOwner()
            shape = self._selected_shape(owner)
            presentation = self._context.SelectedInteractive()
            if shape is not None and not shape.IsNull():
                out.append({
                    "shape": shape,
                    "presentation": presentation,
                    "kind": kinds.get(shape.ShapeType(), "body"),
                })
            self._context.NextSelected()
        return out

    def select_shape(self, presentation, replace: bool = True) -> None:
        """Select a whole presentation programmatically."""
        if self._context is None or presentation is None:
            return
        if replace:
            self._context.ClearSelected(False)
        self._context.AddOrRemoveSelected(presentation, False)
        self.refresh()
        self.selection_changed.emit()

    def set_highlight(self, presentation, on: bool) -> None:
        """Temporarily emphasise a presentation, used for previews."""
        if self._context is None or presentation is None:
            return
        if on:
            self._context.HilightWithColor(
                presentation, self._context.HighlightStyle(), False
            )
        else:
            self._context.Unhilight(presentation, False)
        self.refresh()

    def _selected_shape(self, owner):
        """The sub-shape an owner refers to, or None.

        OCP already hands back the most-derived bound type, so this is an
        isinstance check rather than an explicit downcast -- OCCT's
        ``DownCast`` helpers are not exposed on these classes.
        """
        from OCP.StdSelect import StdSelect_BRepOwner

        if not isinstance(owner, StdSelect_BRepOwner) or not owner.HasShape():
            return None
        shape = owner.Shape()
        if owner.HasLocation():
            shape = shape.Located(owner.Location() * shape.Location())
        return shape

    # -- display --------------------------------------------------------
    def show_ghost(self, shape, color: str | None = None, transparency: float = 0.55):
        """Display a translucent preview shape, replacing any previous one.

        *color* lets the caller say what the preview *means* -- material being
        added reads differently from material being removed, and a single colour
        for both leaves the user guessing which way the drag is going.

        *transparency* says how much of a preview it is. A slab being added to a
        face is an overlay on a body that is still the subject, so it stays
        faint; a filleted body *replaces* what is underneath while the drag
        lasts, so it is drawn nearly solid instead.
        """
        self.clear_ghost()
        if self._context is None or shape is None:
            return None
        from OCP.AIS import AIS_Shape
        from OCP.Quantity import Quantity_Color, Quantity_TOC_sRGB

        presentation = AIS_Shape(shape)
        presentation.SetColor(
            Quantity_Color(*rgb(color or self._palette.ghost), Quantity_TOC_sRGB)
        )
        self._apply_boundary_aspect(presentation)
        presentation.SetTransparency(float(transparency))
        self._context.Display(presentation, 1, -1, False)
        self._ghost = presentation
        self.refresh()
        return presentation

    def show_overlay_shape(self, shape, color: str | None = None,
                           transparency: float = 0.6):
        """Display a shape as scenery: visible, never pickable, caller-owned.

        Distinct from :meth:`show_ghost`, which is a singleton -- there is only
        ever one drag preview, so displaying a second replaces the first. A
        split plane has to coexist with whatever else is on screen and outlive
        any number of drags, so its presentation belongs to the tool that made
        it and is removed with :meth:`erase`.
        """
        if self._context is None or shape is None:
            return None
        from OCP.AIS import AIS_Shape
        from OCP.Quantity import Quantity_Color, Quantity_TOC_sRGB

        presentation = AIS_Shape(shape)
        presentation.SetColor(
            Quantity_Color(*rgb(color or self._palette.accent), Quantity_TOC_sRGB)
        )
        self._apply_boundary_aspect(presentation)
        presentation.SetTransparency(float(transparency))
        # Selection mode -1: drawn, but out of the pick stack entirely, so it
        # can never be selected instead of the model it is lying across.
        self._context.Display(presentation, 1, -1, False)
        self.refresh()
        return presentation

    def clear_ghost(self) -> None:
        ghost = getattr(self, "_ghost", None)
        if ghost is not None and self._context is not None:
            self._context.Remove(ghost, False)
        self._ghost = None

    def display(self, shape, color: str | None = None, transparency: float = 0.0):
        """Show a ``TopoDS_Shape`` and return its ``AIS_Shape`` presentation."""
        if self._context is None:
            return None
        from OCP.AIS import AIS_Shape
        from OCP.Quantity import Quantity_Color, Quantity_TOC_sRGB

        presentation = AIS_Shape(shape)
        chosen = color or self._palette.face
        red, green, blue = rgb(chosen)
        presentation.SetColor(Quantity_Color(red, green, blue, Quantity_TOC_sRGB))
        # SetColor gives the shape its *own* face-boundary aspect and paints it
        # the body colour, which is why edges used to vanish into the surface
        # they were meant to outline. The context's default drawer never gets a
        # look in, so the outline has to be re-stated per presentation.
        self._apply_boundary_aspect(presentation)
        if transparency:
            presentation.SetTransparency(transparency)
        self._context.Display(presentation, 1, int(self._selection_modes[0]), False)
        for mode in self._selection_modes[1:]:
            self._context.Activate(presentation, int(mode))
        self._apply_sensitivity(presentation)
        self.refresh()
        return presentation

    def set_transparency(self, presentation, value: float) -> None:
        """Make a displayed body see-through, or solid again at 0.

        Used while pushing a face inward: the slab being removed sits *inside*
        the solid, so without this the preview is hidden by the very body it is
        cutting into and the drag looks like nothing is happening.
        """
        if self._context is None or presentation is None:
            return
        self._context.SetTransparency(presentation, float(value), False)
        self.refresh()

    def erase(self, presentation) -> None:
        if self._context is not None and presentation is not None:
            self._context.Remove(presentation, False)
            self.refresh()

    def clear(self) -> None:
        if self._context is None:
            return
        self._context.RemoveAll(False)
        if self._view_cube is not None:
            self._context.Display(self._view_cube, False)
        # RemoveAll takes the grid with it; put it back.
        self._grid.redisplay()
        self.refresh()

    # ------------------------------------------------------------------
    # GL lifecycle
    # ------------------------------------------------------------------
    def initializeGL(self) -> None:  # noqa: N802 - Qt naming
        """Build OCCT the first time; re-attach it every time after that.

        Qt calls this again whenever it has made a new context current for this
        widget, which is not an error and usually not even a rebuild. See
        :meth:`_recover_binding` for which of the two handles went stale and
        what each one costs to repair.
        """
        if self._initialised:
            if self._binding_is_stale():
                self._recover_binding()
            return
        self._initialised = True
        self._watch_context()
        try:
            self._init_occt()
        except Exception as exc:  # noqa: BLE001 - report, never crash the app
            self._failure = f"{type(exc).__name__}: {exc}"
            return
        self.ready.emit()

    def _watch_context(self) -> None:
        """Notice when the GL context goes away underneath OCCT.

        OCCT renders through a raw context pointer it was handed once. If Qt
        destroys that context -- a driver reset, the compositor taking it back,
        XWayland losing the surface -- OCCT does not find out. It carries on
        being asked to draw and logs ``glXMakeCurrent() has failed!``, the view
        stops changing, and the application is left looking frozen while its
        event loop is in fact perfectly healthy and idle.

        That is not a state the user can diagnose from the outside, and it is
        what a four-minute "nothing works" session on this machine looked like
        from the inside: an idle loop, an idle geometry process, and a picture
        that had stopped moving.

        Knowing early lets us drop the stale handles before anything is asked to
        draw through them. Re-armed on every rebind, because the notification is
        attached to the context that is going away.
        """
        # QOpenGLWidget.context explicitly: this class defines its own
        # ``context`` property for the AIS interactive context, which shadows
        # Qt's method of the same name.
        context = QOpenGLWidget.context(self)
        if context is None:
            return
        try:
            context.aboutToBeDestroyed.connect(self._on_context_lost)
        except (AttributeError, RuntimeError):  # pragma: no cover - old Qt
            pass

    def _on_context_lost(self) -> None:
        """The context is going. Let go of it and wait for its replacement.

        Deliberately not a failure. Qt destroys and recreates the context for
        ordinary reasons -- the compositor taking the surface back, the widget
        gaining a native ancestor, a move between screens -- and every one of
        those is followed by a fresh context and another ``initializeGL``.
        Recording it as fatal is what left the view white and frozen for the
        rest of the session.
        """
        self._bound_context = None
        self._rebind_pending = True
        # The framebuffer belonged to the context that is going. Holding the
        # wrapper past that point is how a dead FBO gets drawn into.
        self._fbo = None
        log.warning("the OpenGL context was destroyed; the 3D view will rebuild")

    def _init_occt(self) -> None:
        from OCP.AIS import AIS_InteractiveContext, AIS_ViewCube
        from OCP.Aspect import (
            Aspect_DisplayConnection,
            Aspect_NeutralWindow,
            Aspect_TOTP_RIGHT_UPPER,
        )
        from OCP.Graphic3d import (
            Graphic3d_TMF_TriedronPers,
            Graphic3d_TransformPers,
            Graphic3d_TOSM_FRAGMENT,
            Graphic3d_Vec2i,
        )
        from OCP.OpenGl import OpenGl_GraphicDriver
        from OCP.V3d import V3d_Viewer

        init_fonts()

        capsule = _current_gl_context_capsule()
        if capsule is None:
            raise RuntimeError(
                "No OpenGL context is current; SimpleCAD needs QT_QPA_PLATFORM=xcb"
            )

        self._display = Aspect_DisplayConnection()
        self._driver = OpenGl_GraphicDriver(self._display, False)
        # Qt owns presentation: it swaps buffers and owns the surface.
        caps = self._driver.ChangeOptions()
        caps.buffersNoSwap = True
        caps.buffersOpaqueAlpha = True
        caps.useSystemBuffer = False

        self._viewer = V3d_Viewer(self._driver)
        self._viewer.SetDefaultLights()
        self._viewer.SetLightOn()
        self._viewer.SetDefaultShadingModel(Graphic3d_TOSM_FRAGMENT)

        self._context = AIS_InteractiveContext(self._viewer)
        self._context.SetAutoActivateSelection(True)
        drawer = self._context.DefaultDrawer()
        drawer.SetFaceBoundaryDraw(True)
        drawer.SetIsoOnTriangulation(True)
        boundary = drawer.FaceBoundaryAspect()
        red, green, blue = rgb(self._palette.edge)
        # sRGB, not TOC_RGB. OCCT treats TOC_RGB as linear and gamma-encodes it
        # on the way out, which turned the intended near-black #2B303A into a
        # mid-grey #727883 -- indistinguishable from the shaded faces it is
        # meant to outline. Faces already go in as sRGB a few lines below.
        from OCP.Quantity import Quantity_Color, Quantity_TOC_sRGB

        boundary.SetColor(Quantity_Color(red, green, blue, Quantity_TOC_sRGB))
        boundary.SetWidth(EDGE_WIDTH)
        self._context.SetDisplayMode(1, False)  # shaded
        self.apply_pick_tolerance()

        # The neutral window must carry a real X11 Window id -- OCCT reads the
        # visual from it via XGetWindowAttributes.  Use the effective native
        # ancestor, not self.winId(): the latter promotes this QOpenGLWidget to
        # a native child that sits in front of Qt's composited panels and takes
        # their physical XWayland pointer events. See docs/architecture.md.
        self._window = Aspect_NeutralWindow()
        self._window.SetVirtual(True)
        self._window.SetNativeHandle(self._native_window_handle())
        width, height = self._device_size()
        self._window.SetSize(width, height)

        self._view = self._viewer.CreateView()
        self._view.SetWindow(self._window, capsule)
        self._remember_binding()

        params = self._view.ChangeRenderingParams()
        # Qt's FBO already carries 4x MSAA; a second MSAA pass here makes OCCT's
        # resolve-blit fail with GL_INVALID_OPERATION.
        params.NbMsaaSamples = 0
        params.IsAntialiasingEnabled = False
        params.IsShadowEnabled = False

        self._apply_view_colors()
        self._apply_highlight_styles()

        self._grid.attach(self._context, self._palette)

        self._view_cube = AIS_ViewCube()
        self._view_cube.SetSize(58.0)
        self._view_cube.SetFontHeight(11.0)
        self._view_cube.SetDrawAxes(True)
        self._apply_cube_colors()
        self._view_cube.SetTransformPersistence(
            Graphic3d_TransformPers(
                Graphic3d_TMF_TriedronPers,
                Aspect_TOTP_RIGHT_UPPER,
                Graphic3d_Vec2i(90, 90),
            )
        )
        self._context.Display(self._view_cube, False)

        self.set_standard_view(StandardView.ISO)

    def _remember_binding(self) -> None:
        """Record the context and window OCCT is currently drawing through."""
        self._bound_context = QOpenGLWidget.context(self)
        self._bound_winid = self._native_window_handle()
        self._rebind_pending = False

    def _native_window_handle(self) -> int:
        """Return a real X11 window without making the viewport native.

        ``effectiveWinId()`` returns the nearest native ancestor's handle for a
        composited child.  That is enough for OCCT's visual lookup because it
        renders into Qt's current FBO, while keeping the viewport and every
        floating panel in one Qt backing store so normal hit testing works.
        """
        handle = int(self.effectiveWinId())
        if not handle:
            raise RuntimeError("No native ancestor is available for the 3D view")
        return handle

    def _check_health(self) -> None:
        """Keep asking for a frame while a repair is outstanding.

        The whole difficulty with this failure is that it removes its own
        symptom: once OCCT cannot make its context current the view stops
        changing, so nothing asks it to paint, so the recovery that lives in
        ``paintGL`` never runs again. The window then sits there rendering the
        last good frame and ignoring the user -- which is what "it opens and it
        just does not work at all" was.
        """
        if self._failure is not None or self._view is None:
            return
        if self._rebind_pending:
            self.update()

    def _binding_is_stale(self) -> bool:
        """Has either handle OCCT was given been replaced underneath us?

        Both are checked. Watching only the context is what left this bug in
        place through one whole round of fixing it: the journal for a failing
        session shows ``glXMakeCurrent() has failed!`` with no context-lost line
        anywhere near it, because Qt had replaced the *window* and kept the
        context. ``effectiveWinId()`` returns the stored handle of the native
        ancestor, so asking every frame costs nothing and does not promote this
        widget to a native child.
        """
        return (
            QOpenGLWidget.context(self) is not self._bound_context
            or self._native_window_handle() != self._bound_winid
        )

    def _recover_binding(self) -> bool:
        """Repair whichever handle went stale, at the cost that one deserves.

        The two failures are not the same failure. A replaced *context* takes
        every shader, vertex buffer and texture with it, so the scene has to be
        built again. A replaced *window* leaves all of that intact and needs
        nothing but a new drawable -- and rebuilding for that would throw away a
        working scene, and re-tessellate every body, every time the compositor
        handed us a new surface.
        """
        if QOpenGLWidget.context(self) is not self._bound_context:
            return self._rebind_gl()
        if self._recoveries > RESEAT_ATTEMPTS:
            # Re-pointing has not taken. Whatever is wrong is not just the
            # drawable, so stop being economical and build the thing again.
            return self._rebind_gl()
        return self._reseat_window()

    def _reseat_window(self) -> bool:
        """Same GL context, new X window: re-point OCCT and keep the scene.

        ``V3d_View::SetWindow`` is what holds the drawable, and it accepts being
        given a new one. Everything on the GPU still belongs to a live context,
        so nothing else has to be rebuilt.
        """
        if self._view is None or self._window is None or self._failure is not None:
            return False
        capsule = _current_gl_context_capsule()
        if capsule is None:
            self._rebind_pending = True
            return False
        try:
            # The framebuffer wrapper is re-adopted from Qt on the next paint.
            self._fbo = None
            self._window.SetNativeHandle(self._native_window_handle())
            self._window.SetSize(*self._device_size())
            self._view.SetWindow(self._window, capsule)
        except Exception as exc:  # noqa: BLE001 - report, never crash the app
            self._rebind_pending = True
            log.error("could not re-point the 3D view at its new window: %s", exc)
            return False
        # SetWindow re-creates the view's own render objects, so the background
        # has to be re-stated -- otherwise a theme chosen before this point is
        # set on the view and never drawn, which is the white-viewport half of
        # this bug.
        self._apply_view_colors()
        self._view.MustBeResized()
        self._view.Invalidate()
        self.apply_pick_tolerance()
        self._remember_binding()
        log.info("3D view re-pointed at a new effective native window")
        return True

    def _teardown_occt(self) -> None:
        """Let go of every OCCT object tied to the context that has gone.

        Dropped, never removed. Handing the *new* context a presentation built
        in the old one raises ``object has been displayed in another context``,
        so anything holding an AIS object here has to forget it instead. What
        the window owns it forgets in ``MainWindow._on_viewport_rebound``.
        """
        self._grid.detach()
        self._ghost = None
        self.gizmo = None
        self.handles.handles.clear()
        self._view_cube = None
        self._view = None
        self._viewer = None
        self._context = None
        self._driver = None
        self._display = None
        self._window = None
        self._fbo = None
        self._bound_context = None
        self._bound_winid = None

    def _rebind_gl(self) -> bool:
        """Rebuild OCCT's GL side against the context Qt is using *now*.

        Re-seating the existing view -- handing ``V3d_View::SetWindow`` a fresh
        context capsule -- is the obvious fix and it does not work. It appears
        to: no exception, no message. But the viewer still holds the shaders,
        vertex buffers and textures it uploaded to the context that has gone,
        none of which mean anything in the new one, and every draw after that
        fails with ``GL_INVALID_OPERATION`` on an empty screen. The scene has to
        be built again.

        Nothing the user cares about is lost, because none of it lives on the
        GPU: the camera, the grid, what may be picked, and the bodies -- which
        the window re-displays when it sees ``rebound``.

        Must run with Qt's context current, which is true in ``initializeGL``
        and at the top of ``paintGL``. Returns True if the view can draw.
        """
        if not self._initialised or self._failure is not None:
            return False
        if _current_gl_context_capsule() is None:
            # Nothing current to build against yet; the next paint will have one.
            self._rebind_pending = True
            return False

        camera = self.camera_state()
        modes = self._selection_modes
        grid_visible = self._grid.visible

        self._teardown_occt()
        try:
            self._init_occt()
        except Exception as exc:  # noqa: BLE001 - report, never crash the app
            self._failure = f"{type(exc).__name__}: {exc}"
            log.error("could not rebuild the 3D view: %s", exc)
            # Only now is it worth saying out loud. A lost context on its own is
            # routine and recovered from silently; a rebuild that will not come
            # back is the one case where the user is looking at a dead view and
            # deserves to be told rather than left guessing.
            self.notice.emit(
                "The 3D view could not be restored after the graphics driver "
                "reset it. Your model is safe — save it, then reopen SimpleCAD."
            )
            return False

        self._grid.set_visible(grid_visible)
        # After _init_occt, which leaves the camera at the default ISO view.
        self.restore_camera(camera)
        self.set_selection_modes(modes)
        self._watch_context()
        log.info("3D view rebuilt on a new GL context")
        self.rebound.emit()
        return True

    # -- ground grid ----------------------------------------------------
    def show_grid(self, visible: bool) -> None:
        """Show or hide the ground plane grid."""
        self._grid.set_visible(visible)
        self.refresh()

    @property
    def grid_visible(self) -> bool:
        return self._grid.visible

    def _apply_grid_colors(self) -> None:
        self._grid.apply_palette(self._palette)

    def _apply_cube_colors(self) -> None:
        """Re-colour the ViewCube. Called on every theme change, not just setup."""
        from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB

        if self._view_cube is None:
            return
        self._view_cube.SetBoxColor(
            Quantity_Color(*rgb(self._palette.cube_box), Quantity_TOC_RGB)
        )
        self._view_cube.SetTextColor(
            Quantity_Color(*rgb(self._palette.cube_text), Quantity_TOC_RGB)
        )
        if self._context is not None:
            self._context.Redisplay(self._view_cube, False)

    def _apply_boundary_aspect(self, presentation) -> None:
        """Give *presentation* a face boundary drawn in the theme's edge colour."""
        from OCP.Aspect import Aspect_TOL_SOLID
        from OCP.Prs3d import Prs3d_LineAspect
        from OCP.Quantity import Quantity_Color, Quantity_TOC_sRGB

        drawer = presentation.Attributes()
        drawer.SetFaceBoundaryDraw(True)
        drawer.SetFaceBoundaryAspect(
            Prs3d_LineAspect(
                Quantity_Color(*rgb(self._palette.edge), Quantity_TOC_sRGB),
                Aspect_TOL_SOLID,
                EDGE_WIDTH,
            )
        )

    def _apply_edge_color(self) -> None:
        from OCP.Quantity import Quantity_Color, Quantity_TOC_sRGB

        if self._context is None:
            return
        boundary = self._context.DefaultDrawer().FaceBoundaryAspect()
        boundary.SetColor(
            Quantity_Color(*rgb(self._palette.edge), Quantity_TOC_sRGB)
        )
        boundary.SetWidth(EDGE_WIDTH)

    def _apply_view_colors(self) -> None:
        from OCP.Aspect import Aspect_GradientFillMethod_Vertical
        from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB

        # Declared as linear (TOC_RGB) rather than sRGB: OCCT writes background
        # colours straight through without the conversion it applies to shaded
        # geometry, so declaring sRGB here would darken it by a second gamma.
        top = Quantity_Color(*rgb(self._palette.view_top), Quantity_TOC_RGB)
        bottom = Quantity_Color(*rgb(self._palette.view_bottom), Quantity_TOC_RGB)
        self._view.SetBgGradientColors(
            top, bottom, Aspect_GradientFillMethod_Vertical, True
        )

    def _apply_highlight_styles(self) -> None:
        from OCP.Prs3d import (
            Prs3d_TypeOfHighlight_Dynamic,
            Prs3d_TypeOfHighlight_LocalDynamic,
            Prs3d_TypeOfHighlight_LocalSelected,
            Prs3d_TypeOfHighlight_Selected,
        )
        from OCP.Quantity import Quantity_Color, Quantity_TOC_sRGB

        hover = Quantity_Color(*rgb(self._palette.hover), Quantity_TOC_sRGB)
        chosen = Quantity_Color(*rgb(self._palette.selected), Quantity_TOC_sRGB)
        for kind, color in (
            (Prs3d_TypeOfHighlight_Dynamic, hover),
            (Prs3d_TypeOfHighlight_LocalDynamic, hover),
            (Prs3d_TypeOfHighlight_Selected, chosen),
            (Prs3d_TypeOfHighlight_LocalSelected, chosen),
        ):
            style = self._context.HighlightStyle(kind)
            if style is not None:
                style.SetColor(color)
                style.SetDisplayMode(1)
                # A selected face should read as a tinted surface with its own
                # boundary, not as a cage around the complete part.
                style.SetTransparency(
                    0.22 if kind in (
                        Prs3d_TypeOfHighlight_Selected,
                        Prs3d_TypeOfHighlight_LocalSelected,
                    ) else 0.38
                )

    def _device_size(self) -> tuple[int, int]:
        ratio = self.devicePixelRatioF()
        return (
            max(1, int(round(self.width() * ratio))),
            max(1, int(round(self.height() * ratio))),
        )

    def resizeGL(self, width: int, height: int) -> None:  # noqa: N802
        if self._view is None:
            return
        ratio = self.devicePixelRatioF()
        self._window.SetSize(
            max(1, int(round(width * ratio))), max(1, int(round(height * ratio)))
        )
        self._view.MustBeResized()
        self._view.Invalidate()
        # Moving the window to a screen of a different density resizes it, and
        # the pick tolerance is expressed in device pixels, so it is re-derived
        # here rather than left at whatever the first screen implied.
        self.apply_pick_tolerance()

    def paintGL(self) -> None:  # noqa: N802
        if self._view is None:
            return
        from OCP.OpenGl import OpenGl_FrameBuffer

        # Qt does not always route a replaced handle through initializeGL, so
        # the check that matters is here, before anything is drawn through a
        # pointer that may no longer be valid.
        if self._rebind_pending or self._binding_is_stale():
            if not self._recover_binding():
                return

        gl_context = self._driver.GetSharedContext()
        if gl_context is None:
            # A failure, not "nothing to draw" -- so it has to leave a repair
            # outstanding. Returning quietly here is half of why the health
            # check below never ran in a session that needed it.
            self._rebind_pending = True
            return
        # Not ``gl_context.IsCurrent()``. It looks like the ideal health check
        # -- OCCT implements it as ``glXGetCurrentContext() == myGContext &&
        # glXGetCurrentDrawable() == myWindow``, which is exactly the test whose
        # failure prints ``glXMakeCurrent() has failed!``. It was measured
        # instead: True on a bare viewport, and False on every healthy frame
        # once the widget is a child inside the real window, because Qt's
        # current drawable is then the top-level's and OCCT switches to its own
        # inside Redraw. Used as a health signal it condemns a working view on
        # its first frame. The handle comparison in ``_binding_is_stale`` is the
        # check that holds.
        #
        # Adopt whichever FBO Qt has bound right now. Qt recreates it on resize,
        # so this has to happen every frame, not once.
        fbo = gl_context.DefaultFrameBuffer()
        if fbo is None:
            fbo = OpenGl_FrameBuffer()
            gl_context.SetDefaultFrameBuffer(fbo)
        if not fbo.InitWrapper(gl_context):
            # This is the return that mattered. When OCCT cannot make its
            # context current, InitWrapper is the first thing to fail -- so
            # returning here skipped ``_note_frame_health`` precisely when the
            # frame had not drawn, and nothing was ever marked for repair.
            self._rebind_pending = True
            self._view.Invalidate()
            return
        self._fbo = fbo
        width, height = fbo.GetInitVPSizeX(), fbo.GetInitVPSizeY()
        if (width, height) != self._window.Size():
            self._window.SetSize(width, height)
            self._view.MustBeResized()
            self._view.Invalidate()
        # Qt's FBO is plain RGBA8, not sRGB-encoded. Without this OCCT converts
        # sRGB colours to linear and nothing converts them back on write, so the
        # whole scene renders roughly gamma^2 too dark.
        gl_context.SetFrameBufferSRGB(True, False)
        self._painted = True
        if self._pending_fit:
            self._pending_fit = False
            self._view.FitAll(0.15, False)
            # Queued, never emitted straight from here. The receivers are Qt
            # widgets -- the highlight box and the dimension overlay -- and
            # running widget code inside a GL paint pass can re-enter Qt's
            # compositing path. The queued timer also coalesces this with the
            # path ``camera_moved`` uses.
            if not self._view_changed_timer.isActive():
                self._view_changed_timer.start()
        self._view.InvalidateImmediate()
        try:
            self._view.Redraw()
        except Exception as exc:  # noqa: BLE001 - one bad frame is recoverable
            # Said once and turned into a repair, rather than raised out of a
            # paint event every frame for the rest of the session.
            self._rebind_pending = True
            log.warning("the 3D view failed to draw (%s); re-attaching", exc)
            return
        self._note_frame_health(gl_context)

    def _note_frame_health(self, gl_context) -> None:
        """Did that frame actually reach the screen?

        ``Redraw`` does not raise when OCCT cannot bind its context -- it prints
        ``glXMakeCurrent() has failed!`` through OCCT's own message stream and
        returns normally, which is why every failing session in the journal
        shows that line and nothing else. Asking afterwards is what turns it
        into something recoverable.

        Timing is the whole trick. ``IsCurrent`` compares against *OCCT's*
        context and drawable, so at the top of ``paintGL`` -- where Qt's are
        still current -- it is False on perfectly healthy frames, and using it
        there condemns a working view on sight. After ``Redraw`` it is True on
        every healthy frame. Both were measured before this was wired up.
        """
        if gl_context.IsCurrent():
            self._recoveries = 0
            self._rebind_pending = False
            return
        self._recoveries += 1
        if self._recoveries > MAX_GL_RECOVERIES:
            if self._failure is None:
                self._failure = "the OpenGL surface could not be re-attached"
                log.error("gave up re-attaching the 3D view")
                self.notice.emit(
                    "The 3D view lost its connection to the graphics driver and "
                    "could not recover. Your model is safe — save it, then "
                    "reopen SimpleCAD."
                )
            return
        # Handled at the top of the next paint, which _check_health guarantees
        # will come even though this failure is one that stops paints arriving.
        self._rebind_pending = True
        log.warning(
            "the 3D view did not draw (attempt %d); re-attaching",
            self._recoveries,
        )
        self.update()

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Sketching on a plane
    # ------------------------------------------------------------------
    def begin_sketch(self, plane) -> None:
        """Route mouse events to the sketch plane instead of to selection."""
        self._sketch_plane = plane

    def end_sketch(self) -> None:
        self._sketch_plane = None

    @property
    def sketching(self) -> bool:
        return self._sketch_plane is not None

    def sketch_point(self, pos: QPoint):
        """Where the cursor lands on the sketch plane, in sketch (u, v).

        A screen position is a ray, not a point, so it is intersected with the
        sketch plane. Returns None when the plane is edge-on and the ray is
        parallel to it -- there is no meaningful answer there, and guessing one
        would drop geometry at infinity.
        """
        if self._view is None or self._sketch_plane is None:
            return None
        x, y = self._device_pos(pos)
        px, py, pz, vx, vy, vz = self._view.ConvertWithProj(x, y)

        plane = self._sketch_plane
        normal = plane.normal
        denominator = vx * normal[0] + vy * normal[1] + vz * normal[2]
        if abs(denominator) < 1e-9:
            return None
        origin = plane.origin
        numerator = sum(
            (origin[i] - (px, py, pz)[i]) * normal[i] for i in range(3)
        )
        distance = numerator / denominator
        hit = (px + vx * distance, py + vy * distance, pz + vz * distance)

        offset = tuple(hit[i] - origin[i] for i in range(3))
        x_axis, y_axis = plane.x_axis, plane.y_axis()
        return (
            sum(offset[i] * x_axis[i] for i in range(3)),
            sum(offset[i] * y_axis[i] for i in range(3)),
        )

    # -- point picking ---------------------------------------------------
    def begin_point_pick(self) -> None:
        """Route clicks to snap points instead of to selection.

        No selection-mode juggling is needed: the window already activates
        vertex, edge, face and body modes for the whole session, so
        ``detected_shape`` already reports sub-shapes of every kind.
        """
        self._picking_points = True
        # Point mode sends motion to the snapper instead of to hover detection,
        # so nothing will report the cursor leaving whatever it was last over.
        # Said here, or the highlight box from before the switch stays on screen
        # competing with the snap indicator it was replaced by.
        self.hover_changed.emit(None)

    def end_point_pick(self) -> None:
        self._picking_points = False

    @property
    def picking_points(self) -> bool:
        return self._picking_points

    def project(self, point) -> tuple[float, float] | None:
        """Where a 3D point lands on screen, in widget (not device) pixels."""
        if self._view is None:
            return None
        try:
            x, y = self._view.Convert(point[0], point[1], point[2])
        except Exception:  # noqa: BLE001 - behind the camera has no answer
            return None
        ratio = self.devicePixelRatioF()
        return (float(x) / ratio, float(y) / ratio)

    def snap_at(self, pos: QPoint, reuse: bool = False):
        """The best snap point under *pos*, or None.

        Detection lights up whatever it finds, which while point-picking means
        a whole face glowing behind the little snap marker that is the actual
        answer. The highlight is dropped again straight away: here the entity
        under the cursor is a means of finding candidates, not the subject.

        Two things make this the point the user actually meant rather than a
        point they have to hunt for.

        **There is always an answer over geometry.** Corners, midpoints and
        centres are what you usually want, but demanding one within a handful of
        pixels means a click in the middle of a face lands on nothing at all and
        the tool appears broken. :func:`ray_snaps` adds the place the cursor ray
        genuinely meets the surface or the edge, ranked below everything else,
        so it only wins when nothing better is near.

        **The pick is the hover.** With *reuse* the answer the indicator was
        drawn from is returned verbatim, provided the cursor has barely moved.
        Recomputing would run a second, independent detection pass from a
        slightly different pixel, which is exactly how the point you got came to
        differ from the marker you clicked on.
        """
        from ...kernel.snapping import nearest, ray_snaps

        if reuse and self._last_snap is not None:
            where, cached = self._last_snap
            if (where - pos).manhattanLength() <= SNAP_REUSE_PX:
                return cached

        from ...kernel.snapping import SnapPoint

        started = time.perf_counter()
        shape, parent = self.detected_pair(pos)
        # Read before ClearDetected: MoveTo has just worked out exactly where the
        # cursor ray met the model, and asking for that answer costs nothing.
        picked = self.picked_point()
        self._context.ClearDetected(False)
        # No update() here. mouseMoveEvent asks for one immediately after, and
        # two repaint requests per motion event is how a queue that was merely
        # busy became one that never drains.
        if shape is None:
            self._last_snap = (QPoint(pos), None)
            return None

        # How much work this shape can afford -- decided from sub-shape counts
        # before any of it is done, so a heavy body never gets to stall the
        # window even once.
        effort = self._effort_for(shape, parent)
        candidates = (
            self._candidates_for(shape, parent) if effort == "full" else []
        )
        if len(candidates) > SNAP_CANDIDATE_LIMIT:
            # Already sorted most-specific first, so this keeps the corners and
            # the centres and drops the tail nobody could aim at anyway.
            candidates = candidates[:SNAP_CANDIDATE_LIMIT]
        ray = self.cursor_ray(pos)
        if ray is not None and effort != "none":
            # ``parent`` is the fallback that keeps an answer under the cursor
            # when the detected sub-shape itself has no surface to hit. It is
            # also the single most expensive thing here on a heavy body, so only
            # the full tier asks for it.
            candidates = candidates + ray_snaps(
                shape, ray, parent=parent if effort == "full" else None
            )
        if picked is not None:
            # The continuity guarantee, for free and at every tier: wherever
            # OCCT saw the model, there is a point to snap to. Ranked with the
            # other surface hits, so any named place still beats it.
            candidates = candidates + [SnapPoint(picked, "surface")]
        snap = nearest(candidates, self.project, (pos.x(), pos.y()))
        self._note_snap_cost(parent, time.perf_counter() - started)
        self._last_snap = (QPoint(pos), snap)
        return snap

    def picked_point(self):
        """Where the cursor ray met the model on the last ``MoveTo``, or None.

        OCCT computes this as part of detection, so it is already sitting in the
        selector. Re-deriving it with ``BRepIntCurveSurface_Inter`` -- which is
        what the ray fallback does -- costs 18 ms on a 646-face shell and 68 ms
        on a 2554-face one, on the GUI thread, on every mouse-move. Asking for
        the answer that already exists costs nothing and does not grow with the
        model.
        """
        if self._context is None:
            return None
        try:
            selector = self._context.MainSelector()
            if selector.NbPicked() < 1:
                return None
            point = selector.PickedPoint(1)
        except Exception:  # noqa: BLE001 - an older selector simply has none
            return None
        if point is None:
            return None
        return (point.X(), point.Y(), point.Z())

    def _effort_for(self, shape, parent) -> str:
        """What this shape can afford, counted rather than timed.

        The learned downgrade from :meth:`_note_snap_cost` still applies, and
        still only ever makes things cheaper -- it is the backstop for a body
        whose counts looked affordable and turned out not to be.
        """
        from ...kernel.snapping import snap_tier

        key = shape.TShape()
        tier = self._snap_tiers.get(key)
        if tier is None:
            tier = snap_tier(shape)
            if len(self._snap_tiers) >= SNAP_CACHE_LIMIT:
                self._snap_tiers.pop(next(iter(self._snap_tiers)))
            self._snap_tiers[key] = tier
        learned = self._snap_effort.get(self._snap_key(parent), "full")
        order = {"full": 0, "ray": 1, "none": 2}
        return tier if order[tier] >= order[learned] else learned

    @staticmethod
    def _snap_key(parent):
        if parent is None:
            return None
        try:
            return parent.TShape()
        except Exception:  # noqa: BLE001
            return None

    def _note_snap_cost(self, parent, elapsed: float) -> None:
        """Downgrade snapping on a body that cannot afford it, and say so once.

        Only ever downgrades. A single fast reading on a heavy body -- the
        cursor happening to cross a simple face -- must not undo the decision,
        or the stutter comes straight back.
        """
        if elapsed <= SNAP_BUDGET:
            return
        key = self._snap_key(parent)
        level = "none" if elapsed > SNAP_CEILING else "ray"
        order = {"full": 0, "ray": 1, "none": 2}
        current = self._snap_effort.get(key, "full")
        if order[level] <= order[current]:
            return
        self._snap_effort[key] = level
        if key in self._snap_warned:
            return
        self._snap_warned.add(key)
        self.notice.emit(
            "This model is heavy enough that snapping to corners and centres "
            "would stall the view, so the cursor is following the surface "
            "instead."
        )

    def _candidates_for(self, shape, parent):
        """The named snaps on *shape*, computed once per shape rather than
        once per mouse-move. See ``_snap_candidates``."""
        from ...kernel.snapping import snap_points

        key = (shape.TShape(), None if parent is None else parent.TShape())
        found = self._snap_candidates.get(key)
        if found is None:
            found = snap_points(shape, parent=parent)
            if len(self._snap_candidates) >= SNAP_CACHE_LIMIT:
                self._snap_candidates.pop(next(iter(self._snap_candidates)))
            self._snap_candidates[key] = found
        return found

    def _emit_pending_snap(self) -> None:
        """Answer the most recent cursor position, and only that one.

        Guarded against re-entry: OCCT pumps the event loop during some of its
        own work, so without this a slow snap can be entered again from inside
        itself, and a stall becomes a hang.
        """
        pos, self._pending_snap = self._pending_snap, None
        if pos is None or not self._picking_points or self._snapping:
            return
        self._snapping = True
        try:
            snap = self.snap_at(pos)
        finally:
            self._snapping = False
        self.snap_hovered.emit(snap)
        self.update()

    def request_snap(self, pos: QPoint) -> None:
        """Note where the cursor is; the snap follows on the next event turn."""
        self._pending_snap = QPoint(pos)
        if not self._snap_timer.isActive():
            self._snap_timer.start()

    def flush_snap(self) -> None:
        """Answer any pending snap now instead of on the timer.

        Snapping is deliberately paced so it can never monopolise the event
        loop, which means the indicator lands a fraction of a frame after the
        cursor -- invisible to a hand, and awkward for anything driving the
        viewport in a script, which moves the cursor and looks immediately.
        """
        self._snap_timer.stop()
        self._emit_pending_snap()

    def clear_snap_cache(self) -> None:
        from ...kernel.snapping import clear_caches

        self._last_snap = None
        self._pending_snap = None
        self._snap_candidates.clear()
        self._snap_tiers.clear()
        # Deliberately *not* cleared: what a body costs to snap on is a property
        # of the body, and a rebuild that changes it will key the record
        # differently anyway. Forgetting it here would re-learn the same stall
        # every time the measure tool was reopened.
        clear_caches()

    def camera_state(self):
        """Enough of the camera to put it back afterwards."""
        if self._view is None:
            return None
        camera = self._view.Camera()
        eye, at, up = camera.Eye(), camera.Center(), camera.Up()
        return (
            (eye.X(), eye.Y(), eye.Z()),
            (at.X(), at.Y(), at.Z()),
            (up.X(), up.Y(), up.Z()),
            camera.Scale(),
        )

    def restore_camera(self, state) -> None:
        if self._view is None or state is None:
            return
        self.animator.cancel()
        write_state(self._view, state)
        self.camera_moved()

    def look_at_plane(self, plane, margin: float = 0.3) -> None:
        """Face the camera square-on to a sketch plane."""
        if self._view is None:
            return
        # Deliberately immediate rather than eased: entering a sketch is a mode
        # change, and the canvas starts mapping cursor positions onto the plane
        # straight away -- which needs a camera that has already arrived.
        self.animator.cancel()
        normal, up = plane.normal, plane.y_axis()
        self._view.SetProj(normal[0], normal[1], normal[2])
        self._view.SetUp(up[0], up[1], up[2])
        self._view.SetAt(*plane.origin)
        self._view.FitAll(margin, False)
        self.camera_moved()

    def screen_axis_for(self, point, direction) -> tuple[tuple[float, float], float]:
        """How a 3D direction appears on screen, and its scale.

        Returns a unit screen-space vector for *direction* anchored at *point*,
        plus how many millimetres one pixel of movement along it represents.
        This is what turns a mouse drag into a real distance.
        """
        import math

        start = self._view.Convert(point[0], point[1], point[2])
        ahead = self._view.Convert(
            point[0] + direction[0], point[1] + direction[1], point[2] + direction[2]
        )
        dx = float(ahead[0] - start[0])
        dy = float(ahead[1] - start[1])
        length = math.hypot(dx, dy)
        if length < 1e-6:
            # The direction points almost straight at the camera: dragging it
            # would be unusable, so report no scale and let the caller bail.
            return ((0.0, 0.0), 0.0)
        return ((dx / length, dy / length), 1.0 / length)

    def begin_axis_drag(self, anchor, direction, kind: str = "face") -> bool:
        """Start dragging along a direction in space. False if it is edge-on.

        The single mechanism behind Push/Pull, the fillet handle and the split
        plane: project the direction onto the screen, work out how many
        millimetres a pixel of travel along it is worth, and quantise the
        result. Anything that wants to be pulled out to a distance goes through
        here rather than reinventing the projection and the snapping.
        """
        if self._view is None:
            return False
        axis, scale = self.screen_axis_for(anchor, direction)
        if scale <= 0.0:
            return False
        self._drag = (self._press_pos, axis, scale)
        self._drag_kind = kind
        self._nav = _Nav.DRAG_FACE if kind == "face" else _Nav.DRAG_HANDLE
        return True

    def begin_face_drag(self, center, normal) -> bool:
        """Start dragging a face along its normal. Returns False if edge-on."""
        return self.begin_axis_drag(center, normal, "face")

    def _handle_press(self, pos: QPoint) -> bool:
        """Begin a handle drag if one is under the cursor.

        Never while point-picking: a handle left over from a previous tool
        would otherwise swallow the first click of a measurement.
        """
        if self._picking_points:
            return False
        handle = self.handles.hit(self, pos)
        if handle is None:
            return False
        if not self.begin_axis_drag(handle.anchor, handle.direction, handle.key):
            return False
        self._active_handle = handle
        self.handle_pressed.emit(handle.key)
        return True

    def _drag_distance(self, pos: QPoint, modifiers=None) -> float:
        origin, axis, scale = self._drag
        ratio = self.devicePixelRatioF()
        dx = (pos.x() - origin.x()) * ratio
        dy = (pos.y() - origin.y()) * ratio
        raw = (dx * axis[0] + dy * axis[1]) * scale
        return _snap_distance(raw, modifiers)

    def _device_pos(self, pos: QPoint) -> tuple[int, int]:
        ratio = self.devicePixelRatioF()
        return int(round(pos.x() * ratio)), int(round(pos.y() * ratio))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._events["press"] += 1
        self._press_pos = event.position().toPoint()
        self._last_pos = self._press_pos
        self._dragged = False
        button = event.button()
        modifiers = event.modifiers()

        # Any deliberate input cancels a camera sweep in progress -- being
        # carried onward by an animation you have already overruled is the
        # least forgivable kind of camera surprise.
        self.animator.cancel()

        if button == Qt.MiddleButton or (
            button == Qt.LeftButton and modifiers & Qt.AltModifier
        ):
            if modifiers & Qt.ShiftModifier:
                self._nav = _Nav.PAN
            else:
                self._nav = _Nav.ORBIT
                self.camera.begin_orbit(self._press_pos)
        elif button == Qt.RightButton and modifiers & Qt.ShiftModifier:
            self._nav = _Nav.PAN
        elif button == Qt.LeftButton and self._handle_press(self._press_pos):
            pass                                  # _handle_press set the mode
        elif (
            button == Qt.LeftButton
            and self.gizmo is not None
            and self.gizmo.active
            and self.gizmo.press(*self._device_pos(self._press_pos))
        ):
            self._nav = _Nav.GIZMO
        elif button == Qt.LeftButton and self._sketch_plane is not None:
            self._nav = _Nav.NONE
        elif (
            button == Qt.LeftButton
            and not self._picking_points
            and self._picking_enabled
        ):
            self._nav = _Nav.NONE
            if not modifiers:
                # Pull gets first refusal: pressing on an already-selected face
                # is a drag on that face, and always has been.
                self.drag_candidate_requested()
            if self._nav is _Nav.NONE:
                # Nothing else claimed the press, so it may become a marquee.
                # It only becomes one once the cursor actually travels; until
                # then this is still an ordinary click.
                self._nav = _Nav.RUBBER_BAND
        else:
            self._nav = _Nav.NONE
        self.setFocus()

    def drag_candidate_requested(self) -> None:
        """Hook for the window: may call begin_face_drag to start a pull."""

    def detected_shape(self, pos: QPoint):
        """Whatever is under *pos* right now, without changing the selection."""
        return self.detected_pair(pos)[0]

    def detected_pair(self, pos: QPoint):
        """``(sub_shape, owning_body_shape)`` under *pos*, without selecting.

        The owning body comes along because snapping wants the faces *next to*
        the one detected: point at a box near a corner and OCCT quite reasonably
        reports the face, but the corner the user is aiming at is shared with
        two faces it did not report.
        """
        from OCP.AIS import AIS_Shape

        if self._context is None or self._view is None:
            return (None, None)
        x, y = self._device_pos(pos)
        self._context.MoveTo(x, y, self._view, False)
        if not self._context.HasDetected():
            return (None, None)
        shape = self._selected_shape(self._context.DetectedOwner())
        parent = None
        owner = self._context.DetectedInteractive()
        if isinstance(owner, AIS_Shape):
            try:
                parent = owner.Shape()
            except Exception:  # noqa: BLE001
                parent = None
        return (shape, parent)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self._events["move"] += 1
        if self._view is None:
            return
        pos = event.position().toPoint()
        delta = pos - self._last_pos
        # Measured from where the button went down, not from the previous
        # event. Per-event it meant one jumpy motion report marked the whole
        # gesture a drag, and mouseReleaseEvent then threw the selection away
        # -- so a click on an edge with any tremor in it simply did nothing.
        #
        # Only while a button is actually held. Mouse tracking is on, so this
        # runs on every hover move too, and without the guard the flag was set
        # by nothing more than the cursor crossing the viewport -- leaving a
        # "this gesture was a drag" verdict standing on a gesture that never
        # happened, for any press whose own event went astray.
        if event.buttons() and (pos - self._press_pos).manhattanLength() > CLICK_SLOP_PX:
            self._dragged = True

        if self._nav is _Nav.GIZMO and self.gizmo is not None:
            self.gizmo.drag(*self._device_pos(pos))
            self._last_pos = pos
            return
        if self._nav is _Nav.DRAG_FACE and self._drag is not None:
            self.face_dragged.emit(
                self._drag_distance(pos, event.modifiers()), False
            )
            self._last_pos = pos
            return
        if self._nav is _Nav.DRAG_HANDLE and self._drag is not None:
            self.handle_dragged.emit(
                self._drag_kind, self._drag_distance(pos, event.modifiers()), False
            )
            self._last_pos = pos
            return
        if self._nav is _Nav.RUBBER_BAND:
            rect = self._band_rect(pos)
            self._set_band(rect if self._band_is_a_drag(rect) else None)
            self._last_pos = pos
            return
        if self._nav is _Nav.ORBIT:
            self.camera.orbit(delta.x(), delta.y())
            self.camera_moved()
        elif self._nav is _Nav.PAN:
            self.camera.pan(delta.x(), delta.y())
            self.camera_moved()
        elif self._sketch_plane is not None:
            where = self.sketch_point(pos)
            if where is not None:
                self.sketch_moved.emit(where[0], where[1])
        elif self._picking_points:
            self.request_snap(pos)
        elif self._picking_enabled:
            x, y = self._device_pos(pos)
            self._context.MoveTo(x, y, self._view, True)
            self.hover_changed.emit(self._describe_detected())
            self.update()
        self._last_pos = pos

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._events["release"] += 1
        was_nav = self._nav
        self._nav = _Nav.NONE
        if self._view is None:
            return
        if was_nav is _Nav.ORBIT:
            self.camera.end_orbit()
        if was_nav is _Nav.RUBBER_BAND:
            rect = self._band_rect(event.position().toPoint())
            self._set_band(None)
            if self._band_is_a_drag(rect):
                self.select_in_rect(
                    rect,
                    additive=bool(
                        event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier)
                    ),
                    # Dragged leftward means "anything I touched", rightward
                    # means "only what I surrounded" -- the convention every
                    # other CAD program uses, and worth matching because people
                    # arrive already knowing it.
                    crossing=event.position().toPoint().x() < self._press_pos.x(),
                )
                return
            # Too small to have been a rectangle: it was a click, so let the
            # ordinary single-pick path below have it.
            was_nav = _Nav.NONE
        if was_nav is _Nav.DRAG_HANDLE and self._drag is not None:
            distance = self._drag_distance(
                event.position().toPoint(), event.modifiers()
            )
            kind, self._drag, self._active_handle = self._drag_kind, None, None
            self.handle_dragged.emit(kind, distance, True)
            return
        if was_nav is _Nav.GIZMO and self.gizmo is not None:
            components = self.gizmo.release()
            if components is not None:
                self.gizmo.committed.emit(*components)
            return
        if was_nav is _Nav.DRAG_FACE and self._drag is not None:
            distance = self._drag_distance(
                event.position().toPoint(), event.modifiers()
            )
            self._drag = None
            self.face_dragged.emit(distance, True)
            return
        if was_nav is not _Nav.NONE:
            return
        if event.button() == Qt.RightButton:
            if not self._dragged:
                self.context_menu_requested.emit(event.position().toPoint())
            return
        if event.button() != Qt.LeftButton or self._dragged:
            return
        if not self._picking_enabled and self._sketch_plane is None:
            return

        if self._sketch_plane is not None:
            where = self.sketch_point(event.position().toPoint())
            if where is not None:
                self.sketch_clicked.emit(where[0], where[1])
            return

        if self._picking_points:
            # reuse=True: the point that gets picked is exactly the one the
            # indicator was drawn on. See snap_at.
            snap = self.snap_at(event.position().toPoint(), reuse=True)
            if snap is not None:
                self.snap_picked.emit(snap)
            return

        from OCP.AIS import AIS_SelectionScheme_Replace, AIS_SelectionScheme_XOR

        scheme = (
            AIS_SelectionScheme_XOR
            if event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier)
            else AIS_SelectionScheme_Replace
        )
        # Detected at the *press* position. Re-detecting where the button came
        # up means pressing on an edge and lifting a pixel to one side selects
        # the face behind it instead -- the user aimed once, and that is the
        # aim that should count.
        x, y = self._device_pos(self._press_pos)
        self._context.MoveTo(x, y, self._view, False)
        if self._handle_view_cube_click():
            return
        self._context.SelectDetected(scheme)
        self.selection_changed.emit()
        self.update()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """Select the individual object, ignoring any group it belongs to.

        Alt is already orbit and Shift and Ctrl are already selection modifiers,
        so reaching *into* a group is a double-click -- which is what every
        other application that has groups uses anyway.
        """
        from OCP.AIS import AIS_SelectionScheme_Replace

        if (
            self._view is None
            or event.button() != Qt.LeftButton
            or self._sketch_plane is not None
            or self._picking_points
            or not self._picking_enabled
        ):
            return
        x, y = self._device_pos(event.position().toPoint())
        self._context.MoveTo(x, y, self._view, False)
        if self._handle_view_cube_click():
            return
        self._context.SelectDetected(AIS_SelectionScheme_Replace)
        self._pick_inside_group = True
        try:
            self.selection_changed.emit()
        finally:
            self._pick_inside_group = False
        self.update()

    @property
    def picking_inside_group(self) -> bool:
        """True while a double-click selection is being reported."""
        return getattr(self, "_pick_inside_group", False)

    def _handle_view_cube_click(self) -> bool:
        """If the ViewCube was clicked, orient the camera and report it."""
        from OCP.AIS import AIS_ViewCubeOwner

        owner = self._context.DetectedOwner()
        if not isinstance(owner, AIS_ViewCubeOwner):
            return False
        before = read_state(self._view)
        # OCCT will not say what an orientation means as a vector, so ask it by
        # applying the orientation, reading the direction back, and putting the
        # camera where it was before sweeping there properly.
        self._view.SetProj(owner.MainOrientation(), True)
        after = read_state(self._view)
        if before is None or after is None:
            self.camera_moved()
            return True
        target = state_for_direction(before, view_direction(after))
        write_state(self._view, before)
        self.animator.to(target)
        return True

    def wheelEvent(self, event) -> None:  # noqa: N802
        """Zoom at the cursor, from a wheel or from a trackpad.

        A mouse wheel arrives as whole notches in ``angleDelta``; a trackpad
        arrives as a stream of small ``pixelDelta`` values, dozens of them for
        one swipe. The old fixed 15% per event was tuned for the first and made
        the second unusable, so the step is taken from the *magnitude* of the
        gesture and then eased -- see :class:`CameraController`.

        Shift turns the same gesture into a pan, and takes the horizontal axis
        with it, so a trackpad's sideways travel is not simply discarded.
        """
        self._events["wheel"] += 1
        if self._view is None:
            return
        self.animator.cancel()
        pixels = event.pixelDelta()
        degrees = event.angleDelta()
        modifiers = event.modifiers()

        if pixels.x() or pixels.y():
            pan_x, pan_y = pixels.x(), pixels.y()
            units = pixels.y() / TRACKPAD_PX_PER_NOTCH
        else:
            pan_x, pan_y = degrees.x() / 8.0, degrees.y() / 8.0
            units = degrees.y() / 120.0

        if modifiers & Qt.ShiftModifier:
            # Negated: a two-finger swipe down and a drag down should move the
            # model the same way, and Qt reports them with opposite signs.
            self.camera.pan(-pan_x, -pan_y)
            self.camera_moved()
            event.accept()
            return
        if not units:
            event.ignore()
            return
        if modifiers & Qt.ControlModifier:
            units *= 0.35                    # a finer notch, for exact framing
        self.camera.zoom(units, self._device_pos(event.position().toPoint()))
        event.accept()

    def event(self, event) -> bool:
        """Intercept trackpad gestures before Qt's default handling.

        Pinch reaches Qt as a native gesture on some platforms and as a
        synthesised ``QPinchGesture`` on others, and on plain X11 as neither --
        there it turns up as Ctrl and a wheel, which ``wheelEvent`` already
        handles. All three routes are wired so pinch-to-zoom works wherever the
        platform offers it, and its absence costs nothing.
        """
        kind = event.type()
        if kind == QEvent.NativeGesture and self._native_gesture(event):
            return True
        if kind == QEvent.Gesture and self._pinch_gesture(event):
            return True
        return super().event(event)

    def _native_gesture(self, event) -> bool:
        if self._view is None:
            return False
        gesture = event.gestureType()
        if gesture == Qt.ZoomNativeGesture:
            self.animator.cancel()
            self.camera.zoom(
                float(event.value()) * PINCH_UNITS,
                self._device_pos(event.position().toPoint()),
            )
            return True
        # Begin/End carry no movement, but swallowing them stops Qt turning
        # them into stray wheel events.
        return gesture in (Qt.BeginNativeGesture, Qt.EndNativeGesture)

    def _pinch_gesture(self, event) -> bool:
        from PySide6.QtWidgets import QPinchGesture

        pinch = event.gesture(Qt.PinchGesture)
        if not isinstance(pinch, QPinchGesture) or self._view is None:
            return False
        change = pinch.scaleFactor() - 1.0
        if abs(change) > 1e-4:
            self.animator.cancel()
            centre = self.mapFromGlobal(pinch.centerPoint().toPoint())
            self.camera.zoom(change * PINCH_UNITS, self._device_pos(centre))
        event.accept()
        return True

    def _describe_detected(self) -> dict | None:
        """A small, UI-friendly description of what is under the cursor."""
        if not self._context.HasDetected():
            return None
        from OCP.TopAbs import (
            TopAbs_COMPOUND, TopAbs_EDGE, TopAbs_FACE,
            TopAbs_SHELL, TopAbs_SOLID, TopAbs_VERTEX, TopAbs_WIRE,
        )

        shape = self._selected_shape(self._context.DetectedOwner())
        if shape is None or shape.IsNull():
            return {"kind": "object"}
        kind = {
            TopAbs_VERTEX: "vertex",
            TopAbs_EDGE: "edge",
            TopAbs_WIRE: "wire",
            TopAbs_FACE: "face",
            TopAbs_SHELL: "shell",
            TopAbs_SOLID: "solid",
            TopAbs_COMPOUND: "body",
        }.get(shape.ShapeType(), "object")
        return {"kind": kind, "shape": shape}
