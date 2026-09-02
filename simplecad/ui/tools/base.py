"""The shape every tool takes.

One state machine -- activate, preview, commit, cancel -- so that
``Select -> Drag -> Type -> Enter`` works the same way everywhere, Esc always
cancels, and Enter always confirms. Tools own a floating panel rather than a
modal dialog, so the model stays visible and editable while they are open.
"""

from __future__ import annotations

import itertools

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ...core.units import Dimension
from ..theme import METRICS, Palette
from ..widgets.controls import (
    FloatingCard, GhostButton, IconButton, LabeledField, PrimaryButton,
    SectionLabel, ValueField,
)


class ToolPanel(FloatingCard):
    """Base class for a tool's floating panel."""

    #: Marks this as a tool panel so cancel_tool can sweep it away.
    is_tool_panel = True
    title = "Tool"
    confirm_label = "Done"
    width = 292

    def __init__(self, window, palette: Palette | None = None) -> None:
        # The registry instantiates tools with just the window; the palette is
        # the window's unless a caller deliberately overrides it.
        palette = palette or window.palette_
        super().__init__(palette, window.stage)
        self.window_ = window
        self._palette = palette
        self.fields: dict[str, ValueField] = {}
        self.setFixedWidth(self.width)

        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(
            METRICS.space(4), METRICS.space(3.5), METRICS.space(4), METRICS.space(3.5)
        )
        self.root.setSpacing(METRICS.space(2.5))

        header = QHBoxLayout()
        self.title_label = QLabel(self.title)
        self.title_label.setObjectName("PanelTitle")
        close = IconButton("close", palette, "Cancel  (Esc)", size=30)
        close.clicked.connect(window.cancel_tool)
        header.addWidget(self.title_label)
        header.addStretch(1)
        header.addWidget(close)
        self.root.addLayout(header)

        self.subtitle = QLabel("")
        self.subtitle.setWordWrap(True)
        self.subtitle.setStyleSheet(
            f"color:{palette.text_muted}; font-size:12px;"
        )
        self.root.addWidget(self.subtitle)

        self.body = QVBoxLayout()
        self.body.setSpacing(METRICS.space(1.5))
        self.root.addLayout(self.body)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color:{palette.warning}; font-size:12px;")
        self.status.hide()
        self.root.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.setSpacing(METRICS.space(2))
        cancel = GhostButton("Cancel")
        cancel.clicked.connect(window.cancel_tool)
        self.confirm = PrimaryButton(self.confirm_label)
        self.confirm.clicked.connect(self.commit)
        buttons.addWidget(cancel)
        buttons.addWidget(self.confirm, 1)
        self.root.addLayout(buttons)

        self.build()
        self.adjustSize()

    # -- construction helpers -------------------------------------------
    def add_field(
        self,
        key: str,
        label: str,
        value: float,
        dimension: Dimension = Dimension.LENGTH,
        into=None,
    ) -> ValueField:
        field = ValueField(
            self._palette, self.window_.document.parameters, dimension, value
        )
        field.returnPressed.connect(self.commit)
        field.committed.connect(lambda _v: self.preview())
        (into or self.body).addWidget(LabeledField(label, field, self._palette))
        self.fields[key] = field
        return field

    def add_section(self, text: str, into=None) -> None:
        (into or self.body).addWidget(SectionLabel(text, self._palette))

    def add_widget(self, widget: QWidget, into=None) -> None:
        (into or self.body).addWidget(widget)

    def value(self, key: str, default: float = 0.0) -> float:
        field = self.fields.get(key)
        return field.value() if field else default

    def expression(self, key: str, default: str = "0") -> str:
        field = self.fields.get(key)
        return field.expression() if field else default

    def warn(self, message: str) -> None:
        self.status.setText(message)
        self.status.setVisible(bool(message))
        self.adjustSize()

    def set_subtitle(self, text: str) -> None:
        self.subtitle.setText(text)
        self.subtitle.setVisible(bool(text))

    def relayout(self) -> None:
        """Resize to fit whatever the panel is now showing."""
        self.adjustSize()
        self.window_.stage._layout_overlays()

    # -- lifecycle -------------------------------------------------------
    def build(self) -> None:
        """Populate the panel. Subclasses override."""

    def preview(self) -> None:
        """Show the pending result without committing. Optional."""

    def commit(self) -> None:
        """Apply the operation. Subclasses override."""
        self.window_.cancel_tool()

    def teardown(self) -> None:
        """Undo anything this panel turned on in the viewport.

        Called by ``close_tool_panels`` just before the panel is destroyed.
        There is no ``closeEvent`` to hang this on: panels are dismissed with
        ``hide()`` and ``deleteLater()``, and neither of those sends one -- so a
        tool relying on ``closeEvent`` silently never cleaned up after itself,
        which is how the measure tool used to leave the viewport stuck in
        point-picking mode with its overlay still on screen.
        """

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.window_.cancel_tool()
            return
        super().keyPressEvent(event)


_preview_tokens = itertools.count(1)


class FeaturePreviewController:
    """Debounced, crash-isolated preview requests for a tool panel.

    The geometry process has one shared preview signal, so every request gets a
    session-unique token and a closed panel disconnects explicitly.  Callers
    receive the complete protocol message: physical-thread panels need both the
    shape and the kernel's failure text before enabling their Create button.
    """

    def __init__(self, panel: ToolPanel, callback, delay_ms: int = 110) -> None:
        self.panel = panel
        self.callback = callback
        self._state: dict | None = None
        self._token: int | None = None
        self._closed = False
        self.timer = QTimer(panel)
        self.timer.setSingleShot(True)
        self.timer.setInterval(delay_ms)
        self.timer.timeout.connect(self._send)
        panel.window_.geometry.previewed.connect(self._answered)

    def request(self, state: dict | None) -> None:
        self._state = state
        self._token = None
        self.timer.stop()
        if state is None:
            self.callback({
                "token": None,
                "shape": None,
                "error": None,
            })
            return
        self.timer.start()

    def _send(self) -> None:
        if self._closed or self._state is None:
            return
        self._token = next(_preview_tokens)
        if self.panel.window_.geometry.preview(self._state, self._token):
            return
        self.callback({"token": self._token, **self._in_process()})

    def _in_process(self) -> dict:
        """Answer the preview here, because the geometry child is not there.

        Rebuilds already fall back to in-process when the child cannot start --
        the application is documented as staying usable that way. Previews did
        not, and the difference is not cosmetic: a preview-gated tool keeps its
        Create button disabled until a shape comes back, so Thread and Create
        Matching Part were permanently dead on any machine whose child process
        failed to launch, saying only "the geometry engine is unavailable".

        The same pure function the child runs, run here, and serialised the same
        way so the callback cannot tell the two apart.
        """
        from ...core.geometry_service import build_preview_result, serialise_shape

        try:
            result = build_preview_result(self.panel.window_.document, self._state)
            shape = result.get("shape")
            return {**result, "shape": serialise_shape(shape) if shape else None}
        except BaseException as exc:  # noqa: BLE001 - OCCT raises non-Exceptions
            return {
                "shape": None,
                "error": str(exc) or "The preview could not be built.",
                "warnings": [],
                "inputs": {},
                "message": "",
            }

    def _answered(self, message: dict) -> None:
        if self._closed or message.get("token") != self._token:
            return
        self.callback(message)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.timer.stop()
        try:
            self.panel.window_.geometry.previewed.disconnect(self._answered)
        except (RuntimeError, TypeError):
            pass
