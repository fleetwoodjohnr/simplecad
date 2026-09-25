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
from ..viewport.occt_view import SelectionMode
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
        self._validated_feature = None
        self._pending_feature = None
        self._validated_revision = None
        self._listening_for_edits = True
        viewport = self.window_.stage.viewport
        self._previous_selection_modes = viewport.selection_modes
        viewport.set_selection_modes((SelectionMode.BODY,))
        viewport.set_additive_selection(True)
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

        self.retry = GhostButton("Retry preview")
        self.retry.clicked.connect(self.preview)
        self.retry.hide()
        self.add_widget(self.retry)
        self.window_.geometry.started.connect(self._model_changed)

        self._describe()
        self.preview()

    # -- state -----------------------------------------------------------
    @property
    def target(self) -> str:
        return self._bodies[0] if self._bodies else ""

    @property
    def tools(self) -> list[str]:
        return self._bodies[1:]

    def on_selection_changed(self) -> None:
        """Keep the target order while adding or removing live viewport picks."""
        selected = self.selection.bodies
        bodies = [name for name in self._bodies if name in selected]
        bodies.extend(name for name in selected if name not in bodies)
        if bodies == self._bodies:
            return
        self._bodies = bodies
        self._describe()
        self.preview()

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
            self.set_subtitle(
                f"Target: {self.target}. Click another body to add a tool."
                if self._bodies else "Click a body to choose the target."
            )
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
        self._validated_feature = None
        self.retry.hide()
        self._preview_warning = ""
        self._pending_feature = self._feature()
        self.confirm.setEnabled(False)
        self.window_.stage.viewport.clear_ghost()
        self.warn(
            "Building the preview… this can take a few seconds on large parts."
            if self._pending_feature else ""
        )
        self._preview_controller.request(
            self._pending_feature.to_dict() if self._pending_feature else None
        )

    def _previewed(self, message: dict) -> None:
        from ...core.geometry_service import deserialise_shape

        viewport = self.window_.stage.viewport
        blob = message.get("shape")
        error = message.get("error")
        if not blob:
            viewport.clear_ghost()
            self._validated_feature = None
            self._preview_warning = ""
            if len(self._bodies) >= 2:
                self.warn(error or "The combine preview could not be built.")
            else:
                self.warn("")
            if hasattr(self, "confirm"):
                self.confirm.setEnabled(False)
            self.retry.setVisible(bool(self._pending_feature))
            return
        try:
            shape = deserialise_shape(blob)
            viewport.show_ghost(
                shape, self.window_.palette_.accent, transparency=0.12
            )
        except Exception as exc:  # noqa: BLE001 - a corrupt preview is not a commit
            self._validated_feature = None
            self._preview_warning = ""
            self.confirm.setEnabled(False)
            self.warn(f"The preview could not be displayed: {exc}")
            return
        self._validated_feature = self._pending_feature
        self._validated_revision = self.window_.geometry.latest_revision
        self._preview_warning = " ".join(str(warning) for warning in message.get("warnings", []))
        self.warn(self._preview_warning)
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
        if self._listening_for_edits:
            self.window_.geometry.started.disconnect(self._model_changed)
            self._listening_for_edits = False
        self._preview_controller.close()
        viewport = self.window_.stage.viewport
        viewport.clear_ghost()
        viewport.set_additive_selection(False)
        viewport.set_selection_modes(self._previous_selection_modes)

    def _model_changed(self) -> None:
        self.preview()

    def commit(self) -> None:
        if not _need(
            self.window_, len(self._bodies) >= 2,
            "Select two or more bodies to combine.",
        ):
            return
        if self._validated_feature is None or self._validated_revision != self.window_.geometry.latest_revision:
            self.warn("Wait for a successful preview before applying.")
            return
        warning = self._preview_warning
        feature = self._validated_feature
        # Stop listening before the commit announces its own rebuild.
        self.window_.geometry.started.disconnect(self._model_changed)
        self._listening_for_edits = False
        self.window_.add_feature(feature)
        label = dict((key, name) for key, name, _h in OPERATIONS)[self.operation]
        # Said out loud: a Join that keeps its tools leaves both bodies in the
        # browser, which looks exactly like a Join that did not happen.
        kept = (
            f" {', '.join(self.tools)} kept as separate "
            f"{'bodies' if len(self.tools) > 1 else 'body'}."
            if self.keep.isChecked() else ""
        )
        hint = (
            f"{label}: {', '.join(self.tools)} → {self.target}."
            + kept + (f" {warning}" if warning else "")
        )
        self.window_.cancel_tool()
        self.window_.set_hint(hint)


# Registered as one factory per entry point rather than by stacking decorators
# on the class: the registry hands a tool only the window, so the operation the
# user asked for has to be bound here or Join would open showing Subtract.
def _entry(key: str):
    def factory(window):
        return CombinePanel(window, key=key)

    return factory


for _key in KEYS:
    register_tool(_key)(_entry(_key))
