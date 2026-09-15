"""Combine: Subtract, Join and Intersect.

The kernel has had booleans since the beginning; what was missing was any way
to reach them. This is that way, and it is shaped like the same operation in
Fusion, SolidWorks and Onshape: pick the bodies, say which one is being cut,
choose what to do, and decide whether the tools survive it.

The one decision worth stating. **Target and tools are shown, not inferred
silently.** Selection order decides which body is the target, because it has to
decide something -- but a cut is destructive and asymmetric, so the panel names
which way round it is and offers Swap. Getting this wrong and finding out
afterwards is exactly the kind of mistake undo exists for and nobody enjoys.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QGridLayout, QWidget

from ...core.document import BodyRef
from ...kernel.operations import BooleanFeature
from ..theme import METRICS
from ..widgets.controls import GhostButton
from .base import FeaturePreviewController, ToolPanel
from .modeling import _need
from .registry import register_tool

#: How long to wait after a change before building the preview. A boolean over
#: two real parts is not free, and the panel must stay usable while it runs.
PREVIEW_DELAY_MS = 90

OPERATIONS = (
    ("cut", "Subtract", "Removes the tools from the target"),
    ("join", "Join", "Fuses them into one body"),
    ("intersect", "Intersect", "Keeps only what they share"),
)

#: Which operation each entry point starts on.
KEYS = {"subtract": "cut", "join": "join", "intersect": "intersect",
        "combine": "cut"}


class CombinePanel(ToolPanel):
    """Boolean two or more bodies together."""

    title = "Combine"
    confirm_label = "Apply"

    def __init__(self, window, palette=None, key: str = "subtract") -> None:
        self.selection = window.selection
        self.operation = KEYS.get(key, "cut")
        super().__init__(window, palette)

    # -- construction ----------------------------------------------------
    def build(self) -> None:
        self._bodies = list(self.selection.bodies)
        self._preview_controller = FeaturePreviewController(
            self, self._previewed, delay_ms=PREVIEW_DELAY_MS
        )

        self.add_section("Operation")
        chooser = QWidget()
        grid = QGridLayout(chooser)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(METRICS.space(1))
        self._buttons = {}
        for index, (key, label, _hint) in enumerate(OPERATIONS):
            button = GhostButton(label)
            button.setCheckable(True)
            button.setChecked(key == self.operation)
            button.clicked.connect(lambda _=False, k=key: self._choose(k))
            grid.addWidget(button, 0, index)
            self._buttons[key] = button
        self.add_widget(chooser)

        self.swap = GhostButton("Swap target and tool")
        self.swap.clicked.connect(self._swap)
        self.add_widget(self.swap)

        self.keep = QCheckBox("Keep tool bodies")
        self.keep.setCursor(Qt.PointingHandCursor)
        self.keep.stateChanged.connect(lambda _v: self.preview())
        self.add_widget(self.keep)

        self._describe()
        self.preview()

    # -- state -----------------------------------------------------------
    @property
    def target(self) -> str:
        return self._bodies[0] if self._bodies else ""

    @property
    def tools(self) -> list[str]:
        return self._bodies[1:]

    def _choose(self, key: str) -> None:
        self.operation = key
        for name, button in self._buttons.items():
            button.setChecked(name == key)
        self._describe()
        self.preview()

    def _swap(self) -> None:
        """Rotate which body is the target, keeping the rest as tools.

        A rotation rather than a two-way swap, so it still reaches every body
        when three or more are selected.
        """
        if len(self._bodies) >= 2:
            self._bodies = self._bodies[1:] + self._bodies[:1]
        self._describe()
        self.preview()

    def _describe(self) -> None:
        if len(self._bodies) < 2:
            self.set_subtitle("Select two or more bodies to combine.")
            self.swap.setEnabled(False)
            return
        self.swap.setEnabled(True)
        tools = ", ".join(self.tools)
        verb = {
            "cut": f"Removing {tools} from {self.target}",
            "join": f"Joining {tools} into {self.target}",
            "intersect": f"Keeping what {self.target} and {tools} share",
        }[self.operation]
        self.set_subtitle(f"{verb}. The result keeps the name {self.target}.")

    # -- preview ---------------------------------------------------------
    def preview(self) -> None:
        feature = self._feature()
        self._preview_controller.request(feature.to_dict() if feature else None)

    def _previewed(self, message: dict) -> None:
        from ...core.geometry_service import deserialise_shape

        viewport = self.window_.stage.viewport
        blob = message.get("shape")
        error = message.get("error")
        if not blob:
            viewport.clear_ghost()
            if len(self._bodies) >= 2:
                self.warn(error or "These bodies do not overlap in a usable way.")
            if hasattr(self, "confirm"):
                self.confirm.setEnabled(False)
            return
        self.warn("")
        viewport.show_ghost(
            deserialise_shape(blob), self.window_.palette_.accent, transparency=0.12
        )
        if hasattr(self, "confirm"):
            self.confirm.setEnabled(True)

    def _feature(self):
        if len(self._bodies) < 2:
            return None
        return BooleanFeature(
            inputs={
                "body": BodyRef(self.target),
                "tools": [BodyRef(name) for name in self.tools],
                "operation": self.operation,
                "keep_tool": self.keep.isChecked(),
            },
            outputs=[self.target],
        )

    # -- lifecycle -------------------------------------------------------
    def teardown(self) -> None:
        self._preview_controller.close()
        self.window_.stage.viewport.clear_ghost()

    def commit(self) -> None:
        if not _need(
            self.window_, len(self._bodies) >= 2,
            "Select two or more bodies to combine.",
        ):
            return
        self.window_.add_feature(
            BooleanFeature(
                inputs={
                    "body": BodyRef(self.target),
                    "tools": [BodyRef(name) for name in self.tools],
                    "operation": self.operation,
                    "keep_tool": self.keep.isChecked(),
                },
                outputs=[self.target],
            )
        )
        label = dict((key, name) for key, name, _h in OPERATIONS)[self.operation]
        self.window_.set_hint(
            f"{label}: {', '.join(self.tools)} → {self.target}."
        )
        self.window_.cancel_tool()


# Registered as one factory per entry point rather than by stacking decorators
# on the class: the registry hands a tool only the window, so the operation the
# user asked for has to be bound here or Join would open showing Subtract.
def _entry(key: str):
    def factory(window):
        return CombinePanel(window, key=key)

    return factory


for _key in KEYS:
    register_tool(_key)(_entry(_key))
