"""The application shell.

Layout follows one rule: the viewport is the app, and everything else floats
over it. There is a slim top bar, a narrow tool rail, and cards that appear
where they are relevant. No dock widgets, no toolbar rows, no workbench picker.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow, QSizePolicy, QVBoxLayout,
    QWidget,
)

from ..core.document import BodyRef, Document
from ..core.history import History
from ..core.rebuild import Rebuilder
from ..kernel.operations import PushPullFeature
from .icons import icon
from .geometry_client import GeometryClient, apply_result
from .panels.command_search import CommandSearch
from .panels.dimension_labels import DimensionOverlay
from .panels.sketch_bar import SketchBar
from .panels.context_bar import ContextBar
from .panels.model_browser import ModelBrowser
from .selection import SelectionModel, available_actions
from .theme import METRICS, Mode, Palette, resolve, stylesheet
from .viewport.occt_view import OcctViewport, SelectionMode, StandardView
from .widgets.controls import FloatingCard, Hint, IconButton, ToolTile

#: The tool rail. Kept short on purpose -- these are the things a user reaches
#: for constantly; everything else lives in search (S) and context menus.
TOOL_RAIL = (
    ("box", "Shape", "shapes"),
    ("sketch", "Sketch", "sketch"),
    ("extrude", "Pull", "pushpull"),
    ("move", "Move", "move"),
    ("stack", "Align", "align"),
    ("hole", "Hole", "hole"),
    ("thread", "Thread", "thread"),
    ("fillet", "Fillet", "fillet"),
    ("shell", "Hollow", "shell"),
    ("measure", "Measure", "measure"),
    ("print", "Print", "print"),
)


def _combined_bounds(shapes):
    """The axis-aligned bounds enclosing every shape in *shapes*."""
    from ..kernel.occ import bounding_box

    boxes = [bounding_box(shape) for shape in shapes if shape is not None]
    if not boxes:
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    low = tuple(min(box[0][i] for box in boxes) for i in range(3))
    high = tuple(max(box[1][i] for box in boxes) for i in range(3))
    return (low, high)


def _extent_label(normal) -> str:
    """Name the dimension a drag along *normal* changes: Width, Depth, Height."""
    axis = max(range(3), key=lambda i: abs(normal[i]))
    return ("Width", "Depth", "Height")[axis]


class TopBar(QWidget):
    """Slim application bar: identity on the left, actions on the right."""

    theme_toggled = Signal()
    search_requested = Signal()
    action_triggered = Signal(str)

    def __init__(self, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        self._palette = palette
        self.setObjectName("TopBar")
        self.setFixedHeight(52)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(METRICS.space(4), 0, METRICS.space(3), 0)
        layout.setSpacing(METRICS.space(2))

        self.wordmark = QLabel("SimpleCAD")
        self.title = QLabel("Untitled")
        layout.addWidget(self.wordmark)
        layout.addSpacing(METRICS.space(3))
        layout.addWidget(self.title)
        layout.addStretch(1)

        self.buttons: dict[str, IconButton] = {}
        for name, tooltip, action in (
            ("folder", "Open  (Ctrl+O)", "open"),
            ("import", "Import a 3D file  (Ctrl+I)", "import"),
            ("search", "Search commands  (S)", "search"),
            ("undo", "Undo  (Ctrl+Z)", "undo"),
            ("redo", "Redo  (Ctrl+Shift+Z)", "redo"),
            ("save", "Save  (Ctrl+S)", "save"),
            ("export", "Export", "export"),
        ):
            button = IconButton(name, palette, tooltip)
            button.clicked.connect(lambda _=False, a=action: self.action_triggered.emit(a))
            layout.addWidget(button)
            self.buttons[action] = button

        self.theme_button = IconButton("moon", palette, "Light / dark")
        self.theme_button.clicked.connect(self.theme_toggled.emit)
        layout.addWidget(self.theme_button)
        self.apply_palette(palette)

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.wordmark.setStyleSheet(
            f"color:{palette.text}; font-size:15px; font-weight:650;"
            "letter-spacing:0.2px;"
        )
        self.title.setStyleSheet(f"color:{palette.text_faint}; font-size:13px;")
        self.setStyleSheet(
            f"QWidget#TopBar {{ background:{palette.surface}; "
            f"border-bottom:1px solid {palette.border}; }}"
            f"QWidget#TopBar QLabel {{ background:transparent; border:none; }}"
        )
        for button in list(self.buttons.values()) + [self.theme_button]:
            button.apply_palette(palette)


class ToolRail(QWidget):
    """The narrow strip of primary tools down the left edge."""

    tool_selected = Signal(str)

    def __init__(self, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ToolRail")
        self.setFixedWidth(76)
        self._tiles: dict[str, ToolTile] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(METRICS.space(1.5), METRICS.space(2), METRICS.space(1.5), METRICS.space(2))
        layout.setSpacing(METRICS.space(1))
        for icon_name, label, key in TOOL_RAIL:
            tile = ToolTile(icon_name, label, palette)
            tile.clicked.connect(lambda _=False, k=key: self._pick(k))
            layout.addWidget(tile)
            self._tiles[key] = tile
        layout.addStretch(1)
        self.apply_palette(palette)

    def _pick(self, key: str) -> None:
        for name, tile in self._tiles.items():
            tile.setChecked(name == key)
        self.tool_selected.emit(key)

    def clear_selection(self) -> None:
        for tile in self._tiles.values():
            tile.setChecked(False)

    def apply_palette(self, palette: Palette) -> None:
        self.setStyleSheet(
            f"QWidget#ToolRail {{ background:{palette.surface}; "
            f"border-right:1px solid {palette.border}; }}"
        )
        for tile in self._tiles.values():
            tile.apply_palette(palette)


class ViewportStage(QWidget):
    """The viewport plus everything that floats over it.

    Overlays are ordinary child widgets positioned in :meth:`resizeEvent` --
    possible only because OCCT renders into Qt's framebuffer rather than a
    native child window (see docs/architecture.md).
    """

    def __init__(self, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        self._palette = palette
        self.viewport = OcctViewport(palette, self)
        self.overlays: list[tuple[QWidget, str]] = []

        self.hint = Hint(palette, self)
        self.hint.setAttribute(Qt.WA_TransparentForMouseEvents)

        from .panels.drag_readout import DragReadout
        from .panels.measure_overlay import MeasureOverlay

        self.drag_readout = DragReadout(palette, parent=self)
        self.measure_overlay = MeasureOverlay(palette, self)
        self.measure_overlay.hide()

        self.view_controls = FloatingCard(palette, self, shadow=False)
        row = QHBoxLayout(self.view_controls)
        row.setContentsMargins(METRICS.space(1), METRICS.space(1), METRICS.space(1), METRICS.space(1))
        row.setSpacing(2)
        self._view_buttons: dict[str, IconButton] = {}
        for name, tooltip, key in (
            ("fit", "Zoom to fit  (F)", "fit"),
            ("ortho", "Orthographic / perspective", "projection"),
            ("grid", "Grid", "grid"),
        ):
            button = IconButton(name, palette, tooltip, size=32, checkable=key == "grid")
            row.addWidget(button)
            self._view_buttons[key] = button
        self.view_controls.adjustSize()

    def add_overlay(self, widget: QWidget, anchor: str) -> None:
        """Place *widget* over the viewport. Anchor is e.g. ``"top-right"``."""
        widget.setParent(self)
        self.overlays.append((widget, anchor))
        widget.raise_()
        self._layout_overlays()

    #: Called after a resize so the window can re-flow its overlays.
    resized = None

    def resizeEvent(self, event) -> None:  # noqa: N802
        self.viewport.setGeometry(self.rect())
        if self.resized is not None:
            self.resized()
        if self.measure_overlay.isVisible():
            self.measure_overlay.setGeometry(self.rect())
        self._layout_overlays()
        super().resizeEvent(event)

    def _layout_overlays(self) -> None:
        margin = METRICS.space(4)
        width, height = self.width(), self.height()

        controls = self.view_controls
        controls.adjustSize()
        controls.move(width - controls.width() - margin, height - controls.height() - margin)
        controls.raise_()

        if self.measure_overlay.isVisible():
            self.measure_overlay.setGeometry(self.rect())

        for widget, anchor in self.overlays:
            widget.adjustSize() if widget.sizeHint().isValid() else None
            size = widget.size()
            if anchor == "top-right":
                # Sit below the ViewCube so the two never fight for the corner.
                widget.move(width - size.width() - margin, margin + 128)
            elif anchor == "top-left":
                widget.move(margin, margin)
            elif anchor == "top-center":
                widget.move((width - size.width()) // 2, margin + 24)
            elif anchor == "bottom-center":
                widget.move((width - size.width()) // 2, height - size.height() - margin)
            widget.raise_()

        # The hint goes last, once the bottom-centre bar has a width, so it can
        # be trimmed to whatever room is actually left beside it.
        room = width - margin * 2
        for widget, anchor in self.overlays:
            if anchor == "bottom-center" and widget.isVisible():
                room = min(room, (width - widget.width()) // 2 - margin * 2)
        self.hint.elide_to(room)
        self.hint.adjustSize()
        self.hint.move(margin, height - self.hint.height() - margin)

        # Last, so the measurement drawn on the model is never hidden behind a
        # panel. It takes no clicks, so being on top costs nothing.
        if self.measure_overlay.isVisible():
            self.measure_overlay.raise_()

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.viewport.apply_palette(palette)
        self.drag_readout.apply_palette(palette)
        self.measure_overlay.apply_palette(palette)
        self.hint.apply_palette(palette)
        self.view_controls.apply_palette(palette)
        for button in self._view_buttons.values():
            button.apply_palette(palette)


class MainWindow(QMainWindow):
    """SimpleCAD's window."""

    def __init__(self, mode: Mode = Mode.SYSTEM) -> None:
        super().__init__()
        self.setWindowTitle("SimpleCAD")
        self.resize(1440, 900)

        self.mode = mode
        self.palette_ = resolve(mode)
        self.document = Document("Untitled")
        self.rebuilder = Rebuilder(self.document)
        #: Where this document was last saved, so Ctrl+S does not re-ask.
        self._project_path: str | None = None
        self.history = History(self.document)
        #: The live drawing session, while a sketch is open.
        self.canvas = None
        self.sketch_bar = None
        self.dimensions = None
        #: Feature ids the geometry process must not reuse cached results for.
        self._stale: set[str] = set()
        self.geometry = GeometryClient(self)
        self.geometry.started.connect(self._on_geometry_started)
        self.geometry.finished.connect(self._on_geometry_finished)
        self.geometry.failed.connect(self._on_geometry_failed)
        self.geometry.start()
        self._dirty = False
        self._session_id = f"{os.getpid()}"
        self._presentations: dict[str, object] = {}
        #: Guards the re-entrancy of widening a selection to its whole group.
        self._expanding = False
        #: body name -> the shape currently on screen, so unchanged bodies are
        #: not needlessly re-tessellated on every rebuild.
        self._shown: dict[str, object] = {}

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.top_bar = TopBar(self.palette_)
        outer.addWidget(self.top_bar)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        self.rail = ToolRail(self.palette_)
        self.stage = ViewportStage(self.palette_)
        body_layout.addWidget(self.rail)
        body_layout.addWidget(self.stage, 1)
        outer.addWidget(body, 1)

        self.browser = ModelBrowser(self.palette_, self.document)
        self.browser.setFixedWidth(268)
        self.stage.add_overlay(self.browser, "top-right")

        self.selection = SelectionModel(
            self.document, self.stage.viewport, self._presentations
        )
        self.context_bar = ContextBar(self.palette_)
        self.stage.add_overlay(self.context_bar, "bottom-center")
        self.context_bar.hide()
        self.context_bar.action_triggered.connect(self.run_action)

        self.top_bar.theme_toggled.connect(self.toggle_theme)
        self.top_bar.action_triggered.connect(self.run_command)
        self.rail.tool_selected.connect(self.activate_tool)
        self.stage.viewport.ready.connect(self._on_viewport_ready)
        self.stage.viewport.hover_changed.connect(self._on_hover)
        self.stage.viewport.selection_changed.connect(self._on_selection)
        self.stage.viewport.face_dragged.connect(self._on_face_dragged)
        self.stage.viewport.sketch_clicked.connect(self._on_sketch_click)
        self.stage.viewport.sketch_moved.connect(self._on_sketch_move)
        self.stage.viewport.drag_candidate_requested = self._maybe_start_face_drag
        self.stage.resized = self._on_stage_resized
        self.stage._view_buttons["fit"].clicked.connect(self.zoom_to_fit)
        self.stage._view_buttons["projection"].clicked.connect(self._toggle_projection)
        self.stage._view_buttons["grid"].clicked.connect(self._toggle_grid)
        self.browser.visibility_toggled.connect(self._set_body_visible)
        self.browser.isolate_requested.connect(self.isolate_body)
        self.browser.show_all_requested.connect(self.show_all_bodies)
        self.browser.rename_requested.connect(self.rename_body)
        self.browser.duplicate_requested.connect(self.duplicate_body)
        self.browser.delete_requested.connect(self.delete_body)
        self.browser.feature_action.connect(self._feature_action)
        self.browser.body_selected.connect(self.select_item)
        self.browser.group_requested.connect(self.group_names)
        self.browser.ungroup_requested.connect(self.ungroup)
        self.browser.group_visibility_toggled.connect(self.set_group_visible)
        self.browser.group_rename_requested.connect(self.rename_group)
        self.browser.group_delete_requested.connect(self.delete_group)
        self.browser.group_duplicate_requested.connect(self._duplicate_group)


        self._install_shortcuts()
        self._start_autosave()
        self.apply_theme()
        if not os.environ.get("SIMPLECAD_NO_RECOVERY"):
            # Deferred so the window is up before any dialog appears.
            from PySide6.QtCore import QTimer

            QTimer.singleShot(400, self._offer_recovery)
        self.set_hint("Pick a shape from the left to begin.")

    # -- theme ----------------------------------------------------------
    def apply_theme(self) -> None:
        palette = self.palette_
        QApplication.instance().setStyleSheet(stylesheet(palette))
        self.top_bar.apply_palette(palette)
        self.rail.apply_palette(palette)
        self.stage.apply_palette(palette)
        self.browser.apply_palette(palette)
        # Floating surfaces are children of the stage, not of the chrome, so
        # they have to be re-palettes explicitly or they keep the old theme.
        if hasattr(self, "context_bar"):
            self.context_bar.apply_palette(palette)
        if getattr(self, "canvas", None) is not None:
            self.canvas.palette = palette
            self.canvas.refresh()
        if getattr(self, "dimensions", None) is not None:
            self.dimensions.apply_palette(palette)
        for widget, _anchor in self.stage.overlays:
            if widget is not self.browser and hasattr(widget, "apply_palette"):
                widget.apply_palette(palette)
        self.top_bar.theme_button.setIcon(
            icon("sun" if palette.bg == "#14161B" else "moon", palette.text_muted, 20)
        )
        # The viewport deliberately no longer recolours bodies itself, so that a
        # per-body tint survives. Re-display them here instead -- and clear the
        # cache first, because refresh_view skips any body whose shape object is
        # unchanged, which after a mere theme switch is every one of them.
        self._shown.clear()
        self.refresh_view()

    def toggle_theme(self) -> None:
        from .theme import DARK, LIGHT

        self.mode = Mode.LIGHT if self.palette_ is DARK else Mode.DARK
        self.palette_ = LIGHT if self.mode is Mode.LIGHT else DARK
        self.apply_theme()

    # -- shortcuts ------------------------------------------------------
    @staticmethod
    def _is_typing() -> bool:
        """Is the user in a text field right now?

        Qt's shortcut map consumes matching keystrokes before they reach the
        focused widget, so a bare "S" or "5" accelerator swallows those
        characters mid-expression: typing 50 into a Width field would jump the
        camera to Top view, and any expression containing an s would open
        command search instead of being typed.
        """
        from PySide6.QtWidgets import (
            QAbstractSpinBox, QComboBox, QLineEdit, QPlainTextEdit, QTextEdit,
        )

        widget = QApplication.focusWidget()
        if isinstance(widget, QComboBox):
            return widget.isEditable()
        return isinstance(
            widget, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)
        )

    def _install_shortcuts(self) -> None:
        from ..core.settings import (
            VIEWPORT_ONLY, YIELDS_TO_TYPING, shortcuts,
        )

        def unless_typing(handler):
            """Single-character shortcuts yield to whatever is being typed."""

            def guarded() -> None:
                if not self._is_typing():
                    handler()

            return guarded

        bindings = shortcuts()
        actions = {
            "search": self.open_search,
            "dimension": self._dimension_shortcut,
            "extrude": lambda: self.activate_tool("pushpull"),
            "move": lambda: self.activate_tool("move"),
            "hole": lambda: self.activate_tool("hole"),
            "fit_view": self.zoom_to_fit,
            "zoom_selection": self.zoom_to_selection,
            "undo": self.undo,
            "redo": self.redo,
            "redo_alt": self.redo,
            "save": self.save_document,
            "save_as": lambda: self.save_document(ask=True),
            "open": self.show_open_menu,
            "export": self.export_model,
            "import": self.import_model,
            "group": self.group_selection,
            "ungroup": self.ungroup_selection,
            "cancel": self.cancel_tool,
            "confirm": self._confirm_shortcut,
            "confirm_alt": self._confirm_shortcut,
            "delete": self.delete_selection,
            "view_front": lambda: self._standard_view(StandardView.FRONT),
            "view_back": lambda: self._standard_view(StandardView.BACK),
            "view_left": lambda: self._standard_view(StandardView.LEFT),
            "view_right": lambda: self._standard_view(StandardView.RIGHT),
            "view_top": lambda: self._standard_view(StandardView.TOP),
            "view_bottom": lambda: self._standard_view(StandardView.BOTTOM),
            "view_iso": lambda: self._standard_view(StandardView.ISO),
        }

        self._shortcuts = []
        for action, handler in actions.items():
            binding = bindings.get(action)
            if not binding:
                continue
            # Anything that could be typed must not be eaten by a shortcut.
            if action in YIELDS_TO_TYPING or len(binding) == 1:
                handler = unless_typing(handler)

            if action in VIEWPORT_ONLY:
                # Scoped to the 3D view, so an inline editor elsewhere keeps
                # its Return and Delete. A window-wide guard cannot achieve
                # this: Qt consumes the key whether or not the handler acts.
                shortcut = QShortcut(
                    QKeySequence(binding), self.stage.viewport, activated=handler
                )
                shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            else:
                shortcut = QShortcut(QKeySequence(binding), self, activated=handler)
            self._shortcuts.append(shortcut)

    def reload_shortcuts(self) -> None:
        """Re-read the bindings after they have been changed."""
        for shortcut in getattr(self, "_shortcuts", []):
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        self._install_shortcuts()

    def _confirm_shortcut(self) -> None:
        """Enter: finish a spline or polyline that has no fixed click count."""
        if self.sketching and self.canvas is not None:
            if self.canvas.finish_open_shape():
                self._refresh_dimensions()
                self.sketch_bar.show_state(self.canvas.last_result, "shape closed")

    def _dimension_shortcut(self) -> None:
        """D: dimension mode while sketching, otherwise a hint."""
        if self.sketching and self.sketch_bar is not None:
            self.sketch_bar.choose("dimension")
            self.set_hint("Dimension: click a line or circle, type a value, Enter.")
        else:
            self.set_hint("Open a sketch first — D dimensions sketch geometry.")

    def _standard_view(self, which: StandardView) -> None:
        self.stage.viewport.set_standard_view(which)

    def zoom_to_fit(self) -> None:
        """Frame the whole model, sweeping there. The user-facing Fit."""
        self.stage.viewport.fit_all(animate=True)

    def zoom_to_selection(self) -> None:
        self.stage.viewport.zoom_to_selection(animate=True)

    def _toggle_grid(self) -> None:
        """Show or hide the ground plane, and remember the choice."""
        from ..core import settings

        visible = not self.stage.viewport.grid_visible
        self.stage.viewport.show_grid(visible)
        self.stage._view_buttons["grid"].setChecked(visible)
        settings.set_view_preference("grid", visible)
        self.set_hint("Ground grid on." if visible else "Ground grid off.")

    def _toggle_projection(self) -> None:
        viewport = self.stage.viewport
        perspective = not viewport.is_perspective()
        viewport.set_perspective(perspective)
        self.stage._view_buttons["projection"]._name = (
            "perspective" if perspective else "ortho"
        )
        self.stage._view_buttons["projection"].apply_palette(self.palette_)

    # -- document -------------------------------------------------------
    def _on_viewport_ready(self) -> None:
        if self.stage.viewport.failure:
            self.set_hint(f"3D view unavailable: {self.stage.viewport.failure}")
            return
        self.stage.viewport.set_selection_modes(
            [
                SelectionMode.BODY, SelectionMode.FACE,
                SelectionMode.EDGE, SelectionMode.VERTEX,
            ]
        )
        from ..core import settings

        # Scripted checks count the objects in the AIS context, so they need a
        # scene with nothing in it but the model. Same escape hatch as
        # SIMPLECAD_NO_RECOVERY, and for the same reason.
        grid = (
            False if os.environ.get("SIMPLECAD_NO_GRID")
            else settings.view_preference("grid", True)
        )
        self.stage.viewport.show_grid(grid)
        self.stage._view_buttons["grid"].setChecked(grid)
        self.refresh_view()

    def add_feature(self, feature) -> None:
        """Add a feature, rebuild, and show the result."""
        self.history.record(feature.label)
        self.document.add_feature(feature)
        self.mark_dirty()
        self.rebuild()

    def rebuild(self, force: bool = False) -> None:
        """Rebuild the model and bring the view up to date.

        Runs in the geometry process when one is available, so the window stays
        live while OCCT works. A worker *thread* cannot achieve this -- the OCP
        bindings hold the GIL for the whole of every kernel call -- which is why
        this is a process. See docs/architecture.md.

        If the child is unavailable the rebuild happens here instead: slower and
        blocking, but the application keeps working.
        """
        if self.geometry.available and self.geometry.request(
            self.document, self._stale, force
        ):
            self._stale.clear()
            return
        self._rebuild_in_process(force)

    def _rebuild_in_process(self, force: bool = False) -> None:
        """The fallback path, used when the geometry process is unavailable."""
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QApplication

        QApplication.setOverrideCursor(QCursor(Qt.BusyCursor))
        self.set_hint("Working…")
        QApplication.processEvents()

        def progress(feature, index: int, total: int) -> None:
            if total > 1:
                self.set_hint(f"Rebuilding {feature.name} ({index + 1} of {total})…")
                QApplication.processEvents()

        try:
            self.rebuilder.invalidate(self._stale)
            self._stale.clear()
            report = self.rebuilder.rebuild(force=force, on_feature=progress)
            self.refresh_view()
            self.browser.refresh()
        finally:
            QApplication.restoreOverrideCursor()

        if report.warnings:
            self.set_hint(report.warnings[-1])
        else:
            self.set_hint(report.summary())

    def invalidate(self, feature_ids) -> None:
        """Mark features as needing a rebuild on the next pass."""
        self._stale |= set(feature_ids or ())
        self.rebuilder.invalidate(feature_ids)

    def _on_geometry_started(self) -> None:
        self.set_hint("Working…")

    def _on_geometry_finished(self, message) -> None:
        apply_result(self.document, message)
        self.refresh_view()
        self.browser.refresh()
        report = message.get("report", {})
        warnings = report.get("warnings") or []
        self.set_hint(warnings[-1] if warnings else report.get("summary", ""))

    def _on_geometry_failed(self, error: str) -> None:
        self.set_hint(error)
        if not self.geometry.available:
            # The child died; carry on in-process so the session is not lost.
            self._rebuild_in_process()

    def wait_for_rebuild(self, timeout_ms: int = 300_000) -> bool:
        """Block until the model is up to date. For scripts and tests only."""
        if not self.geometry.available:
            return True
        return self.geometry.wait(timeout_ms)

    @property
    def is_rebuilding(self) -> bool:
        return self.geometry.busy

    # -- transform gizmo ---------------------------------------------------
    def attach_gizmo(self, presentation):
        """Put the transform gizmo on a presentation, replacing any previous."""
        from .viewport.gizmo import TransformGizmo

        self.detach_gizmo()
        gizmo = TransformGizmo(self.stage.viewport, self)
        if not gizmo.attach(presentation):
            return None
        self.stage.viewport.gizmo = gizmo
        return gizmo

    def detach_gizmo(self) -> None:
        gizmo = self.stage.viewport.gizmo
        if gizmo is not None:
            gizmo.detach()
        self.stage.viewport.gizmo = None

    # -- direct manipulation ---------------------------------------------
    def _maybe_start_face_drag(self) -> None:
        """Pressing on an already-selected flat face starts a Pull.

        This is the interaction the whole design is built around: select, drag,
        watch it happen, type an exact number if you want one. It only triggers
        on a face that is *already* selected, so the first click still selects
        and orbiting is unaffected.
        """
        faces = self.selection.planar_faces()
        if len(faces) != 1 or self.selection.count != 1:
            return
        pick = faces[0]
        # Only drag if the press actually landed on the selected face. Without
        # this check, every click after a selection starts a drag and nothing
        # else can ever be selected again.
        under_cursor = self.stage.viewport.detected_shape(
            self.stage.viewport._press_pos
        )
        if under_cursor is None or not under_cursor.IsSame(pick.shape):
            return
        if self.stage.viewport.begin_face_drag(pick.info.center, pick.info.normal):
            self._drag_pick = pick
            self._drag_distance = 0.0

    def _on_face_dragged(self, distance: float, finished: bool) -> None:
        pick = getattr(self, "_drag_pick", None)
        if pick is None:
            return
        self._drag_distance = distance

        if not finished:
            cutting = distance < 0
            self.stage.viewport.show_ghost(
                self._pull_preview(pick, distance),
                self.palette_.danger if cutting else self.palette_.accent,
            )
            # The slab a cut removes lies inside the solid, so the body has to
            # get out of its own way for the preview to be visible at all.
            self._set_drag_transparency(pick.body, 0.6 if cutting else 0.0)
            self._show_drag_readout(pick, distance)
            verb = "Adding" if distance >= 0 else "Cutting"
            self.set_hint(f"{verb} {abs(distance):.2f} mm — release to apply")
            return

        self._end_face_drag()
        if abs(distance) < 0.05:
            self.set_hint(self.selection.summary())
            return
        self.add_feature(
            PushPullFeature(
                inputs={
                    "body": BodyRef(pick.body),
                    "face": pick.reference(self.document),
                    "distance": round(distance, 3),
                },
                outputs=[pick.body],
            )
        )

    # -- drag feedback ---------------------------------------------------
    def _set_drag_transparency(self, body: str, value: float) -> None:
        presentation = self._presentations.get(body)
        if presentation is None:
            return
        self.stage.viewport.set_transparency(presentation, value)
        self._drag_transparent = body if value else None

    def _show_drag_readout(self, pick, distance: float) -> None:
        """Put the resulting size at the cursor, not the delta in the corner."""
        from PySide6.QtGui import QCursor

        readout = self.stage.drag_readout
        body = self.document.body(pick.body)
        resulting, label = None, ""
        if body is not None and body.shape is not None:
            from ..kernel.occ import bounding_box

            low, high = bounding_box(body.shape)
            normal = pick.info.normal
            # How thick the part is along the direction being dragged. Adding
            # the delta gives the number the user is steering toward.
            extent = sum(abs((high[i] - low[i]) * normal[i]) for i in range(3))
            if extent > 0:
                resulting = extent + distance
                label = _extent_label(normal)
        readout.show_drag(
            self.stage.mapFromGlobal(QCursor.pos()), distance, resulting, label
        )

    def _end_face_drag(self) -> None:
        """Undo every temporary thing a drag turned on.

        Called from the release, from Esc and from cancel_tool -- a body left
        half-transparent because the drag ended down a path nobody thought about
        is a bug the user cannot undo.
        """
        self.stage.viewport.clear_ghost()
        self.stage.drag_readout.finish()
        stale = getattr(self, "_drag_transparent", None)
        if stale is not None:
            self._set_drag_transparency(stale, 0.0)
        self._drag_transparent = None
        self._drag_pick = None

    def _pull_preview(self, pick, distance: float):
        """The slab of material a pull would add or remove, for the ghost."""
        if abs(distance) < 1e-6:
            return None
        from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
        from OCP.gp import gp_Vec
        from OCP.TopoDS import TopoDS

        try:
            direction = gp_Vec(*pick.info.normal) * distance
            return BRepPrimAPI_MakePrism(TopoDS.Face_s(pick.shape), direction).Shape()
        except Exception:  # noqa: BLE001 - a preview is never worth failing over
            return None

    # -- sketching -------------------------------------------------------
    def begin_sketch(self, plane, sketch=None) -> None:
        """Open a drawing session on *plane*."""
        from ..sketch.sketch import Sketch
        from .viewport.sketch_canvas import SketchCanvas

        self.close_tool_panels()
        self.end_sketch(commit=False, quiet=True)

        sketch = sketch or Sketch(self.document.unique_name("Sketch"), plane)
        self.canvas = SketchCanvas(sketch, self.stage.viewport, self.palette_)
        # Remember where the camera was, so leaving the sketch does not strand
        # the user staring straight down at their model.
        self._camera_before_sketch = self.stage.viewport.camera_state()
        self.stage.viewport.begin_sketch(plane)
        self.stage.viewport.clear_selection()
        self.stage.viewport.look_at_plane(plane)

        self.sketch_bar = SketchBar(self.palette_, self.stage)
        self.sketch_bar.tool_chosen.connect(self.canvas.set_tool)
        self.sketch_bar.finished.connect(lambda: self.end_sketch(commit=True))
        self.sketch_bar.cancelled.connect(lambda: self.end_sketch(commit=False))
        self.sketch_bar.undo_requested.connect(self._sketch_undo)
        self.stage.add_overlay(self.sketch_bar, "bottom-center")
        self.sketch_bar.show()
        self.dimensions = DimensionOverlay(self.stage, self.palette_)
        self.dimensions.value_changed.connect(self._on_dimension_changed)
        self.stage.viewport.view_changed.connect(self._reposition_dimensions)

        self.canvas.refresh()
        self._refresh_dimensions()
        self.sketch_bar.show_state(None, "Click to start drawing, or D to dimension.")
        self.set_hint(f"Sketching on the {self._plane_name(plane)} plane — Esc to leave.")

    @staticmethod
    def _plane_name(plane) -> str:
        normal = tuple(round(v, 6) for v in plane.normal)
        return {
            (0.0, 0.0, 1.0): "XY", (0.0, -1.0, 0.0): "XZ", (1.0, 0.0, 0.0): "YZ",
        }.get(normal, "sketch")

    @property
    def sketching(self) -> bool:
        return self.canvas is not None

    def _on_sketch_move(self, u: float, v: float) -> None:
        if self.canvas is None:
            return
        self.canvas.move(u, v)
        cursor = self.canvas.state.cursor
        snap = " · snapped" if self.canvas.state.snapped_to else ""
        self.set_hint(f"{cursor[0]:.2f}, {cursor[1]:.2f} mm{snap}")

    def _refresh_dimensions(self) -> None:
        if self.canvas is None or self.dimensions is None:
            return
        self.dimensions.rebuild(self.canvas.dimension_anchors())

    def _reposition_dimensions(self) -> None:
        if self.dimensions is not None:
            self.dimensions.reposition()

    def _on_dimension_changed(self, entity, value: float) -> None:
        """A dimension label was edited: apply it, or say why it cannot be."""
        if self.canvas is None:
            return
        if self.canvas.add_dimension(entity, value) is None:
            self.sketch_bar.show_state(
                self.canvas.last_result,
                f"{value:g} mm would over-constrain the sketch — not applied",
            )
        else:
            self.sketch_bar.show_state(self.canvas.last_result, "dimension applied")
        self._refresh_dimensions()

    def _on_sketch_click(self, u: float, v: float) -> None:
        if self.canvas is None:
            return
        if self.canvas.state.tool == "dimension":
            entity = self.canvas.pick_entity(u, v)
            if entity is None:
                self.set_hint("Click a line or a circle to dimension it.")
                return
            kind, current = self.canvas.measurement(entity)
            anchor = next(
                (a for a in self.canvas.dimension_anchors() if a["entity"] is entity),
                None,
            )
            if anchor is None:
                # Not dimensioned yet: constrain it at its current size first,
                # so there is a label to type into.
                if self.canvas.add_dimension(entity, current) is None:
                    self.set_hint("That would over-constrain the sketch.")
                    return
                self._refresh_dimensions()
                anchor = next(
                    (a for a in self.canvas.dimension_anchors()
                     if a["entity"] is entity),
                    None,
                )
            if anchor is not None:
                self.dimensions._begin_edit(anchor)
                self.set_hint("Type a value and press Enter.")
            return

        completed = self.canvas.click(u, v)
        if completed and self.sketch_bar is not None:
            inferred = self.canvas.state.inferred
            hint = f"{inferred} inferred" if inferred else ""
            self.sketch_bar.show_state(self.canvas.last_result, hint)
            self._refresh_dimensions()

    def _sketch_undo(self) -> None:
        if self.canvas is None:
            return
        self.canvas.undo_last()
        self._refresh_dimensions()
        self.sketch_bar.show_state(self.canvas.last_result)

    def end_sketch(self, commit: bool = True, quiet: bool = False) -> None:
        """Leave the drawing session, optionally keeping what was drawn."""
        if self.canvas is None:
            return
        canvas, self.canvas = self.canvas, None
        canvas.clear_display()
        if self.dimensions is not None:
            try:
                self.stage.viewport.view_changed.disconnect(
                    self._reposition_dimensions
                )
            except (RuntimeError, TypeError):
                pass
            self.dimensions.clear()
            self.dimensions.deleteLater()
            self.dimensions = None
        self.stage.viewport.end_sketch()
        self.stage.viewport.restore_camera(getattr(self, "_camera_before_sketch", None))
        self._camera_before_sketch = None

        if self.sketch_bar is not None:
            self.stage.overlays = [
                (w, a) for w, a in self.stage.overlays if w is not self.sketch_bar
            ]
            self.sketch_bar.hide()
            self.sketch_bar.deleteLater()
            self.sketch_bar = None

        if not commit:
            if not quiet:
                self.set_hint("Sketch discarded.")
            return
        if not canvas.sketch.entities:
            self.set_hint("Nothing was drawn, so no sketch was created.")
            return

        from ..kernel.sketch_features import SketchFeature

        feature = SketchFeature(inputs={"sketch": canvas.sketch})
        feature.name = canvas.sketch.name
        self.add_feature(feature)
        self.wait_for_rebuild()
        self.set_hint(
            f"{feature.name} created — use Extrude to turn it into a solid."
        )

    # -- undo / redo -----------------------------------------------------
    def undo(self) -> None:
        label = self.history.undo_label
        stale = self.history.undo()
        if stale is None:
            self.set_hint("Nothing to undo.")
            return
        self.mark_dirty()
        self.invalidate(stale)
        self.rebuild()
        self.set_hint(f"Undid {label}." if label else "Undone.")

    def redo(self) -> None:
        label = self.history.redo_label
        stale = self.history.redo()
        if stale is None:
            self.set_hint("Nothing to redo.")
            return
        self.mark_dirty()
        self.invalidate(stale)
        self.rebuild()
        self.set_hint(f"Redid {label}." if label else "Redone.")

    # -- autosave --------------------------------------------------------
    def mark_dirty(self) -> None:
        self._dirty = True
        self.top_bar.title.setText(f"{self.document.title} \u2022")

    def mark_clean(self) -> None:
        self._dirty = False
        self.top_bar.title.setText(self.document.title)

    def _start_autosave(self) -> None:
        from PySide6.QtCore import QTimer

        from ..core.autosave import INTERVAL

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(INTERVAL * 1000)
        self._autosave_timer.timeout.connect(self._autosave)
        self._autosave_timer.start()

    def _autosave(self) -> None:
        from ..core import autosave

        if not self._dirty or not self.document.features:
            return
        if autosave.write(self.document, self._session_id):
            self.set_hint("Autosaved.")

    def _offer_recovery(self) -> None:
        """If a previous session did not exit cleanly, offer its work back."""
        from PySide6.QtWidgets import QMessageBox

        from ..core import autosave

        leftovers = [
            (path, when) for path, when in autosave.pending()
            if f"session-{self._session_id}.scad3" not in path
        ]
        if not leftovers:
            return
        path, when = leftovers[0]
        answer = QMessageBox.question(
            self,
            "Recover unsaved work",
            f"SimpleCAD closed unexpectedly. There is work from "
            f"{autosave.describe_age(when)} that was not saved.\n\n"
            "Would you like to recover it?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self._open_path(path)
            self._project_path = None      # recovered, but not yet saved anywhere
            self.mark_dirty()
            self.set_hint("Recovered. Save it somewhere to keep it.")
        else:
            for leftover, _when in leftovers:
                try:
                    os.remove(leftover)
                except OSError:
                    pass

    def closeEvent(self, event) -> None:  # noqa: N802
        """A clean exit clears the recovery file; a crash leaves it behind."""
        from ..core import autosave

        autosave.clear(self._session_id)
        self.geometry.stop()
        super().closeEvent(event)

    def _tint_for(self, name: str) -> str:
        """The default colour for a body that has not been given one.

        Keyed on position in the document's body order rather than on a counter,
        so a body keeps its colour across rebuilds, saves and reopens -- a part
        that changes colour every time you edit it is worse than no colour.
        """
        tints = self.palette_.body_tints
        # Deliberately *not* keyed on the body's group. Giving a group one
        # shared tint sounds tidy and makes two touching members of it read as a
        # single silhouette, which is the exact problem per-body tints exist to
        # solve. The model tree is where group membership is shown.
        order = list(self.document.bodies)
        index = order.index(name) if name in order else 0
        return tints[index % len(tints)]

    def refresh_view(self) -> None:
        """Bring the viewport in line with the document, redrawing once."""
        viewport = self.stage.viewport
        if not viewport.is_ready:
            return
        first_body = not self._presentations

        with viewport.batch():
            for name, presentation in list(self._presentations.items()):
                body = self.document.body(name)
                if body is None or not body.visible or body.shape is None:
                    viewport.erase(presentation)
                    self._presentations.pop(name, None)
                    self._shown.pop(name, None)

            for body in self.document.bodies.values():
                if not body.visible or body.shape is None:
                    continue
                # Cached features hand back the same shape object, so an
                # unchanged body needs no work at all.
                if self._shown.get(body.name) is body.shape:
                    continue
                existing = self._presentations.get(body.name)
                if existing is not None:
                    viewport.erase(existing)
                self._presentations[body.name] = viewport.display(
                    body.shape, body.color or self._tint_for(body.name)
                )
                self._shown[body.name] = body.shape

        if first_body and self._presentations:
            viewport.fit_all()

    def isolate_body(self, name: str) -> None:
        """Show only this body."""
        for body in self.document.bodies.values():
            body.visible = body.name == name
        self.refresh_view()
        self.browser.refresh()
        self.stage.viewport.fit_all()
        self.set_hint(f"Isolated {name}.")

    def show_all_bodies(self) -> None:
        for body in self.document.bodies.values():
            body.visible = True
        self.refresh_view()
        self.browser.refresh()
        self.set_hint("Showing everything.")

    def rename_body(self, old: str, new: str) -> None:
        """Rename a body, and every reference to it.

        A body name is what features refer to each other by, so a rename has to
        rewrite the graph -- outputs, body inputs and sub-shape references --
        or the next rebuild loses track of it.
        """
        from ..core.document import BodyRef

        if new in self.document.bodies:
            self.set_hint(f"There is already a body called '{new}'.")
            return
        self.history.record("Rename")

        for feature in self.document.features:
            feature.outputs = [new if o == old else o for o in feature.outputs]
            for key, value in list(feature.inputs.items()):
                if isinstance(value, BodyRef) and str(value) == old:
                    feature.inputs[key] = BodyRef(new)
                elif isinstance(value, list):
                    feature.inputs[key] = [
                        BodyRef(new) if isinstance(v, BodyRef) and str(v) == old
                        else v for v in value
                    ]
            for ref in feature.shape_refs():
                if ref.body == old:
                    object.__setattr__(ref, "body", new)

        body = self.document.bodies.pop(old, None)
        if body is not None:
            body.name = new
            self.document.bodies[new] = body
        presentation = self._presentations.pop(old, None)
        if presentation is not None:
            self._presentations[new] = presentation
        self._shown[new] = self._shown.pop(old, None)
        for group in self.document.groups.values():
            group.members = [new if m == old else m for m in group.members]

        self.mark_dirty()
        self.browser.refresh()
        self.set_hint(f"Renamed {old} to {new}.")

    def duplicate_body(self, name: str) -> None:
        """Copy a body, offset clear of the original so both are visible."""
        from ..core.document import BodyRef
        from ..kernel.occ import bounding_box
        from ..kernel.operations import MoveFeature

        body = self.document.body(name)
        if body is None or body.shape is None:
            return
        low, high = bounding_box(body.shape)
        offset = (high[0] - low[0]) * 1.2 or 20.0

        producers = [f for f in self.document.features if name in f.outputs]
        if not producers:
            self.set_hint(f"'{name}' has no feature history to copy.")
            return

        self.history.record("Duplicate")
        copy_name = self.document.unique_name(name)
        import copy as copy_module

        for feature in producers:
            clone = copy_module.deepcopy(feature)
            clone.id = f"f{__import__('uuid').uuid4().hex[:10]}"
            clone.name = self.document.unique_name(feature.name)
            clone.outputs = [copy_name if o == name else o for o in clone.outputs]
            for key, value in list(clone.inputs.items()):
                if isinstance(value, BodyRef) and str(value) == name:
                    clone.inputs[key] = BodyRef(copy_name)
            self.document.add_feature(clone)

        self.document.add_feature(
            MoveFeature(
                inputs={"body": BodyRef(copy_name), "dx": round(offset, 3)},
                outputs=[copy_name],
            )
        )
        self.mark_dirty()
        self.rebuild()
        self.set_hint(f"Duplicated {name} as {copy_name}.")

    def _retire_body(self, name: str) -> None:
        """Take *name* out of the feature graph, without collateral damage.

        Deleting a body normally means deleting the features that produced it.
        But Split writes *two* bodies from one node, so removing that node
        because one half was deleted would silently take the other half with
        it. A feature with outputs beyond this one is told to stop emitting this
        name instead, and goes on producing the rest.
        """
        for feature in [f for f in self.document.features if name in f.outputs]:
            self._stale.add(feature.id)
            remaining = [o for o in feature.outputs if o != name]
            if remaining and set(remaining) - set(feature.dropped):
                feature.dropped = sorted(set(feature.dropped) | {name})
                continue
            # Nothing left for this feature to produce, so it goes -- and so
            # does anything it had consumed, or deleting both halves of a split
            # would hand the user back the undivided part they just cut up.
            self.document.remove_feature(feature.id)
            for source in feature.consumed_bodies():
                if source != name:
                    self._retire_body(source)
                    self.document.bodies.pop(source, None)

    def delete_body(self, name: str) -> None:
        self.history.record("Delete")
        self._retire_body(name)
        presentation = self._presentations.pop(name, None)
        if presentation is not None:
            self.stage.viewport.erase(presentation)
        self._shown.pop(name, None)
        self.document.bodies.pop(name, None)
        self.document.forget_member(name)
        self.mark_dirty()
        self.rebuild()
        self.set_hint(f"Deleted {name}.")

    def _feature_action(self, target: str, action: str) -> None:
        if action == "rename":
            feature_id, _, new = target.partition(":")
            try:
                self.document.rename_feature(feature_id, new)
            except Exception as exc:  # noqa: BLE001
                from ..core.errors import translate

                self.set_hint(str(translate(exc, "rename")))
                return
            self.mark_dirty()
            self.browser.refresh()
            self.set_hint(f"Renamed to {new}.")
            return

        feature = self.document.feature(target)
        if feature is None:
            return
        self.history.record(action.title())
        if action == "delete":
            self._stale.add(feature.id)
            self.document.remove_feature(feature.id)
        else:
            feature.suppressed = action == "suppress"
            self._stale.add(feature.id)
        self.mark_dirty()
        self.rebuild()
        self.set_hint(f"{action.title()}d {feature.name}.")

    # -- groups ----------------------------------------------------------
    def group_selection(self) -> None:
        """Put the selected things into one group.

        Organisational only. The bodies keep their own geometry, their own
        history and their own entries in the tree -- this is emphatically not a
        boolean union, and the two must never be confused, because one is
        reversible by selecting Ungroup and the other is not reversible at all.
        """
        from ..core.errors import CadError

        self.selection.refresh()
        items = self.selection.items
        if len(items) < 2:
            self.set_hint("Select two or more objects to group them.")
            return
        self.history.record("Group")
        try:
            group = self.document.add_group(items)
        except CadError as exc:
            self.set_hint(str(exc))
            return
        self.mark_dirty()
        self.browser.refresh()
        self._on_selection()
        self.set_hint(f"Grouped {len(self.document.expand([group.name]))} objects "
                      f"as {group.name}.")

    def group_names(self, names) -> None:
        """Group a list of bodies or groups, named from the model tree."""
        from ..core.errors import CadError

        names = [n for n in names if n]
        if len(names) < 2:
            self.set_hint("Select two or more objects to group them.")
            return
        self.history.record("Group")
        try:
            group = self.document.add_group(names)
        except CadError as exc:
            self.set_hint(str(exc))
            return
        self.mark_dirty()
        self.browser.refresh()
        self.set_hint(f"Grouped {len(names)} objects as {group.name}.")

    def ungroup_selection(self) -> None:
        """Dissolve the selected groups, leaving their contents in place."""
        self.selection.refresh()
        names = self.selection.groups
        if not names:
            self.set_hint("Select a group to ungroup it.")
            return
        self.history.record("Ungroup")
        for name in names:
            self.document.ungroup(name)
        self.mark_dirty()
        self.browser.refresh()
        self._on_selection()
        self.set_hint(f"Ungrouped {', '.join(names)}.")

    def ungroup(self, name: str) -> None:
        """Dissolve one named group, from the tree's context menu."""
        if name not in self.document.groups:
            return
        self.history.record("Ungroup")
        self.document.ungroup(name)
        self.mark_dirty()
        self.browser.refresh()
        self.set_hint(f"Ungrouped {name}.")

    def rename_group(self, old: str, new: str) -> None:
        group = self.document.groups.pop(old, None)
        if group is None:
            return
        if new in self.document.groups or new in self.document.bodies:
            self.document.groups[old] = group
            self.set_hint(f"There is already something called '{new}'.")
            return
        self.history.record("Rename")
        group.name = new
        self.document.groups[new] = group
        for other in self.document.groups.values():
            other.members = [new if m == old else m for m in other.members]
        self.mark_dirty()
        self.browser.refresh()
        self.set_hint(f"Renamed {old} to {new}.")

    def set_group_visible(self, name: str, visible: bool) -> None:
        """Show or hide every body in a group, together."""
        group = self.document.group(name)
        if group is None:
            return
        group.visible = visible
        for body_name in self.document.expand([name]):
            body = self.document.body(body_name)
            if body is not None:
                body.visible = visible
        self.refresh_view()
        self.browser.refresh()
        self.set_hint(f"{'Showing' if visible else 'Hidden'}: {name}.")

    def delete_group(self, name: str) -> None:
        """Delete a group and everything in it."""
        members = self.document.expand([name])
        if not members:
            return
        self.history.record("Delete")
        self.mark_dirty()
        for body_name in members:
            self._retire_body(body_name)
            presentation = self._presentations.pop(body_name, None)
            if presentation is not None:
                self.stage.viewport.erase(presentation)
            self._shown.pop(body_name, None)
            self.document.bodies.pop(body_name, None)
            self.document.forget_member(body_name)
        self.document.groups.pop(name, None)
        self.document.forget_member(name)
        self.rebuild()
        self.browser.refresh()
        self.set_hint(f"Deleted {name} and its {len(members)} objects.")

    def duplicate_selection(self) -> None:
        """Copy what is selected, keeping a group a group."""
        self.selection.refresh()
        groups = self.selection.groups
        if groups:
            for name in groups:
                self._duplicate_group(name)
            return
        for name in self.selection.bodies:
            self.duplicate_body(name)

    def _duplicate_group(self, name: str) -> None:
        """Copy every body in a group, and group the copies together."""
        members = self.document.expand([name])
        before = set(self.document.bodies)
        for body_name in members:
            self.duplicate_body(body_name)
        self.wait_for_rebuild()
        copies = [n for n in self.document.bodies if n not in before]
        if len(copies) >= 2:
            self.document.add_group(copies, self.document.unique_name(name))
        self.browser.refresh()
        self.set_hint(f"Duplicated {name}.")

    def select_item(self, name: str) -> None:
        """Select a body or a group from the model tree."""
        viewport = self.stage.viewport
        names = (
            self.document.expand([name]) if name in self.document.groups
            else [name]
        )
        self._expanding = True
        try:
            viewport.clear_selection()
            for body_name in names:
                presentation = self._presentations.get(body_name)
                if presentation is not None:
                    viewport.select_shape(presentation, replace=False)
        finally:
            self._expanding = False
        self._on_selection()

    def _expand_selection_to_groups(self) -> bool:
        """Widen a click on a grouped body to its whole group.

        The behaviour that makes a group feel like one object. Skipped when the
        user double-clicked -- that is how they say they want the one piece --
        and skipped for faces and edges, where they are plainly working on a
        detail rather than on the assembly.
        """
        if self._expanding or not self.document.groups:
            return False
        if self.stage.viewport.picking_inside_group:
            return False
        if not self.selection.only_bodies:
            return False

        wanted: list[str] = []
        for name in self.selection.bodies:
            group = self.document.top_group_of(name)
            wanted.extend(
                self.document.expand([group.name]) if group is not None else [name]
            )
        wanted = list(dict.fromkeys(wanted))
        if wanted == self.selection.bodies:
            return False

        viewport = self.stage.viewport
        self._expanding = True
        try:
            viewport.clear_selection()
            for name in wanted:
                presentation = self._presentations.get(name)
                if presentation is not None:
                    viewport.select_shape(presentation, replace=False)
        finally:
            self._expanding = False
        self.selection.refresh()
        return True

    def _set_body_visible(self, name: str, visible: bool) -> None:
        body = self.document.body(name)
        if body is None:
            return
        body.visible = visible
        self.refresh_view()

    # -- tools ----------------------------------------------------------
    def sketch_on_selection(self) -> bool:
        """Start a sketch on the selected planar face. False if none is picked."""
        from ..kernel.construction import plane_from_face

        faces = self.selection.planar_faces()
        if not faces:
            return False
        pick = faces[0]
        try:
            plane = plane_from_face(pick.shape, pick.body)
        except Exception as exc:  # noqa: BLE001
            from ..core.errors import translate

            self.set_hint(str(translate(exc, "sketch")))
            return False
        self.begin_sketch(plane.as_sketch_plane())
        self.set_hint(f"Sketching on a face of {pick.body} — Esc to leave.")
        return True

    def run_action(self, key: str) -> None:
        """Run *key* as a tool if there is one, otherwise as a command.

        The contextual bar and command search both offer a mix -- Fillet opens a
        panel, Delete just happens -- so both go through here rather than each
        deciding for itself and disagreeing about, say, Duplicate.
        """
        from .tools.registry import is_tool

        # ``is_tool`` rather than a bare lookup in TOOLS: the registry fills
        # itself in on import, so asking before that has happened answers no
        # for every tool in the application.
        if is_tool(key):
            self.activate_tool(key)
        else:
            self.run_command(key)

    def activate_tool(self, key: str) -> None:
        from .tools.registry import activate

        # A flat face already selected is an unambiguous request to sketch on
        # it, so skip the plane picker.
        if key == "sketch" and self.sketch_on_selection():
            return
        activate(self, key)

    def close_tool_panels(self) -> None:
        """Dismiss any open tool panel without touching the selection."""
        for widget, anchor in list(self.stage.overlays):
            if getattr(widget, "is_tool_panel", False):
                self.stage.overlays.remove((widget, anchor))
                teardown = getattr(widget, "teardown", None)
                if teardown is not None:
                    try:
                        teardown()
                    except Exception:  # noqa: BLE001 - never block the dismissal
                        pass
                widget.hide()
                widget.deleteLater()

    def cancel_tool(self) -> None:
        # Esc during a drag has to put the scene back too, or the body stays
        # translucent with a ghost slab floating in it.
        self._end_face_drag()
        self.stage.viewport.handles.clear(self.stage.viewport)
        self.detach_gizmo()
        if self.sketching:
            self.end_sketch(commit=False)
            return
        self.close_search()
        self.rail.clear_selection()
        self.close_tool_panels()
        self.stage.viewport.clear_selection()
        self.set_hint("Pick a shape from the left, or select geometry to act on it.")

    def delete_selection(self) -> None:
        """Delete the selected bodies, and the features that produced them."""
        self.selection.refresh()
        names = self.selection.bodies
        if not names:
            self.set_hint("Select a body to delete.")
            return
        self.history.record("Delete")
        self.mark_dirty()
        for name in names:
            self._retire_body(name)
            presentation = self._presentations.pop(name, None)
            if presentation is not None:
                self.stage.viewport.erase(presentation)
            self.document.bodies.pop(name, None)
            self.document.forget_member(name)
        self.rebuild()
        self.set_hint(f"Deleted {', '.join(names)}.")

    # -- commands -------------------------------------------------------
    def run_command(self, key: str) -> None:
        handlers = {
            "export": self.export_model,
            "import": self.import_model,
            "save": self.save_document,
            "save_as": lambda: self.save_document(ask=True),
            "open": self.show_open_menu,
            "undo": self.undo,
            "redo": self.redo,
            "search": self.open_search,
            "delete": self.delete_selection,
            "duplicate": self.duplicate_selection,
            "group": self.group_selection,
            "ungroup": self.ungroup_selection,
            "hide": self.hide_selection,
            "theme": self.toggle_theme,
            "projection": self._toggle_projection,
            "view_grid": self._toggle_grid,
            "view_fit": self.zoom_to_fit,
            "view_selection": self.zoom_to_selection,
            "view_iso": lambda: self._standard_view(StandardView.ISO),
            "view_top": lambda: self._standard_view(StandardView.TOP),
            "view_front": lambda: self._standard_view(StandardView.FRONT),
            "view_right": lambda: self._standard_view(StandardView.RIGHT),
        }
        handlers.get(key, lambda: self.set_hint(f"{key} is not available yet."))()

    def hide_selection(self) -> None:
        """Hide whatever is selected -- a group as one thing."""
        self.selection.refresh()
        groups = self.selection.groups
        if groups:
            for name in groups:
                self.set_group_visible(name, False)
            return
        names = self.selection.bodies
        if not names:
            self.set_hint("Select something to hide.")
            return
        for name in names:
            self._set_body_visible(name, False)
        self.browser.refresh()
        self.set_hint(f"Hid {', '.join(names)}.")

    def open_search(self) -> None:
        """Show the command palette, or dismiss it if already up."""
        existing = getattr(self, "_search", None)
        if existing is not None:
            self.close_search()
            return
        panel = CommandSearch(self.palette_, self.stage)
        panel.chosen.connect(self._run_searched)
        panel.dismissed.connect(self.close_search)
        self.stage.add_overlay(panel, "top-center")
        panel.show()
        panel.focus_query()
        self._search = panel

    def close_search(self) -> None:
        panel = getattr(self, "_search", None)
        if panel is None:
            return
        self.stage.overlays = [
            (w, a) for w, a in self.stage.overlays if w is not panel
        ]
        panel.hide()
        panel.deleteLater()
        self._search = None

    def _run_searched(self, key: str) -> None:
        self.close_search()
        self.run_action(key)

    def export_model(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from ..kernel.io_formats import export_shapes

        shapes = [b.shape for b in self.document.visible_bodies()]
        if not shapes:
            self.set_hint("There is nothing to export yet.")
            return
        path, _filter = QFileDialog.getSaveFileName(
            self, "Export", f"{self.document.title}.3mf",
            "3MF for printing (*.3mf);;STEP (*.step);;STL (*.stl);;OBJ (*.obj)",
        )
        if not path:
            return
        try:
            export_shapes(shapes, path)
        except Exception as exc:  # noqa: BLE001 - surfaced, never fatal
            from ..core.errors import translate

            self.set_hint(str(translate(exc, "export")))
            return
        self.set_hint(f"Exported to {path}")

    # -- import ----------------------------------------------------------
    def import_model(self) -> None:
        """Bring a 3D file into the current document.

        One feature per body in the file, all under a single history entry, so
        importing a ten-part assembly is one thing to undo rather than ten. The
        bodies keep their relative positions to one another and the whole import
        is placed clear of whatever is already in the scene.
        """
        from PySide6.QtWidgets import QFileDialog

        from ..core.errors import translate
        from ..kernel.importing import ImportFeature, forget, read_bodies
        from ..kernel.io_formats import import_filter

        path, _filter = QFileDialog.getOpenFileName(
            self, "Import a 3D model", "", import_filter()
        )
        if path:
            self.import_path(path)

    def import_path(self, path: str) -> list[str]:
        """Import *path*, returning the names of the bodies it created.

        Split out from :meth:`import_model` so the import can be driven without
        a file dialog -- by a scripted check, by a drop, or by anything else
        that already knows which file it wants.
        """
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QApplication

        from ..core.errors import translate
        from ..kernel.importing import ImportFeature, forget, read_bodies
        from ..kernel.io_formats import import_filter  # noqa: F401

        # The file may have changed since it was last read in this session.
        forget(path)
        # Read here rather than in the geometry process because the placement
        # below needs the bounds before any feature exists. A large mesh takes
        # seconds, so it says so -- an unannounced pause on a file the user
        # just chose reads as the application having ignored them.
        self.set_hint(f"Reading {os.path.basename(path)}…")
        QApplication.setOverrideCursor(QCursor(Qt.BusyCursor))
        QApplication.processEvents()
        try:
            bodies = read_bodies(path)
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            self._report_import_failure(path, translate(exc, "import"))
            return []
        finally:
            QApplication.restoreOverrideCursor()

        offset = self._placement_for([body.shape for body in bodies])
        self.history.record("Import")
        created = []
        for index, body in enumerate(bodies):
            name = self.document.unique_name(body.name or "Imported")
            feature = ImportFeature(
                inputs={
                    "path": path,
                    "index": index,
                    "dx": round(offset[0], 4),
                    "dy": round(offset[1], 4),
                    "dz": round(offset[2], 4),
                },
                outputs=[name],
            )
            feature.name = name
            self.document.add_feature(feature)
            created.append(name)

        self.mark_dirty()
        self.rebuild()
        self.wait_for_rebuild()
        self.refresh_view()
        self.browser.refresh()
        self.stage.viewport.fit_all(animate=True)
        from ..core.settings import remember_file

        remember_file(path)
        self.set_hint(
            f"Imported {len(created)} bod{'y' if len(created) == 1 else 'ies'} "
            f"from {os.path.basename(path)}: {', '.join(created[:4])}"
            + ("…" if len(created) > 4 else "")
        )
        return created

    def _report_import_failure(self, path: str, error) -> None:
        """Say what went wrong loudly enough to be noticed.

        The hint line is the right place for the outcome of something the user
        watched happen. An import that produced nothing is different: they chose
        a file, waited, and got an unchanged screen -- so this one gets a dialog
        as well, or it reads as the application having ignored them.
        """
        from PySide6.QtWidgets import QMessageBox

        self.set_hint(str(error))
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Could not import")
        box.setText(f"{os.path.basename(path)} could not be imported.")
        box.setInformativeText(str(error))
        box.setAttribute(Qt.WA_DeleteOnClose, True)
        # open(), not exec(): window-modal, so it cannot be missed, but it does
        # not spin its own event loop -- which would freeze autosave, the
        # geometry process and any rebuild still in flight behind it.
        box.open()

    def _placement_for(self, shapes) -> tuple[float, float, float]:
        """Where to drop an import so it lands somewhere sensible and visible.

        Into an empty document, nowhere: the file's own coordinates are usually
        meaningful and moving them would be presumptuous. Into a document that
        already has geometry, beside it -- an imported part landing inside the
        one you are working on looks like a broken import, not a placement.
        """
        from ..kernel.occ import bounding_box

        if not shapes:
            return (0.0, 0.0, 0.0)
        low, high = _combined_bounds(shapes)
        existing = [b.shape for b in self.document.visible_bodies()]
        if not existing:
            return (0.0, 0.0, 0.0)

        here_low, here_high = _combined_bounds(existing)
        gap = max(10.0, (high[0] - low[0]) * 0.15)
        return (
            here_high[0] + gap - low[0],
            here_low[1] - low[1],
            here_low[2] - low[2],
        )

    # -- project file ---------------------------------------------------
    def save_document(self, ask: bool = False) -> None:
        """Save the project, asking for a location the first time."""
        from PySide6.QtWidgets import QFileDialog

        from ..core.project import EXTENSION, save

        path = self._project_path
        if ask or not path:
            path, _filter = QFileDialog.getSaveFileName(
                self, "Save project", f"{self.document.title}{EXTENSION}",
                f"SimpleCAD project (*{EXTENSION})",
            )
            if not path:
                return
        try:
            path = save(self.document, path, thumbnail=self._thumbnail())
        except Exception as exc:  # noqa: BLE001 - surfaced, never fatal
            from ..core.errors import translate

            self.set_hint(str(translate(exc, "save")))
            return
        from ..core.settings import remember_file

        self._project_path = path
        self.document.title = os.path.splitext(os.path.basename(path))[0]
        self.mark_clean()
        remember_file(path)
        self.set_hint(f"Saved to {path}")

    def show_open_menu(self) -> None:
        """Open, with recent projects listed underneath."""
        from PySide6.QtWidgets import QMenu

        from ..core.settings import forget_files, recent_files

        menu = QMenu(self)
        menu.addAction("Open…\tCtrl+O", self.open_document)
        recent = recent_files()
        if recent:
            menu.addSeparator()
            for path in recent:
                label = os.path.basename(path)
                menu.addAction(label, lambda p=path: self._open_path(p))
            menu.addSeparator()
            menu.addAction("Clear recent", forget_files)
        button = self.top_bar.buttons.get("open")
        origin = button.mapToGlobal(button.rect().bottomLeft()) if button else None
        menu.exec(origin)

    def open_document(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from ..core.project import EXTENSION, load

        path, _filter = QFileDialog.getOpenFileName(
            self, "Open project", "", f"SimpleCAD project (*{EXTENSION})"
        )
        if not path:
            return
        self._open_path(path)

    def _open_path(self, path: str) -> None:
        from ..core.project import load

        try:
            document, cached = load(path)
        except Exception as exc:  # noqa: BLE001
            from ..core.errors import translate

            self.set_hint(str(translate(exc, "open")))
            return

        self.set_document(document, path)
        if cached:
            # The saved geometry came from a consistent build, so show it as-is.
            # Rebuilding here would re-execute every feature and throw the cache
            # away -- a fresh Rebuilder starts with an empty cache, so this is
            # the difference between opening instantly and waiting out every
            # thread in the model. The first edit rebuilds what it touches.
            self.refresh_view()
            self.browser.refresh()
        else:
            self.set_hint("Rebuilding from the feature history…")
            self.rebuild()
        from ..core.settings import remember_file

        remember_file(path)
        self.stage.viewport.fit_all()
        self.set_hint(f"Opened {os.path.basename(path)}")

    def set_document(self, document, path: str | None = None) -> None:
        """Replace the open document, clearing anything left on screen."""
        for presentation in self._presentations.values():
            self.stage.viewport.erase(presentation)
        self._presentations.clear()
        self._shown.clear()

        self.document = document
        self.rebuilder = Rebuilder(document)
        self.selection = SelectionModel(
            document, self.stage.viewport, self._presentations
        )
        self.browser.document = document
        self._expanding = False
        self._project_path = path
        self.history.reset(document)
        self.mark_clean()
        self.context_bar.hide()

    def _thumbnail(self) -> bytes | None:
        """A PNG of the model, stored in the project for previews."""
        return self.stage.viewport.capture_png(640, 400)

    # -- feedback -------------------------------------------------------
    def set_hint(self, text: str) -> None:
        self.stage.hint.setText(text)
        self.stage._layout_overlays()

    def _on_hover(self, description) -> None:
        if description and not self.selection.count:
            self.set_hint(f"{description['kind'].title()} — click to select")

    def _on_selection(self) -> None:
        if self._expanding:
            return
        self.selection.refresh()
        self._expand_selection_to_groups()
        self.refresh_context_bar()
        if self.selection.count:
            self.set_hint(self.selection.summary())
        else:
            self.set_hint("Pick a shape from the left, or select geometry to act on it.")

    def refresh_context_bar(self) -> None:
        """Rebuild the contextual bar from the current selection.

        Every route that changes what is selected comes through here -- viewport
        clicks, the model tree, programmatic selection, group expansion -- so
        there is no path that leaves the bar showing the previous selection's
        tools and no reason for anyone to deselect and reselect to fix it.
        """
        actions = available_actions(self.selection)
        self.context_bar.show_actions(
            self.selection.summary(), actions, self._context_bar_width()
        )
        self.stage._layout_overlays()

    def _on_stage_resized(self) -> None:
        """A narrower window means fewer buttons fit, so re-flow the bar."""
        if self.context_bar.isVisible():
            self.refresh_context_bar()

    def _context_bar_width(self) -> int:
        """How wide the bar may be without colliding with its neighbours.

        The hint sits bottom-left and the view controls bottom-right, at the
        same height as the bar. The bar is centred, so what it may occupy is the
        window less twice whichever of those two is wider.
        """
        stage = self.stage
        margin = METRICS.space(4)
        # Measured against the view controls only. The hint shares this edge
        # too, but it elides to whatever is left over -- a status line should
        # give way to a row of controls, not the other way round.
        reserved = stage.view_controls.width() + margin * 2
        return max(420, stage.width() - reserved * 2)
