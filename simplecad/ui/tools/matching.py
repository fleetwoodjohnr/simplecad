"""Create Matching Part.

Select something threaded and ask for the part that fits it. The thread is read
off the feature history, so the size, hand and clearance are already known --
the user picks *what* they want, never *what size*.
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QGridLayout, QWidget

from ...core.document import BodyRef
from ...kernel.fasteners import (
    ApplyMatchingThreadFeature, MatchingBoltFeature, MatchingNutFeature, complement,
)
from ...kernel.thread_specs import by_designation, clearance_presets
from ..theme import METRICS
from ..widgets.controls import GhostButton
from .base import ToolPanel
from .registry import register_tool


@register_tool("matching_part")
class MatchingPartPanel(ToolPanel):
    title = "Create matching part"
    confirm_label = "Create"
    width = 316

    KINDS = (
        ("bolt", "Bolt"),
        ("nut", "Nut"),
        ("hole", "Threaded hole"),
        ("apply", "Apply to selection"),
    )

    def build(self) -> None:
        self.selection = self.window_.selection
        self.kind = "bolt"
        self.thread = self._detect_thread()

        if self.thread is None:
            self.set_subtitle(
                "No thread found yet. Create one first, then come back — the "
                "matching part is built from it."
            )
        else:
            size = by_designation(self.thread["designation"])
            pairing = complement(size, existing_internal=self._existing_is_internal())
            self.set_subtitle(
                f"Found {self.thread['designation']} on {self.thread['feature']}. "
                f"The matching part needs a {pairing.describe()}."
            )

        self.add_section("What to create")
        chooser = QWidget()
        grid = QGridLayout(chooser)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(METRICS.space(1))
        self._buttons = {}
        for index, (key, label) in enumerate(self.KINDS):
            button = GhostButton(label)
            button.setCheckable(True)
            button.setChecked(key == self.kind)
            button.clicked.connect(lambda _=False, k=key: self.choose(k))
            grid.addWidget(button, index // 2, index % 2)
            self._buttons[key] = button
        self.add_widget(chooser)

        self.add_section("Size")
        self.sizes = QComboBox()
        for size in _candidate_sizes(self.thread):
            self.sizes.addItem(size.describe(), size.designation)
        if self.thread is not None:
            index = self.sizes.findData(self.thread["designation"])
            if index >= 0:
                self.sizes.setCurrentIndex(index)
        self.add_widget(self.sizes)

        self.head = QComboBox()
        self.head.addItem("Hex head", "hex")
        self.head.addItem("Socket cap", "socket")
        self.add_widget(self.head)

        self.add_field("length", "Length", 20.0)

        self.add_section("Printable clearance")
        self.clearance = QComboBox()
        for key, entry in clearance_presets().items():
            self.clearance.addItem(
                f"{entry['label']} — {entry['clearance']:.2f} mm", key
            )
        self.clearance.setCurrentIndex(1)
        self.add_widget(self.clearance)

    # -- detection -------------------------------------------------------
    def _detect_thread(self) -> dict | None:
        document = self.window_.document
        for name in self.selection.bodies:
            found = document.threads_on(name)
            if found:
                return found[0]
        return document.any_thread()

    def _existing_is_internal(self) -> bool:
        """Is the thread we are matching a hole?"""
        faces = self.selection.round_faces()
        if faces:
            return bool(faces[0].info.internal)
        # A threaded hole feature implies an internal thread.
        return self.thread is not None and self.thread["type"] == "hole"

    def choose(self, key: str) -> None:
        self.kind = key
        for name, button in self._buttons.items():
            button.setChecked(name == key)
        self.head.setVisible(key == "bolt")
        self.fields["length"].setEnabled(key in ("bolt", "hole"))

    # -- creation --------------------------------------------------------
    def commit(self) -> None:
        designation = self.sizes.currentData()
        if not designation:
            self.warn("Pick a thread size first.")
            return
        clearance = self.clearance.currentData()
        document = self.window_.document

        if self.kind == "bolt":
            feature = MatchingBoltFeature(
                inputs={
                    "designation": designation,
                    "length": self.expression("length", "20"),
                    "head": self.head.currentData(),
                }
            )
            feature.name = document.unique_name("Bolt")
            feature.outputs = [feature.name]
        elif self.kind == "nut":
            feature = MatchingNutFeature(
                inputs={"designation": designation, "clearance": clearance}
            )
            feature.name = document.unique_name("Nut")
            feature.outputs = [feature.name]
        elif self.kind == "apply":
            faces = self.selection.round_faces()
            if not faces:
                self.warn(
                    "Select the round face to thread — a shaft or a hole on the "
                    "other part."
                )
                return
            pick = faces[0]
            feature = ApplyMatchingThreadFeature(
                inputs={
                    "body": BodyRef(pick.body),
                    "face": pick.reference(document),
                    "designation": designation,
                    "clearance": clearance,
                },
                outputs=[pick.body],
            )
        else:                                    # threaded hole
            faces = self.selection.planar_faces()
            if not faces:
                self.warn("Select the flat face to drill the threaded hole into.")
                return
            from ...kernel.operations import HoleFeature

            pick = faces[0]
            size = by_designation(designation)
            feature = HoleFeature(
                inputs={
                    "body": BodyRef(pick.body),
                    "face": pick.reference(document),
                    "diameter": size.diameter,
                    "style": "threaded",
                    "depth_mode": "blind",
                    "depth": self.expression("length", "20"),
                    "clearance": clearance,
                    "position": tuple(pick.info.center),
                },
                outputs=[pick.body],
            )

        self.window_.add_feature(feature)
        self.window_.cancel_tool()


def _candidate_sizes(thread) -> list:
    """Sizes to offer: the detected one first, then the common metric range."""
    from ...kernel.thread_specs import load_sizes

    common = [s for s in load_sizes() if s.code == "iso_metric" and s.series == "coarse"]
    if thread is None:
        return common
    detected = by_designation(thread["designation"])
    if detected is None:
        return common
    return [detected] + [s for s in common if s.designation != detected.designation]
