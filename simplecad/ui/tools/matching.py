"""Create Matching Part.

Select something threaded and ask for the part that fits it. The thread is read
off the feature history, so the size, hand and clearance are already known --
the user picks *what* they want, never *what size*.

The panel opens on whatever the selection is asking for: a flat face on another
part means "drill and thread it here", a bore or a shaft means "thread that",
and nothing selected means a free-standing bolt or nut. Opening on a nut while
the user is plainly pointing at the face they want threaded is the panel
answering a question nobody asked, and it was the single thing that made the
obvious two-part workflow -- thread a post, select the post and the plate, get a
hole that fits -- read as broken.
"""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QGridLayout, QWidget

from ...core.document import BodyRef
from ...kernel.fasteners import (
    ApplyMatchingThreadFeature, MatchingBoltFeature, MatchingNutFeature, complement,
)
from ...kernel.occ import make_transform, transformed
from ...kernel.thread_specs import (
    by_designation, clearance_presets, effective_clearance_for,
)
from ..selection import WHOLE_OBJECT_KINDS, Picked
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
        ("apply", "Thread a face"),
    )

    def build(self) -> None:
        self.selection = self.window_.selection
        self.thread = self._detect_thread()
        self.kind = self._default_kind()
        self._preview_valid = False
        self._preview_body: str | None = None
        self._placement = (0.0, 0.0, 0.0)
        self._preview = FeaturePreviewController(
            self, self._preview_answered, delay_ms=140
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

        # Only shown, and only needed, when the face the mate goes on is not
        # already the size the thread wants. Off by default: opening somebody's
        # bore out by four millimetres is not something to do behind their back.
        self.resize = QCheckBox("Resize to suit the thread")
        self.resize.toggled.connect(lambda _on: self.preview())
        self.resize.setVisible(False)
        self.add_widget(self.resize)

        self.add_section("Printable clearance")
        self.clearance = QComboBox()
        for key, entry in clearance_presets().items():
            value = effective_clearance_for(key)
            self.clearance.addItem(
                f"{entry['label']} — {value:.2f} mm diameter · "
                f"{value / 2.0:.2f} per side", key
            )
        source_clearance = (self.thread or {}).get("clearance", "normal")
        index = self.clearance.findData(source_clearance)
        if index < 0 and isinstance(source_clearance, (int, float)):
            self.clearance.addItem(
                f"Custom — {source_clearance:.2f} mm diameter",
                source_clearance,
            )
            index = self.clearance.count() - 1
        self.clearance.setCurrentIndex(index if index >= 0 else 1)
        self.clearance.setEnabled(False)
        self.add_widget(self.clearance)
        self._apply_kind_compatibility()
        if any(button.isEnabled() for button in self._buttons.values()):
            self.choose(self.kind)
        else:
            # Nothing in the document to match. Say so and leave Create off,
            # rather than inviting a click that cannot go anywhere. ``choose``
            # declines disabled kinds, so without this the panel would open
            # blank -- no subtitle, and a live Create button.
            self._describe()
            self.confirm.setEnabled(False)

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

    def _source_size(self):
        return by_designation(str((self.thread or {}).get("designation", "")))

    def _default_kind(self) -> str:
        """The kind the current selection is asking for."""
        if self.thread is None or self._source_size() is None:
            return "bolt"
        if self._target_round_face() is not None:
            return "apply"
        if not self._existing_is_internal() and self._target_planar_face() is not None:
            return "hole"
        return "bolt" if self._existing_is_internal() else "nut"

    def _has_target(self, kind: str) -> bool:
        if kind == "apply":
            return self._target_round_face() is not None
        if kind == "hole":
            return self._target_planar_face() is not None
        return True

    def _apply_kind_compatibility(self) -> None:
        has_thread = self.thread is not None and self._source_size() is not None
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
        self._refresh_resize()
        self._describe()
        self.preview()

    def on_selection_changed(self) -> None:
        # The source thread remains locked while the user selects the receiving
        # face. Re-detecting here would silently change the specification. What
        # must follow the selection is which kinds have something to act on, and
        # which one the panel is currently offering.
        self._apply_kind_compatibility()
        if not self._has_target(self.kind):
            wanted = self._default_kind()
            if wanted != self.kind and self._has_target(wanted):
                self.choose(wanted)
                return
        self._refresh_resize()
        self._describe()
        self.preview()

    # -- targets ---------------------------------------------------------
    def _round_faces_available(self) -> list:
        """Round faces the mate could go on, whole bodies included.

        Selecting a tube and asking for its matching thread is how a cap gets
        made, and answering "select a round face" to that is pedantry: a tube
        has exactly one bore, so there is nothing to disambiguate and no reason
        to make anyone hunt for it.
        """
        from ...core.naming import sub_shapes
        from ...kernel.detect import analyse_cylinder

        # Memoised on the selection itself. Half a dozen methods ask this
        # question every time the selection moves, and walking every face of a
        # body that already carries a modelled thread -- which is hundreds of
        # them, each analysed -- once per question is a stall the user feels.
        signature = tuple(
            (pick.body, pick.kind, id(pick.shape)) for pick in self.selection.picks
        )
        if getattr(self, "_round_cache_key", None) == signature:
            return self._round_cache

        found = list(self.selection.round_faces())
        document = self.window_.document
        source = (self.thread or {}).get("body")
        for pick in self.selection.picks:
            # The source body is never its own mate, and it is the expensive one
            # to walk, so it is skipped rather than filtered out afterwards.
            if pick.kind not in WHOLE_OBJECT_KINDS or pick.body == source:
                continue
            body = document.body(pick.body)
            if body is None or body.shape is None:
                continue
            for face in sub_shapes(body.shape, "face"):
                info = analyse_cylinder(face)
                if info is None:
                    continue
                found.append(Picked(
                    body=pick.body, kind="face", shape=face,
                    presentation=pick.presentation, info=info,
                ))
        self._round_cache_key = signature
        self._round_cache = found
        return found

    def _target_round_face(self):
        if self.thread is None:
            return None
        required_internal = not self._existing_is_internal()
        source = self.thread.get("body")
        size = self._source_size()
        nominal = size.diameter if size is not None else 0.0
        candidates = [
            face for face in self._round_faces_available()
            if face.body != source and bool(face.info.internal) == required_internal
        ]
        if not candidates:
            return None
        # Several bores on one body: the one nearest the thread's own size is
        # the one that was meant.
        return min(candidates, key=lambda f: abs(f.info.diameter - nominal))

    def _target_planar_face(self):
        source = (self.thread or {}).get("body")
        return next(
            (face for face in self.selection.planar_faces() if face.body != source),
            None,
        )

    def _resize_wanted(self) -> tuple[object, float] | None:
        """The target face and the diameter it would have to become, or None."""
        from ...kernel.threads import required_bore

        if self.kind != "apply":
            return None
        pick = self._target_round_face()
        size = self._source_size()
        if pick is None or size is None:
            return None
        clearance = (self.thread or {}).get("clearance", "normal")
        wanted = (
            required_bore(size, clearance) if pick.info.internal else size.diameter
        )
        # Offered only when the feature is meaningfully undersize -- the same
        # 0.05 mm the kernel uses. A bore already at the thread's nominal size
        # needs nothing, and offering to change it there is noise.
        if pick.info.diameter >= size.diameter - 0.05:
            return None
        return pick, wanted

    def _refresh_resize(self) -> None:
        """Say what resizing would actually do, in millimetres."""
        wanted = self._resize_wanted()
        self.resize.setVisible(wanted is not None)
        if wanted is None:
            return
        pick, diameter = wanted
        verb = "Open the hole" if pick.info.internal else "Build the shaft up"
        self.resize.setText(
            f"{verb} ⌀{pick.info.diameter:.2f} → ⌀{diameter:.2f} mm"
        )
        self.relayout()

    # -- description -----------------------------------------------------
    def _describe(self) -> None:
        """One sentence saying exactly what Create will do."""
        if self.thread is None:
            self.set_subtitle(
                "No thread found yet. Create one first, then come back — the "
                "matching part is built from it."
            )
            return
        size = self._source_size()
        if size is None:
            self.set_subtitle("The selected feature names an unknown thread size.")
            return
        pairing = complement(size, existing_internal=self._existing_is_internal())
        hand = "left-hand" if self.thread.get("left_hand") else "right-hand"
        locked = (
            f"Locked to {self.thread['designation']} on {self.thread['feature']}: "
            f"{self.thread.get('form', 'printed')} form, {hand}. "
            f"The mate needs a {pairing.describe()}."
        )
        if self.kind == "apply":
            pick = self._target_round_face()
            detail = (
                f" It will be cut into {pick.body}'s "
                f"⌀{pick.info.diameter:.2f} mm {pick.info.kind}."
                if pick is not None else
                f" Select the {'hole' if pairing.internal else 'shaft'} on the "
                "other part to cut it into."
            )
        elif self.kind == "hole":
            pick = self._target_planar_face()
            detail = (
                f" A ⌀{size.diameter:.2f} mm threaded hole will be drilled into "
                f"{pick.body}, centred on the selected face."
                if pick is not None else
                " Select the flat face on the other part to drill into."
            )
        else:
            detail = " It will be placed beside the model."
        self.set_subtitle(locked + detail)

    # -- feature ---------------------------------------------------------
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
                    "resize": self.resize.isChecked(),
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
            self.warn(message.get("error") or self._nothing_to_act_on())
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

    def _nothing_to_act_on(self) -> str:
        """Why there is no preview, said in terms of what to do next."""
        if self.thread is None or self._source_size() is None:
            return (
                "No thread to match yet. Thread a shaft or a hole first, then "
                "come back with the other part selected."
            )
        if self.kind == "apply":
            if self._target_round_face() is None:
                needed = "hole" if not self._existing_is_internal() else "shaft"
                return (
                    f"Select the {needed} on the other part — a round face, or "
                    "the whole part if it only has one."
                )
            return "The matching thread could not be built on that face."
        if self.kind == "hole":
            if self._target_planar_face() is None:
                return "Select a flat face on the other part to drill into."
            return "The threaded hole could not be built on that face."
        return "The physical matching thread could not be built."

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
