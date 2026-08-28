"""The tools that do the modelling: Pull, Move, Align, Hole, Thread, Fillet.

Each reads the current selection, offers only the numbers that matter, and
commits a parametric feature. Nothing here opens a modal dialog and nothing
asks the user for information the geometry already carries -- a thread tool that
has been handed a cylindrical face knows the diameter and whether it is a hole.
"""

from __future__ import annotations

import math

from PySide6.QtWidgets import QButtonGroup, QComboBox, QGridLayout, QWidget

from ...core.document import BodyRef
from ...core.units import Dimension
from ...kernel.operations import (
    AlignFeature, ChamferFeature, FilletFeature, HoleFeature, MoveFeature,
    PushPullFeature, RoundPushPullFeature, ScaleFeature, ShellFeature,
    ThreadedConnectionFeature, ThreadFeature,
)
from ...kernel.thread_specs import clearance_presets, recommend
from ..theme import METRICS
from ..widgets.controls import GhostButton
from .base import ToolPanel
from ...kernel.vent import VentCutFeature
from .registry import register_tool


def _need(window, test: bool, message: str) -> bool:
    if not test:
        window.set_hint(message)
        return False
    return True


class _SelectionTool(ToolPanel):
    """A tool that acts on whatever is selected when it opens."""

    def __init__(self, window, palette=None) -> None:
        self.selection = window.selection
        super().__init__(window, palette)


# ----------------------------------------------------------------------
@register_tool("pushpull")
class PushPullPanel(_SelectionTool):
    """Pull a face. One command, because there is one gesture.

    A flat face moves along its normal and asks for a distance. A round face
    moves along its radius and asks for a diameter, because nobody thinks about
    a shaft in terms of how far its surface travelled -- they think about how
    thick it ends up. Both are the same idea and the same panel, and asking the
    user which one they meant would be asking about something the selection
    already says.
    """

    title = "Pull face"
    confirm_label = "Pull"

    def build(self) -> None:
        self.pick = self._pick()
        if self.pick is None:
            self.set_subtitle("Select a flat face, or the side of a shaft or hole.")
            self.add_field("distance", "Distance", 5.0)
            return
        if self.pick.is_round_face:
            info = self.pick.info
            self.set_subtitle(
                f"{self.pick.describe()} — set the diameter it should end up."
                + self._wall_note(info)
            )
            self.add_field("diameter", "Diameter", round(info.diameter, 3))
        else:
            self.set_subtitle(
                f"{self.pick.describe()} — positive adds material, negative cuts."
            )
            self.add_field("distance", "Distance", 5.0)

    def _pick(self):
        faces = self.selection.planar_faces() or self.selection.round_faces()
        return faces[0] if faces else None

    def _wall_note(self, info) -> str:
        """How thick the wall would be, when there is a wall to speak of."""
        from ...kernel.operations import _coaxial_faces

        body = self.window_.document.body(self.pick.body)
        if body is None or body.shape is None:
            return ""
        others = _coaxial_faces(body.shape, info, internal=not info.internal)
        if not others:
            return ""
        wall = abs(others[0].radius - info.radius)
        return f" Wall is {wall:.2f} mm now."

    def commit(self) -> None:
        pick = self._pick()
        if not _need(
            self.window_, pick is not None,
            "Select a flat face, or the side of a shaft or hole.",
        ):
            return
        document = self.window_.document
        if pick.is_round_face:
            wanted = self.value("diameter", pick.info.diameter)
            delta = (wanted - pick.info.diameter) / 2.0
            if not _need(
                self.window_, abs(delta) > 1e-9,
                "That is the diameter it already has.",
            ):
                return
            # Stored as the radial move rather than the diameter, so the feature
            # still means the same thing after an upstream edit resizes the
            # face -- exactly as Pull stores a distance and not a height.
            self.window_.add_feature(
                RoundPushPullFeature(
                    inputs={
                        "body": BodyRef(pick.body),
                        "face": pick.reference(document),
                        "delta": round(
                            delta if not pick.info.internal else -delta, 4
                        ),
                    },
                    outputs=[pick.body],
                )
            )
            self.window_.cancel_tool()
            return
        self.window_.add_feature(
            PushPullFeature(
                inputs={
                    "body": BodyRef(pick.body),
                    "face": pick.reference(document),
                    "distance": self.expression("distance", "5"),
                },
                outputs=[pick.body],
            )
        )
        self.window_.cancel_tool()


@register_tool("move")
@register_tool("rotate")
class MovePanel(_SelectionTool):
    """Move and rotate, by dragging the gizmo or by typing exact numbers.

    Both routes write the same feature, so a drag can be corrected by typing
    afterwards and neither is second class.
    """

    title = "Move"
    confirm_label = "Move"

    def build(self) -> None:
        bodies = self.selection.bodies
        groups = self.selection.groups
        subject = ", ".join(groups) if groups else ", ".join(bodies)
        if groups:
            subject += f" ({len(bodies)} objects, moving together)"
        self.set_subtitle(
            f"{subject} — drag a handle, or type exact values."
            if bodies else "Select a body to move."
        )
        for key, label in (("dx", "X"), ("dy", "Y"), ("dz", "Z")):
            self.add_field(key, label, 0.0)
        self.add_section("Rotate")
        for key, label in (("rx", "Around X"), ("ry", "Around Y"), ("rz", "Around Z")):
            self.add_field(key, label, 0.0, Dimension.ANGLE)
        self._attach_gizmo()

    def _attach_gizmo(self) -> None:
        bodies = self.selection.bodies
        if not bodies:
            return
        presentation = self.window_._presentations.get(bodies[0])
        if presentation is None:
            return
        gizmo = self.window_.attach_gizmo(presentation)
        if gizmo is not None:
            gizmo.changed.connect(self._on_gizmo_drag)
            gizmo.committed.connect(self._on_gizmo_commit)

    def _on_gizmo_drag(self, dx, dy, dz, rx, ry, rz) -> None:
        moved = math.sqrt(dx * dx + dy * dy + dz * dz)
        turned = max(abs(rx), abs(ry), abs(rz))
        if turned > 0.01:
            self.window_.set_hint(f"Rotating {turned:.1f}°")
        else:
            self.window_.set_hint(f"Moving {moved:.2f} mm")

    def _on_gizmo_commit(self, dx, dy, dz, rx, ry, rz) -> None:
        """A released drag fills the fields, then applies them."""
        for key, value in (
            ("dx", dx), ("dy", dy), ("dz", dz), ("rx", rx), ("ry", ry), ("rz", rz)
        ):
            field = self.fields.get(key)
            if field is not None:
                field.set_value(round(value, 3))
        if any(abs(v) > 1e-4 for v in (dx, dy, dz, rx, ry, rz)):
            self.commit()

    def _shared_pivot(self, names):
        """The centre of everything selected, for a rotation about the lot."""
        from ...kernel.occ import bounding_box

        boxes = [
            bounding_box(body.shape)
            for body in (self.window_.document.body(n) for n in names)
            if body is not None and body.shape is not None
        ]
        if not boxes:
            return None
        return tuple(
            (min(b[0][i] for b in boxes) + max(b[1][i] for b in boxes)) / 2.0
            for i in range(3)
        )

    def commit(self) -> None:
        bodies = self.selection.bodies
        if not _need(self.window_, bool(bodies), "Select a body to move."):
            return
        if all(abs(self.value(k, 0.0)) < 1e-9
               for k in ("dx", "dy", "dz", "rx", "ry", "rz")):
            self.window_.set_hint("Nothing to move — drag a handle or enter a value.")
            return
        self.window_.detach_gizmo()
        self.window_.history.record("Move")
        # Several bodies turn about their *shared* centre. Letting each spin
        # about its own would leave a rotated group in the same places it
        # started, every part facing a new way -- which is not what turning an
        # assembly means.
        pivot = self._shared_pivot(bodies) if len(bodies) > 1 else None
        for name in bodies:
            inputs = {
                "body": BodyRef(name),
                **{k: self.expression(k, "0")
                   for k in ("dx", "dy", "dz", "rx", "ry", "rz")},
            }
            if pivot is not None:
                inputs["pivot"] = list(pivot)
            self.window_.document.add_feature(
                MoveFeature(inputs=inputs, outputs=[name])
            )
        self.window_.mark_dirty()
        self.window_.rebuild()
        self.window_.cancel_tool()


# ----------------------------------------------------------------------
@register_tool("align")
@register_tool("stack")
@register_tool("concentric")
@register_tool("center")
class AlignPanel(_SelectionTool):
    """Stack / Center / Concentric, with the right one already chosen."""

    title = "Align"
    confirm_label = "Done"

    OPERATIONS = (
        ("stack", "Stack"),
        ("center", "Center"),
        ("concentric", "Concentric"),
    )

    def build(self) -> None:
        from ...kernel.align import suggest

        faces = self.selection.faces()
        self.operation = "stack"
        if len(faces) == 2:
            self.operation = suggest(faces[0].shape, faces[1].shape)
            moving, target = faces[0], faces[1]
            self.set_subtitle(
                f"Moving {moving.body} onto {target.body}. "
                f"Suggested: {dict(self.OPERATIONS).get(self.operation, 'Align')}."
            )
        else:
            self.set_subtitle("Select one face on each of two parts.")

        self.add_section("Operation")
        chooser = QWidget()
        grid = QGridLayout(chooser)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(METRICS.space(1))
        self._buttons = QButtonGroup(self)
        self._buttons.setExclusive(True)
        for index, (key, label) in enumerate(self.OPERATIONS):
            button = GhostButton(label)
            button.setCheckable(True)
            button.setChecked(key == self.operation)
            button.clicked.connect(lambda _=False, k=key: self._choose(k))
            self._buttons.addButton(button)
            grid.addWidget(button, 0, index)
        self.add_widget(chooser)

        self.add_field("offset", "Offset", 0.0)
        self.flip = GhostButton("Flip")
        self.flip.setCheckable(True)
        self.flip.clicked.connect(lambda: self.preview())
        self.add_widget(self.flip)

    def _choose(self, key: str) -> None:
        self.operation = key
        self.preview()

    def commit(self) -> None:
        faces = self.selection.faces()
        if not _need(
            self.window_, len(faces) == 2,
            "Select one face on each of the two parts you want to align.",
        ):
            return
        moving, target = faces[0], faces[1]
        if not _need(
            self.window_, moving.body != target.body,
            "Those faces are on the same body. Pick a face on each part.",
        ):
            return
        document = self.window_.document
        self.window_.add_feature(
            AlignFeature(
                inputs={
                    "body": BodyRef(moving.body),
                    "moving_face": moving.reference(document),
                    "target_face": target.reference(document),
                    "operation": self.operation,
                    "offset": self.expression("offset", "0"),
                    "flip": self.flip.isChecked(),
                },
                outputs=[moving.body],
            )
        )
        self.window_.cancel_tool()


# ----------------------------------------------------------------------
@register_tool("hole")
class HolePanel(_SelectionTool):
    title = "Hole"
    confirm_label = "Create"

    STYLES = (
        ("simple", "Simple"),
        ("counterbore", "Counterbore"),
        ("countersink", "Countersink"),
        ("threaded", "Threaded"),
    )

    def build(self) -> None:
        faces = self.selection.planar_faces()
        self.style = "simple"
        if faces:
            self.set_subtitle(
                f"Drilling into {faces[0].body}. The hole is centred on the face "
                "unless you click a position first."
            )
        else:
            self.set_subtitle("Select the flat face to drill into.")

        self.add_section("Type")
        chooser = QWidget()
        grid = QGridLayout(chooser)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(METRICS.space(1))
        for index, (key, label) in enumerate(self.STYLES):
            button = GhostButton(label)
            button.setCheckable(True)
            button.setChecked(key == "simple")
            button.clicked.connect(lambda _=False, k=key: self._choose(k))
            grid.addWidget(button, index // 2, index % 2)
            setattr(self, f"_button_{key}", button)
        self.add_widget(chooser)

        self.add_field("diameter", "Diameter", 6.0)
        self.add_section("Depth")
        self.depth_mode = QComboBox()
        self.depth_mode.addItem("Through all", "through")
        self.depth_mode.addItem("To a depth", "blind")
        self.depth_mode.addItem("To another body", "to_object")
        self.depth_mode.currentIndexChanged.connect(self._depth_changed)
        self.add_widget(self.depth_mode)

        self.until = QComboBox()
        for name in self.window_.document.bodies:
            self.until.addItem(name, name)
        self.until.setVisible(False)
        self.add_widget(self.until)

        self.add_field("depth", "Depth", 10.0)
        self.fields["depth"].setEnabled(False)

    def _choose(self, key: str) -> None:
        self.style = key
        for candidate, _label in self.STYLES:
            getattr(self, f"_button_{candidate}").setChecked(candidate == key)

    def _depth_changed(self) -> None:
        mode = self.depth_mode.currentData()
        self.fields["depth"].setEnabled(mode == "blind")
        self.until.setVisible(mode == "to_object")
        self.adjustSize()
        self.window_.stage._layout_overlays()

    def commit(self) -> None:
        faces = self.selection.planar_faces()
        if not _need(self.window_, bool(faces), "Select a flat face to drill into."):
            return
        pick = faces[0]
        document = self.window_.document
        inputs = {
            "body": BodyRef(pick.body),
            "face": pick.reference(document),
            "diameter": self.expression("diameter", "6"),
            "style": self.style,
            "depth_mode": self.depth_mode.currentData(),
            "depth": self.expression("depth", "10"),
        }
        if self.depth_mode.currentData() == "to_object" and self.until.currentData():
            inputs["until"] = BodyRef(self.until.currentData())
        if pick.info is not None:
            inputs["position"] = tuple(pick.info.center)
        self.window_.add_feature(HoleFeature(inputs=inputs, outputs=[pick.body]))
        self.window_.cancel_tool()


# ----------------------------------------------------------------------
#: The tooth shapes offered, best first. Printed leads because SimpleCAD makes
#: parts for a printer, and a 60-degree ISO tooth printed axis-up overhangs by
#: 63 degrees -- it needs supports, and supports inside a thread are what stops
#: the two halves ever turning.
THREAD_FORMS = (
    ("printed", "Printed — 45° teeth, no supports"),
    ("iso", "Standard ISO — 60° V, needs supports"),
)


class _ThreadTool(_SelectionTool):
    """Shared furniture for the tools that create threads."""

    def add_form_chooser(self) -> None:
        self.form = QComboBox()
        for key, label in THREAD_FORMS:
            self.form.addItem(label, key)
        self.form.currentIndexChanged.connect(lambda _i: self.check_printability())
        self.add_section("Tooth shape")
        self.add_widget(self.form)

    def chosen_form(self) -> str:
        return self.form.currentData() if hasattr(self, "form") else "printed"

    def check_printability(self, designation: str | None = None) -> None:
        """Say up front what will be wrong with printing this thread.

        The same sentence the kernel would put on the finished feature, said at
        the point of choosing instead -- discovering that a thread was too fine
        for the nozzle after a four-hour print is not feedback, it is a bill.
        """
        from ...kernel.thread_specs import by_designation
        from ...kernel.threads import printable_note, thread_form

        designation = designation or (
            self.sizes.currentData() if hasattr(self, "sizes") else None
        )
        size = by_designation(str(designation)) if designation else None
        if size is None:
            return
        try:
            shape = thread_form(size.pitch, size.angle, self.chosen_form())
        except Exception:  # noqa: BLE001 - a warning is never worth failing over
            return
        self.warn(printable_note(shape, size))


@register_tool("thread")
class ThreadPanel(_ThreadTool):
    """Select a round face; the size is already worked out."""

    title = "Thread"
    confirm_label = "Create"

    def build(self) -> None:
        faces = self.selection.round_faces()
        self.sizes = QComboBox()
        self.clearance = QComboBox()

        if faces:
            info = faces[0].info
            self.set_subtitle(
                f"Detected a {info.kind}, ⌀{info.diameter:.2f} mm. "
                f"{'Internal' if info.internal else 'External'} thread."
            )
            for option in recommend(info.diameter, internal=info.internal, limit=8):
                self.sizes.addItem(option.describe(), option.size.designation)
            if self.sizes.count() == 0:
                self.warn(
                    f"No standard thread is close to ⌀{info.diameter:.2f} mm. "
                    "Resize the feature, or pick a size below."
                )
        else:
            self.set_subtitle("Select the round face of a shaft or a hole.")

        self.add_section("Size")
        self.add_widget(self.sizes)
        self.add_section("Printable clearance")
        for key, entry in clearance_presets().items():
            self.clearance.addItem(
                f"{entry['label']} — {entry['clearance']:.2f} mm  ·  {entry['hint']}", key
            )
        self.clearance.setCurrentIndex(1)   # Normal
        self.add_widget(self.clearance)
        self.add_form_chooser()
        self.add_field("length", "Length", 0.0)
        self.fields["length"].setPlaceholderText("full face")
        self.sizes.currentIndexChanged.connect(lambda _i: self.check_printability())
        self.check_printability()

    def commit(self) -> None:
        faces = self.selection.round_faces()
        if not _need(
            self.window_, bool(faces), "Select the round face of a shaft or hole."
        ):
            return
        pick = faces[0]
        self.window_.add_feature(
            ThreadFeature(
                inputs={
                    "body": BodyRef(pick.body),
                    "face": pick.reference(self.window_.document),
                    "designation": self.sizes.currentData(),
                    "clearance": self.clearance.currentData(),
                    "form": self.chosen_form(),
                    "length": self.expression("length", "0"),
                },
                outputs=[pick.body],
            )
        )
        self.window_.cancel_tool()


@register_tool("align_threaded")
class AlignThreadedPanel(_ThreadTool):
    """Align two parts by their round faces *and* thread the pair, in one step.

    The spec asks for this as one command rather than two: selecting a post and
    its mating hole is already an unambiguous statement of intent, so making the
    user run Concentric and then Create Threaded Connection separately is asking
    them to say it twice.
    """

    title = "Align & thread"
    confirm_label = "Align and thread"
    width = 316

    def build(self) -> None:
        faces = self.selection.round_faces()
        self.sizes = QComboBox()
        self.clearance = QComboBox()

        if len(faces) == 2 and faces[0].info.internal != faces[1].info.internal:
            male = next(f for f in faces if not f.info.internal)
            female = next(f for f in faces if f.info.internal)
            self.set_subtitle(
                f"{male.body} will be seated into {female.body}, and both "
                "threads created to match."
            )
            for option in recommend(male.info.diameter, internal=False, limit=8):
                self.sizes.addItem(option.describe(), option.size.designation)
        else:
            self.set_subtitle(
                "Select the shaft on one part and its mating hole on the other."
            )

        self.add_section("Thread")
        self.add_widget(self.sizes)
        self.add_section("Printable clearance")
        for key, entry in clearance_presets().items():
            self.clearance.addItem(f"{entry['label']} — {entry['clearance']:.2f} mm", key)
        self.clearance.setCurrentIndex(1)
        self.add_widget(self.clearance)
        self.add_form_chooser()
        self.add_field("offset", "Seat offset", 0.0)
        self.sizes.currentIndexChanged.connect(lambda _i: self.check_printability())
        self.check_printability()

    def commit(self) -> None:
        faces = self.selection.round_faces()
        if not _need(
            self.window_, len(faces) == 2,
            "Select one round face on each part — a shaft and its mating hole.",
        ):
            return
        if not _need(
            self.window_, faces[0].info.internal != faces[1].info.internal,
            "Both selections are the same kind. Pick one shaft and one hole.",
        ):
            return

        document = self.window_.document
        male = next(f for f in faces if not f.info.internal)
        female = next(f for f in faces if f.info.internal)

        self.window_.history.record("Align & thread")
        # Move the male part onto the female's axis first, so the preview shows
        # the assembly rather than two parts that merely have matching threads.
        document.add_feature(
            AlignFeature(
                inputs={
                    "body": BodyRef(male.body),
                    "moving_face": male.reference(document),
                    "target_face": female.reference(document),
                    "operation": "concentric",
                    "offset": self.expression("offset", "0"),
                },
                outputs=[male.body],
            )
        )
        document.add_feature(
            ThreadedConnectionFeature(
                inputs={
                    "body_a": BodyRef(male.body),
                    "body_b": BodyRef(female.body),
                    "face_a": male.reference(document),
                    "face_b": female.reference(document),
                    "designation": self.sizes.currentData(),
                    "clearance": self.clearance.currentData(),
                    "form": self.chosen_form(),
                },
                outputs=[male.body, female.body],
            )
        )
        self.window_.mark_dirty()
        self.window_.rebuild()
        self.window_.cancel_tool()


@register_tool("align_stack")
class AlignAndStackPanel(AlignPanel):
    """Stack, with the offset field to hand -- the spec's *Align & Stack*."""

    title = "Align & stack"

    def build(self) -> None:
        super().build()
        self._choose("stack")
        self.set_subtitle(
            self.subtitle.text() + "  Set an offset to leave a gap."
        )


@register_tool("threaded_connection")
class ThreadedConnectionPanel(_ThreadTool):
    """The headline feature: two parts in, a matched printable pair out."""

    title = "Threaded connection"
    confirm_label = "Create pair"
    width = 316

    def build(self) -> None:
        faces = self.selection.round_faces()
        self.sizes = QComboBox()
        self.clearance = QComboBox()

        if len(faces) == 2 and faces[0].info.internal != faces[1].info.internal:
            male = next(f for f in faces if not f.info.internal)
            female = next(f for f in faces if f.info.internal)
            self.set_subtitle(
                f"{male.body} ⌀{male.info.diameter:.2f} shaft into "
                f"{female.body} ⌀{female.info.diameter:.2f} hole. "
                "Both threads are created together, with clearance on the hole."
            )
            for option in recommend(male.info.diameter, internal=False, limit=8):
                self.sizes.addItem(option.describe(), option.size.designation)
        else:
            self.set_subtitle(
                "Select the shaft on one part and its mating hole on the other."
            )

        self.add_section("Thread")
        self.add_widget(self.sizes)
        self.add_section("Printable clearance")
        for key, entry in clearance_presets().items():
            self.clearance.addItem(
                f"{entry['label']} — {entry['clearance']:.2f} mm", key
            )
        self.clearance.setCurrentIndex(1)
        self.add_widget(self.clearance)
        self.add_form_chooser()
        self.add_field("length", "Length", 0.0)
        self.fields["length"].setPlaceholderText("full engagement")
        self.sizes.currentIndexChanged.connect(lambda _i: self.check_printability())
        self.check_printability()

    def commit(self) -> None:
        faces = self.selection.round_faces()
        if not _need(
            self.window_, len(faces) == 2,
            "Select one round face on each part — a shaft and its mating hole.",
        ):
            return
        if not _need(
            self.window_, faces[0].info.internal != faces[1].info.internal,
            "Both selections are the same kind. Pick one shaft and one hole.",
        ):
            return
        document = self.window_.document
        first, second = faces[0], faces[1]
        self.window_.add_feature(
            ThreadedConnectionFeature(
                inputs={
                    "body_a": BodyRef(first.body),
                    "body_b": BodyRef(second.body),
                    "face_a": first.reference(document),
                    "face_b": second.reference(document),
                    "designation": self.sizes.currentData(),
                    "clearance": self.clearance.currentData(),
                    "form": self.chosen_form(),
                    "length": self.expression("length", "0"),
                },
                outputs=[first.body, second.body],
            )
        )
        self.window_.cancel_tool()


# ----------------------------------------------------------------------
class _EdgeTool(_SelectionTool):
    """Fillet and Chamfer: pull the size out on the model, or type it.

    The panel opens with a handle sitting on the selected edge, offset from it
    by the current radius so the handle's position *is* the size. Dragging it
    outward makes the corner rounder and shows the real filleted body while you
    move; the number in the panel follows the drag, and typing in the panel
    moves the handle.

    **Letting go commits**, exactly as letting go of a dragged face does. A
    drag is a complete gesture with an obvious end, and finishing one only to
    find the model unchanged until you hunt for a button reads as the drag not
    working at all. Typing a value still goes through Apply, because typing has
    no natural end.

    The preview is the genuine kernel result rather than an approximation,
    because the interesting radii are exactly the ones near where the operation
    stops being buildable. It is rebuilt on a short timer rather than on every
    mouse event, and a radius OCCT refuses leaves the last good preview standing
    and turns the readout red instead of throwing the drag away.
    """

    feature_class = FilletFeature
    field_key = "radius"
    field_label = "Radius"
    default_value = 2.0
    #: How long to coalesce drag events before rebuilding the preview, in ms.
    PREVIEW_MS = 70

    @staticmethod
    def preview_builder(body):
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet

        return BRepFilletAPI_MakeFillet(body)

    # -- construction ----------------------------------------------------
    def build(self) -> None:
        from PySide6.QtCore import QTimer

        self.picks = self._edge_picks()
        self._body_shape = self._resolve_body()
        self._max_value = self._resolve_max()
        self._handle = None
        self._anchor = None
        self._direction = None
        self._dragging = False
        self._drag_base = self.default_value
        self._dimmed = None
        self._last_good = None
        self._preview_failed = False

        self.set_subtitle(self._describe())
        self.add_field(self.field_key, self.field_label, self.default_value)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(self.PREVIEW_MS)
        self._preview_timer.timeout.connect(self._refresh_preview)

        viewport = self.window_.stage.viewport
        viewport.handle_pressed.connect(self._on_handle_pressed)
        viewport.handle_dragged.connect(self._on_handle_dragged)
        self._install_handle()

    def _describe(self) -> str:
        if not self.picks:
            return "Select one or more edges, or a corner."
        return (
            f"{len(self.picks)} edge(s) selected — drag the handle on the model, "
            "or type an exact value."
        )

    def _edge_picks(self) -> list:
        """The edges to work on, expanding a picked corner into its edges.

        "Fillet this corner" is a perfectly ordinary request and a vertex is a
        perfectly ordinary thing to click, so a vertex selection is read as the
        three edges that meet there rather than rejected.
        """
        from ..selection import Picked

        edges = self.selection.edges()
        if edges:
            return edges
        from ...kernel.edge_frame import edges_at_vertex
        from OCP.TopoDS import TopoDS

        expanded: list = []
        for pick in self.selection.picks:
            if pick.kind != "vertex":
                continue
            body = self.window_.document.body(pick.body)
            if body is None or body.shape is None:
                continue
            try:
                found = edges_at_vertex(body.shape, TopoDS.Vertex_s(pick.shape))
            except Exception:  # noqa: BLE001 - a stray vertex simply expands to nothing
                continue
            expanded.extend(
                Picked(
                    body=pick.body, kind="edge", shape=TopoDS.Edge_s(edge),
                    presentation=pick.presentation,
                )
                for edge in found
            )
        return expanded

    def _resolve_body(self):
        if not self.picks:
            return None
        body = self.window_.document.body(self.picks[0].body)
        return body.shape if body is not None else None

    def _resolve_max(self) -> float:
        from ...kernel.edge_frame import max_radius

        if self._body_shape is None:
            return 1e6
        try:
            return max_radius(self._body_shape)
        except Exception:  # noqa: BLE001
            return 1e6

    # -- the handle ------------------------------------------------------
    def _handle_pick(self):
        """Which selected edge gets the handle: the one nearest the cursor.

        With several edges selected -- and a filleted corner is three of them
        -- putting the handle on whichever happened to be picked first can land
        it on the far side of the part, out of sight behind the model.
        """
        from PySide6.QtGui import QCursor

        viewport = self.window_.stage.viewport
        if len(self.picks) == 1:
            return self.picks[0]
        try:
            cursor = viewport.mapFromGlobal(QCursor.pos())
        except Exception:  # noqa: BLE001 - no cursor to speak of, headless
            return self.picks[0]

        from ...kernel.edge_frame import edge_midpoint

        best, best_distance = self.picks[0], None
        for pick in self.picks:
            try:
                screen = viewport.project(edge_midpoint(pick.shape))
            except Exception:  # noqa: BLE001 - an edge we cannot place
                continue
            if screen is None:
                continue
            distance = (screen[0] - cursor.x()) ** 2 + (screen[1] - cursor.y()) ** 2
            if best_distance is None or distance < best_distance:
                best, best_distance = pick, distance
        return best

    def _install_handle(self) -> None:
        from ...kernel.edge_frame import edge_drag_frame
        from ..viewport.handles import DragHandle

        if self._body_shape is None or not self.picks:
            return
        try:
            anchor, direction, _limit = edge_drag_frame(
                self._body_shape, self._handle_pick().shape
            )
        except Exception:  # noqa: BLE001 - no handle is better than a wrong one
            return
        self._anchor, self._direction = anchor, direction
        viewport = self.window_.stage.viewport
        self._handle = viewport.handles.add(
            DragHandle(self._offset_anchor(self.value(self.field_key,
                                                      self.default_value)),
                       direction, key=self.field_key),
            viewport, self.window_.palette_.accent,
        )
        self.window_.set_hint(
            "Drag the handle to size the "
            f"{self.title.lower()}, or type a value — then Apply."
        )

    def _offset_anchor(self, value: float):
        """Where the handle sits for a given value.

        Offset from the edge by the value itself, so the handle is not merely a
        control but a reading: how far it stands off the corner is how big the
        result will be.
        """
        return tuple(
            self._anchor[i] + self._direction[i] * max(0.0, value)
            for i in range(3)
        )

    def _move_handle(self, value: float) -> None:
        """Put the handle where *value* says, and redraw so it is seen there.

        The refresh is the point. ``move_to`` only updates a transformation;
        without asking the viewport to repaint, the handle stays where it was
        drawn and the drag looks like it is doing nothing at all -- which is
        precisely how this read. Split does the same thing and refreshes.
        """
        if self._handle is not None and self._anchor is not None:
            self._handle.move_to(self._offset_anchor(value))
            self.window_.stage.viewport.refresh()

    # -- dragging --------------------------------------------------------
    def _on_handle_pressed(self, key: str) -> None:
        if key != self.field_key:
            return
        self._dragging = True
        self._drag_base = self.value(self.field_key, self.default_value)

    def _on_handle_dragged(self, key: str, distance: float, finished: bool) -> None:
        from ...kernel.edge_frame import MIN_RADIUS

        if key != self.field_key or not self._dragging:
            return
        value = self._drag_base + distance
        value = max(MIN_RADIUS, min(self._max_value, value))
        field = self.fields.get(self.field_key)
        if field is not None:
            field.set_value(round(value, 3))
        self._move_handle(value)
        self._show_readout(value)
        if finished:
            self._dragging = False
            self._preview_timer.stop()
            # A press and release that went nowhere is a click on the handle,
            # not a gesture -- committing the default radius for it would be a
            # change nobody asked for. Push/Pull draws the same line.
            if abs(distance) < 0.05:
                self._refresh_preview()
                return
            self.commit()
            return
        # Started only if it is not already running. Restarting it on every
        # drag event -- which is what a debounce does -- means a 70 ms timer
        # never elapses while the mouse is moving, because the events arrive
        # every few milliseconds. The preview then appears only once the user
        # stops, which is not a preview.
        if not self._preview_timer.isActive():
            self._preview_timer.start()

    def _show_readout(self, value: float) -> None:
        from PySide6.QtGui import QCursor

        palette = self.window_.palette_
        # Unbuildable means "tried and failed", not "not tried yet". Reading
        # this straight off ``_last_good`` made the readout red for the first
        # few frames of every drag, before the preview timer had run once.
        buildable = self._last_good is not None or not self._preview_failed
        self.window_.stage.drag_readout.show_text(
            self.window_.stage.mapFromGlobal(QCursor.pos()),
            f"{self.field_label[0]} {value:.2f} mm",
            "" if buildable else "too large here",
            palette.accent if buildable else palette.danger,
        )

    # -- preview ---------------------------------------------------------
    def preview(self) -> None:
        """A typed value moves the handle and refreshes the preview."""
        value = self.value(self.field_key, self.default_value)
        self._move_handle(value)
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        value = self.value(self.field_key, self.default_value)
        shape = self._build(value)
        viewport = self.window_.stage.viewport
        if shape is None:
            # Keep whatever last worked on screen. Blanking the preview at the
            # exact moment the radius becomes invalid is the least useful thing
            # to do: it hides the shape you were steering toward just as you
            # need to see how far you overshot.
            self._preview_failed = True
            self.warn(
                f"{self.field_label} {value:.2f} mm is more than this shape can "
                "take here."
            )
            self._show_readout(value)
            return
        self._last_good = shape
        self._preview_failed = False
        self.warn("")
        viewport.show_ghost(shape, self.window_.palette_.accent, transparency=0.12)
        self._dim_body(0.88)
        self._show_readout(value)

    def _build(self, value: float):
        """The real kernel result, or None if this value cannot be built."""
        from ...kernel.occ import built_shape

        if self._body_shape is None or not self.picks or value <= 0:
            return None
        from OCP.TopoDS import TopoDS

        try:
            builder = self.preview_builder(self._body_shape)
            for pick in self.picks:
                builder.Add(value, TopoDS.Edge_s(pick.shape))
            return built_shape(builder, self.title.lower())
        except BaseException:  # noqa: BLE001 - OCCT raises non-Exceptions
            return None

    def _dim_body(self, amount: float) -> None:
        presentation = self.window_._presentations.get(self.picks[0].body)
        if presentation is None:
            return
        self.window_.stage.viewport.set_transparency(presentation, amount)
        self._dimmed = self.picks[0].body

    # -- lifecycle -------------------------------------------------------
    def teardown(self) -> None:
        viewport = self.window_.stage.viewport
        self._preview_timer.stop()
        viewport.clear_ghost()
        viewport.handles.clear(viewport)
        self.window_.stage.drag_readout.finish()
        if self._dimmed is not None:
            presentation = self.window_._presentations.get(self._dimmed)
            if presentation is not None:
                viewport.set_transparency(presentation, 0.0)
            self._dimmed = None
        for signal, slot in (
            (viewport.handle_pressed, self._on_handle_pressed),
            (viewport.handle_dragged, self._on_handle_dragged),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    def commit(self) -> None:
        if not _need(self.window_, bool(self.picks), "Select one or more edges."):
            return
        body = self.picks[0].body
        document = self.window_.document
        self.window_.add_feature(
            self.feature_class(
                inputs={
                    "body": BodyRef(body),
                    "edges": [
                        p.reference(document) for p in self.picks if p.body == body
                    ],
                    self.field_key: self.expression(
                        self.field_key, str(self.default_value)
                    ),
                },
                outputs=[body],
            )
        )
        self.window_.cancel_tool()


@register_tool("fillet")
class FilletPanel(_EdgeTool):
    title = "Fillet"
    confirm_label = "Apply"
    feature_class = FilletFeature
    field_key = "radius"
    field_label = "Radius"


@register_tool("chamfer")
class ChamferPanel(_EdgeTool):
    title = "Chamfer"
    confirm_label = "Apply"
    feature_class = ChamferFeature
    field_key = "distance"
    field_label = "Distance"

    @staticmethod
    def preview_builder(body):
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer

        return BRepFilletAPI_MakeChamfer(body)


@register_tool("scale")
class ScalePanel(_SelectionTool):
    """Resize a body by a factor, uniformly or per axis."""

    title = "Scale"
    confirm_label = "Scale"

    def build(self) -> None:
        bodies = self.selection.bodies
        self.set_subtitle(
            f"{', '.join(bodies)} — 2 doubles it, 0.5 halves it."
            if bodies else "Select a body to scale."
        )
        self.add_field("factor", "Scale", 1.0, Dimension.SCALAR)
        self.add_section("Per axis")
        for key, label in (("sx", "X"), ("sy", "Y"), ("sz", "Z")):
            field = self.add_field(key, label, 1.0, Dimension.SCALAR)
            field.setPlaceholderText("follows Scale")
        self._uniform = True

    def commit(self) -> None:
        bodies = self.selection.bodies
        if not _need(self.window_, bool(bodies), "Select a body to scale."):
            return
        factor = self.value("factor", 1.0)
        per_axis = {k: self.value(k, 1.0) for k in ("sx", "sy", "sz")}
        # A per-axis field left at 1 while Scale says 2 means "follow Scale",
        # not "keep this axis the same" -- otherwise the obvious way to double
        # a part silently only doubles nothing.
        if all(abs(v - 1.0) < 1e-12 for v in per_axis.values()):
            inputs = {"factor": self.expression("factor", "1")}
        else:
            inputs = {k: self.expression(k, "1") for k in per_axis}
        if all(abs(self.value(k, 1.0) - 1.0) < 1e-12
               for k in ("factor", "sx", "sy", "sz")):
            self.window_.set_hint("Nothing to scale — enter a factor.")
            return

        self.window_.history.record("Scale")
        for name in bodies:
            self.window_.document.add_feature(
                ScaleFeature(
                    inputs={"body": BodyRef(name), **inputs}, outputs=[name]
                )
            )
        self.window_.mark_dirty()
        self.window_.rebuild()
        self.window_.cancel_tool()


@register_tool("shell")
class ShellPanel(_SelectionTool):
    """Hollow a solid out, leaving walls of a chosen thickness.

    Named Hollow rather than Shell in the UI: "shell" is the CAD term, but the
    thing people arrive wanting is a bucket, and they look for the word they
    already have.
    """

    title = "Hollow"
    confirm_label = "Apply"

    def build(self) -> None:
        faces = self.selection.planar_faces()
        self.set_subtitle(
            f"{len(faces)} face(s) will be opened. The remaining walls keep "
            "this thickness."
            if faces else
            "Select the flat face to open. The remaining walls keep this "
            "thickness."
        )
        self.add_field("thickness", "Wall thickness", 2.0)

    def commit(self) -> None:
        faces = self.selection.planar_faces()
        if not _need(
            self.window_, bool(faces),
            "Select the flat face to open before hollowing.",
        ):
            return
        body = faces[0].body
        document = self.window_.document
        self.window_.add_feature(
            ShellFeature(
                inputs={
                    "body": BodyRef(body),
                    "faces": [f.reference(document) for f in faces if f.body == body],
                    "thickness": self.expression("thickness", "2"),
                },
                outputs=[body],
            )
        )
        self.window_.cancel_tool()


class VentPanel(_SelectionTool):
    """Perforate a flat wall with a hex grid -- a fan grille, in place.

    Dispatches on the selection: with a flat face picked it cuts through that
    wall, with nothing picked it hands over to the Shape panel to make a
    standalone vent plate. Same command, whichever way round the user got here.
    """

    title = "Vent"
    confirm_label = "Cut"

    def build(self) -> None:
        faces = self.selection.planar_faces()
        self.set_subtitle(
            f"{faces[0].describe()} — a hex grid will be cut through it."
            if faces else "Select the flat wall the fan blows through."
        )
        self.add_field("across_flats", "Hole size", 5.0)
        self.add_field("wall", "Wall between", 1.2)
        self.add_field("margin", "Border", 2.0)

    def commit(self) -> None:
        faces = self.selection.planar_faces()
        if not _need(
            self.window_, bool(faces), "Select the flat face to vent."
        ):
            return
        pick = faces[0]
        self.window_.add_feature(
            VentCutFeature(
                inputs={
                    "body": BodyRef(pick.body),
                    "face": pick.reference(self.window_.document),
                    "across_flats": self.expression("across_flats", "5"),
                    "wall": self.expression("wall", "1.2"),
                    "margin": self.expression("margin", "2"),
                },
                outputs=[pick.body],
            )
        )
        self.window_.cancel_tool()


@register_tool("vent")
def open_vent_tool(window):
    """Vent means two different things depending on what is selected.

    With a flat face picked, the user is pointing at the wall they want
    perforated, so cut it there. With nothing picked they want a grille as its
    own part, which is the Shape panel's job. One command either way -- asking
    "plate or cutter?" would be asking about something the selection already
    says.
    """
    from .shapes import ShapePanel

    if window.selection.planar_faces():
        return VentPanel(window, window.palette_)
    panel = ShapePanel(window, window.palette_)
    panel.choose("vent_plate")
    return panel
