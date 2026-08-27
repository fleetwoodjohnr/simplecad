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
    QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout,
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
        Command("vent", "Vent", "vent",
                "A hex grid of holes -- a fan grille",
                "fan grille hex holes perforate grid cooling exhaust"),
        Command("pushpull", "Pull face", "extrude", "Move a face along its normal",
                "push extrude offset"),
        Command("move", "Move", "move", "Translate and rotate a body",
                "translate rotate position"),
        Command("rotate", "Rotate", "rotate", "Turn a body about an axis",
                "turn spin orient angle"),
        Command("scale", "Scale", "scale", "Resize a body by a factor",
                "resize bigger smaller shrink grow size"),
        Command("split", "Split", "split",
                "Cut a body into two independent parts",
                "cut divide halve section separate print plate"),
        Command("group", "Group", "group",
                "Handle several objects as one, without joining them",
                "assembly collect together organise"),
        Command("ungroup", "Ungroup", "ungroup",
                "Break a group back into its objects", "separate dissolve"),
        Command("duplicate", "Duplicate", "duplicate", "Copy the selection",
                "copy clone repeat"),
        Command("hide", "Hide selection", "eye_off", "", "invisible conceal"),
        Command("stack", "Stack", "stack", "Seat one face flat on another",
                "align mate assemble"),
        Command("concentric", "Concentric", "concentric",
                "Line up two round features", "align axis shaft hole"),
        Command("center", "Center", "align", "Centre one face on another", "align"),
        Command("hole", "Hole", "hole",
                "Simple, counterbore, countersink or threaded", "drill bore"),
        Command("thread", "Thread", "thread",
                "Thread a shaft or a hole", "screw bolt nut metric iso"),
        Command("threaded_connection", "Create Threaded Connection", "thread",
                "A matched printable thread pair across two parts",
                "screw bolt nut pair mating"),
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
        Command("view_grid", "Ground grid", "grid",
                "Show or hide the ground plane", "grid floor plane depth"),
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
        self.setFixedWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            METRICS.space(2), METRICS.space(2), METRICS.space(2), METRICS.space(2)
        )
        layout.setSpacing(METRICS.space(1.5))

        self.query = QLineEdit()
        self.query.setPlaceholderText("Search commands…")
        self.query.setMinimumHeight(METRICS.control_height_lg)
        self.query.textChanged.connect(self.refilter)
        layout.addWidget(self.query)

        self.results = QListWidget()
        self.results.setFrameShape(QListWidget.NoFrame)
        self.results.setMinimumHeight(300)
        self.results.itemActivated.connect(self._activate)
        self.results.itemClicked.connect(self._activate)
        layout.addWidget(self.results)

        self.refilter("")

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
            item = QListWidgetItem(
                f"{command.label}    {command.hint}" if command.hint else command.label
            )
            item.setData(Qt.UserRole, command.key)
            item.setIcon(icon(command.icon, self._palette.text_muted, 18))
            item.setSizeHint(QSize(0, 36))
            self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)

    def _activate(self, item: QListWidgetItem) -> None:
        self.chosen.emit(item.data(Qt.UserRole))

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
