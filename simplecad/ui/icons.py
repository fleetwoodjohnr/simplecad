"""Line icons, drawn from inline SVG so they follow the theme.

Icons are stroke-based on a 24x24 grid in the manner of Feather/Lucide: one
weight, round caps, no fills. They are generated at the requested colour rather
than shipped as coloured assets, so light and dark themes need no separate set
and a hovered icon can simply be re-rendered in the accent colour.
"""

from __future__ import annotations

from functools import lru_cache

#: name -> SVG body (paths only; the wrapper supplies the svg element).
PATHS: dict[str, str] = {
    # --- primitives ---
    "box": '<path d="M12 2.6 21 7.3v9.4L12 21.4 3 16.7V7.3z"/><path d="M3 7.3 12 12l9-4.7"/><path d="M12 12v9.4"/>',
    "cylinder": '<ellipse cx="12" cy="6" rx="7.5" ry="3.4"/><path d="M4.5 6v12M19.5 6v12"/><path d="M4.5 18a7.5 3.4 0 0 0 15 0"/>',
    "sphere": '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="9" ry="3.6"/><ellipse cx="12" cy="12" rx="3.6" ry="9"/>',
    "cone": '<path d="M12 3 20 18H4z"/><ellipse cx="12" cy="18" rx="8" ry="3.2"/>',
    "torus": '<ellipse cx="12" cy="12" rx="9.2" ry="6"/><ellipse cx="12" cy="12" rx="3.6" ry="2"/>',
    "tube": '<ellipse cx="12" cy="6" rx="7.5" ry="3.2"/><ellipse cx="12" cy="6" rx="3.6" ry="1.5"/><path d="M4.5 6v12M19.5 6v12"/><path d="M4.5 18a7.5 3.2 0 0 0 15 0"/>',
    "wedge": '<path d="M3 19V9l9-5v15z"/><path d="M12 4l9 5v10H3"/>',
    "polygon": '<path d="M12 2.7 20 7.2v9L12 20.7 4 16.2v-9z"/>',
    "triangle": '<path d="M12 3.4 20.6 19H3.4z"/>',
    "pentagon": '<path d="M12 2.9 20.6 9.2 17.3 19.4H6.7L3.4 9.2z"/>',
    "hexagon": '<path d="M12 2.7 20 7.2v9.6L12 21.3 4 16.8V7.2z"/>',
    "octagon": '<path d="M8.6 3.2h6.8l4.4 4.4v6.8l-4.4 4.4H8.6l-4.4-4.4V7.6z"/>',
    "vent": '<path d="M8.0 8.5 5.4 7.0 5.4 4.0 8.0 2.5 10.6 4.0 10.6 7.0z"/><path d="M16.0 8.5 13.4 7.0 13.4 4.0 16.0 2.5 18.6 4.0 18.6 7.0z"/><path d="M4.0 15.0 1.4 13.5 1.4 10.5 4.0 9.0 6.6 10.5 6.6 13.5z"/><path d="M12.0 15.0 9.4 13.5 9.4 10.5 12.0 9.0 14.6 10.5 14.6 13.5z"/><path d="M20.0 15.0 17.4 13.5 17.4 10.5 20.0 9.0 22.6 10.5 22.6 13.5z"/><path d="M8.0 21.5 5.4 20.0 5.4 17.0 8.0 15.5 10.6 17.0 10.6 20.0z"/><path d="M16.0 21.5 13.4 20.0 13.4 17.0 16.0 15.5 18.6 17.0 18.6 20.0z"/>',
    "star": '<path d="m12 2.5 2.8 5.7 6.3.9-4.6 4.4 1.1 6.3-5.6-3-5.6 3 1.1-6.3-4.6-4.4 6.3-.9z"/>',
    "heart": '<path d="M12 20.5 4.2 13A5.2 5.2 0 0 1 11.6 5.7l.4.5.4-.5A5.2 5.2 0 0 1 19.8 13z"/>',
    "cross": '<path d="M9 3h6v6h6v6h-6v6H9v-6H3V9h6z"/>',
    "crescent": '<path d="M18.8 17.7A8.5 8.5 0 1 1 14.2 3.8 7 7 0 0 0 18.8 17.7z"/>',
    "lightning": '<path d="m14 2-8 11h5l-1 9 8-12h-5z"/>',
    # --- operations ---
    "extrude": '<path d="M4 20h16"/><path d="M8 16V8h8v8"/><path d="M12 3v6"/><path d="m9 6 3-3 3 3"/>',
    "move": '<path d="M12 3v18M3 12h18"/><path d="m9 6 3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3M18 9l3 3-3 3"/>',
    "rotate": '<path d="M20.5 12a8.5 8.5 0 1 1-2.9-6.4"/><path d="M20.5 4v5h-5"/>',
    "scale": '<path d="M4 4h7v7H4z"/><path d="M13 13h7v7h-7z"/><path d="m11 11 2 2"/>',
    "align": '<path d="M4 4v16"/><path d="M8 8h9M8 16h6"/><path d="m14 5 3 3-3 3" opacity=".55"/>',
    "stack": '<path d="M3 8.5 12 4l9 4.5L12 13z"/><path d="m3 14 9 4.5L21 14"/>',
    "concentric": '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="3.5"/><path d="M12 3.5v3M12 17.5v3M3.5 12h3M17.5 12h3"/>',
    "hole": '<path d="M4 5h16v14H4z"/><ellipse cx="12" cy="12" rx="4" ry="4"/><path d="M12 8v8" opacity=".4"/>',
    "thread": '<path d="M7 3v18M17 3v18"/><path d="M7 6.5h10M7 11h10M7 15.5h10M7 20h10" opacity=".8"/>',
    "fillet": '<path d="M4 20V10A6 6 0 0 1 10 4h10"/><path d="M4 20h4M20 4v4" opacity=".45"/>',
    "chamfer": '<path d="M4 20V11l7-7h9"/><path d="M4 20h4M20 4v4" opacity=".45"/>',
    "shell": '<path d="M4 5h16v14H4z"/><path d="M7.5 8.5h9v7h-9z" opacity=".55"/>',
    "clip": '<path d="M4 5h5v4l-2 2 2 2v6H4"/><path d="M20 5h-5v4l2 2-2 2v6h5"/><path d="M9 12h6"/>',
    "sketch": '<path d="M3 21 6 13 17.5 1.5a2.1 2.1 0 0 1 3 3L9 16z"/><path d="m15.5 3.5 3 3"/>',
    "text": '<path d="M5 5h14M12 5v14M8 19h8"/>',
    "measure": '<path d="m3 15 12-12 6 6-12 12z"/><path d="M7 11l2 2M10 8l2 2M13 5l2 2"/>',
    "boolean": '<circle cx="9" cy="12" r="6"/><circle cx="15" cy="12" r="6"/>',
    "cut": '<path d="M4 5h6v14H4zM14 5h6v14h-6z"/><path d="M12 3v18" stroke-dasharray="2 2"/><path d="m9 8 3 4-3 4" opacity=".55"/>',
    # The three combines differ only in which region is emphasised, which is
    # exactly how they differ in meaning.
    "subtract": '<circle cx="9" cy="12" r="6"/><circle cx="15" cy="12" r="6" stroke-dasharray="2.5 2.5"/>',
    "union": '<circle cx="9" cy="12" r="6"/><circle cx="15" cy="12" r="6"/><path d="M12 7.1a6 6 0 0 0 0 9.8" opacity=".35"/>',
    "intersect": '<circle cx="9" cy="12" r="6" stroke-dasharray="2.5 2.5"/><circle cx="15" cy="12" r="6" stroke-dasharray="2.5 2.5"/><path d="M12 7.1a6 6 0 0 1 0 9.8 6 6 0 0 1 0-9.8"/>',
    "pattern": '<circle cx="6" cy="6" r="2.2"/><circle cx="18" cy="6" r="2.2"/><circle cx="6" cy="18" r="2.2"/><circle cx="18" cy="18" r="2.2"/><circle cx="12" cy="12" r="2.2" opacity=".5"/>',
    "print": '<path d="M6 9V3h12v6"/><path d="M6 18H4a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-2"/><path d="M6 14h12v7H6z"/>',
    # --- chrome ---
    "create": '<path d="M12 4v16M4 12h16"/><path d="m18.5 3 .6 1.4L20.5 5l-1.4.6-.6 1.4-.6-1.4-1.4-.6 1.4-.6z"/>',
    "modify": '<path d="M4 6h10M18 6h2M4 12h3M11 12h9M4 18h9M17 18h3"/><circle cx="16" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="15" cy="18" r="2"/>',
    "inspect": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5M7.5 10.5h6M10.5 7.5v6"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.6-3.6"/>',
    "undo": '<path d="M3 8h11a6 6 0 0 1 0 12h-6"/><path d="m7 4-4 4 4 4"/>',
    "redo": '<path d="M21 8H10a6 6 0 0 0 0 12h6"/><path d="m17 4 4 4-4 4"/>',
    "sun": '<circle cx="12" cy="12" r="4.2"/><path d="M12 2v2.5M12 19.5V22M4.2 4.2 6 6M18 18l1.8 1.8M2 12h2.5M19.5 12H22M4.2 19.8 6 18M18 6l1.8-1.8"/>',
    "moon": '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4 8.5 8.5 0 1 0 20 14.5"/>',
    "eye": '<path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12"/><circle cx="12" cy="12" r="3"/>',
    "eye_off": '<path d="M10.6 6.1A9.9 9.9 0 0 1 12 6c6.4 0 10 6 10 6a17 17 0 0 1-3.2 3.9M6.3 7.9A17 17 0 0 0 2 12s3.6 6 10 6c1.5 0 2.8-.3 4-.8"/><path d="m3 3 18 18"/>',
    "xray": '<path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12"/><path d="M9 9l6 6M15 9l-6 6"/>',
    "trash": '<path d="M4 7h16"/><path d="M9 7V5h6v2"/><path d="M6 7l1 13h10l1-13"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "check": '<path d="m4.5 12.5 5 5 10-11"/>',
    "close": '<path d="M6 6 18 18M18 6 6 18"/>',
    "chevron_down": '<path d="m6 9 6 6 6-6"/>',
    "chevron_left": '<path d="m15 6-6 6 6 6"/>',
    "chevron_right": '<path d="m9 6 6 6-6 6"/>',
    "fit": '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
    "ortho": '<path d="M4 5h16v14H4z"/><path d="M4 9h16M9 5v14" opacity=".5"/>',
    "perspective": '<path d="m5 6 14-2v16L5 18z"/><path d="M5 12h14" opacity=".5"/>',
    "grid": '<path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-2.7 1.1V21a2 2 0 1 1-4 0v-.1A1.6 1.6 0 0 0 8 19.4a1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0-1.1-2.7H2a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 3.7 8a1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H8a1.6 1.6 0 0 0 1-1.5V2a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 2.7 1.1 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V8a1.6 1.6 0 0 0 1.5 1H22a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "save": '<path d="M5 3h11l3 3v15H5z"/><path d="M8 3v6h8V3M8 21v-7h8v7"/>',
    "body": '<path d="M12 2.6 21 7.3v9.4L12 21.4 3 16.7V7.3z"/><path d="M3 7.3 12 12l9-4.7M12 12v9.4" opacity=".45"/>',
    "history": '<path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1"/><path d="M3 4v5h5"/><path d="M12 7.5V12l3 2"/>',
    "export": '<path d="M12 3v12"/><path d="m8 7 4-4 4 4"/><path d="M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/>',
    "import": '<path d="M12 3v12"/><path d="m8 11 4 4 4-4"/><path d="M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/>',
    "split": '<path d="M4 5h6v14H4zM14 5h6v14h-6z"/><path d="M12 2v20" stroke-dasharray="2.5 2.5"/>',
    "group": '<path d="M3 7V4h3M18 4h3v3M21 17v3h-3M6 20H3v-3" stroke-dasharray="0"/><path d="M7 9h4v4H7zM13 11h4v4h-4z"/>',
    "ungroup": '<path d="M3 6V3h3M14 3h3v3" stroke-dasharray="0"/><path d="M3 10h7v7H3z"/><path d="M14 12h7v7h-7z"/>',
    "duplicate": '<path d="M9 3h9a2 2 0 0 1 2 2v9"/><path d="M4 8h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V10a2 2 0 0 1 2-2z"/>',
    "warning": '<path d="M12 3.5 22 20H2z"/><path d="M12 9.5v5M12 17.2v.1"/>',
}

_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
    'width="{size}" height="{size}" fill="none" stroke="{color}" '
    'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round">'
    "{body}</svg>"
)


def svg(name: str, color: str, size: int = 24, stroke: float = 1.6) -> bytes:
    """The raw SVG for an icon, coloured."""
    body = PATHS.get(name, PATHS["close"])
    return _TEMPLATE.format(
        size=size, color=color, width=stroke, body=body
    ).encode("utf-8")


@lru_cache(maxsize=512)
def icon(name: str, color: str, size: int = 22, stroke: float = 1.6):
    """A ``QIcon`` for *name* rendered in *color*."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon, QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer

    ratio = 2  # render at 2x so icons stay crisp on HiDPI
    pixmap = QPixmap(size * ratio, size * ratio)
    pixmap.fill(Qt.transparent)
    renderer = QSvgRenderer(svg(name, color, size, stroke))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return QIcon(pixmap)


def available() -> list[str]:
    return sorted(PATHS)
