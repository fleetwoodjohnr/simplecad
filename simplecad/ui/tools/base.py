"""The shape every tool takes.

One state machine -- activate, preview, commit, cancel -- so that
``Select -> Drag -> Type -> Enter`` works the same way everywhere, Esc always
cancels, and Enter always confirms. Tools own a floating panel rather than a
modal dialog, so the model stays visible and editable while they are open.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
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
    ) -> ValueField:
        field = ValueField(
            self._palette, self.window_.document.parameters, dimension, value
        )
        field.returnPressed.connect(self.commit)
        field.committed.connect(lambda _v: self.preview())
        self.body.addWidget(LabeledField(label, field, self._palette))
        self.fields[key] = field
        return field

    def add_section(self, text: str) -> None:
        self.body.addWidget(SectionLabel(text, self._palette))

    def add_widget(self, widget: QWidget) -> None:
        self.body.addWidget(widget)

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
