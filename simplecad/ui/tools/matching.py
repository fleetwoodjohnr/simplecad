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
from ...kernel.occ import make_transform, transformed
from ...kernel.thread_specs import by_designation, clearance_presets
from ..theme import METRICS
from ..widgets.controls import GhostButton
from .base import FeaturePreviewController, ToolPanel
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
        self.thread = self._detect_thread()
        self.kind = (
            "bolt" if self.thread is None or self._existing_is_internal() else "nut"
        )
        self._preview_valid = False
        self._preview_body: str | None = None
        self._placement = (0.0, 0.0, 0.0)
        self._preview = FeaturePreviewController(
            self, self._preview_answered, delay_ms=140
        )

        if self.thread is None:
            self.set_subtitle(
                "No thread found yet. Create one first, then come back — the "
                "matching part is built from it."
            )
        else:
            size = by_designation(self.thread["designation"])
            if size is None:
                self.set_subtitle("The selected feature names an unknown thread size.")
            else:
                pairing = complement(
                    size, existing_internal=self._existing_is_internal()
                )
                hand = "left-hand" if self.thread.get("left_hand") else "right-hand"
                self.set_subtitle(
                    f"Locked to {self.thread['designation']} on "
                    f"{self.thread['feature']}: {self.thread.get('form', 'printed')} "
                    f"form, {hand}. The mate needs a {pairing.describe()}."
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
        self.sizes.setEnabled(False)
        self.add_widget(self.sizes)

        self.head = QComboBox()
        self.head.addItem("Hex head", "hex")
        self.head.addItem("Socket cap", "socket")
        self.head.currentIndexChanged.connect(lambda _i: self.preview())
        self.add_widget(self.head)

        self.add_field("length", "Length", 20.0)

        self.add_section("Printable clearance")
        self.clearance = QComboBox()
        for key, entry in clearance_presets().items():
            self.clearance.addItem(
                f"{entry['label']} — {entry['clearance']:.2f} mm", key
            )
        source_clearance = (self.thread or {}).get("clearance", "normal")
        index = self.clearance.findData(source_clearance)
        self.clearance.setCurrentIndex(index if index >= 0 else 1)
        self.clearance.setEnabled(False)
        self.add_widget(self.clearance)
        self._apply_kind_compatibility()
        self.choose(self.kind)

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
        if self.thread is not None and self.thread.get("internal") is not None:
            return bool(self.thread["internal"])
        faces = self.selection.round_faces()
        return bool(faces and faces[0].info.internal)

    def _apply_kind_compatibility(self) -> None:
        has_thread = self.thread is not None and by_designation(
            self.thread.get("designation", "")
        ) is not None
        existing_internal = self._existing_is_internal()
        # A bolt supplies an external mate; a nut and threaded hole supply an
        # internal one.  Do not offer a same-polarity part that can never mate.
        allowed = {
            "bolt": has_thread and existing_internal,
            "nut": has_thread and not existing_internal,
            "hole": has_thread and not existing_internal,
            "apply": has_thread,
        }
        for key, button in self._buttons.items():
            button.setEnabled(allowed[key])

    def choose(self, key: str) -> None:
        if key in self._buttons and not self._buttons[key].isEnabled():
            return
        self.kind = key
        for name, button in self._buttons.items():
            button.setChecked(name == key)
        self.head.setVisible(key == "bolt")
        self.fields["length"].setEnabled(key in ("bolt", "hole"))
        self.preview()

    def on_selection_changed(self) -> None:
        # The source thread remains locked while the user selects the receiving
        # face. Re-detecting here would silently change the specification.
        self.preview()

    def _target_round_face(self):
        if self.thread is None:
            return None
        required_internal = not self._existing_is_internal()
        source = self.thread.get("body")
        return next(
            (
                face for face in self.selection.round_faces()
                if face.body != source and bool(face.info.internal) == required_internal
            ),
            None,
        )

    def _target_planar_face(self):
        source = (self.thread or {}).get("body")
        return next(
            (face for face in self.selection.planar_faces() if face.body != source),
            None,
        )

    def _build_feature(self, *, for_preview: bool = False):
        if self.thread is None:
            return None
        designation = self.thread.get("designation")
        if by_designation(str(designation)) is None:
            return None
        common = {
            "designation": designation,
            "clearance": self.thread.get("clearance", self.clearance.currentData()),
            "form": self.thread.get("form", "printed"),
            "left_hand": bool(self.thread.get("left_hand", False)),
        }
        document = self.window_.document
        placement = (0.0, 0.0, 0.0) if for_preview else self._placement
        if self.kind == "bolt":
            feature = MatchingBoltFeature(inputs={
                **common,
                "length": self.expression("length", "20"),
                "head": self.head.currentData(),
                "x": placement[0],
                "y": placement[1],
                "z": placement[2],
            })
            feature.name = document.unique_name("Bolt")
            feature.outputs = [feature.name]
            return feature
        if self.kind == "nut":
            feature = MatchingNutFeature(inputs={
                **common,
                "x": placement[0],
                "y": placement[1],
                "z": placement[2],
            })
            feature.name = document.unique_name("Nut")
            feature.outputs = [feature.name]
            return feature
        if self.kind == "apply":
            pick = self._target_round_face()
            if pick is None:
                return None
            return ApplyMatchingThreadFeature(
                inputs={
                    **common,
                    "body": BodyRef(pick.body),
                    "face": pick.reference(document),
                },
                outputs=[pick.body],
            )

        pick = self._target_planar_face()
        if pick is None:
            return None
        from ...kernel.operations import HoleFeature

        size = by_designation(designation)
        return HoleFeature(
            inputs={
                **common,
                "body": BodyRef(pick.body),
                "face": pick.reference(document),
                "diameter": size.diameter,
                "style": "threaded",
                "depth_mode": "blind",
                "depth": self.expression("length", "20"),
                "position": tuple(pick.info.center),
            },
            outputs=[pick.body],
        )

    def preview(self) -> None:
        self._preview_valid = False
        self.confirm.setEnabled(False)
        self.confirm.setText("Building preview…")
        feature = self._build_feature(for_preview=True)
        self._preview.request(feature.to_dict() if feature is not None else None)

    def _preview_answered(self, message: dict) -> None:
        from ...core.geometry_service import deserialise_shape

        blob = message.get("shape")
        shape = deserialise_shape(blob) if blob else None
        if shape is None:
            self._clear_preview()
            self.confirm.setText(self.confirm_label)
            if self.kind == "apply":
                default = "Select a round face on the other part with the opposite polarity."
            elif self.kind == "hole":
                default = "Select a flat face on the other part for the matching hole."
            else:
                default = "The physical matching thread could not be built."
            self.warn(message.get("error") or default)
            return

        self._clear_preview()
        viewport = self.window_.stage.viewport
        if self.kind in ("bolt", "nut"):
            # Generated fasteners are built around the origin. Put both their
            # preview and committed feature beside the current model so the new
            # part cannot be mistaken for a no-op hidden inside its mate.
            raw_shape = shape
            self._placement = self.window_._placement_for([raw_shape])
            if any(abs(value) > 1e-12 for value in self._placement):
                shape = transformed(
                    raw_shape, make_transform(translate=self._placement)
                )
        else:
            pick = (
                self._target_round_face()
                if self.kind == "apply"
                else self._target_planar_face()
            )
            self._preview_body = pick.body if pick is not None else None
            viewport.set_transparency(
                self.window_._presentations.get(self._preview_body), 0.88
            )
        viewport.show_ghost(shape, self.window_.palette_.accent, transparency=0.12)
        self._preview_valid = True
        self.confirm.setEnabled(True)
        self.confirm.setText(self.confirm_label)
        self.warn(" · ".join(message.get("warnings") or []))

    def _clear_preview(self) -> None:
        viewport = self.window_.stage.viewport
        viewport.clear_ghost()
        if self._preview_body:
            viewport.set_transparency(
                self.window_._presentations.get(self._preview_body), 0.0
            )
        self._preview_body = None

    def teardown(self) -> None:
        self._preview.close()
        self._clear_preview()

    # -- creation --------------------------------------------------------
    def commit(self) -> None:
        if not self._preview_valid:
            self.warn("Wait for a valid physical preview before creating the part.")
            return
        feature = self._build_feature()
        if feature is None:
            self.warn("The selected target is no longer compatible with this thread.")
            return
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
