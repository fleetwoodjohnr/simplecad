"""Command search.

Press `S` and type. Everything the application can do is reachable from one
place, which is what lets the visible interface stay small: the tool rail only
has to carry the things people reach for constantly, and the long tail lives
here instead of in nested menus.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QSizePolicy, QVBoxLayout, QWidget,
)

from ..icons import icon
from ..theme import METRICS, Palette
from ..widgets.controls import FloatingCard


@dataclass(frozen=True)
class Command:
    key: str
    label: str
    icon: str
    hint: str = ""
    keywords: str = ""
    category: str = ""
    shortcut: str = ""

    def matches(self, query: str) -> int:
        """Score against *query*; 0 means no match, higher is better."""
        if not query:
            return 1
        haystack = f"{self.label} {self.keywords} {self.hint}".lower()
        label = self.label.lower()
        if label.startswith(query):
            return 100
        if query in label:
            return 60
        if query in haystack:
            return 30
        # Subsequence match, so "thrcon" finds "Threaded Connection".
        position = 0
        for character in query:
            position = haystack.find(character, position) + 1
            if position == 0:
                return 0
        return 10


def catalogue() -> list[Command]:
    """Everything reachable from search."""
    return [
        Command("shapes", "Add a shape", "box", "Box, cylinder, sphere and more",
                "primitive new create cube"),
        Command("shape_triangle", "Triangle", "triangle",
                "A three-sided prism", "polygon prism three"),
        Command("shape_pentagon", "Pentagon", "pentagon",
                "A five-sided prism", "polygon prism five"),
        Command("shape_hexagon", "Hexagon", "hexagon",
                "A six-sided prism, sized across flats", "polygon prism nut six hex"),
        Command("shape_octagon", "Octagon", "octagon",
                "An eight-sided prism", "polygon prism eight"),
        Command("shape_polygon_prism", "Polygon", "polygon",
                "A prism with any number of sides", "ngon prism sides"),
        Command("shape_star", "Star", "star",
                "A five-point decorative prism", "shape character icon"),
        Command("shape_heart", "Heart", "heart",
                "A heart-shaped decorative prism", "shape character love icon"),
        Command("shape_cross", "Cross", "cross",
                "A cross-shaped decorative prism", "shape character plus icon"),
        Command("shape_crescent", "Crescent", "crescent",
                "A crescent-shaped decorative prism", "shape character moon icon"),
        Command("shape_lightning", "Lightning", "lightning",
                "A lightning-bolt decorative prism", "shape character bolt icon"),
        Command("vent", "Vent", "vent",
                "A hex grid of holes -- a fan grille",
                "fan grille hex holes perforate grid cooling exhaust"),
        Command("pushpull", "Pull face", "extrude", "Move a face along its normal",
                "push extrude offset"),
        Command("text", "Add text", "text",
                "Raise or engrave text on a flat face",
                "label letters words emboss deboss carve"),
        Command("move", "Move", "move", "Translate and rotate a body",
                "translate rotate position"),
        Command("arrange", "Arrange objects", "pattern",
                "Equalize clear spacing and keep objects in line",
                "distribute align row gap spacing multiple"),
        Command("rotate", "Rotate", "rotate", "Turn a body about an axis",
                "turn spin orient angle"),
        Command("scale", "Scale", "scale", "Resize a body by a factor",
                "resize bigger smaller shrink grow size"),
        Command("split", "Split", "split",
                "Cut a body into two independent parts",
                "cut divide halve section separate print plate"),
        Command("subtract", "Subtract", "subtract",
                "Cut one body out of another",
                "boolean cut remove difference minus combine hole carve"),
        Command("join", "Join", "union",
                "Fuse bodies into one", "boolean union merge fuse combine weld"),
        Command("intersect", "Intersect", "intersect",
                "Keep only what two bodies share",
                "boolean common overlap combine"),
        Command("group", "Group", "group",
                "Handle several objects as one, without joining them",
                "assembly collect together organise"),
        Command("ungroup", "Ungroup", "ungroup",
                "Break a group back into its objects", "separate dissolve"),
        Command("duplicate", "Duplicate", "duplicate", "Copy the selection",
                "copy clone repeat"),
        Command("copy", "Copy", "duplicate", "Copy whole bodies or groups",
                "clipboard control c clone"),
        Command("paste", "Paste", "duplicate", "Paste beside the model",
                "clipboard control v insert clone"),
        Command("hide", "Hide selection", "eye_off", "", "invisible conceal"),
        Command("stack", "Stack", "stack", "Seat one face flat on another",
                "align mate assemble"),
        Command("concentric", "Concentric", "concentric",
                "Line up two round features", "align axis shaft hole"),
        Command("center", "Center", "align", "Centre one face on another", "align"),
        Command("place_on_face", "Place on Face", "align",
                "Position a body from a face edge or centre",
                "align x y left right bottom top offset"),
        Command("section_replace", "Section Replace", "cut",
                "Clear a silhouette and integrate an aligned insert",
                "vent grille replace merge opening panel inset"),
        Command("hole", "Hole", "hole",
                "Simple, counterbore, countersink or threaded", "drill bore"),
        Command("thread", "Thread", "thread",
                "Thread a shaft or a hole", "screw bolt nut metric iso"),
        Command("threaded_connection", "Create Threaded Connection", "thread",
                "A matched printable thread pair across two parts",
                "screw bolt nut pair mating"),
        Command("clip_joint", "Create Clip Joint", "clip",
                "Align two flat faces and place reusable internal clips",
                "snap latch connector socket removable uniform row grid"),
        Command("align_threaded", "Align Threaded Parts", "thread",
                "Seat one part into the other and thread both at once",
                "assemble mate screw preview assembled"),
        Command("align_stack", "Align & Stack", "stack",
                "Stack two faces with an offset", "mate assemble"),
        Command("matching_part", "Create Matching Part", "thread",
                "A bolt, nut or threaded hole that fits an existing thread",
                "bolt nut screw fastener mating pair"),
        Command("fillet", "Fillet", "fillet", "Round off selected edges",
                "round corner"),
        Command("chamfer", "Chamfer", "chamfer", "Bevel selected edges", "bevel"),
        Command("shell", "Hollow", "shell",
                "Hollow a body out to a wall thickness",
                "shell bucket wall thin hollow empty"),
        Command("fit", "Fit & Clearance", "settings",
                "Tune clearances to your printer with a calibration model",
                "tolerance calibrate press snug sliding"),
        Command("print", "3D Print", "print",
                "Place on the plate, check printability, export", "slice 3mf stl"),
        Command("sketch", "Sketch", "sketch",
                "Draw a profile and extrude it", "2d profile rectangle circle"),
        Command("sweep", "Sweep", "extrude", "Drag a profile along a path", "path"),
        Command("loft", "Loft", "extrude", "Blend between profiles", "between"),
        Command("mirror", "Mirror", "align", "Reflect across a plane", "flip"),
        Command("measure", "Measure", "measure",
                "Length, distance, angle, area, volume",
                "dimension distance size how big"),
        Command("measure_points", "Measure point to point", "measure",
                "Click two points on the model for the distance between them",
                "distance corner centre center hole spacing between two points"),
        Command("view_grid", "Ground grid", "grid",
                "Show or hide the ground plane", "grid floor plane depth"),
        Command("view_xray", "X-ray model", "xray",
                "See and select faces inside the model",
                "transparent hidden internal face see through"),
        Command("view_fit", "Zoom to fit", "fit", "Frame the whole model", "zoom all"),
        Command("view_selection", "Zoom to selection", "fit", "", "zoom"),
        Command("view_iso", "Isometric view", "ortho", "", "view 3d"),
        Command("view_top", "Top view", "ortho", "", "view"),
        Command("view_front", "Front view", "ortho", "", "view"),
        Command("view_right", "Right view", "ortho", "", "view"),
        Command("projection", "Toggle perspective", "perspective", "", "ortho camera"),
        Command("theme", "Light / dark", "moon", "Switch the theme", "appearance"),
        Command("save", "Save", "save", "Save this project", "store write"),
        Command("save_as", "Save as…", "save", "", "store write"),
        Command("open", "Open…", "folder", "Open a project", "load"),
        Command("import", "Import…", "import",
                "Bring in a STEP, STL, OBJ, 3MF, IGES or glTF file",
                "open load bring step stp stl obj 3mf iges brep gltf model file"),
        Command("export", "Export…", "export", "STEP, STL, 3MF or OBJ",
                "3mf stl step obj print save"),
        Command("undo", "Undo", "undo", "", "back"),
        Command("redo", "Redo", "redo", "", "forward"),
        Command("delete", "Delete selection", "trash", "", "remove"),
    ]


class CommandSearch(FloatingCard):
    """A filterable palette of every command."""

    chosen = Signal(str)
    dismissed = Signal()

    def __init__(self, palette: Palette, parent=None) -> None:
        super().__init__(palette, parent)
        self._palette = palette
        self._commands = catalogue()
        from ...core.settings import shortcuts

        self._bindings = shortcuts()
        self.setFixedWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            METRICS.space(2), METRICS.space(2), METRICS.space(2), METRICS.space(2)
        )
        layout.setSpacing(METRICS.space(1.5))

        top = QHBoxLayout()
        self.eyebrow = QLabel("COMMAND PALETTE")
        self.key_hint = QLabel("↑↓  Navigate    Enter  Run    Esc  Close")
        top.addWidget(self.eyebrow)
        top.addStretch(1)
        top.addWidget(self.key_hint)
        layout.addLayout(top)

        self.query = QLineEdit()
        self.query.setObjectName("CommandQuery")
        self.query.setPlaceholderText("Search tools, actions, and views…")
        self.query.setMinimumHeight(METRICS.control_height_lg)
        self.query.textChanged.connect(self.refilter)
        layout.addWidget(self.query)

        self.results = QListWidget()
        self.results.setFrameShape(QListWidget.NoFrame)
        self.results.setFixedHeight(340)
        self.results.itemActivated.connect(self._activate)
        self.results.itemClicked.connect(self._activate)
        layout.addWidget(self.results)

        self.apply_palette(palette)
        self.refilter("")

    @staticmethod
    def _category(command: Command) -> str:
        if command.category:
            return command.category
        key = command.key
        if key.startswith("view_") or key in {"projection", "theme"}:
            return "View"
        if key in {"save", "save_as", "open", "import", "export"}:
            return "File"
        if key in {
            "duplicate", "copy", "paste", "group", "ungroup", "hide",
            "delete", "undo", "redo",
        }:
            return "Edit"
        if key.startswith("shape_") or key in {
            "shapes", "text", "sketch", "threaded_connection", "clip_joint",
            "matching_part", "sweep", "loft",
        }:
            return "Create"
        if key.startswith("measure") or key in {"fit", "print"}:
            return "Inspect"
        return "Modify"

    def _shortcut(self, command: Command) -> str:
        if command.shortcut:
            return command.shortcut
        aliases = {
            "pushpull": "extrude", "view_fit": "fit_view",
            "view_xray": "xray", "save_as": "save_as",
        }
        return self._bindings.get(aliases.get(command.key, command.key), "")

    def apply_palette(self, palette: Palette) -> None:
        super().apply_palette(palette)
        self._palette = palette
        self.eyebrow.setStyleSheet(
            f"color:{palette.accent_hover}; font-size:9px; font-weight:700;"
            "letter-spacing:1px;"
        )
        self.key_hint.setStyleSheet(
            f"color:{palette.text_faint}; font-size:10px;"
        )
        if hasattr(self, "query"):
            self.refilter(self.query.text())

    def refilter(self, text: str) -> None:
        from PySide6.QtCore import QSize

        query = text.strip().lower()
        scored = [
            (command.matches(query), command) for command in self._commands
        ]
        scored = [(score, c) for score, c in scored if score > 0]
        scored.sort(key=lambda item: (-item[0], item[1].label))

        self.results.clear()
        for _score, command in scored:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, command.key)
            item.setSizeHint(QSize(0, 50))
            self.results.addItem(item)
            self.results.setItemWidget(item, self._result_widget(command))
        if not scored:
            item = QListWidgetItem("No matching commands")
            item.setFlags(Qt.NoItemFlags)
            item.setSizeHint(QSize(0, 56))
            self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)

    def _result_widget(self, command: Command) -> QWidget:
        palette = self._palette
        row = QWidget(self.results)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 4, 10, 4)
        layout.setSpacing(10)
        mark = QLabel()
        mark.setFixedSize(24, 24)
        mark.setPixmap(icon(command.icon, palette.text_muted, 20).pixmap(20, 20))
        layout.addWidget(mark)
        copy = QVBoxLayout()
        copy.setContentsMargins(0, 0, 0, 0)
        copy.setSpacing(1)
        title = QLabel(command.label)
        title.setStyleSheet(f"color:{palette.text}; font-size:12.5px; font-weight:600;")
        detail = QLabel(
            f"{self._category(command)}  ·  {command.hint}"
            if command.hint else self._category(command)
        )
        detail.setStyleSheet(f"color:{palette.text_faint}; font-size:10.5px;")
        copy.addWidget(title)
        copy.addWidget(detail)
        layout.addLayout(copy, 1)
        shortcut = self._shortcut(command)
        if shortcut:
            badge = QLabel(shortcut)
            badge.setAlignment(Qt.AlignCenter)
            badge.setStyleSheet(
                f"color:{palette.text_muted}; background:{palette.surface_sunken};"
                f"border:1px solid {palette.border}; border-radius:5px;"
                "padding:3px 6px; font-size:9.5px;"
            )
            badge.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            layout.addWidget(badge)
        return row

    def _activate(self, item: QListWidgetItem) -> None:
        key = item.data(Qt.UserRole)
        if key:
            self.chosen.emit(key)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key_Escape:
            self.dismissed.emit()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            item = self.results.currentItem()
            if item is not None:
                self._activate(item)
            return
        if key in (Qt.Key_Down, Qt.Key_Up):
            row = self.results.currentRow() + (1 if key == Qt.Key_Down else -1)
            self.results.setCurrentRow(max(0, min(row, self.results.count() - 1)))
            return
        super().keyPressEvent(event)

    def focus_query(self) -> None:
        self.query.clear()
        self.query.setFocus()
