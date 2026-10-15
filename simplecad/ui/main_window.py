"""The application shell.

Layout follows one rule: the viewport is the app, and everything else floats
over it. There is a slim top bar, a narrow tool rail, and cards that appear
where they are relevant. No dock widgets, no toolbar rows, no workbench picker.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path

from PySide6.QtCore import QEvent, QMimeData, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QMainWindow,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from ..core.document import BodyRef, Document
from ..core.history import History
from ..core.rebuild import Rebuilder
from ..kernel.operations import (
    MoveManyFeature, PushPullFeature, RoundPushPullFeature,
)
from .icons import icon
from .geometry_client import GeometryClient, apply_result
from .panels.command_search import CommandSearch
from .panels.dimension_labels import DimensionOverlay
from .panels.sketch_bar import SketchBar
from .panels.context_bar import ContextBar
from .panels.model_browser import ModelBrowser
from .selection import SelectionModel, available_actions, texture_features
from .theme import DARK, METRICS, Mode, Palette, resolve, stylesheet
from .viewport.occt_view import OcctViewport, SelectionMode, StandardView
from .widgets.controls import FloatingCard, Hint, IconButton, ToolTile


@dataclass(frozen=True)
class ToolSpec:
    """One tool entry shared by the pinned rail and grouped flyouts."""

    icon: str
    label: str
    key: str


@dataclass(frozen=True)
class ToolGroup:
    """A named family of less-frequent tools."""

    icon: str
    label: str
    key: str
    tools: tuple[ToolSpec, ...]


PINNED_TOOLS = (
    ToolSpec("box", "Shape", "shapes"),
    ToolSpec("sketch", "Sketch", "sketch"),
    ToolSpec("extrude", "Pull", "pushpull"),
    ToolSpec("move", "Move", "move"),
    ToolSpec("measure", "Measure", "measure"),
)

TOOL_GROUPS = (
    ToolGroup("create", "Create", "create", (
        ToolSpec("text", "Text", "text"),
        ToolSpec("thread", "Threaded pair", "threaded_connection"),
        ToolSpec("clip", "Clip / Join Parts", "clip_joint"),
        ToolSpec("thread", "Matching part", "matching_part"),
    )),
    ToolGroup("modify", "Modify", "modify", (
        ToolSpec("align", "Align", "align"),
        ToolSpec("pattern", "Arrange", "arrange"),
        ToolSpec("rotate", "Rotate", "rotate"),
        ToolSpec("scale", "Scale", "scale"),
        ToolSpec("hole", "Hole", "hole"),
        ToolSpec("thread", "Thread", "thread"),
        ToolSpec("fillet", "Fillet", "fillet"),
        ToolSpec("chamfer", "Chamfer", "chamfer"),
        ToolSpec("shell", "Hollow", "shell"),
        ToolSpec("vent", "Vent", "vent"),
        ToolSpec("split", "Split", "split"),
        ToolSpec("subtract", "Subtract", "subtract"),
        ToolSpec("union", "Join", "join"),
        ToolSpec("intersect", "Intersect", "intersect"),
        ToolSpec("cut", "Section replace", "section_replace"),
    )),
    ToolGroup("inspect", "Inspect", "inspect", (
        ToolSpec("measure", "Point to point", "measure_points"),
        ToolSpec("settings", "Fit & clearance", "fit"),
        ToolSpec("print", "3D Print", "print"),
    )),
)

# Backwards-compatible flattened form used by the completeness smoke check.
TOOL_RAIL = tuple((tool.icon, tool.label, tool.key) for tool in PINNED_TOOLS)

MODEL_CLIPBOARD_MIME = "application/x-simplecad-model-fragment+json"

EXPORT_FILTERS = (
    ("3MF for printing (*.3mf)", ".3mf"),
    ("STEP (*.step)", ".step"),
    ("STL (*.stl)", ".stl"),
    ("OBJ (*.obj)", ".obj"),
)
EXPORT_FILTER_TEXT = ";;".join(label for label, _suffix in EXPORT_FILTERS)
EXPORT_SUFFIXES = frozenset(suffix for _label, suffix in EXPORT_FILTERS) | {".stp"}

#: Every accepted arrow-key press moves geometry by this exact distance.
NUDGE_STEP_MM = 0.25


def resolved_export_path(path: str, selected_filter: str) -> str:
    """Give an export filename the suffix selected in the save dialog.

    Qt's native dialog does not reliably replace a pre-filled ``.3mf`` when a
    different filter is selected.  Start without a suffix and settle it here:
    an extension the user typed explicitly wins; otherwise the selected filter
    supplies one.
    """
    extension = os.path.splitext(path)[1].lower()
    if extension in EXPORT_SUFFIXES:
        return path
    suffix = dict(EXPORT_FILTERS).get(selected_filter, ".3mf")
    return f"{path}{suffix}"


def _moved_copy(base, shape) -> bool:
    """True when *shape* is *base*'s geometry under another placement."""
    try:
        return (base is not None and shape is not None and base.IsPartner(shape)
                and base.Orientation() == shape.Orientation())
    except Exception:  # noqa: BLE001 - a failed check just redisplays
        return False


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
    """Studio header: project identity, command search and grouped actions."""

    theme_toggled = Signal()
    search_requested = Signal()
    action_triggered = Signal(str)

    def __init__(self, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        self._palette = palette
        self.setObjectName("TopBar")
        self.setFixedHeight(56)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 12, 0)
        layout.setSpacing(8)

        self.brand_icon = QLabel()
        self.brand_icon.setObjectName("BrandIcon")
        self.brand_icon.setFixedSize(28, 28)
        app_icon = QApplication.instance().windowIcon()
        if app_icon.isNull():
            icon_path = Path(__file__).resolve().parents[2] / "assets" / "simplecad.svg"
            if icon_path.exists():
                app_icon = QIcon(str(icon_path))
        if not app_icon.isNull():
            self.brand_icon.setPixmap(app_icon.pixmap(28, 28))
        self.wordmark = QLabel("SimpleCAD")
        self.title = QLabel("Untitled")
        self.title.setMaximumWidth(220)
        layout.addWidget(self.brand_icon)
        layout.addWidget(self.wordmark)
        layout.addSpacing(4)
        layout.addWidget(self.title)
        layout.addStretch(1)

        self.search_control = QPushButton("Search tools and commands", self)
        self.search_control.setObjectName("CommandSearchButton")
        self.search_control.setCursor(Qt.PointingHandCursor)
        self.search_control.setFixedSize(224, 36)
        self.search_control.clicked.connect(
            lambda _=False: self.action_triggered.emit("search")
        )
        layout.addWidget(self.search_control)
        layout.addSpacing(4)

        self.buttons: dict[str, QWidget] = {"search": self.search_control}
        self._dividers: list[QFrame] = []
        for name, tooltip, action in (
            ("folder", "Open  (Ctrl+O)", "open"),
            ("import", "Import a 3D file  (Ctrl+I)", "import"),
            ("undo", "Undo  (Ctrl+Z)", "undo"),
            ("redo", "Redo  (Ctrl+Shift+Z)", "redo"),
            ("save", "Save  (Ctrl+S)", "save"),
            ("export", "Export", "export"),
        ):
            button = IconButton(name, palette, tooltip)
            button.clicked.connect(lambda _=False, a=action: self.action_triggered.emit(a))
            layout.addWidget(button)
            self.buttons[action] = button
            if action in ("import", "redo", "export"):
                divider = QFrame(self)
                divider.setFrameShape(QFrame.VLine)
                divider.setFixedSize(1, 22)
                layout.addWidget(divider)
                self._dividers.append(divider)

        self.theme_button = IconButton("moon", palette, "Light / dark")
        self.theme_button.clicked.connect(self.theme_toggled.emit)
        layout.addWidget(self.theme_button)
        self.apply_palette(palette)
        self._update_responsive()

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.wordmark.setStyleSheet(
            f"color:{palette.text}; font-size:14px; font-weight:700;"
            "letter-spacing:0.35px;"
        )
        self.title.setStyleSheet(f"color:{palette.text_muted}; font-size:12px;")
        self.brand_icon.setStyleSheet("background:transparent; border:none;")
        self.search_control.setIcon(icon("search", palette.text_muted, 18))
        self.setStyleSheet(
            f"QWidget#TopBar {{ background:{palette.surface}; "
            f"border-bottom:1px solid {palette.border}; }}"
            f"QWidget#TopBar QLabel {{ background:transparent; border:none; }}"
        )
        for divider in self._dividers:
            divider.setStyleSheet(f"background:{palette.border}; border:none;")
        for button in [
            value for key, value in self.buttons.items() if key != "search"
        ] + [self.theme_button]:
            button.apply_palette(palette)
        self._update_responsive()

    def _update_responsive(self) -> None:
        compact = self.width() < 980
        self.wordmark.setVisible(not compact)
        self.search_control.setText("" if compact else "Search tools and commands")
        self.search_control.setToolTip(
            "Search tools & commands  (S)" if compact else "Press S to search"
        )
        self.search_control.setFixedWidth(36 if compact else 224)
        palette = self._palette
        alignment = "center" if compact else "left"
        padding = "0" if compact else "0 12px"
        self.search_control.setStyleSheet(
            f"QPushButton#CommandSearchButton {{ background:{palette.surface_sunken};"
            f"border:1px solid {palette.border}; border-radius:{METRICS.radius_sm}px;"
            f"color:{palette.text_muted}; text-align:{alignment}; padding:{padding}; }}"
            f"QPushButton#CommandSearchButton:hover {{ background:{palette.surface_raised};"
            f"border-color:{palette.border_strong}; color:{palette.text}; }}"
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._update_responsive()
        super().resizeEvent(event)


class ToolRail(QWidget):
    """Pinned tools plus three compact, discoverable tool families."""

    tool_selected = Signal(str)
    group_requested = Signal(str)

    def __init__(self, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ToolRail")
        self.setFixedWidth(72)
        self._tiles: dict[str, ToolTile] = {}
        self._group_tiles: dict[str, ToolTile] = {}
        self._active_key: str | None = None
        self._open_group: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, METRICS.space(3), 7, METRICS.space(3))
        layout.setSpacing(METRICS.space(1))
        for spec in PINNED_TOOLS:
            tile = ToolTile(spec.icon, spec.label, palette)
            tile.clicked.connect(lambda _=False, k=spec.key: self._pick(k))
            layout.addWidget(tile, 0, Qt.AlignHCenter)
            self._tiles[spec.key] = tile

        layout.addStretch(1)
        divider = QFrame(self)
        divider.setObjectName("RailDivider")
        divider.setFrameShape(QFrame.HLine)
        divider.setFixedHeight(1)
        layout.addWidget(divider)
        self._divider = divider

        for group in TOOL_GROUPS:
            tile = ToolTile(group.icon, group.label, palette)
            tile.setToolTip(f"Open {group.label} tools")
            tile.clicked.connect(lambda _=False, k=group.key: self._open(k))
            layout.addWidget(tile, 0, Qt.AlignHCenter)
            self._group_tiles[group.key] = tile
        self.apply_palette(palette)

    def _pick(self, key: str) -> None:
        self.set_active(key)
        self.tool_selected.emit(key)

    def _open(self, key: str) -> None:
        self.group_requested.emit(key)

    def group_button(self, key: str) -> ToolTile | None:
        return self._group_tiles.get(key)

    def set_group_open(self, key: str | None) -> None:
        self._open_group = key
        self._refresh_selection()

    def set_active(self, key: str | None) -> None:
        self._active_key = key
        self._refresh_selection()

    def _refresh_selection(self) -> None:
        for name, tile in self._tiles.items():
            tile.setChecked(name == self._active_key)
            tile.apply_palette(self._palette)
        for group in TOOL_GROUPS:
            owns_active = any(tool.key == self._active_key for tool in group.tools)
            tile = self._group_tiles[group.key]
            tile.setChecked(group.key == self._open_group or owns_active)
            tile.apply_palette(self._palette)

    def clear_selection(self) -> None:
        self._active_key = None
        self._open_group = None
        self._refresh_selection()

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.setStyleSheet(
            f"QWidget#ToolRail {{ background:{palette.surface}; "
            f"border-right:1px solid {palette.border}; }}"
        )
        self._divider.setStyleSheet(
            f"background:{palette.border}; border:none; margin:0 8px;"
        )
        for tile in list(self._tiles.values()) + list(self._group_tiles.values()):
            tile.apply_palette(palette)


class ToolFlyout(FloatingCard):
    """A keyboard-friendly child overlay for one rail tool family."""

    tool_selected = Signal(str)
    dismissed = Signal()

    def __init__(self, group: ToolGroup, palette: Palette, parent=None) -> None:
        super().__init__(palette, parent)
        self.group = group
        self._palette = palette
        self._buttons: list[QPushButton] = []
        self.setProperty("role", "tool-flyout")
        self.setFixedWidth(288)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)
        eyebrow = QLabel("TOOL LIBRARY")
        eyebrow.setObjectName("FlyoutEyebrow")
        title = QLabel(group.label)
        title.setObjectName("PanelTitle")
        root.addWidget(eyebrow)
        root.addWidget(title)

        grid = QGridLayout()
        grid.setContentsMargins(0, 2, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        for index, spec in enumerate(group.tools):
            button = QPushButton(spec.label, self)
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(42)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.installEventFilter(self)
            button.clicked.connect(
                lambda _=False, key=spec.key: self.tool_selected.emit(key)
            )
            grid.addWidget(button, index // 2, index % 2)
            self._buttons.append(button)
        root.addLayout(grid)
        self._eyebrow = eyebrow
        self._specs = group.tools
        self.apply_palette(palette)

    def apply_palette(self, palette: Palette) -> None:
        super().apply_palette(palette)
        self._palette = palette
        self._eyebrow.setStyleSheet(
            f"color:{palette.accent_hover}; font-size:9px; font-weight:700;"
            "letter-spacing:1px;"
        )
        for button, spec in zip(self._buttons, self._specs):
            button.setIcon(icon(spec.icon, palette.text_muted, 18))
            button.setStyleSheet(
                f"QPushButton {{ text-align:left; background:{palette.surface_sunken};"
                f"border:1px solid {palette.border}; padding:0 10px; }}"
                f"QPushButton:hover, QPushButton:focus {{ background:{palette.accent_soft};"
                f"border-color:{palette.accent}; color:{palette.text}; }}"
            )

    def focus_first(self) -> None:
        if self._buttons:
            self._buttons[0].setFocus()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.dismissed.emit()
            event.accept()
            return
        if event.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            focused = QApplication.focusWidget()
            try:
                index = self._buttons.index(focused)
            except ValueError:
                index = 0
            delta = {
                Qt.Key_Left: -1, Qt.Key_Right: 1,
                Qt.Key_Up: -2, Qt.Key_Down: 2,
            }[event.key()]
            self._buttons[max(0, min(index + delta, len(self._buttons) - 1))].setFocus()
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched in self._buttons and event.type() in (
            QEvent.ShortcutOverride, QEvent.KeyPress,
        ):
            key = event.key()
            handled = key == Qt.Key_Escape or key in (
                Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down,
            )
            if handled and event.type() == QEvent.ShortcutOverride:
                event.accept()
                return True
            if handled:
                self.keyPressEvent(event)
                return True
        return super().eventFilter(watched, event)


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
        #: The stacking order last applied, so it is only re-applied when it
        #: actually changes. See :meth:`_restack_overlays`.
        self._stacking: tuple[tuple[int, bool], ...] = ()

        self.hint = Hint(palette, self)
        self.hint.setAttribute(Qt.WA_TransparentForMouseEvents)

        from .panels.drag_readout import DragReadout
        from .panels.measure_overlay import MeasureOverlay
        from .panels.selection_band import SelectionBand

        self.drag_readout = DragReadout(palette, parent=self)
        self.measure_overlay = MeasureOverlay(palette, self)
        self.measure_overlay.hide()
        self.selection_band = SelectionBand(palette, self)
        self.viewport.band_changed.connect(self.selection_band.show_rect)

        self.view_controls = FloatingCard(palette, self, shadow=False)
        row = QHBoxLayout(self.view_controls)
        row.setContentsMargins(METRICS.space(1), METRICS.space(1), METRICS.space(1), METRICS.space(1))
        row.setSpacing(2)
        self._view_buttons: dict[str, IconButton] = {}
        for name, tooltip, key in (
            ("fit", "Zoom to fit  (F)", "fit"),
            ("ortho", "Orthographic / perspective", "projection"),
            ("grid", "Grid", "grid"),
            ("xray", "X-ray model  (X); Tab cycles hidden faces", "xray"),
        ):
            button = IconButton(
                name, palette, tooltip, size=32,
                checkable=key in ("grid", "xray"),
            )
            row.addWidget(button)
            self._view_buttons[key] = button
        self.view_controls.adjustSize()

    def add_overlay(self, widget: QWidget, anchor: str) -> None:
        """Place *widget* over the viewport. Anchor is e.g. ``"top-right"``."""
        widget.setParent(self)
        self.overlays.append((widget, anchor))
        self._layout_overlays()

    def remove_overlay(self, widget: QWidget) -> None:
        """Take *widget* off the stage. Hiding and deleting it stay the caller's.

        Going through here rather than filtering ``overlays`` in place is what
        keeps :meth:`_restack_overlays` honest. Its memo is a tuple of object
        addresses, and every one of these removals is followed by a
        ``deleteLater``, so a panel opened afterwards can be handed the address
        a closed one had. Forgetting the memo on the way out costs one raise
        and removes the coincidence entirely.
        """
        self.overlays = [(w, a) for w, a in self.overlays if w is not widget]
        self._stacking = ()

    #: Called after a resize so the window can re-flow its overlays.
    resized = None

    def resizeEvent(self, event) -> None:  # noqa: N802
        self.viewport.setGeometry(self.rect())
        if self.resized is not None:
            self.resized()
        # The full-size overlays are re-fitted by _layout_overlays itself.
        self._layout_overlays()
        super().resizeEvent(event)

    def _layout_overlays(self) -> None:
        """Place everything that floats over the viewport, then stack it.

        Placing is cheap -- moves and ``adjustSize`` measure at 0.0 ms even with
        several panels open -- so it happens on every call. Stacking is not, and
        is split out into :meth:`_restack_overlays` for that reason alone.
        """
        margin = METRICS.space(4)
        width, height = self.width(), self.height()

        controls = self.view_controls
        controls.adjustSize()
        controls.move(width - controls.width() - margin, height - controls.height() - margin)

        for widget, anchor in self.overlays:
            if anchor == "full":
                widget.setGeometry(self.rect())
                continue
            fitter = getattr(widget, "fit_to_height", None)
            if fitter is not None:
                fitter(height - margin * 2)
            widget.adjustSize() if widget.sizeHint().isValid() else None
            size = widget.size()
            if anchor == "top-right":
                # Sit below the ViewCube so the two never fight for the corner.
                widget.move(width - size.width() - margin, margin + 128)
            elif anchor == "top-left":
                widget.move(margin, margin)
            elif anchor == "rail-flyout":
                preferred = int(getattr(widget, "anchor_y", margin))
                y = max(margin, min(preferred, height - size.height() - margin))
                widget.move(margin, y)
            elif anchor == "top-center":
                widget.move((width - size.width()) // 2, margin + 24)
            elif anchor == "bottom-center":
                widget.move((width - size.width()) // 2, height - size.height() - margin)

        # The hint goes last, once the bottom-centre bar has a width, so it can
        # be trimmed to whatever room is actually left beside it.
        room = width - margin * 2
        for widget, anchor in self.overlays:
            if anchor == "bottom-center" and widget.isVisible():
                room = min(room, (width - widget.width()) // 2 - margin * 2)
        self.hint.elide_to(room)
        self.hint.adjustSize()
        self.hint.move(margin, height - self.hint.height() - margin)

        # The three full-size overlays have to track a resize whether or not
        # anything is being re-stacked. Setting a geometry that is already set
        # costs nothing.
        for overlay in (self.measure_overlay, self.selection_band):
            if overlay.isVisible():
                overlay.setGeometry(self.rect())

        self._restack_overlays()

    def _stacking_order(self) -> tuple[QWidget, ...]:
        """Bottom to top: who should be above whom over the viewport.

        The panels first, then the measurement and marquee, which are the
        active gesture: a measurement drawn on the model must never be
        hidden behind a panel, and a rubber-band rectangle that stopped at a
        panel's edge would look like the drag had. Neither takes clicks, so
        being on top costs nothing.
        """
        # Full-size drawing layers sit above the GL view but below interactive
        # cards. Otherwise a projected marker can paint across its own panel.
        order: list[QWidget] = [
            widget for widget, anchor in self.overlays if anchor == "full"
        ]
        order.append(self.view_controls)
        order.extend(
            widget for widget, anchor in self.overlays if anchor != "full"
        )
        order.extend(
            overlay
            for overlay in (self.measure_overlay, self.selection_band)
            if overlay.isVisible()
        )
        return tuple(order)

    def _restack_overlays(self) -> None:
        """Raise the overlays into order, but only when the order has changed.

        ``raise_()`` over a ``QOpenGLWidget`` is not free: it re-composites the
        whole viewport and re-blurs the drop shadow on every floating
        panel. Measured at 23.7 ms of a 26.5 ms layout pass with two panels
        open, and 58 ms with three. ``_layout_overlays`` is reached from
        ``set_hint``, which the hover path calls on every mouse move, so paying
        that per motion event is what made a window with a shape in it feel
        dead -- 70 ms a move, against a 17 ms frame.

        Re-raising is only ever needed when something joined, left, or appeared
        over the stage, and every one of those changes the tuple below. Nothing
        else in the app reorders these siblings except the widgets that raise
        *themselves*, and those are the ones that belong on top anyway.
        """
        order = self._stacking_order()
        # Visibility is part of the key, not just membership: a panel appearing
        # does not change who is on the stage, but it does change who is on top
        # of whom -- and a bar that showed itself while the highlight box was up
        # would otherwise stay in front of it and clip the box.
        key = tuple((id(widget), widget.isVisible()) for widget in order)
        if key == self._stacking:
            return
        for widget in order:
            widget.raise_()
        self._stacking = key

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.viewport.apply_palette(palette)
        self.drag_readout.apply_palette(palette)
        self.measure_overlay.apply_palette(palette)
        self.selection_band.apply_palette(palette)
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

        # A choice the user has made outranks the desktop's; having none is
        # what SYSTEM means, and is the right default for a fresh install.
        if mode is Mode.SYSTEM:
            from ..core.settings import theme_choice

            stored = theme_choice()
            if stored in ("light", "dark"):
                mode = Mode(stored)
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
        self.geometry.progress.connect(self._on_geometry_progress)
        self.geometry.saved.connect(self._on_project_saved)
        self._edit_epoch = 0
        self.geometry.start()
        self._dirty = False
        self._session_id = f"{os.getpid()}"
        from .selection import PresentationMap
        self._presentations: dict[str, object] = PresentationMap()
        #: The recovery offer, while it is on screen. Never modal -- see
        #: ``_offer_recovery``.
        self._recovery_bar = None
        #: Guards the re-entrancy of widening a selection to its whole group.
        self._expanding = False
        #: body name -> the shape currently on screen, so unchanged bodies are
        #: not needlessly re-tessellated on every rebuild.
        self._shown: dict[str, object] = {}
        #: The shape each presentation was built from, when it has since been
        #: re-placed rather than rebuilt (see ``refresh_view``).
        self._display_base: dict[str, object] = {}
        #: Root items to select when the outstanding paste rebuild completes.
        self._select_after_rebuild: list[str] = []
        self._select_after_rebuild_revision = 0
        self._geometry_recovery_revision = 0
        #: Arrow presses inside one 250 ms burst share these latched camera axes
        #: and become one feature/history operation.
        self._nudge_state: dict | None = None
        self._nudge_restore: dict | None = None
        self._nudge_keys: set[int] = set()
        self._nudge_queue: list[tuple[str, bool]] = []
        self._nudge_refreshing = False
        self._nudge_timer = QTimer(self)
        self._nudge_timer.setSingleShot(True)
        self._nudge_timer.setInterval(250)
        self._nudge_timer.timeout.connect(self._commit_nudge)
        self._tool_flyout: ToolFlyout | None = None
        self._browser_narrow: bool | None = None

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
        from ..core import settings

        self._browser_preferred_expanded = settings.ui_preference(
            "browser_expanded", True
        )
        self.browser.set_collapsed(
            not self._browser_preferred_expanded, emit=False
        )
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
        self.rail.group_requested.connect(self._open_tool_group)
        self.stage.viewport.ready.connect(self._on_viewport_ready)
        self.stage.viewport.rebound.connect(self._on_viewport_rebound)
        self.stage.viewport.hover_changed.connect(self._on_hover)
        self.stage.viewport.notice.connect(self.set_hint)
        self.stage.viewport.selection_changed.connect(self._on_selection)
        self.stage.viewport.context_menu_requested.connect(
            self._show_viewport_context_menu
        )
        self.stage.viewport.face_dragged.connect(self._on_face_dragged)
        self.stage.viewport.sketch_clicked.connect(self._on_sketch_click)
        self.stage.viewport.sketch_moved.connect(self._on_sketch_move)
        self.stage.viewport.drag_candidate_requested = self._maybe_start_face_drag
        self.stage.resized = self._on_stage_resized
        self.stage._view_buttons["fit"].clicked.connect(self.zoom_to_fit)
        self.stage._view_buttons["projection"].clicked.connect(self._toggle_projection)
        self.stage._view_buttons["grid"].clicked.connect(self._toggle_grid)
        self.stage._view_buttons["xray"].clicked.connect(self._toggle_xray)
        self.browser.visibility_toggled.connect(self._set_body_visible)
        self.browser.isolate_requested.connect(self.isolate_body)
        self.browser.show_all_requested.connect(self.show_all_bodies)
        self.browser.rename_requested.connect(self.rename_body)
        self.browser.duplicate_requested.connect(self.duplicate_body)
        self.browser.delete_requested.connect(self.delete_body)
        self.browser.feature_action.connect(self._feature_action)
        self.browser.bodies_selected.connect(self.select_items)
        self.browser.group_requested.connect(self.group_names)
        self.browser.ungroup_requested.connect(self.ungroup)
        self.browser.group_visibility_toggled.connect(self.set_group_visible)
        self.browser.group_rename_requested.connect(self.rename_group)
        self.browser.group_delete_requested.connect(self.delete_group)
        self.browser.group_duplicate_requested.connect(self._duplicate_group)
        self.browser.collapsed_changed.connect(self._browser_collapsed)

        # Arrow nudging belongs to the two places a model selection is made.
        # A narrow event filter lets an unhandled key fall through normally --
        # important for tree navigation and for every editor elsewhere in the
        # window. A window-wide QShortcut would consume the key even when the
        # nudge guard declined to act.
        self._nudge_key_sources = (
            self.stage.viewport,
            self.browser.bodies_tree,
            self.browser.bodies_tree.viewport(),
        )
        # Application-level routing is intentional. Tool cards and their
        # buttons can own keyboard focus while the model remains the subject;
        # filtering only the viewport made arrows appear randomly broken.
        QApplication.instance().installEventFilter(self)


        self._install_shortcuts()
        self._start_autosave()
        self.apply_theme()
        if not os.environ.get("SIMPLECAD_NO_RECOVERY"):
            # Deferred so the window is up before any dialog appears.
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
            icon("sun" if palette is DARK else "moon", palette.text_muted, 20)
        )
        # The viewport deliberately no longer recolours bodies itself, so that a
        # per-body tint survives. Re-display them here instead -- and clear the
        # cache first, because refresh_view skips any body whose shape object is
        # unchanged, which after a mere theme switch is every one of them.
        self._shown.clear()
        self._display_base.clear()
        self.refresh_view()

    def toggle_theme(self) -> None:
        from ..core.settings import set_theme_choice
        from .theme import DARK, LIGHT

        self.mode = Mode.LIGHT if self.palette_ is DARK else Mode.DARK
        self.palette_ = LIGHT if self.mode is Mode.LIGHT else DARK
        # Remembered, or every launch argues with the user about it.
        set_theme_choice(self.mode.value)
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

    def _history_key(self, event) -> str | None:
        """'undo' or 'redo' when *event* is one of the history shortcuts."""
        from ..core.settings import shortcuts

        if not hasattr(self, "_history_sequences"):
            bindings = shortcuts()
            self._history_sequences = [
                (QKeySequence(bindings[name]), "undo" if name == "undo" else "redo")
                for name in ("undo", "redo", "redo_alt") if bindings.get(name)
            ]
        sequence = QKeySequence(event.keyCombination())
        for binding, kind in self._history_sequences:
            if sequence.matches(binding) == QKeySequence.ExactMatch:
                return kind
        return None

    def _history_key_for_project(self, watched, event) -> bool:
        """Should this Undo/Redo keystroke go to the project, not a text field?

        A focused ``QLineEdit`` claims the standard Undo/Redo keys through
        ``ShortcutOverride`` before the window's shortcut sees them. With
        nothing of its own to undo that silently swallowed Ctrl+Z whenever a
        dimension field or search box had focus -- which is most of the time a
        tool panel is open. The field keeps the key only while it has its own
        edit to undo or redo.
        """
        if not (isinstance(watched, QWidget) and (watched is self or self.isAncestorOf(watched))):
            return False
        kind = self._history_key(event)
        if kind is None:
            return False
        return not self._field_can(kind, watched)

    @staticmethod
    def _field_can(kind: str, widget=None) -> bool:
        from PySide6.QtWidgets import QLineEdit, QPlainTextEdit, QTextEdit

        widget = widget or QApplication.focusWidget()
        if isinstance(widget, QLineEdit):
            return widget.isUndoAvailable() if kind == "undo" else widget.isRedoAvailable()
        if isinstance(widget, (QTextEdit, QPlainTextEdit)):
            document = widget.document()
            return document.isUndoAvailable() if kind == "undo" else document.isRedoAvailable()
        return False

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
            "xray": self._toggle_xray,
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
            "copy": self._copy_shortcut,
            "paste": self._paste_shortcut,
            "duplicate": self.duplicate_selection,
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
                if action == "delete":
                    # The model tree selects bodies too. WidgetShortcut, not
                    # WithChildren, so its rename editor keeps its own Delete.
                    tree_delete = QShortcut(
                        QKeySequence(binding), self.browser.bodies_tree, activated=handler
                    )
                    tree_delete.setContext(Qt.WidgetShortcut)
                    self._shortcuts.append(tree_delete)
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

    @staticmethod
    def _focused_clipboard_method(name: str):
        """Return a native text copy/paste method when an editor has focus."""
        widget = QApplication.focusWidget()
        method = getattr(widget, name, None)
        return method if callable(method) else None

    def _copy_shortcut(self) -> None:
        native = self._focused_clipboard_method("copy")
        if native is not None and self._is_typing():
            native()
            return
        self.copy_selection()

    def _paste_shortcut(self) -> None:
        native = self._focused_clipboard_method("paste")
        if native is not None and self._is_typing():
            native()
            return
        self.paste_selection()

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

    def _toggle_xray(self) -> None:
        viewport = self.stage.viewport
        viewport.set_xray(not viewport.xray_enabled)
        self.stage._view_buttons["xray"].setChecked(viewport.xray_enabled)
        self.set_hint(
            "X-ray on — Tab / Shift+Tab cycles faces under the cursor."
            if viewport.xray_enabled else "X-ray off."
        )

    def _on_viewport_rebound(self) -> None:
        """OCCT was rebuilt on a new GL context. Hand the bodies back.

        Every AIS object this window is holding was built in the context that
        has gone, and the new one refuses them -- ``erase`` on a presentation
        from a previous context raises ``object has been displayed in another
        context``. So they are dropped rather than removed, and the document is
        displayed again from scratch.

        ``_presentations`` is cleared in place because ``SelectionModel`` was
        handed the same dict and reads it live.
        """
        self.detach_gizmo()
        self._presentations.clear()
        self._shown.clear()
        self._display_base.clear()
        self.selection.refresh()
        self.refresh_view()

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
        self._cancel_nudge()
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

        If the worker is unavailable, retry its asynchronous startup and preserve
        the last valid displayed geometry.
        """
        if self.geometry.available and self.geometry.request(
            self.document, self._stale, force
        ):
            self._stale.clear()
            return
        if self.geometry.start() and self.geometry.request(self.document, self._stale, force):
            self._stale.clear()
            return
        self.set_hint("The geometry engine is unavailable. The last valid model is preserved; retry to restart it.")

    def _rebuild_in_process(self, force: bool = False) -> None:
        """Explicit synchronous rebuild for controlled diagnostics and tests."""
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
            self._refresh_rebuilt_model(report.ok)
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

    def _on_geometry_progress(self, message: dict) -> None:
        """Say which feature the geometry process is on.

        A rebuild of a large history is minutes of honest work. Without this it
        is minutes of an application that looks hung.
        """
        total = int(message.get("total") or 0)
        name = message.get("feature") or "the model"
        self.set_hint(
            f"Building {name}…" if total <= 1 else
            f"Building {name} ({int(message.get('index', 0)) + 1} of {total})…"
        )

    def _on_geometry_finished(self, message) -> None:
        self._geometry_recovery_revision = 0
        apply_result(self.document, message)
        self._refresh_rebuilt_model(message.get("report", {}).get("ok", True))
        if message.get("revision", 0) >= self._select_after_rebuild_revision:
            self._select_pasted_items()
        report = message.get("report", {})
        warnings = report.get("warnings") or []
        self.set_hint(warnings[-1] if warnings else report.get("summary", ""))

    def _on_geometry_failed(self, error: str) -> None:
        self._cancel_nudge()
        self.set_hint(error)
        if self.geometry.available:
            if self.geometry.busy:
                # A restarted worker is already receiving the latest document.
                return
            if (
                self._geometry_recovery_revision
                and self._geometry_recovery_revision == self.geometry.latest_revision
            ):
                # A failed recovery is a real error. Repeating the same force
                # rebuild forever would leave every preview parked behind it.
                return
            # A cached-shape sync failed, or the worker had no state to replay.
            # Rebuild once from the feature history before pending previews.
            self.rebuild(force=True)
            self._geometry_recovery_revision = self.geometry.latest_revision
            return
        # No child at all any more. In-process is slower and cannot protect the
        # window from OCCT, but losing the session outright is worse.
        # Kernel failures must never move their work onto the GUI thread.
        # Retain the displayed model until the user explicitly retries.

    def wait_for_rebuild(self, timeout_ms: int = 300_000) -> bool:
        """Block until the model is up to date. For scripts and tests only."""
        if not self.geometry.available:
            return True
        return self.geometry.wait(timeout_ms)

    @property
    def is_rebuilding(self) -> bool:
        return self.geometry.busy

    # -- transform gizmo ---------------------------------------------------
    def attach_gizmo(self, presentation, **options):
        """Put the transform gizmo on one or several presentations."""
        from .viewport.gizmo import TransformGizmo

        self.detach_gizmo()
        gizmo = TransformGizmo(self.stage.viewport, self)
        if not gizmo.attach(presentation, **options):
            return None
        self.stage.viewport.gizmo = gizmo
        return gizmo

    def detach_gizmo(self) -> None:
        gizmo = self.stage.viewport.gizmo
        if gizmo is not None:
            gizmo.detach()
        self.stage.viewport.gizmo = None

    # -- direct manipulation ---------------------------------------------
    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        """Route model nudges without stealing arrows from unrelated controls."""
        if event.type() == QEvent.ShortcutOverride and self._history_key_for_project(watched, event):
            # Declining the override lets the window's Undo/Redo shortcut fire.
            event.ignore()
            return True
        flyout = getattr(self, "_tool_flyout", None)
        inside_flyout = flyout is not None and (
            watched is flyout or (
                isinstance(watched, QWidget) and flyout.isAncestorOf(watched)
            )
        )
        if flyout is not None and flyout.isVisible() and event.type() == QEvent.MouseButtonPress:
            group_trigger = watched in self.rail._group_tiles.values()
            if not inside_flyout and not group_trigger:
                self.close_tool_flyout()
        if inside_flyout and event.type() in (
            QEvent.ShortcutOverride, QEvent.KeyPress, QEvent.KeyRelease,
        ):
            return super().eventFilter(watched, event)
        active_panel = next((
            widget for widget, _anchor in reversed(self.stage.overlays)
            if getattr(widget, "is_tool_panel", False) and not widget.isHidden()
        ), None)
        inside_window = isinstance(watched, QWidget) and (
            watched is self or self.isAncestorOf(watched)
        )
        source = (
            watched in getattr(self, "_nudge_key_sources", ())
            or active_panel is not None or inside_window
        )
        if (source and event.type() == QEvent.FocusOut and not self._nudge_refreshing) or (
            watched is self and event.type() == QEvent.WindowDeactivate
        ):
            # A release may go to another window after focus changes.
            self._nudge_keys.clear()
            if self._nudge_state is not None:
                self._nudge_timer.start()
        if source and event.type() == QEvent.MouseButtonPress:
            self._cancel_nudge()
        if source and event.type() in (
            QEvent.ShortcutOverride, QEvent.KeyPress, QEvent.KeyRelease,
        ):
            key = event.key()
            pending = self._nudge_state is not None or self._nudge_restore is not None
            if key == Qt.Key_Escape and pending:
                if event.type() == QEvent.ShortcutOverride:
                    event.accept()
                    return True
                if event.type() == QEvent.KeyPress:
                    self._cancel_nudge()
                    event.accept()
                    return True
            if event.type() == QEvent.KeyRelease and key in self._nudge_keys:
                if not event.isAutoRepeat():
                    self._nudge_keys.discard(key)
                    if not self._nudge_keys and self._nudge_state is not None:
                        self._nudge_timer.start()
                event.accept()
                return True
            directions = {
                Qt.Key_Left: "left", Qt.Key_Right: "right",
                Qt.Key_Up: "up", Qt.Key_Down: "down",
            }
            direction = directions.get(key)
            if direction is not None and event.type() == QEvent.KeyPress:
                if self._is_typing():
                    return super().eventFilter(watched, event)
                if active_panel is not None and event.modifiers() == Qt.NoModifier:
                    arrow_handler = getattr(active_panel, "handle_arrow_key", None)
                    if callable(arrow_handler) and arrow_handler(key):
                        event.accept()
                        return True
                if event.isAutoRepeat() and key not in self._nudge_keys:
                    return super().eventFilter(watched, event)
                relevant = event.modifiers() & (
                    Qt.ShiftModifier | Qt.ControlModifier
                    | Qt.AltModifier | Qt.MetaModifier
                )
                plain = relevant == Qt.NoModifier
                depth = relevant == Qt.ControlModifier
                if (plain or depth) and (not depth or direction in ("up", "down")):
                    if self._nudge_key(direction, depth):
                        self._nudge_keys.add(key)
                        self._nudge_timer.stop()
                        event.accept()
                        return True
        return super().eventFilter(watched, event)

    def _nudge_allowed(self, *, allow_busy: bool = False) -> bool:
        viewport = self.stage.viewport
        if (
            (self.geometry.busy and not allow_busy)
            or self.sketching or viewport.picking_points
            or viewport.gizmo is not None or viewport._nav.name != "NONE"
        ):
            return False
        return not any(
            getattr(widget, "is_tool_panel", False) and widget.isVisible()
            for widget, _anchor in self.stage.overlays
        )

    @staticmethod
    def _unit(vector):
        length = math.sqrt(sum(value * value for value in vector))
        return tuple(value / length for value in vector) if length else (0.0, 0.0, 0.0)

    def _begin_nudge(self, direction: str, depth: bool = False) -> dict | None:
        """Latch selection and camera axes until this arrow burst finishes."""
        if not self._nudge_allowed():
            return None
        self.selection.refresh()
        faces = self.selection.planar_faces() or self.selection.round_faces()
        if self.selection.count == 1 and len(faces) == 1:
            if depth or direction not in ("up", "down"):
                return None
            pick = faces[0]
            state = {
                "kind": "face", "pick": pick, "amount": 0.0,
                "reference": pick.reference(self.document),
            }
            self._nudge_state = state
            return state
        if not self.selection.only_bodies or not self.selection.bodies:
            return None

        from .viewport.camera import read_state, view_direction

        camera = read_state(self.stage.viewport.view)
        if camera is None:
            return None
        forward = view_direction(camera)
        up = self._unit(camera[2])
        right = self._unit((
            forward[1] * up[2] - forward[2] * up[1],
            forward[2] * up[0] - forward[0] * up[2],
            forward[0] * up[1] - forward[1] * up[0],
        ))
        names = list(self.selection.bodies)
        state = {
            "kind": "move", "bodies": names,
            "right": right, "up": up, "forward": forward,
            "offset": [0.0, 0.0, 0.0],
            "shapes": [self.document.body(name).shape for name in names],
        }
        self._nudge_state = state
        return state

    def _nudge_key(self, direction: str, depth: bool = False) -> bool:
        if self._nudge_restore is not None:
            if not self._nudge_allowed(allow_busy=True):
                return False
            if self._nudge_restore["kind"] == "face" and (
                depth or direction not in ("up", "down")
            ):
                return False
            self._nudge_queue.append((direction, depth))
            self.set_hint("Finishing the previous step — additional arrows are queued")
            return True
        state = self._nudge_state or self._begin_nudge(direction, depth)
        if state is None:
            return False
        if state["kind"] == "face":
            if depth or direction not in ("up", "down"):
                return False
            state["amount"] += NUDGE_STEP_MM if direction == "up" else -NUDGE_STEP_MM
        else:
            if depth:
                axis = state["forward"]
                sign = 1.0 if direction == "up" else -1.0
            elif direction in ("left", "right"):
                axis = state["right"]
                sign = 1.0 if direction == "right" else -1.0
            else:
                axis = state["up"]
                sign = 1.0 if direction == "up" else -1.0
            for index in range(3):
                state["offset"][index] += axis[index] * NUDGE_STEP_MM * sign
        self._preview_nudge()
        self._nudge_timer.start()
        return True

    def _preview_nudge(self) -> None:
        from ..kernel.occ import compound, make_transform, transformed

        state = self._nudge_state
        if state is None:
            return
        viewport = self.stage.viewport
        if state["kind"] == "face":
            pick = state["pick"]
            amount = state["amount"]
            surface_delta = -amount if pick.is_round_face and pick.info.internal else amount
            viewport.show_ghost(
                self._pull_preview(pick, surface_delta),
                self.palette_.danger if amount < 0.0 else self.palette_.accent,
            )
            viewport.set_transparency(
                self._presentations.get(pick.body), 0.6 if amount < 0.0 else 0.0
            )
            self._show_drag_readout(
                pick,
                surface_delta,
                at=self._pull_readout_anchor(pick),
                material_change=amount,
            )
            self.set_hint(
                f"{'Adding' if amount >= 0 else 'Removing'} "
                f"{abs(amount):.2f} mm — arrows continue, Esc cancels"
            )
            return

        offset = tuple(state["offset"])
        transform = make_transform(translate=offset)
        # Built once per nudge and then only re-placed. Copying every selected
        # body and rebuilding its presentation on each arrow press (including
        # key repeat) made nudging a large part stutter.
        if state.get("ghost") is not viewport.ghost_presentation():
            state["ghost"] = viewport.show_ghost(
                compound(state["shapes"]), self.palette_.accent, transparency=0.14,
            )
        viewport.transform_ghost(transform)
        for name in state["bodies"]:
            viewport.set_transparency(self._presentations.get(name), 0.78)
        self.set_hint(
            f"Moving {math.sqrt(sum(value * value for value in offset)):.2f} mm — "
            "arrows continue, Esc cancels"
        )

    def _clear_nudge_preview(self) -> None:
        state = self._nudge_state
        self.stage.viewport.clear_ghost()
        self.stage.drag_readout.finish()
        if state is None:
            return
        names = [state["pick"].body] if state["kind"] == "face" else state["bodies"]
        for name in names:
            self.stage.viewport.set_transparency(self._presentations.get(name), 0.0)

    def _cancel_nudge(self) -> bool:
        pending = self._nudge_state is not None or self._nudge_restore is not None
        self._nudge_timer.stop()
        if self._nudge_state is not None:
            self._clear_nudge_preview()
        self._nudge_state = None
        self._nudge_restore = None
        self._nudge_queue.clear()
        self._nudge_keys.clear()
        if pending:
            self.set_hint(self.selection.summary())
        return pending

    def _commit_nudge(self) -> None:
        state = self._nudge_state
        if state is None or self._nudge_keys:
            return
        if self.geometry.busy:
            # The old path simply returned here and discarded the only timer.
            # A rebuild that happened to overlap the timeout left the visible
            # preview pending forever and the next click cancelled it.
            self._nudge_timer.start()
            return
        self._clear_nudge_preview()
        self._nudge_state = None
        if state["kind"] == "face":
            amount = round(float(state["amount"]), 9)
            if abs(amount) < 1.0e-9:
                self.set_hint(self.selection.summary())
                return
            pick = state["pick"]
            feature_class = RoundPushPullFeature if pick.is_round_face else PushPullFeature
            key = "delta" if pick.is_round_face else "distance"
            feature = feature_class(
                inputs={
                    "body": BodyRef(pick.body),
                    "face": state["reference"], key: amount,
                },
                outputs=[pick.body],
            )
            self._nudge_restore = {
                "kind": "face", "body": pick.body,
                "reference": state["reference"],
            }
            label = "Nudge face"
        else:
            offset = tuple(round(float(value), 9) for value in state["offset"])
            if math.sqrt(sum(value * value for value in offset)) < 1.0e-9:
                self.set_hint(self.selection.summary())
                return
            feature = MoveManyFeature(
                inputs={
                    "bodies": [BodyRef(name) for name in state["bodies"]],
                    "dx": offset[0], "dy": offset[1], "dz": offset[2],
                },
                outputs=list(state["bodies"]),
            )
            self._nudge_restore = {
                "kind": "bodies", "bodies": list(state["bodies"]),
                "axes": {key: state[key] for key in ("right", "up", "forward")},
            }
            label = "Nudge objects"

        self.history.record(label)
        if state["kind"] == "face" or not self.document.fold_translation(state["bodies"], offset):
            self.document.add_feature(feature)
        self.mark_dirty()
        self.rebuild()

    def _refresh_rebuilt_model(self, ok: bool) -> None:
        # Replacing AIS presentations emits selection changes too. Those are
        # not user input and must not discard queued arrows or their target.
        self._nudge_refreshing = True
        try:
            self.refresh_view()
            self.browser.refresh()
            if ok:
                self._restore_nudge_selection()
            else:
                self._cancel_nudge()
            self.selection.refresh()
            self.refresh_context_bar()
        finally:
            self._nudge_refreshing = False
        # Placement stores durable references and re-resolves them against a
        # newly displayed body without reading the camera again.
        for panel, _anchor in self.stage.overlays:
            if hasattr(panel, "_placement_refs"):
                panel.preview()

    def _restore_nudge_selection(self) -> None:
        state, self._nudge_restore = self._nudge_restore, None
        if state is None:
            return
        viewport = self.stage.viewport
        restored = False
        if state["kind"] == "bodies":
            restored = all(self._presentations.get(name) for name in state["bodies"])
            if restored:
                viewport.select_presentations(
                    self._presentations[name] for name in state["bodies"]
                )
        else:
            body = self.document.body(state["body"])
            presentation = self._presentations.get(state["body"])
            if body is not None and body.shape is not None and presentation is not None:
                try:
                    from ..core.naming import resolve

                    face = resolve(state["reference"], body.shape)
                    restored = viewport.select_subshape(presentation, face)
                except Exception:  # noqa: BLE001 - never apply queued arrows to another face
                    viewport.select_shape(presentation)
        self.selection.refresh()
        queued, self._nudge_queue = self._nudge_queue, []
        if not restored:
            self._cancel_nudge()
            return
        if queued:
            direction, depth = queued[0]
            resumed = self._begin_nudge(direction, depth)
            if resumed is not None:
                resumed.update(state.get("axes", {}))
                for direction, depth in queued:
                    self._nudge_key(direction, depth)
                if self._nudge_keys:
                    self._nudge_timer.stop()

    def _maybe_start_face_drag(self) -> None:
        """Pressing on an already-selected face starts a Pull.

        This is the interaction the whole design is built around: select, drag,
        watch it happen, type an exact number if you want one. It only triggers
        on a face that is *already* selected, so the first click still selects
        and orbiting is unaffected.

        A round face is dragged along its **radius** rather than a normal, which
        is how a shaft or a tube is made thinner: the same gesture, aimed at the
        one direction a cylinder can actually move in.
        """
        for widget, _anchor in reversed(self.stage.overlays):
            starter = getattr(widget, "begin_freeform_drag", None)
            if getattr(widget, "isVisible", lambda: False)() and starter is not None:
                if starter(self.stage.viewport._press_pos):
                    return
        if self.selection.count != 1:
            return
        faces = self.selection.planar_faces() or self.selection.round_faces()
        if len(faces) != 1:
            return
        pick = faces[0]
        viewport = self.stage.viewport
        # Only drag if the press actually landed on the selected face. Without
        # this check, every click after a selection starts a drag and nothing
        # else can ever be selected again.
        under_cursor = viewport.detected_shape(viewport._press_pos)
        if under_cursor is None or not under_cursor.IsSame(pick.shape):
            return

        if pick.is_round_face:
            frame = self._radial_frame(pick)
            if frame is None:
                return
            anchor, direction = frame
        else:
            anchor, direction = pick.info.center, pick.info.normal
        if viewport.begin_face_drag(anchor, direction):
            self._drag_pick = pick
            self._drag_distance = 0.0

    def _radial_frame(self, pick):
        """``(point on the face, outward radial direction)`` under the cursor.

        Anchored where the user actually pressed rather than at some canonical
        point on the axis, so the handle-less drag tracks the surface they are
        holding -- and so the direction is the one facing the camera, which is
        the only one a mouse can push along.
        """
        import math

        from ..kernel.snapping import _surface_hit

        viewport = self.stage.viewport
        info = pick.info
        ray = viewport.cursor_ray(viewport._press_pos)
        if ray is None:
            return None
        hit = _surface_hit(pick.shape, ray[0], ray[1])
        if hit is None:
            return None
        along = sum(
            (hit[i] - info.origin[i]) * info.direction[i] for i in range(3)
        )
        axis_point = tuple(
            info.origin[i] + info.direction[i] * along for i in range(3)
        )
        radial = tuple(hit[i] - axis_point[i] for i in range(3))
        length = math.sqrt(sum(v * v for v in radial))
        if length < 1e-9:
            return None
        return (hit, tuple(v / length for v in radial))

    def _on_face_dragged(self, distance: float, finished: bool) -> None:
        pick = getattr(self, "_drag_pick", None)
        if pick is None:
            return
        self._drag_distance = distance
        # Dragging a round face outward makes a shaft fatter and a hole bigger,
        # so on a bore the gesture *removes* material. Everything downstream
        # talks in "material added", so the sign is settled once, here.
        adds = -distance if pick.is_round_face and pick.info.internal else distance

        if not finished:
            cutting = adds < 0
            self.stage.viewport.show_ghost(
                self._pull_preview(pick, distance),
                self.palette_.danger if cutting else self.palette_.accent,
            )
            # The slab a cut removes lies inside the solid, so the body has to
            # get out of its own way for the preview to be visible at all.
            self._set_drag_transparency(pick.body, 0.6 if cutting else 0.0)
            self._show_drag_readout(pick, distance, material_change=adds)
            verb = "Adding" if adds >= 0 else "Cutting"
            self.set_hint(f"{verb} {abs(adds):.2f} mm — release to apply")
            return

        self._end_face_drag()
        if abs(distance) < 0.05:
            self.set_hint(self.selection.summary())
            return
        if pick.is_round_face:
            self.add_feature(
                RoundPushPullFeature(
                    inputs={
                        "body": BodyRef(pick.body),
                        "face": pick.reference(self.document),
                        "delta": round(adds, 3),
                    },
                    outputs=[pick.body],
                )
            )
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

    def _show_drag_readout(
        self,
        pick,
        distance: float,
        *,
        at: QPoint | None = None,
        material_change: float | None = None,
    ) -> None:
        """Show signed surface travel and the resulting overall dimension."""
        if at is None:
            from PySide6.QtGui import QCursor

            at = self.stage.mapFromGlobal(QCursor.pos())
        readout = self.stage.drag_readout
        body = self.document.body(pick.body)
        if pick.is_round_face:
            # A cylinder has one size worth reading, and it is not how far the
            # surface travelled.
            readout.show_drag(
                at,
                distance,
                pick.info.diameter + 2.0 * distance,
                "Diameter",
                material_change,
            )
            return
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
            at, distance, resulting, label, material_change
        )

    def _pull_readout_anchor(self, pick) -> QPoint:
        """Project the affected face into the stage for keyboard feedback."""
        try:
            from ..kernel.occ import bounding_box

            low, high = bounding_box(pick.shape)
            centre = tuple((low[index] + high[index]) / 2.0 for index in range(3))
            projected = self.stage.viewport.project(centre)
        except Exception:  # noqa: BLE001 - a readout must never block modelling
            projected = None
        if projected is None:
            return self.stage.rect().center()
        point = QPoint(round(projected[0]), round(projected[1]))
        return self.stage.viewport.mapTo(self.stage, point)

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
        """The material a pull would add or remove, for the ghost.

        A slab for a flat face, a tube for a round one -- and the tube is built
        by the very function the feature uses, so what is previewed is what will
        be committed rather than a lookalike computed a second way.
        """
        if abs(distance) < 1e-6:
            return None
        if pick.is_round_face:
            from ..kernel.operations import radial_ring

            info = pick.info
            try:
                return radial_ring(
                    info, info.radius + distance,
                    adding=(distance < 0) if info.internal else (distance > 0),
                )
            except BaseException:  # noqa: BLE001 - OCCT raises non-Exceptions
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

    def _sketch_redo(self) -> None:
        if self.canvas is None:
            return
        self.canvas.redo_last()
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
            self.stage.remove_overlay(self.sketch_bar)
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
    def _before_history_step(self) -> None:
        """Put away anything holding on to the model that is about to change.

        An open tool keeps presentations, a gizmo and face references from the
        current model. Left open across an undo it could commit against a
        history it never saw, or drag a gizmo whose body had been replaced.
        """
        self._cancel_nudge()
        self._close_texture_editor()
        self._end_face_drag()
        self.stage.viewport.handles.clear(self.stage.viewport)
        self.detach_gizmo()
        self.close_tool_panels()

    def undo(self) -> None:
        focused = QApplication.focusWidget()
        if self._is_typing() and self._field_can("undo", focused):
            focused.undo()
            return
        if self.sketching:
            self._sketch_undo()
            return
        self._before_history_step()
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
        focused = QApplication.focusWidget()
        if self._is_typing() and self._field_can("redo", focused):
            focused.redo()
            return
        if self.sketching:
            self._sketch_redo()
            return
        self._before_history_step()
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
        self._edit_epoch = getattr(self, "_edit_epoch", 0) + 1
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
        if not self.geometry.saving:
            self.geometry.save(self.document, autosave.recovery_path(self._session_id),
                               token={"epoch": self._edit_epoch, "autosave": True})

    def _offer_recovery(self) -> None:
        """If a previous session did not exit cleanly, offer its work back.

        Offered on the stage rather than in a modal dialog. See
        :mod:`simplecad.ui.panels.recovery_bar` for why -- in short, a message
        box here locks the whole window and can hide behind it, and an
        application that draws fine and ignores every click is one the user
        reasonably calls frozen.
        """
        from ..core import autosave
        from .panels.recovery_bar import RecoveryBar

        leftovers = [
            (path, when) for path, when in autosave.pending()
            if f"session-{self._session_id}.scad3" not in path
        ]
        if not leftovers:
            return
        path, when = leftovers[0]
        bar = RecoveryBar(self.palette_, autosave.describe_age(when), self.stage)
        self._recovery_bar = bar

        def dismiss() -> None:
            self.stage.remove_overlay(bar)
            bar.hide()
            bar.deleteLater()
            self._recovery_bar = None

        def recover() -> None:
            dismiss()
            self._open_path(path)
            self._project_path = None   # recovered, but not yet saved anywhere
            self.mark_dirty()
            self.set_hint("Recovered. Save it somewhere to keep it.")

        def discard() -> None:
            dismiss()
            for leftover, _when in leftovers:
                try:
                    os.remove(leftover)
                except OSError:
                    pass
            self.set_hint("Discarded the unsaved work from last time.")

        bar.recover_requested.connect(recover)
        bar.discard_requested.connect(discard)
        self.stage.add_overlay(bar, "top-center")
        bar.show()

    def closeEvent(self, event) -> None:  # noqa: N802
        """Finish an outstanding atomic save before stopping its worker."""
        if self.geometry.saving:
            event.ignore()
            QTimer.singleShot(100, self.close)
            return
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
        document = self.document
        key = (id(document), document.revision, len(document.bodies))
        if getattr(self, "_tint_key", None) != key:
            self._tint_key = key
            self._tint_index = {body: i for i, body in enumerate(document.bodies)}
        index = self._tint_index.get(name, 0)
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
                    self._display_base.pop(name, None)

            for body in self.document.bodies.values():
                if not body.visible or body.shape is None:
                    continue
                # Cached features hand back the same shape object, so an
                # unchanged body needs no work at all.
                if self._shown.get(body.name) is body.shape:
                    continue
                existing = self._presentations.get(body.name)
                base = self._display_base.get(body.name)
                if existing is not None and base is not None and _moved_copy(base, body.shape):
                    # The same geometry somewhere else: re-place the existing
                    # presentation. Rebuilding it re-tessellated the display
                    # and every face/edge/vertex pick zone -- seconds for a
                    # large imported mesh, on every move and every undo.
                    viewport.place(existing, body.shape.Location().Multiplied(
                        base.Location().Inverted()))
                    self._shown[body.name] = body.shape
                    continue
                if existing is not None:
                    viewport.erase(existing)
                self._presentations[body.name] = viewport.display(
                    body.shape, body.color or self._tint_for(body.name)
                )
                self._shown[body.name] = body.shape
                self._display_base[body.name] = body.shape

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

        self.document.rename_body(old, new)
        presentation = self._presentations.pop(old, None)
        if presentation is not None:
            self._presentations[new] = presentation
        self._shown[new] = self._shown.pop(old, None)
        self._display_base[new] = self._display_base.pop(old, None)
        for group in self.document.groups.values():
            group.members = [new if m == old else m for m in group.members]

        self.mark_dirty()
        self.browser.refresh()
        self.set_hint(f"Renamed {old} to {new}.")

    def duplicate_body(self, name: str) -> None:
        """Copy a body, offset clear of the original so both are visible."""
        from ..core.model_clipboard import copy_fragment
        from ..kernel.occ import bounding_box

        body = self.document.body(name)
        if body is None or body.shape is None:
            return
        fragment = copy_fragment(
            self.document, [name], bounds=bounding_box(body.shape),
            capture_geometry=not self.is_rebuilding,
        )
        if not fragment.features:
            self.set_hint(f"'{name}' has no feature history to copy.")
            return
        roots = self._paste_model_fragment(fragment, "Duplicate")
        if roots:
            self.set_hint(f"Duplicated {name} as {roots[0]}.")

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
            self.document.remove_feature(feature.id, splice=False)
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
        self._display_base.pop(name, None)
        self.document.bodies.pop(name, None)
        self.document.forget_member(name)
        self.mark_dirty()
        self.rebuild()
        self.set_hint(f"Deleted {name}.")

    def _close_texture_editor(self) -> None:
        from .tools.face_texture import FaceTexturePanel

        if any(isinstance(widget, FaceTexturePanel) for widget, _ in self.stage.overlays):
            self.cancel_tool()

    def _texture_action(self, action: str) -> None:
        """Choose a texture from the selected part, then act on its feature ID."""
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QMenu
        from ..kernel.face_texture import PACKAGES

        self.selection.refresh()
        candidates = texture_features(self.selection)
        if not candidates:
            self.set_hint("Select a part with an applied texture.")
            return
        target = candidates[0].id
        if len(candidates) > 1:
            menu = QMenu(self)
            menu.setObjectName("TextureChooser")
            for feature in candidates:
                pattern = str(feature.inputs.get("pattern", "horizontal_lines"))
                definition = PACKAGES.get(
                    str(feature.inputs.get("package", "builtin.v1")), {},
                ).get(pattern)
                label = definition.label if definition else pattern.replace("_", " ")
                item = menu.addAction(f"{feature.name} — {label}")
                item.setData(feature.id)
            chosen = menu.exec(QCursor.pos())
            if chosen is None:
                menu.deleteLater()
                return
            target = chosen.data()
            menu.deleteLater()
        # The menu runs an event loop; the model may have changed meanwhile.
        self.selection.refresh()
        if target not in {feature.id for feature in texture_features(self.selection)}:
            self.set_hint("That texture is no longer available.")
            return
        self._feature_action(target, action)

    def _remove_face_texture(self, target: str) -> None:
        feature = self.document.feature(target)
        if feature is None or feature.type_name != "face_texture":
            self.set_hint("That texture is no longer available.")
            return
        self._cancel_nudge()
        self._close_texture_editor()
        self.history.record(f"Remove Texture from {feature.inputs.get('body', feature.name)}")
        dirty = self.document.dependents_of({target})
        self.invalidate(dirty)
        # Keep the body record (including the last valid solid and appearance)
        # while its earlier features and downstream modifiers rebuild.
        bodies = {name: self.document.body(name) for name in feature.outputs}
        self.document.remove_feature(target)
        self.document.bodies.update({name: body for name, body in bodies.items() if body is not None})
        self.selection.refresh()
        self.refresh_context_bar()
        self.mark_dirty()
        self.rebuild()
        self.set_hint(f"Removed {feature.name}.")

    def _edit_joint_from_selection(self) -> None:
        from .selection import joint_for

        self.selection.refresh()
        feature = joint_for(self.selection)
        if feature is None:
            self.set_hint("Select a face of a joined part to edit its joint.")
            return
        self._feature_action(feature.id, "edit")

    def _feature_action(self, target: str, action: str) -> None:
        if action == "edit":
            feature = self.document.feature(target)
            if feature is not None and feature.type_name == "face_texture":
                self._texture_edit_feature_id = target
                self.run_action("face_texture")
            elif feature is not None and feature.type_name in {"fillet", "chamfer", "edge_finish"}:
                self._edge_edit_feature_id = target
                self.run_action("edit_edge_finish")
            elif feature is not None and feature.type_name == "clip_joint":
                self._clip_edit_feature_id = target
                self.activate_tool("clip_joint")
            return
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
        if action == "delete" and feature.type_name == "face_texture":
            self._remove_face_texture(target)
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
            self._display_base.pop(body_name, None)
            self.document.bodies.pop(body_name, None)
            self.document.forget_member(body_name)
        self.document.groups.pop(name, None)
        self.document.forget_member(name)
        self.rebuild()
        self.browser.refresh()
        self.set_hint(f"Deleted {name} and its {len(members)} objects.")

    def duplicate_selection(self) -> None:
        """Copy what is selected, keeping a group a group."""
        from ..core.model_clipboard import copy_fragment

        self.selection.refresh()
        if not self.selection.only_bodies:
            self.set_hint("Select one or more whole bodies or groups to duplicate.")
            return
        items = self.selection.items
        shapes = [
            body.shape
            for body in (
                self.document.body(name) for name in self.document.expand(items)
            )
            if body is not None and body.shape is not None
        ]
        if not shapes:
            return
        fragment = copy_fragment(
            self.document, items, bounds=_combined_bounds(shapes),
            capture_geometry=not self.is_rebuilding,
        )
        roots = self._paste_model_fragment(fragment, "Duplicate")
        if roots:
            self.set_hint(f"Duplicated {', '.join(items)} as {', '.join(roots)}.")

    def copy_selection(self) -> None:
        """Copy whole selected bodies/groups as an independent feature graph."""
        from ..core.model_clipboard import copy_fragment

        self.selection.refresh()
        if not self.selection.only_bodies:
            self.set_hint("Copy works on whole bodies or groups; select the object first.")
            return
        items = self.selection.items
        bodies = self.document.expand(items)
        shapes = [
            body.shape for body in (self.document.body(name) for name in bodies)
            if body is not None and body.shape is not None
        ]
        if not shapes:
            self.set_hint("There is no built model in the selection to copy.")
            return
        fragment = copy_fragment(
            self.document, items, bounds=_combined_bounds(shapes),
            capture_geometry=not self.is_rebuilding,
        )
        mime = QMimeData()
        mime.setData(
            MODEL_CLIPBOARD_MIME,
            json.dumps(fragment.to_dict(), separators=(",", ":")).encode("utf-8"),
        )
        mime.setText(", ".join(items))
        QApplication.clipboard().setMimeData(mime)
        self.set_hint(f"Copied {', '.join(items)}. Press Ctrl+V to paste a new part.")

    def paste_selection(self) -> None:
        """Paste a copied parametric fragment beside the current model."""
        from ..core.model_clipboard import ModelFragment

        mime = QApplication.clipboard().mimeData()
        if not mime.hasFormat(MODEL_CLIPBOARD_MIME):
            self.set_hint("The clipboard does not contain a SimpleCAD body or group.")
            return
        try:
            payload = bytes(mime.data(MODEL_CLIPBOARD_MIME)).decode("utf-8")
            fragment = ModelFragment.from_dict(json.loads(payload))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.set_hint(f"That SimpleCAD clipboard data is not valid: {exc}")
            return
        if not fragment.features or not fragment.root_bodies:
            self.set_hint("The copied model has no feature history to paste.")
            return

        root_items = self._paste_model_fragment(fragment, "Paste")
        if root_items:
            self.set_hint(f"Pasted {', '.join(root_items)} beside the model.")

    def _paste_model_fragment(self, fragment, history_label: str,
                              assets: dict[str, bytes] | None = None,
                              keep_parameter_names: bool = False) -> list[str]:
        """Insert one fragment; shared by Paste and Duplicate."""
        from ..core.errors import CadError
        from ..core.model_clipboard import paste_fragment

        offset = (0.0, 0.0, 0.0)
        existing = [
            body.shape for body in self.document.visible_bodies()
            if body.shape is not None
        ]
        if existing and fragment.bounds is not None:
            source_low, source_high = fragment.bounds
            here_low, here_high = _combined_bounds(existing)
            gap = max(10.0, (source_high[0] - source_low[0]) * 0.15)
            offset = (
                here_high[0] + gap - source_low[0],
                here_low[1] - source_low[1],
                here_low[2] - source_low[2],
            )

        self.history.record(history_label)
        try:
            for blob in (assets or {}).values():
                self.document.add_asset(blob)
            _features, root_items = paste_fragment(self.document, fragment, offset,
                                                   keep_parameter_names=keep_parameter_names)
        except (CadError, ValueError) as exc:
            self.history.cancel()
            self.set_hint(f"{history_label} failed: {exc}")
            return []
        self._select_after_rebuild = root_items
        self.mark_dirty()
        self.rebuild()
        self._select_after_rebuild_revision = self.geometry.latest_revision
        if not self.is_rebuilding:
            self.browser.refresh()
            self._select_pasted_items()
        return root_items

    def _select_pasted_items(self) -> None:
        """Select a just-built paste without recursively widening per click."""
        if not self._select_after_rebuild:
            return
        items, self._select_after_rebuild = self._select_after_rebuild, []
        names = self.document.expand(items)
        viewport = self.stage.viewport
        self._expanding = True
        try:
            viewport.clear_selection()
            for name in names:
                presentation = self._presentations.get(name)
                if presentation is not None:
                    viewport.select_shape(presentation, replace=False)
        finally:
            self._expanding = False
        self._on_selection()

    def _duplicate_group(self, name: str) -> None:
        """Copy every body in a group, and group the copies together."""
        from ..core.model_clipboard import copy_fragment

        members = self.document.expand([name])
        shapes = [
            body.shape for body in (self.document.body(item) for item in members)
            if body is not None and body.shape is not None
        ]
        if not shapes:
            return
        fragment = copy_fragment(
            self.document, [name], bounds=_combined_bounds(shapes),
            capture_geometry=not self.is_rebuilding,
        )
        roots = self._paste_model_fragment(fragment, "Duplicate")
        if roots:
            self.set_hint(f"Duplicated {name} as {roots[0]}.")

    def select_item(self, name: str) -> None:
        """Select a body or a group from the model tree."""
        self.select_items([name])

    def select_items(self, items) -> None:
        """Mirror an ordered multi-row browser selection into the viewport."""
        viewport = self.stage.viewport
        names: list[str] = []
        for item in items:
            expanded = (
                self.document.expand([item]) if item in self.document.groups
                else [item]
            )
            for name in expanded:
                if name not in names:
                    names.append(name)
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

    def _show_viewport_context_menu(self, position) -> None:
        """Show the same valid actions as the contextual bar on a right click."""
        from PySide6.QtWidgets import QMenu

        self.selection.refresh()
        actions = available_actions(self.selection)
        if not actions:
            return
        menu = QMenu(self)
        for key, label, _icon_name in actions:
            menu.addAction(label, lambda _checked=False, k=key: self.run_action(k))
        menu.exec(self.stage.viewport.mapToGlobal(position))

    def activate_tool(self, key: str) -> None:
        from .tools.registry import activate

        self.close_tool_flyout()
        self.rail.set_active(key)
        self._cancel_nudge()
        # A flat face already selected is an unambiguous request to sketch on
        # it, so skip the plane picker.
        if key == "sketch" and self.sketch_on_selection():
            return
        activate(self, key)

    def _open_tool_group(self, key: str) -> None:
        """Open one non-native flyout beside the category that requested it."""
        current = self._tool_flyout
        if current is not None and current.group.key == key and current.isVisible():
            self.close_tool_flyout()
            return
        self.close_tool_flyout()
        self.close_search()
        group = next((item for item in TOOL_GROUPS if item.key == key), None)
        if group is None:
            return
        flyout = ToolFlyout(group, self.palette_, self.stage)
        button = self.rail.group_button(key)
        if button is not None:
            origin = button.mapToGlobal(QPoint(0, 0))
            flyout.anchor_y = self.stage.mapFromGlobal(origin).y()
        flyout.tool_selected.connect(self._run_flyout_tool)
        flyout.dismissed.connect(self.close_tool_flyout)
        self.stage.add_overlay(flyout, "rail-flyout")
        self._tool_flyout = flyout
        self.rail.set_group_open(key)
        flyout.show()
        self.stage._layout_overlays()
        flyout.focus_first()

    def _run_flyout_tool(self, key: str) -> None:
        self.close_tool_flyout()
        self.run_action(key)

    def close_tool_flyout(self) -> None:
        flyout = getattr(self, "_tool_flyout", None)
        if flyout is None:
            return
        self.stage.remove_overlay(flyout)
        flyout.hide()
        flyout.deleteLater()
        self._tool_flyout = None
        self.rail.set_group_open(None)

    def close_tool_panels(self) -> None:
        """Dismiss any open tool panel without touching the selection."""
        focused = QApplication.focusWidget()
        for widget, _anchor in list(self.stage.overlays):
            if getattr(widget, "is_tool_panel", False):
                if focused is not None and widget.isAncestorOf(focused):
                    # Its Move/Done/Cancel had focus. Hand it back to the model,
                    # or Qt passes it down the tab chain to some unrelated
                    # button and Delete stops reaching the view.
                    self.stage.viewport.setFocus()
                self.stage.remove_overlay(widget)
                teardown = getattr(widget, "teardown", None)
                if teardown is not None:
                    try:
                        teardown()
                    except Exception:  # noqa: BLE001 - never block the dismissal
                        pass
                widget.hide()
                widget.deleteLater()

    def cancel_tool(self) -> None:
        if self._tool_flyout is not None:
            self.close_tool_flyout()
            return
        if self._cancel_nudge():
            return
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
        self._cancel_nudge()
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
            "edit_face_texture": lambda: self._texture_action("edit"),
            "edit_joint": self._edit_joint_from_selection,
            "remove_face_texture": lambda: self._texture_action("delete"),
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
            "copy": self.copy_selection,
            "paste": self.paste_selection,
            "group": self.group_selection,
            "ungroup": self.ungroup_selection,
            "hide": self.hide_selection,
            "rebuild_all": self.rebuild_everything,
            "theme": self.toggle_theme,
            "projection": self._toggle_projection,
            "view_grid": self._toggle_grid,
            "view_xray": self._toggle_xray,
            "view_fit": self.zoom_to_fit,
            "view_selection": self.zoom_to_selection,
            "view_iso": lambda: self._standard_view(StandardView.ISO),
            "view_top": lambda: self._standard_view(StandardView.TOP),
            "view_front": lambda: self._standard_view(StandardView.FRONT),
            "view_right": lambda: self._standard_view(StandardView.RIGHT),
        }
        handlers.get(key, lambda: self.set_hint(f"{key} is not available yet."))()

    def rebuild_everything(self) -> None:
        """Replay the whole feature history, ignoring any adopted geometry.

        Opening a saved project adopts the shapes stored with it, so an edit
        builds only itself. This is how to ask for the honest replay instead --
        and how to find out that a history no longer reproduces what was saved,
        which is worth knowing before it is discovered by accident.
        """
        self.set_hint("Rebuilding every feature…")
        self.rebuilder.drop_baseline()
        self.rebuild(force=True)

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
        self.close_tool_flyout()
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
        self.stage.remove_overlay(panel)
        panel.hide()
        panel.deleteLater()
        self._search = None

    def _run_searched(self, key: str) -> None:
        self.close_search()
        self.run_action(key)

    def export_model(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from ..kernel.io_formats import export_shapes

        # Items, not bodies: a group has to reach the slicer as one object.
        items = self.document.export_items()
        if not items:
            self.set_hint("There is nothing to export yet.")
            return
        path, selected_filter = QFileDialog.getSaveFileName(
            self, "Export", self.document.title, EXPORT_FILTER_TEXT,
        )
        if not path:
            return
        path = resolved_export_path(path, selected_filter)
        try:
            export_shapes(items, path)
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

        from ..kernel.io_formats import import_filter

        path, _filter = QFileDialog.getOpenFileName(
            self, "Import a 3D model", "", import_filter()
        )
        if not path:
            return
        self.choose_import(path)

    def choose_import(self, path: str):
        """Show what *path* contains and import the objects the user ticks.

        A file with one object imports straight away. Anything with more
        opens a picker listing its objects -- for a SimpleCAD project, its
        groups and bodies as the model tree shows them -- so one part can be
        taken from a large project without merging the whole of it. Returns
        the picker, if one was opened.
        """
        from ..core.errors import translate
        from ..core.project import inspect_tree, read_thumbnail
        from ..kernel.importing import forget, read_bodies
        from .dialogs.import_picker import ImportPicker

        native = path.lower().endswith(".scad3")
        try:
            if native:
                nodes = inspect_tree(path)
                thumbnail = read_thumbnail(path)
            else:
                forget(path)
                nodes = [{"kind": "body", "name": body.name or f"Object {index + 1}",
                          "key": index, "children": [],
                          "detail": getattr(body, "note", "") or ""}
                         for index, body in enumerate(read_bodies(path))]
                thumbnail = None
        except Exception as exc:  # noqa: BLE001 - report file and parser errors
            self._report_import_failure(path, translate(exc, "import"))
            return None
        if not nodes:
            self.set_hint("This file contains no objects to import.")
            return None
        if len(nodes) == 1 and not nodes[0]["children"]:
            self.import_path(path, items=[nodes[0]["key"]], _inspected=True)
            return None
        picker = ImportPicker(self, f"Import from {os.path.basename(path)}", nodes,
                              thumbnail=thumbnail)
        picker.chosen.connect(lambda keys: self.import_path(path, items=keys, _inspected=True))
        picker.setAttribute(Qt.WA_DeleteOnClose, True)
        # open(), not exec(): no nested event loop to stall the geometry
        # process, autosave or a rebuild in flight.
        picker.open()
        self._import_picker = picker
        return picker

    def import_path(self, path: str, object_key=None, *, items=None, _inspected=False) -> list[str]:
        """Import *path*, returning the names of the bodies it created.

        Split out from :meth:`import_model` so the import can be driven without
        a file dialog -- by a scripted check, by a drop, or by anything else
        that already knows which file it wants.
        """
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QApplication

        from ..core.errors import translate
        from ..kernel.importing import EmbeddedImportFeature, forget, read_bodies
        from ..kernel.io_formats import import_filter  # noqa: F401

        if path.lower().endswith(".scad3"):
            return self._import_native_project(path, object_key, items=items)
        if items is None and object_key is not None:
            items = [object_key]

        # The file may have changed since it was last read in this session.
        if not _inspected:
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
            chosen = (list(enumerate(bodies)) if items is None
                      else [(int(key), bodies[int(key)]) for key in items])
            if not chosen:
                raise ValueError("The file contains no objects to import.")
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            self._report_import_failure(path, translate(exc, "import"))
            return []
        finally:
            QApplication.restoreOverrideCursor()

        offset = self._placement_for([body.shape for _index, body in chosen])
        self.history.record("Import")
        created = []
        from ..core.geometry_service import serialise_shape
        for _index, body in chosen:
            name = self.document.unique_name(body.name or "Imported")
            asset = self.document.add_asset(serialise_shape(body.shape))
            feature = EmbeddedImportFeature(
                inputs={
                    "asset": asset,
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

    def _import_native_project(self, path: str, object_key=None, items=None) -> list[str]:
        """Clone the editable history of chosen objects from a native project.

        *items* are body ids or names and group names, as the import picker
        returns them; a group brings its members and stays a group. Each
        object arrives as ordinary editable history -- features, parameters,
        textures, appearance -- with no link back to the source file.
        """
        from ..core.errors import CadError, translate
        from ..core.geometry_service import serialise_shape
        from ..core.model_clipboard import copy_fragment
        from ..core.project import load
        from ..core.rebuild import Rebuilder

        try:
            source, _cached = load(path)
            if items is None and object_key is not None:
                items = [object_key]
            if items is None:
                # Everything: every top-level group and loose body, so the
                # source's grouping comes across too.
                chosen = [name for _kind, name in source.root_items()]
            else:
                by_id = {body.id: body.name for body in source.bodies.values()}
                chosen = []
                for key in items:
                    name = by_id.get(key, key)
                    if name not in source.bodies and name not in source.groups:
                        raise CadError(f"'{key}' is not in {os.path.basename(path)}.")
                    chosen.append(name)
            # A group already brings its members; do not list them twice.
            inside = set(source.expand([n for n in chosen if n in source.groups]))
            chosen = [n for n in chosen if n in source.groups or n not in inside]
            names = source.expand(chosen)
            if any(source.body(name).shape is None for name in names):
                report = Rebuilder(source).rebuild()
                if not report.ok:
                    raise CadError("The selected model could not be rebuilt from its source project.")
            if any(source.body(name).shape is None for name in names):
                raise CadError("The selected model has no usable geometry.")
            from ..kernel.occ import bounding_box as bounds_of
            bounds = [bounds_of(source.body(name).shape) for name in names]
            low = tuple(min(pair[0][axis] for pair in bounds) for axis in range(3))
            high = tuple(max(pair[1][axis] for pair in bounds) for axis in range(3))
            fragment = copy_fragment(source, chosen, bounds=(low, high))
            assets = {}
            source_builder = source._rebuilder or Rebuilder(source)
            source_builder._prepare()
            for entry in fragment.features:
                if entry["type"] == "embedded_import":
                    sha = entry["inputs"]["asset"]
                    assets[sha] = source._all_assets[sha]
                elif entry["type"] == "import":
                    feature = source.feature(entry["id"])
                    outputs = source_builder._get(feature.id)
                    shape = outputs[feature.outputs[0]]
                    blob = serialise_shape(shape)
                    import hashlib
                    sha = hashlib.sha256(blob).hexdigest()
                    assets[sha] = blob
                    entry["type"] = "embedded_import"
                    entry["inputs"] = {"asset": sha}
            created = self._paste_model_fragment(fragment, "Import", assets,
                                                 keep_parameter_names=True)
            self.set_hint(f"Imported {', '.join(created)} from {os.path.basename(path)}.")
            return created
        except Exception as exc:  # noqa: BLE001 - never leave a partial import
            self._report_import_failure(path, translate(exc, "import"))
            return []

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
        token = {"epoch": self._edit_epoch, "autosave": False}
        if self.geometry.save(self.document, path, thumbnail=self._thumbnail(), token=token):
            self.set_hint("Saving project…")
        else:
            self.set_hint("The geometry engine is unavailable; the project could not be saved. Retry to restart it.")

    def _on_project_saved(self, message):
        token = message.get("token") or {}
        if message.get("error"):
            self.set_hint(f"Could not save: {message['error']}")
            return
        if message.get("generation") != self.geometry._generation:
            return
        if token.get("autosave"):
            self.set_hint("Autosaved.")
            return
        from ..core.settings import remember_file
        path = message["path"]
        self._project_path = path
        self.document.title = os.path.splitext(os.path.basename(path))[0]
        if token.get("epoch") == self._edit_epoch:
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
        if cached and getattr(document, "_cache_complete", False):
            # The saved geometry came from a consistent build, so show it
            # as-is, and let it stand in for the history that made it -- here
            # and in the worker. Replaying that history instead is the
            # difference between opening instantly and waiting out every thread
            # in the model, and on a model whose features no longer reproduce
            # exactly it is the difference between keeping the part and losing
            # it. Editing anything already in the timeline drops the adopted
            # geometry and replays the history honestly; Rebuild All asks for
            # that outright.
            self.refresh_view()
            self.browser.refresh()
            self.geometry.sync_cached(document)
            if not document._geometry_store.entries:
                self.rebuilder.adopt_baseline()
        else:
            self.set_hint("Restoring the missing geometry…")
            self.rebuild()
        from ..core.settings import remember_file

        remember_file(path)
        self.stage.viewport.fit_all()
        self.set_hint(f"Opened {os.path.basename(path)}")

    def set_document(self, document, path: str | None = None) -> None:
        """Replace the open document, clearing anything left on screen."""
        self._cancel_nudge()
        self.close_tool_panels()
        self.stage.viewport.clear_ghost()
        self.stage.viewport.clear_selection()
        for presentation in self._presentations.values():
            self.stage.viewport.erase(presentation)
        self._presentations.clear()
        self._shown.clear()
        self._display_base.clear()

        self.document = document
        self.geometry.reset_document(document)
        self._select_after_rebuild = []
        self._stale.clear()
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
        """Say what to do next -- and say nothing at all when it already says it.

        The hover path calls this on every mouse move that finds geometry, and
        over a single face it is the same sentence every time: measured at
        twelve calls carrying one distinct string across one sweep. Each of the
        eleven repeats re-ran ``_layout_overlays``, which is the expensive half.

        Compared against ``full_text`` rather than ``text``: :class:`Hint` keeps
        the whole string and pushes an elided one to the label, so ``text``
        would compare against the trimmed version and let a repeat through
        whenever the line was too long to fit.
        """
        if self.stage.hint.full_text() == text:
            return
        self.stage.hint.setText(text)
        self.stage._layout_overlays()

    def _on_hover(self, description) -> None:
        if description and not self.selection.count:
            self.set_hint(f"{description['kind'].title()} — click to select")

    def _on_selection(self) -> None:
        if self._expanding:
            return
        if not self._nudge_refreshing:
            self._cancel_nudge()
        self.selection.refresh()
        self._expand_selection_to_groups()
        # Only whole bodies light their rows: with a face picked, clicking the
        # row must still change the selection to the body.
        self.browser.show_selected(self.selection.bodies if self.selection.only_bodies else [])
        self.refresh_context_bar()
        self._probe_edge_finish()
        self._retell_open_tools()
        if self.selection.count:
            # The context bar already carries the selection summary. Keep the
            # bottom-left chip for instructions and results instead of saying
            # the same thing twice.
            self.set_hint("" if self.context_bar.isVisible() else self.selection.summary())
        else:
            self.set_hint("Pick a shape from the left, or select geometry to act on it.")

    def _probe_edge_finish(self) -> None:
        """Ask the geometry process whether the picked edge was filleted.

        The answer lands in ``selection.edge_finish_known`` and refreshes the
        context bar, so "Edit fillet / chamfer" appears only on real blends.
        """
        from .selection import edge_finish_candidate, edge_probe_key

        known = getattr(self.selection, "edge_finish_known", None)
        if known is None:
            known = self.selection.edge_finish_known = {}
        if not edge_finish_candidate(self.selection) or not self.geometry.available:
            return
        if any(getattr(widget, "is_tool_panel", False) for widget, _ in self.stage.overlays):
            # A panel owns the preview channel while it is open; a probe must
            # never supersede its request.
            return
        key = edge_probe_key(self.selection)
        if key is None or key in known:
            return
        if len(known) > 256:
            known.clear()
        if not getattr(self, "_edge_probe_connected", False):
            self.geometry.previewed.connect(self._edge_probe_answer)
            self._edge_probe_connected = True
        self._edge_probe_serial = getattr(self, "_edge_probe_serial", 0) + 1
        token = f"edge-probe-{self._edge_probe_serial}"
        self._edge_probe_pending = (token, key)
        pick = self.selection.picks[0]
        self.geometry.preview({"__inspect_edge": {
            "body": str(pick.body), "pick": pick.reference(self.document).to_dict(),
        }}, token)

    def _edge_probe_answer(self, message) -> None:
        pending = getattr(self, "_edge_probe_pending", None)
        if pending is None or message.get("token") != pending[0]:
            return
        self._edge_probe_pending = None
        if message.get("failed"):
            return   # superseded or the worker restarted: no answer either way
        found = bool(message.get("inspection")) and not message.get("error")
        self.selection.edge_finish_known[pending[1]] = found
        if found:
            self.refresh_context_bar()

    def _retell_open_tools(self) -> None:
        """Let an open tool panel re-read the selection.

        Panels are built once, at activation, so a tool opened before its
        subject was picked used to stay stuck on "select a face" -- and Thread
        in particular would then commit a feature carrying no size. Driven from
        here rather than from a signal the panel subscribes to, because
        ``close_tool_panels`` removes a panel from ``stage.overlays`` before
        tearing it down, so a dying panel is never told.
        """
        for widget, _anchor in list(self.stage.overlays):
            if not getattr(widget, "is_tool_panel", False):
                continue
            listener = getattr(widget, "on_selection_changed", None)
            if listener is None:
                continue
            try:
                listener()
            except Exception:  # noqa: BLE001 - never break selection over a panel
                pass

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
        narrow = self.stage.width() < 900
        if narrow != self._browser_narrow:
            self._browser_narrow = narrow
            self.browser.set_collapsed(
                True if narrow else not self._browser_preferred_expanded,
                emit=False,
            )
        if self.context_bar.isVisible():
            self.refresh_context_bar()

    def _browser_collapsed(self, collapsed: bool) -> None:
        """Remember an explicit inspector choice without treating reflow as one."""
        from ..core import settings

        self._browser_preferred_expanded = not collapsed
        settings.set_ui_preference("browser_expanded", not collapsed)
        self.stage._layout_overlays()

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
