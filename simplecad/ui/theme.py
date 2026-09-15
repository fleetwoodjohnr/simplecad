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
    bg="#0B1018",
    surface="#111823",
    surface_raised="#18212E",
    surface_sunken="#0C121B",
    border="#253247",
    border_strong="#3A4D68",
    text="#EAF2FF",
    text_muted="#9AABC0",
    text_faint="#61738A",
    text_on_accent="#FFFFFF",
    accent="#2F9BFF",
    accent_hover="#55B0FF",
    accent_press="#1E79D0",
    accent_soft="#163452",
    danger="#F0616D",
    warning="#E8A33D",
    success="#3FBF7F",
    view_top="#1B2635",
    view_bottom="#090E15",
    face="#B9C2CF",
    body_tints=(
        "#B9C2CF",  # slate
        "#9FB4CC",  # steel blue
        "#A8C0AE",  # sage
        "#CBB2A0",  # clay
        "#CFC5A2",  # sand
        "#B6ADC9",  # lavender
        "#9FC3C4",  # teal
        "#CBA9AE",  # rose
    ),
    edge="#263447",
    hover="#55B0FF",
    selected="#FF9A3D",
    ghost="#2F9BFF",
    grid="#172130",
    grid_axis_x="#87505A",
    grid_axis_y="#477765",
    cube_box="#33445B",
    cube_text="#EAF2FF",
)

LIGHT = Palette(
    bg="#E8EEF6",
    surface="#F6F9FC",
    surface_raised="#FFFFFF",
    surface_sunken="#ECF2F8",
    border="#D2DDE9",
    border_strong="#9DB1C7",
    text="#142033",
    text_muted="#5E7086",
    text_faint="#8798AB",
    text_on_accent="#FFFFFF",
    accent="#0A84F3",
    accent_hover="#2999FF",
    accent_press="#0568BF",
    accent_soft="#DDEFFF",
    danger="#D93F4C",
    warning="#B7791F",
    success="#1F9D5F",
    view_top="#F8FBFF",
    view_bottom="#CFDBE9",
    face="#C9D2DE",
    body_tints=(
        "#C9D2DE",  # slate
        "#AFC4DC",  # steel blue
        "#B7CFBD",  # sage
        "#DCC3B1",  # clay
        "#DED4B1",  # sand
        "#C5BCD8",  # lavender
        "#AFD2D3",  # teal
        "#DBB9BE",  # rose
    ),
    edge="#344457",
    hover="#0A84F3",
    selected="#E8730C",
    ghost="#0A84F3",
    grid="#C6D2DF",
    grid_axis_x="#A86B73",
    grid_axis_y="#67917F",
    cube_box="#D2DEEB",
    cube_text="#142033",
)


@dataclass(frozen=True)
class Metrics:
    """Spacing, radius and type scale for the compact studio shell."""

    unit: int = 4
    radius_sm: int = 8
    radius: int = 12
    radius_lg: int = 14
    control_height: int = 36
    control_height_lg: int = 40
    font_size: int = 13
    font_size_sm: int = 11
    font_size_lg: int = 15
    font_size_title: int = 18
    font_family: str = field(
        default="Inter, 'Adwaita Sans', 'Cantarell', 'Segoe UI', system-ui, sans-serif"
    )
    font_mono: str = field(
        default="'JetBrains Mono', 'Source Code Pro', 'DejaVu Sans Mono', monospace"
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

    Deliberately restrained: compact controls, crisp hairlines and clearly
    layered surfaces. The 3D content remains the visual focus.
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
    border: 1px solid {p.border};
    border-radius: {m.radius_sm}px;
    padding: {m.space(1.5)}px {m.space(2.5)}px;
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
    font-weight: 650;
    letter-spacing: 0.15px;
    color: {p.text};
}}
QLabel#Muted, QLabel#Hint {{ color: {p.text_muted}; font-size: {m.font_size_sm}px; }}

/* ---- Buttons ---- */
QPushButton {{
    background: {p.surface_raised};
    border: 1px solid {p.border};
    border-radius: {m.radius_sm}px;
    padding: 0 {m.space(3)}px;
    min-height: {m.control_height}px;
    color: {p.text};
}}
QPushButton:hover  {{ background: {p.surface_sunken}; border-color: {p.border_strong}; }}
QPushButton:pressed{{ background: {p.accent_soft}; }}
QPushButton:disabled {{ color: {p.text_faint}; border-color: {p.border}; }}
QPushButton[variant="primary"] {{
    background: {p.accent};
    border: 1px solid {p.accent};
    color: {p.text_on_accent};
    font-weight: 600;
}}
QPushButton[variant="primary"]:hover   {{ background: {p.accent_hover}; border-color: {p.accent_hover}; }}
QPushButton[variant="primary"]:pressed {{ background: {p.accent_press}; }}
QPushButton[variant="ghost"] {{ background: transparent; border: 1px solid transparent; }}
QPushButton[variant="ghost"]:hover {{ background: {p.surface_raised}; }}
QPushButton:checked {{
    background: {p.accent_soft};
    border-color: {p.accent};
    color: {p.accent_hover};
}}

/* ---- Inputs ---- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {p.surface_sunken};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: {m.radius_sm}px;
    padding: 0 {m.space(2.5)}px;
    min-height: {m.control_height}px;
    selection-background-color: {p.accent};
    selection-color: {p.text_on_accent};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 2px solid {p.accent};
}}
QLineEdit[invalid="true"] {{ border: 1px solid {p.danger}; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    color: {p.text_muted};
}}
QComboBox::drop-down {{ border: none; width: {m.space(6)}px; }}
QComboBox QAbstractItemView {{
    background: {p.surface_raised};
    border: 1px solid {p.border};
    border-radius: {m.radius_sm}px;
    padding: {m.space(1)}px;
    outline: none;
}}

/* ---- Lists / trees ---- */
QTreeView, QListView {{
    background: transparent;
    border: none;
    outline: none;
    show-decoration-selected: 1;
}}
QTreeView::item, QListView::item {{
    min-height: {m.space(8)}px;
    border-radius: {m.radius_sm}px;
    padding: {m.space(0.5)}px {m.space(1.5)}px;
}}
QTreeView::item:hover, QListView::item:hover {{ background: {p.surface_sunken}; }}
QTreeView::item:selected, QListView::item:selected {{
    background: {p.accent_soft};
    color: {p.text};
}}

/* ---- Scrollbars ---- */
QScrollBar:vertical, QScrollBar:horizontal {{ background: transparent; width: 10px; height: 10px; margin: 0; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {p.border_strong}; border-radius: 5px; min-height: 32px; min-width: 32px;
}}
QScrollBar::handle:hover {{ background: {p.text_faint}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    background: none; border: none; height: 0; width: 0;
}}

/* ---- Menus ---- */
QMenu {{
    background: {p.surface_raised};
    border: 1px solid {p.border};
    border-radius: {m.radius}px;
    padding: {m.space(1.5)}px;
}}
QMenu::item {{
    padding: {m.space(2)}px {m.space(4)}px;
    border-radius: {m.radius_sm}px;
    min-width: 160px;
}}
QMenu::item:selected {{ background: {p.accent_soft}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: {m.space(1.5)}px {m.space(2)}px; }}

/* ---- Misc ---- */
QSplitter::handle {{ background: transparent; }}
QToolTip, QMenu, QFrame#FloatingPanel {{ font-size: {m.font_size}px; }}
"""
