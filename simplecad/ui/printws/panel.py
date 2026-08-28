"""The 3D Print workspace.

Everything between a finished model and a printed part: put it on the plate,
turn it so it prints well, see what a printer will make of it, and hand it to a
slicer.

The analysis reports; it never blocks. Every finding says what it found in
millimetres and degrees, and what to do about it.
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ...core.document import BodyRef
from ...kernel.operations import MoveFeature
from ...kernel.printing import Severity, analyse, best_print_orientation
from ...kernel.thread_specs import printer_profile
from ..icons import icon
from ..theme import METRICS
from ..widgets.controls import GhostButton, PrimaryButton, SectionLabel
from ..tools.base import ToolPanel
from ..tools.registry import register_tool


class FindingRow(QWidget):
    """One analysis result: an icon, a message, and what to do about it."""

    def __init__(self, finding, palette, width: int, parent=None) -> None:
        super().__init__(parent)
        self._text_width = max(120, width - 44)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, METRICS.space(0.5), 0, METRICS.space(0.5))
        layout.setSpacing(METRICS.space(2))

        colour, name = {
            Severity.OK: (palette.success, "check"),
            Severity.NOTE: (palette.text_muted, "warning"),
            Severity.WARNING: (palette.warning, "warning"),
        }[finding.severity]

        badge = QLabel()
        badge.setPixmap(icon(name, colour, 16).pixmap(16, 16))
        badge.setFixedWidth(20)
        badge.setAlignment(Qt.AlignTop)
        layout.addWidget(badge)

        text = QLabel(
            finding.message if not finding.detail
            else f"{finding.message}<br><span style='color:{palette.text_faint}'>"
                 f"{finding.detail}</span>"
        )
        text.setWordWrap(True)
        text.setTextFormat(Qt.RichText)
        # A word-wrapped label reports no useful height until it knows how wide
        # it will be, which leaves the whole list collapsed to nothing.
        text.setFixedWidth(self._text_width)
        text.setMinimumHeight(text.heightForWidth(self._text_width))
        text.setStyleSheet(
            f"color:{colour if finding.severity is not Severity.OK else palette.text_muted};"
            "font-size:12px;"
        )
        layout.addWidget(text, 1)


@register_tool("print")
class PrintPanel(ToolPanel):
    title = "3D Print"
    confirm_label = "Export for Printing"
    width = 340

    def build(self) -> None:
        self.profile = printer_profile()
        self.set_subtitle(
            f"{self.profile['name']} — "
            f"{self.profile['build_volume']['x']:.0f} x "
            f"{self.profile['build_volume']['y']:.0f} x "
            f"{self.profile['build_volume']['z']:.0f} mm, "
            f"{self.profile['nozzle_diameter']:.1f} mm nozzle"
        )

        self.add_section("Placement")
        placement = QWidget()
        row = QHBoxLayout(placement)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(METRICS.space(1))
        for label, handler in (
            ("Place on plate", self.place_on_plate),
            ("Center", self.center_on_plate),
            ("Orient", self.orient_for_printing),
        ):
            button = GhostButton(label)
            button.clicked.connect(handler)
            row.addWidget(button)
        self.add_widget(placement)

        self.add_section("Printability")
        self.findings_host = QWidget()
        self.findings_layout = QVBoxLayout(self.findings_host)
        self.findings_layout.setContentsMargins(0, 0, 0, 0)
        self.findings_layout.setSpacing(METRICS.space(0.5))
        self.add_widget(self.findings_host)

        self.verdict = QLabel("")
        self.verdict.setWordWrap(True)
        self.add_widget(self.verdict)

        slicer = GhostButton("Export and open in slicer")
        slicer.clicked.connect(self.open_in_slicer)
        self.add_widget(slicer)

        self.refresh_analysis()

    # -- analysis --------------------------------------------------------
    def _shapes(self) -> list:
        """Every visible body, flat. Plate placement and analysis want solids."""
        return [b.shape for b in self.window_.document.visible_bodies()]

    def _items(self) -> list:
        """What to write to a file: a group counts as one object."""
        return self.window_.document.export_items()

    def refresh_analysis(self) -> None:
        while self.findings_layout.count():
            item = self.findings_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        analysis = analyse(self._shapes(), self.profile)
        inner = self.width - 2 * METRICS.space(4)
        height = 0
        for finding in analysis.findings:
            row = FindingRow(finding, self._palette, inner, self.findings_host)
            row.adjustSize()
            height += row.sizeHint().height() + METRICS.space(0.5)
            self.findings_layout.addWidget(row)
        self.findings_host.setMinimumHeight(height)
        colour = {
            Severity.OK: self._palette.success,
            Severity.NOTE: self._palette.text_muted,
            Severity.WARNING: self._palette.warning,
        }[analysis.worst]
        self.verdict.setStyleSheet(f"color:{colour}; font-size:12.5px; font-weight:600;")
        self.verdict.setText(analysis.summary())
        self.adjustSize()
        self.window_.stage._layout_overlays()
        self.analysis = analysis

    # -- placement -------------------------------------------------------
    def _move_all(self, offset, label: str) -> None:
        from ...kernel.printing import place_on_plate

        bodies = [b.name for b in self.window_.document.visible_bodies()]
        if not bodies:
            self.window_.set_hint("There is nothing on the plate yet.")
            return
        self.window_.history.record(label)
        for name in bodies:
            self.window_.document.add_feature(
                MoveFeature(
                    inputs={"body": BodyRef(name), "dx": offset[0],
                            "dy": offset[1], "dz": offset[2]},
                    outputs=[name],
                )
            )
        self.window_.mark_dirty()
        self.window_.rebuild()
        self.window_.wait_for_rebuild()
        self.refresh_analysis()
        self.window_.set_hint(label)

    def place_on_plate(self) -> None:
        from ...kernel.printing import place_on_plate

        self._move_all(place_on_plate(self._shapes()), "Placed on the build plate")

    def center_on_plate(self) -> None:
        from ...kernel.printing import center_on_plate

        self._move_all(
            center_on_plate(self._shapes(), self.profile),
            "Centred on the build plate",
        )

    def orient_for_printing(self) -> None:
        """Turn the model so the least surface overhangs."""
        shapes = self._shapes()
        if not shapes:
            self.window_.set_hint("There is nothing to orient yet.")
            return
        axis, angle = best_print_orientation(shapes[0], self.profile)
        if not angle:
            self.window_.set_hint("This is already the best of the six flat lays.")
            self.refresh_analysis()
            return

        self.window_.history.record("Rotate for printing")
        for body in list(self.window_.document.visible_bodies()):
            self.window_.document.add_feature(
                MoveFeature(
                    inputs={
                        "body": BodyRef(body.name),
                        "rx": angle if axis[0] else 0,
                        "ry": angle if axis[1] else 0,
                        "rz": angle if axis[2] else 0,
                    },
                    outputs=[body.name],
                )
            )
        self.window_.mark_dirty()
        self.window_.rebuild()
        self.window_.wait_for_rebuild()
        self.place_on_plate()
        self.window_.set_hint(f"Rotated {angle:.0f}° for printing.")

    # -- export ----------------------------------------------------------
    def commit(self) -> None:
        """Export for Printing: 3MF, the format slicers prefer."""
        from PySide6.QtWidgets import QFileDialog

        from ...kernel.io_formats import export_shapes

        items = self._items()
        if not items:
            self.window_.set_hint("There is nothing to export yet.")
            return
        path, _filter = QFileDialog.getSaveFileName(
            self, "Export for printing",
            f"{self.window_.document.title}.3mf",
            "3MF for slicers (*.3mf);;STL (*.stl)",
        )
        if not path:
            return
        try:
            export_shapes(items, path)
        except Exception as exc:  # noqa: BLE001
            from ...core.errors import translate

            self.warn(str(translate(exc, "export")))
            return
        warnings = self.analysis.warnings()
        note = f" ({len(warnings)} warning(s) noted)" if warnings else ""
        self.window_.set_hint(f"Exported {os.path.basename(path)}{note}")

    def open_in_slicer(self) -> None:
        import tempfile

        from ...kernel.io_formats import export_shapes
        from ...kernel.printing import open_in_slicer

        items = self._items()
        if not items:
            self.window_.set_hint("There is nothing to slice yet.")
            return
        path = os.path.join(
            tempfile.mkdtemp(prefix="simplecad-"),
            f"{self.window_.document.title}.3mf",
        )
        try:
            export_shapes(items, path)
            name = open_in_slicer(path)
        except Exception as exc:  # noqa: BLE001
            from ...core.errors import translate

            self.warn(str(translate(exc, "export")))
            return
        self.window_.set_hint(f"Opened in {name}.")
