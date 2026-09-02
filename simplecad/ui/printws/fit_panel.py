"""Fit & Clearance.

Clearance is a property of a printer, not of a model. This panel shows what each
fit class currently means, generates a calibration model to find out what it
*should* mean on this machine, and stores the answer so every later thread and
fit uses it.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel

from ...core.units import Dimension
from ...kernel.calibration import (
    DEFAULT_SWEEP, build_model, effective_fits, effective_thread_clearance,
    load_measurements, save_measurements,
)
from ...kernel.thread_specs import fit_presets, printer_profile
from ..theme import METRICS
from ..widgets.controls import GhostButton, SectionLabel
from ..tools.base import ToolPanel
from ..tools.registry import register_tool


@register_tool("fit")
class FitPanel(ToolPanel):
    title = "Fit & Clearance"
    confirm_label = "Save measurements"
    width = 340

    def build(self) -> None:
        self.profile = printer_profile()
        self.printer_id = self.profile["id"]
        measured = load_measurements(self.printer_id)
        self.set_subtitle(
            f"{self.profile['name']} — "
            + ("using your measured values."
               if measured else
               "using shipped defaults. Print the calibration model to tune them.")
        )

        self.add_section("Fits")
        current = effective_fits(self.printer_id)
        for key, entry in fit_presets().items():
            field = self.add_field(
                f"fit_{key}", entry["label"], current.get(key, entry["clearance"])
            )
            field.setToolTip(entry["hint"])

        self.add_section("Thread clearance")
        default = effective_thread_clearance(self.printer_id, "normal")
        self.add_field("thread", "Normal (diametral)", default)

        self.add_section("Calibration")
        note = QLabel(
            "Generates a pin strip and a matching plate with holes at "
            f"{DEFAULT_SWEEP[0]:.2f}–{DEFAULT_SWEEP[-1]:.2f} mm clearance, each "
            "labelled. Print both, find the first pair that fits the way you "
            "want, and enter that number above."
        )
        note.setWordWrap(True)
        note.setStyleSheet(
            f"color:{self._palette.text_muted}; font-size:12px;"
        )
        self.add_widget(note)

        generate = GhostButton("Add calibration model to the document")
        generate.clicked.connect(self.generate)
        self.add_widget(generate)

        thread_generate = GhostButton("Add P30 thread gauges to the document")
        thread_generate.clicked.connect(self.generate_threads)
        self.add_widget(thread_generate)

    def generate(self) -> None:
        """Put the calibration pieces in the document so they can be exported."""
        from ...core.document import Body

        self.window_.set_hint("Building the calibration model…")
        try:
            model = build_model()
        except Exception as exc:  # noqa: BLE001
            from ...core.errors import translate

            self.warn(str(translate(exc, "calibration")))
            return

        document = self.window_.document
        self.window_.history.record("Calibration model")
        for name, shape in (
            ("Calibration pins", model.pins),
            ("Calibration plate", model.plate),
        ):
            unique = document.unique_name(name)
            body = Body(name=unique, shape=shape)
            document.bodies[unique] = body
            self.window_._shown.pop(unique, None)
        self.window_.mark_dirty()
        self.window_.refresh_view()
        self.window_.browser.refresh()
        self.window_.stage.viewport.fit_all()
        self.window_.set_hint(
            f"{model.describe()}. Export it, print both pieces, then come back "
            "with the numbers."
        )

    def generate_threads(self) -> None:
        """Add parametric P30 gauges; the normal geometry worker builds them."""
        from ...kernel.fasteners import MatchingBoltFeature, MatchingNutFeature

        designation = "P30"
        pitch = 5.0
        length = pitch * 2.0
        clearances = (0.40, 0.60, 0.80, 1.00)
        document = self.window_.document
        self.window_.history.record("P30 thread calibration")

        plug_name = document.unique_name("P30 calibration plug")
        document.add_feature(MatchingBoltFeature(
            inputs={
                "designation": designation,
                "length": length,
                "thread_length": length,
                "form": "printed",
                "x": 30.0,
                "y": 30.0,
            },
            outputs=[plug_name],
        ))
        spacing = 55.0
        for index, clearance in enumerate(clearances):
            name = document.unique_name(f"P30 gauge {clearance:.2f} mm")
            document.add_feature(MatchingNutFeature(
                inputs={
                    "designation": designation,
                    "height": length,
                    "clearance": clearance,
                    "form": "printed",
                    "x": 30.0 + spacing * (1 + index % 2),
                    "y": 30.0 + spacing * (index // 2),
                },
                outputs=[name],
            ))
        self.window_.mark_dirty()
        self.window_.rebuild()
        self.window_.set_hint(
            "Building the P30 plug and 0.40/0.60/0.80/1.00 mm gauges. "
            "Print them axis-up and save the smallest value that turns freely."
        )

    def commit(self) -> None:
        fits = {
            key: self.value(f"fit_{key}", entry["clearance"])
            for key, entry in fit_presets().items()
        }
        path = save_measurements(
            self.printer_id, self.profile, fits, self.value("thread", 0.60)
        )
        self.window_.set_hint(f"Saved your measured fits to {path}")
        self.window_.cancel_tool()
