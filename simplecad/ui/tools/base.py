"""The shape every tool takes.

One state machine -- activate, preview, commit, cancel -- so that
``Select -> Drag -> Type -> Enter`` works the same way everywhere, Esc always
cancels, and Enter always confirms. Tools own a floating panel rather than a
modal dialog, so the model stays visible and editable while they are open.
"""

from __future__ import annotations

import itertools

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractScrollArea, QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from ...core.units import Dimension
from ..theme import METRICS, Palette
from ..widgets.controls import (
    FloatingCard, GhostButton, IconButton, LabeledField, PrimaryButton,
    SectionLabel, ValueField,
)


class ToolTabs(QWidget):
    """A segmented strip that switches between sibling tools on one selection.

    Used by Align & Arrange, whose three jobs -- line objects up, put one on
    another, lay several out -- are separate panels with their own previews
    and commits. The strip makes them one place to go without merging them.
    """

    def __init__(self, window, tabs, current: str, parent=None) -> None:
        from PySide6.QtWidgets import QButtonGroup, QToolButton

        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.buttons = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for index, (key, label) in enumerate(tabs):
            button = QToolButton(self)
            button.setObjectName("Segment")
            button.setText(label.replace("&", "&&"))
            button.setCheckable(True)
            button.setChecked(key == current)
            button.setCursor(Qt.PointingHandCursor)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.setMinimumHeight(METRICS.control_height)
            radius = METRICS.radius_sm
            left = f"{radius}px" if index == 0 else "0px"
            right = f"{radius}px" if index == len(tabs) - 1 else "0px"
            button.setStyleSheet(
                f"QToolButton#Segment {{ border-top-left-radius:{left};"
                f"border-bottom-left-radius:{left}; border-top-right-radius:{right};"
                f"border-bottom-right-radius:{right}; }}"
            )
            if key != current:
                button.clicked.connect(lambda _=False, k=key: window.activate_tool(k))
            group.addButton(button)
            layout.addWidget(button)
            self.buttons[key] = button


class ToolPanel(FloatingCard):
    """Base class for a tool's floating panel."""

    #: Marks this as a tool panel so cancel_tool can sweep it away.
    is_tool_panel = True
    title = "Tool"
    confirm_label = "Done"
    width = 312

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
            METRICS.space(3.5), METRICS.space(3.5),
            METRICS.space(3.5), METRICS.space(3.5),
        )
        self.root.setSpacing(METRICS.space(2))

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
            f"color:{palette.text_muted}; font-size:{METRICS.font_size_ui}px;"
        )
        self.root.addWidget(self.subtitle)

        self.body_host = QWidget(self)
        self.body_host.setObjectName("ToolBody")
        self.body = QVBoxLayout(self.body_host)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(METRICS.space(1.5))
        self.body_scroll = QScrollArea(self)
        self.body_scroll.setObjectName("ToolBodyScroll")
        self.body_scroll.setFrameShape(QFrame.NoFrame)
        self.body_scroll.setWidgetResizable(True)
        self.body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.body_scroll.setSizeAdjustPolicy(QAbstractScrollArea.AdjustToContents)
        self.body_scroll.setWidget(self.body_host)
        self.body_scroll.setStyleSheet(
            "QScrollArea#ToolBodyScroll, QScrollArea#ToolBodyScroll > QWidget > QWidget "
            "{ background: transparent; border: none; }"
        )
        self.root.addWidget(self.body_scroll, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color:{palette.warning}; font-size:{METRICS.font_size_ui}px;")
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
        self.fit_to_height(max(460, self.window_.stage.height() - METRICS.space(8)))
        self.adjustSize()

    # -- construction helpers -------------------------------------------
    def add_field(
        self,
        key: str,
        label: str,
        value: float,
        dimension: Dimension = Dimension.LENGTH,
        into=None,
        commit_on_return: bool = True,
    ) -> ValueField:
        field = ValueField(
            self._palette, self.window_.document.parameters, dimension, value
        )
        field.returnPressed.connect(self.commit if commit_on_return else self.preview)
        field.committed.connect(lambda _v: self.preview())
        (into or self.body).addWidget(LabeledField(label, field, self._palette))
        self.fields[key] = field
        return field

    def _choices(self, options, changed, columns=3) -> tuple[QWidget, dict]:
        """A segmented row of checkable buttons, the house pattern for a choice."""
        host = QWidget()
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(METRICS.space(1))
        buttons = {}
        for index, (key, label) in enumerate(options):
            button = GhostButton(label)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, k=key: changed(k))
            grid.addWidget(button, index // columns, index % columns)
            buttons[key] = button
        return host, buttons

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
        visible = bool(message)
        if self.status.text() == message and self.status.isVisible() == visible:
            return
        self.status.setText(message)
        self.status.setVisible(visible)
        # Status text is fixed chrome below the scroll area. Re-budget the body
        # whenever it appears or changes height so the last control never sits
        # underneath the message or Confirm row.
        self.fit_to_height(max(460, self.window_.stage.height() - METRICS.space(8)))
        self.adjustSize()
        self.window_.stage._layout_overlays()

    def set_subtitle(self, text: str) -> None:
        changed = text != self.subtitle.text()
        self.subtitle.setText(text)
        self.subtitle.setVisible(bool(text))
        if changed and self.isVisible():
            # A longer description wraps onto more lines: re-budget the body.
            self.fit_to_height(max(460, self.window_.stage.height() - METRICS.space(8)))
            self.adjustSize()

    def relayout(self) -> None:
        """Resize to fit whatever the panel is now showing."""
        self.fit_to_height(max(460, self.window_.stage.height() - METRICS.space(8)))
        self.adjustSize()
        self.window_.stage._layout_overlays()

    def fit_to_height(self, maximum: int) -> None:
        """Keep panel actions visible; scroll only a long tool's controls."""
        maximum = max(360, int(maximum))
        # Measure with the stylesheet's fonts and padding, not Qt's defaults.
        self.ensurePolished()
        self.subtitle.ensurePolished()
        self.body_host.adjustSize()
        margins = self.root.contentsMargins()
        chrome = margins.top() + margins.bottom()
        inner = self.contentsRect().width() - margins.left() - margins.right()
        visible_items = []
        for index in range(self.root.count()):
            item = self.root.itemAt(index)
            widget = item.widget()
            if widget is self.body_scroll:
                continue
            if widget is not None and widget.isHidden():
                continue
            # A wrapped label's plain hint is its height on one long line; at
            # the panel's real width it is several lines taller, and the
            # difference was squeezed out of the controls above the buttons.
            if widget is not None and widget.hasHeightForWidth():
                height = widget.heightForWidth(inner)
            else:
                height = item.sizeHint().height()
            if height > 0:
                visible_items.append(height)
        chrome += sum(visible_items)
        chrome += self.root.spacing() * max(0, len(visible_items))
        # A few pixels of allowance: hints measured before the stylesheet
        # polishes every control run short, and the last row was clipped
        # under the Cancel / Done buttons.
        content = max(40, self.body_host.sizeHint().height()) + METRICS.space(2)
        available = max(120, maximum - chrome)
        body = min(content, available)
        self.body_scroll.setFixedHeight(body)
        self.setMaximumHeight(maximum)
        # Set the height from that budget rather than leaving it to the
        # layout's hint, which counts a wrapped subtitle as one line: the panel
        # came out short and the Cancel / Done row overlapped the last control.
        self.setFixedHeight(min(maximum, chrome + body))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # Polished now, so measure again with the real control heights.
        self.fit_to_height(max(460, self.window_.stage.height() - METRICS.space(8)))
        self.adjustSize()

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

    def handle_arrow_key(self, key: int, modifiers=None) -> bool:
        """Handle a model-space arrow key (plain, or with Ctrl) for this tool."""
        return False

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

    def __init__(self, panel: ToolPanel, callback, delay_ms: int = 110,
                 allow_in_process: bool = False) -> None:
        self.panel = panel
        self.callback = callback
        self._state: dict | None = None
        self._token: int | None = None
        self._tokens: set[int] = set()
        self._closed = False
        self._retried = False
        self.allow_in_process = allow_in_process
        self.timer = QTimer(panel)
        self.timer.setSingleShot(True)
        self.timer.setInterval(delay_ms)
        self.timer.timeout.connect(self._send)
        panel.window_.geometry.previewed.connect(self._answered)

    def request(self, state: dict | None) -> None:
        self._state = state
        self._token = None
        cancel = getattr(self.panel.window_.geometry, "cancel_preview", None)
        if cancel is not None and state is None:
            for token in tuple(self._tokens):
                cancel(token)
            self._tokens.clear()
        self._retried = False
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
        self._tokens.add(self._token)
        geometry = self.panel.window_.geometry
        if not geometry.available and not self.allow_in_process:
            if geometry.start():
                geometry.sync_cached(self.panel.window_.document)
        if self.panel.window_.geometry.preview(self._state, self._token):
            return
        if self.allow_in_process:
            self.callback({"token": self._token, **self._in_process()})
        else:
            self.callback({
                "token": self._token, "shape": None,
                "error": "The geometry engine is unavailable.",
            })

    def _in_process(self) -> dict:
        """Explicit synchronous helper for controlled tests and diagnostics.

        Interactive tools leave allow_in_process=False so a failed worker
        cannot move expensive or crash-prone OCCT work into the window.
        """
        from ...core.geometry_service import build_preview_result, serialise_shape

        try:
            result = build_preview_result(self.panel.window_.document, self._state)
            shape = result.get("shape")
            parts = {
                name: serialise_shape(part)
                for name, part in result.get("parts", {}).items()
                if part is not None
            }
            return {
                **result,
                "shape": serialise_shape(shape) if shape else None,
                "parts": parts,
            }
        except BaseException as exc:  # noqa: BLE001 - OCCT raises non-Exceptions
            return {
                "shape": None,
                "parts": {},
                "error": str(exc) or "The preview could not be built.",
                "warnings": [],
                "inputs": {},
                "message": "",
            }

    def _answered(self, message: dict) -> None:
        self._tokens.discard(message.get("token"))
        if self._closed or message.get("token") != self._token:
            return
        if message.get("retryable") and not message.get("timeout") and self._state is not None and not self._retried:
            # The engine let go of this request -- it crashed, or restarted --
            # rather than answering it. It has been brought back and given the
            # document again, so ask once more before telling the user their
            # tool is broken.
            self._retried = True
            self.timer.start()
            return
        self.callback(message)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.timer.stop()
        cancel = getattr(self.panel.window_.geometry, "cancel_preview", None)
        if cancel is not None:
            for token in tuple(self._tokens):
                cancel(token)
            self._tokens.clear()
        try:
            self.panel.window_.geometry.previewed.disconnect(self._answered)
        except (RuntimeError, TypeError):
            pass
