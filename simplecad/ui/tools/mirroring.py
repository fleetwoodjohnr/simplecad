"""Mirror: flip objects across a plane, in place or as a mirrored copy.

In place, the plane runs through the selection's centre, so the objects stay
where they are, reversed. A copy is mirrored across the selection's far side
along the chosen axis, so the new half sits against the original: the usual
way to make the other side of a symmetric pair. Either way it is one undo step,
and the copy is an ordinary, independently editable object.
"""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox

from ...core.document import BodyRef
from .modeling import _SelectionTool
from .registry import register_tool

#: (axis, button label, what it does)
PLANES = (
    (0, "Left ↔ right", "Across the YZ plane: X is reversed"),
    (1, "Front ↔ back", "Across the XZ plane: Y is reversed"),
    (2, "Top ↔ bottom", "Across the XY plane: Z is reversed"),
)


def mirror_plane(low, high, axis: int, copy: bool):
    """(origin, normal) of the mirror plane for a selection's bounding box."""
    normal = [0.0, 0.0, 0.0]
    normal[axis] = 1.0
    origin = [(a + b) / 2.0 for a, b in zip(low, high)]
    if copy:
        origin[axis] = high[axis]
    return tuple(origin), tuple(normal)


@register_tool("mirror")
class MirrorPanel(_SelectionTool):
    title = "Mirror"
    confirm_label = "Mirror"

    def build(self) -> None:
        self.set_subtitle("Flip the selected objects across a plane.")
        self.add_section("Flip")
        host, self.plane_buttons = self._choices(
            [(str(axis), label) for axis, label, _tip in PLANES], self._choose, columns=1)
        for axis, _label, tip in PLANES:
            self.plane_buttons[str(axis)].setToolTip(tip)
        self.add_widget(host)
        self.copy = QCheckBox("Keep the original and add a mirrored copy beside it")
        self.add_widget(self.copy)
        self._choose("0")

    def _choose(self, key: str) -> None:
        self.axis = int(key)
        for name, button in self.plane_buttons.items():
            button.setChecked(name == key)

    def commit(self) -> None:
        from ...core.model_clipboard import copy_fragment, paste_fragment
        from ...kernel.advanced import MirrorFeature
        from ..main_window import _combined_bounds

        window = self.window_
        document = window.document
        names = window._selected_body_names()
        if not names:
            self.warn("Select one or more objects to mirror.")
            return
        if window.refuse_locked(names):
            return
        if window.is_rebuilding:
            self.warn("Still updating the last change — try again in a moment.")
            return
        low, high = _combined_bounds([document.body(n).shape for n in names])
        copy = self.copy.isChecked()
        origin, normal = mirror_plane(low, high, self.axis, copy)
        targets = names
        window.history.record("Mirror copy" if copy else "Mirror")
        if copy:
            items = window.selection.items or names
            fragment = copy_fragment(document, list(items), bounds=(low, high),
                                     capture_geometry=True)
            if not fragment.features:
                window.history.cancel()
                self.warn("These objects have no history to copy.")
                return
            _features, roots = paste_fragment(document, fragment, (0.0, 0.0, 0.0))
            targets = document.expand(roots)
            window._select_after_rebuild = list(roots)
        for name in targets:
            document.add_feature(MirrorFeature(
                inputs={"body": BodyRef(name), "normal": list(normal), "origin": list(origin),
                        "keep": False},
                outputs=[name]))
        window.mark_dirty()
        window.rebuild()
        window.set_hint(("Added a mirrored copy of " if copy else "Mirrored ") + ", ".join(names) + ".")
        window.cancel_tool()
