"""Design tokens and stylesheet generation.

SimpleCAD's look is defined here and nowhere else: every colour, radius, spacing
step and type size is a token, and the Qt stylesheet is generated from those
tokens. That keeps light and dark genuinely equivalent instead of one being a
retrofit of the other, and it means the viewport's 3D colours (which Qt style
sheets cannot reach) stay in step with the surrounding chrome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Mode(str, Enum):
    LIGHT = "light"
    DARK = "dark"
    SYSTEM = "system"


@dataclass(frozen=True)
class Palette:
    """Every colour the application uses, as hex strings."""

    # Chrome
    bg: str                 # window background, behind everything
    surface: str            # panels, toolbars
    surface_raised: str     # floating panels, menus, popovers
    surface_sunken: str     # input wells, list backgrounds
    border: str             # hairlines
    border_strong: str      # focused/active outlines

    # Text
    text: str
    text_muted: str
    text_faint: str
    text_on_accent: str

    # Accent + state
    accent: str
    accent_hover: str
    accent_press: str
    accent_soft: str        # tinted background for selected rows
    accent_text: str        # accent used as text/icon colour on a surface
    danger: str
    warning: str
    success: str

    # Viewport (3D — applied through OCCT, not QSS)
    view_top: str           # background gradient, top
    view_bottom: str        # background gradient, bottom
    face: str               # default body colour
    body_tints: tuple[str, ...]  # per-body colours, cycled in creation order
    edge: str               # visible isoline/edge colour
    hover: str              # dynamic highlight under the cursor
    selected: str           # confirmed selection
    ghost: str              # live preview of a pending operation
    grid: str
    grid_axis_x: str
    grid_axis_y: str
    cube_box: str           # ViewCube facets
    cube_text: str          # ViewCube labels


DARK = Palette(
    bg="#0F1012",
    surface="#16171A",
    surface_raised="#1D1F23",
    surface_sunken="#121315",
    border="#2A2C31",
    border_strong="#3D4047",
    text="#ECEDEF",
    text_muted="#A0A4AB",
    text_faint="#6C7078",
    text_on_accent="#1A1206",
    accent="#F2A93B",
    accent_hover="#F7BC5E",
    accent_press="#D98F22",
    accent_soft="#33281A",
    accent_text="#F7BC5E",
    danger="#F25C69",
    warning="#FF7A45",
    success="#4CC38A",
    view_top="#202226",
    view_bottom="#0E0F11",
    face="#B8BCC4",
    body_tints=(
        "#B8BCC4",  # slate
        "#A3B1C2",  # steel
        "#A9BFAF",  # sage
        "#C9B4A4",  # clay
        "#CCC4A6",  # sand
        "#B5AFC8",  # lavender
        "#A3C2C2",  # teal
        "#C9ACB1",  # rose
    ),
    edge="#2A2D33",
    hover="#8EC5FF",
    selected="#F2A93B",
    ghost="#8EC5FF",
    grid="#1C1E22",
    grid_axis_x="#8A4B52",
    grid_axis_y="#4A7A5E",
    cube_box="#34373D",
    cube_text="#ECEDEF",
)

LIGHT = Palette(
    bg="#EEEEEC",
    surface="#F6F6F4",
    surface_raised="#FFFFFF",
    surface_sunken="#EFEFED",
    border="#DCDCD8",
    border_strong="#B5B5AF",
    text="#1B1C1E",
    text_muted="#5F6268",
    text_faint="#8E9197",
    text_on_accent="#1B1C1E",
    accent="#E8A33A",
    accent_hover="#F0B457",
    accent_press="#D08A1F",
    accent_soft="#FBEBD0",
    accent_text="#8F5400",
    danger="#D33A48",
    warning="#C2410C",
    success="#1F9D5F",
    view_top="#FAFAF9",
    view_bottom="#DCDDDF",
    face="#C8CBD0",
    body_tints=(
        "#C8CBD0",  # slate
        "#B2C0D2",  # steel
        "#B9CFBF",  # sage
        "#DAC5B4",  # clay
        "#DCD4B6",  # sand
        "#C5BFD8",  # lavender
        "#B3D2D2",  # teal
        "#D9BCC1",  # rose
    ),
    edge="#3A3D44",
    hover="#2F7FE0",
    selected="#E08A12",
    ghost="#2F7FE0",
    grid="#D3D4D6",
    grid_axis_x="#B0707A",
    grid_axis_y="#6A9A80",
    cube_box="#DDDEE0",
    cube_text="#1B1C1E",
)


@dataclass(frozen=True)
class Metrics:
    """Spacing, radius and type scale for the Graphite shell.

    One type scale, used everywhere: ``font_size_xs`` for badges and rail
    labels, ``sm`` for hints and section labels, ``ui`` for dense rows and
    numeric fields, the base size for controls, ``lg`` for panel titles and
    ``title`` for the rare large heading.
    """

    unit: int = 4
    radius_sm: int = 6
    radius: int = 8
    radius_lg: int = 10
    control_height: int = 32
    control_height_lg: int = 36
    font_size_xs: int = 10
    font_size_sm: int = 11
    font_size_ui: int = 12
    font_size: int = 13
    font_size_lg: int = 15
    font_size_title: int = 20
    font_family: str = field(
        default="Inter, 'Segoe UI', 'SF Pro Text', 'Adwaita Sans', system-ui, sans-serif"
    )
    font_mono: str = field(
        default="'JetBrains Mono', 'SF Mono', Consolas, 'DejaVu Sans Mono', monospace"
    )

    def space(self, steps: float) -> int:
        return int(self.unit * steps)


METRICS = Metrics()


def rgb(hex_color: str) -> tuple[float, float, float]:
    """Convert ``#RRGGBB`` to floats in 0..1, for the OCCT viewport."""
    value = hex_color.lstrip("#")
    return (
        int(value[0:2], 16) / 255.0,
        int(value[2:4], 16) / 255.0,
        int(value[4:6], 16) / 255.0,
    )


def resolve(mode: Mode) -> Palette:
    """Pick a palette, following the desktop for :attr:`Mode.SYSTEM`."""
    if mode is Mode.SYSTEM:
        return DARK if _system_prefers_dark() else LIGHT
    return DARK if mode is Mode.DARK else LIGHT


def _system_prefers_dark() -> bool:
    """Ask the desktop, in the order that actually answers.

    Qt is asked *last*, and that is the fix rather than an oversight. SimpleCAD
    runs under ``QT_QPA_PLATFORM=xcb`` because OCCT needs GLX, and on that
    platform Qt never sees GNOME's preference: measured on a machine set to
    ``prefer-dark``, ``styleHints().colorScheme()`` answered ``Light`` and the
    application opened light on a dark desktop every time.

    The portal is the cross-desktop answer and works inside a sandbox; gsettings
    is the direct one and works when no portal is running. Either beats a Qt
    answer that is a guess from a palette.
    """
    for source in (_portal_prefers_dark, _gsettings_prefers_dark, _qt_prefers_dark):
        try:
            answer = source()
        except Exception:  # noqa: BLE001 - detection must never break startup
            answer = None
        if answer is not None:
            return answer
    return False


def _portal_prefers_dark() -> bool | None:
    """``org.freedesktop.appearance``/``color-scheme``: 1 means prefer dark."""
    from PySide6.QtDBus import QDBusConnection, QDBusInterface

    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return None
    interface = QDBusInterface(
        "org.freedesktop.portal.Desktop",
        "/org/freedesktop/portal/desktop",
        "org.freedesktop.portal.Settings",
        bus,
    )
    if not interface.isValid():
        return None
    reply = interface.call("Read", "org.freedesktop.appearance", "color-scheme")
    arguments = reply.arguments()
    if not arguments:
        return None
    value = arguments[0]
    # The portal answers a variant wrapped in a variant.
    for _ in range(3):
        inner = getattr(value, "value", None)
        if inner is None:
            break
        value = inner
    try:
        return int(value) == 1
    except (TypeError, ValueError):
        return None


def _gsettings_prefers_dark() -> bool | None:
    """What GNOME itself says, when there is no portal to ask."""
    import shutil
    import subprocess

    if shutil.which("gsettings") is None:
        return None
    result = subprocess.run(
        ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
        capture_output=True, text=True, timeout=2,
    )
    if result.returncode != 0:
        return None
    return "prefer-dark" in result.stdout


def _qt_prefers_dark() -> bool | None:
    """Qt's own view. Right on Wayland, a guess under xcb -- hence last."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication, QPalette

    app = QGuiApplication.instance()
    if app is None:
        return None
    hints = app.styleHints()
    scheme = getattr(hints, "colorScheme", None)
    if scheme is not None:
        resolved = scheme()
        if resolved != Qt.ColorScheme.Unknown:
            return resolved == Qt.ColorScheme.Dark
    return app.palette().color(QPalette.ColorRole.Window).lightness() < 128


def stylesheet(palette: Palette, metrics: Metrics = METRICS) -> str:
    """Generate the application stylesheet from tokens.

    Graphite: neutral surfaces, hairline borders and a single amber accent, so
    the model stays the most colourful thing on screen. Every standard control
    gets the same four states -- hover, pressed, focus and disabled -- here,
    once, rather than per widget.
    """
    p, m = palette, metrics
    return f"""
* {{
    font-family: {m.font_family};
    font-size: {m.font_size}px;
    color: {p.text};
}}
QWidget#Root, QMainWindow {{ background: {p.bg}; }}
QToolTip {{
    background: {p.surface_raised};
    color: {p.text};
    border: 1px solid {p.border_strong};
    border-radius: {m.radius_sm}px;
    padding: {m.space(1)}px {m.space(2)}px;
    font-size: {m.font_size_ui}px;
}}

/* ---- Surfaces ---- */
QFrame#Panel, QWidget#Panel {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {m.radius}px;
}}
QFrame#FloatingPanel {{
    background: {p.surface_raised};
    border: 1px solid {p.border};
    border-radius: {m.radius_lg}px;
}}
QLabel#PanelTitle {{
    font-size: {m.font_size_lg}px;
    font-weight: 600;
    color: {p.text};
}}
QLabel#Muted, QLabel#Hint {{ color: {p.text_muted}; font-size: {m.font_size_sm}px; }}
QLabel#SectionLabel {{
    color: {p.text_faint}; font-size: {m.font_size_sm}px;
    font-weight: 600; letter-spacing: 0.8px;
}}

/* ---- Buttons ---- */
QPushButton, QToolButton#Segment {{
    background: {p.surface_raised};
    border: 1px solid {p.border};
    border-radius: {m.radius_sm}px;
    padding: 0 {m.space(3)}px;
    min-height: {m.control_height}px;
    color: {p.text};
    font-weight: 500;
}}
QPushButton:hover, QToolButton#Segment:hover {{
    background: {p.surface_sunken}; border-color: {p.border_strong};
}}
QPushButton:pressed, QToolButton#Segment:pressed {{ background: {p.accent_soft}; }}
QPushButton:focus, QToolButton#Segment:focus {{ border-color: {p.accent}; }}
QPushButton:disabled, QToolButton#Segment:disabled {{
    color: {p.text_faint}; border-color: {p.border}; background: {p.surface};
}}
QPushButton[variant="primary"] {{
    background: {p.accent};
    border: 1px solid {p.accent};
    color: {p.text_on_accent};
    font-weight: 600;
}}
QPushButton[variant="primary"]:hover   {{ background: {p.accent_hover}; border-color: {p.accent_hover}; }}
QPushButton[variant="primary"]:pressed {{ background: {p.accent_press}; border-color: {p.accent_press}; }}
QPushButton[variant="primary"]:disabled {{
    background: {p.surface_sunken}; border-color: {p.border}; color: {p.text_faint};
}}
QPushButton[variant="ghost"] {{ background: transparent; border: 1px solid transparent; }}
QPushButton[variant="ghost"]:hover {{ background: {p.surface_sunken}; }}
QPushButton:checked, QToolButton#Segment:checked {{
    background: {p.accent_soft};
    border-color: {p.accent};
    color: {p.accent_text};
}}

/* ---- Inputs ---- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {p.surface_sunken};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: {m.radius_sm}px;
    padding: 0 {m.space(2)}px;
    min-height: {m.control_height}px;
    selection-background-color: {p.accent};
    selection-color: {p.text_on_accent};
}}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{
    border-color: {p.border_strong};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {p.accent};
    background: {p.surface_raised};
}}
QLineEdit[invalid="true"] {{ border: 1px solid {p.danger}; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    color: {p.text_faint}; background: {p.surface};
}}
QLineEdit#ValueField {{
    font-family: {m.font_mono};
    font-size: {m.font_size_ui}px;
    font-weight: 600;
}}
QComboBox::drop-down {{ border: none; width: {m.space(6)}px; }}
QComboBox::down-arrow {{ image: url("{_arrow_file(p.text_muted)}"); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{
    background: {p.surface_raised};
    border: 1px solid {p.border_strong};
    border-radius: {m.radius_sm}px;
    padding: {m.space(1)}px;
    outline: none;
    selection-background-color: {p.accent_soft};
    selection-color: {p.text};
}}
QCheckBox, QRadioButton {{ spacing: {m.space(2)}px; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {p.border_strong};
    background: {p.surface_sunken};
}}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {p.accent}; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {p.accent}; border-color: {p.accent};
}}
QCheckBox:disabled, QRadioButton:disabled {{ color: {p.text_faint}; }}
QSlider::groove:horizontal {{
    height: 4px; border-radius: 2px; background: {p.border};
}}
QSlider::sub-page:horizontal {{ background: {p.accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 14px; height: 14px; margin: -6px 0; border-radius: 7px;
    background: {p.surface_raised}; border: 1px solid {p.border_strong};
}}
QSlider::handle:horizontal:hover {{ border-color: {p.accent}; }}

/* ---- Tabs ---- */
QTabWidget::pane {{ border: 1px solid {p.border}; border-radius: {m.radius}px; top: -1px; }}
QTabBar::tab {{
    background: transparent; color: {p.text_muted};
    padding: {m.space(1.5)}px {m.space(3)}px; border: none;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:hover {{ color: {p.text}; }}
QTabBar::tab:selected {{ color: {p.text}; border-bottom: 2px solid {p.accent}; }}

/* ---- Lists / trees ---- */
QTreeView, QListView, QTableView {{
    background: transparent;
    border: none;
    outline: none;
    show-decoration-selected: 1;
}}
QTreeView::item, QListView::item {{
    min-height: {m.space(7)}px;
    border-radius: {m.radius_sm}px;
    padding: {m.space(0.5)}px {m.space(1.5)}px;
}}
QTreeView::item:hover, QListView::item:hover {{ background: {p.surface_sunken}; }}
QTreeView::branch {{ background: transparent; }}
QTreeView::branch:selected {{ background: {p.accent_soft}; }}
QTreeWidget#BodiesTree::item {{ border-radius: 0px; }}
QTreeView::item:selected, QListView::item:selected {{
    background: {p.accent_soft};
    color: {p.text};
}}
QHeaderView::section {{
    background: transparent; color: {p.text_faint}; border: none;
    border-bottom: 1px solid {p.border}; padding: {m.space(1)}px {m.space(2)}px;
    font-size: {m.font_size_sm}px; font-weight: 600;
}}

/* ---- Scrollbars ---- */
QScrollBar:vertical, QScrollBar:horizontal {{ background: transparent; width: 8px; height: 8px; margin: 0; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {p.border_strong}; border-radius: 4px; min-height: 32px; min-width: 32px;
}}
QScrollBar::handle:hover {{ background: {p.text_faint}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    background: none; border: none; height: 0; width: 0;
}}

/* ---- Menus ---- */
QMenu {{
    background: {p.surface_raised};
    border: 1px solid {p.border_strong};
    border-radius: {m.radius}px;
    padding: {m.space(1)}px;
}}
QMenu::item {{
    padding: {m.space(1.5)}px {m.space(6)}px {m.space(1.5)}px {m.space(3)}px;
    border-radius: {m.radius_sm}px;
    min-width: 180px;
}}
QMenu::item:selected {{ background: {p.accent_soft}; }}
QMenu::item:disabled {{ color: {p.text_faint}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: {m.space(1)}px {m.space(2)}px; }}

/* ---- Dialogs ---- */
QDialog {{ background: {p.surface}; }}
QMessageBox QLabel {{ color: {p.text}; }}

/* ---- Misc ---- */
QSplitter::handle {{ background: transparent; }}
QToolTip, QMenu, QFrame#FloatingPanel {{ font-size: {m.font_size}px; }}
"""


def _arrow_file(colour: str) -> str:
    """A chevron for combo boxes, written once per colour.

    Qt style sheets take images only by URL, and a styled combo box draws no
    arrow at all without one -- every drop-down looked like a text field.
    """
    import os
    import tempfile

    from .icons import svg

    folder = os.path.join(tempfile.gettempdir(), f"simplecad-icons-{os.getuid() if hasattr(os, 'getuid') else 0}")
    path = os.path.join(folder, f"chevron-{colour.lstrip('#')}.svg")
    if not os.path.exists(path):
        os.makedirs(folder, exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(svg("chevron_down", colour, 12, 2.0))
    return path.replace(os.sep, "/")


_FONTS: list[str] | None = None


def load_fonts() -> list[str]:
    """Register the bundled Inter and JetBrains Mono with Qt.

    Bundling is what makes the type identical on Linux, Windows and macOS --
    without it the stack above falls back to whatever each desktop ships.
    Returns the families that registered; a missing file is skipped, never fatal.
    """
    global _FONTS
    if _FONTS is not None:
        return _FONTS
    from pathlib import Path

    from PySide6.QtGui import QFontDatabase

    folder = Path(__file__).resolve().parents[1] / "assets" / "fonts"
    families: list[str] = []
    for path in sorted(folder.glob("*.ttf")):
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id >= 0:
            families.extend(QFontDatabase.applicationFontFamilies(font_id))
    _FONTS = sorted(set(families))
    return _FONTS
