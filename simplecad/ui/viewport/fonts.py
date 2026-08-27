"""Font registration for OCCT text (ViewCube labels, dimensions, annotations).

The bundled OCCT is not built against fontconfig, so ``InitFontDataBase`` finds
nothing on Fedora and every label renders as tofu boxes. We seed the database
ourselves from a short prioritised list -- registering all ~230 system fonts
would mean opening each one with FreeType at every startup for no benefit -- and
then alias the generic families OCCT asks for.
"""

from __future__ import annotations

import glob
import os
from functools import lru_cache

#: Concrete faces to look for, most preferred first, per generic family.
#: Globs are resolved against the system font directories.
FAMILY_FILES: dict[str, tuple[str, ...]] = {
    "sans-serif": (
        "adwaita-sans-fonts/AdwaitaSans-Regular.ttf",
        "abattis-cantarell-fonts/Cantarell-Regular.otf",
        "dejavu-sans-fonts/DejaVuSans.ttf",
        "liberation-sans-fonts/LiberationSans-Regular.ttf",
        "open-sans/OpenSans-Regular.ttf",
        "google-noto*/NotoSans-Regular.ttf",
    ),
    "serif": (
        "dejavu-serif-fonts/DejaVuSerif.ttf",
        "liberation-serif-fonts/LiberationSerif-Regular.ttf",
        "google-noto*/NotoSerif-Regular.ttf",
    ),
    "monospace": (
        "adwaita-mono-fonts/AdwaitaMono-Regular.ttf",
        "liberation-mono-fonts/LiberationMono-Regular.ttf",
        "dejavu-sans-mono-fonts/DejaVuSansMono.ttf",
        "adobe-source-code-pro-fonts/SourceCodePro-Regular.otf",
    ),
}

FONT_DIRS = (
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    os.path.expanduser("~/.local/share/fonts"),
)


@lru_cache(maxsize=1)
def init_fonts() -> dict[str, str]:
    """Populate OCCT's font database. Safe to call repeatedly; runs once.

    Returns a mapping of generic family -> the concrete family name bound to it.
    """
    from OCP.Font import Font_FontMgr
    from OCP.TCollection import TCollection_AsciiString

    mgr = Font_FontMgr.GetInstance_s()
    try:
        mgr.InitFontDataBase()
    except Exception:  # noqa: BLE001 - fonts must never break startup
        pass

    bound: dict[str, str] = {}
    for generic, patterns in FAMILY_FILES.items():
        for path in _resolve(patterns):
            family = _register(mgr, path)
            if family is None:
                continue
            try:
                mgr.AddFontAlias(
                    TCollection_AsciiString(generic),
                    TCollection_AsciiString(family),
                )
            except Exception:  # noqa: BLE001
                pass
            bound[generic] = family
            break

    # Last resort so text is never invisible on a system with no fonts at all.
    try:
        Font_FontMgr.EmbedFallbackFont_s()
    except Exception:  # noqa: BLE001
        pass
    return bound


def _resolve(patterns: tuple[str, ...]) -> list[str]:
    """Expand the per-family globs against the font directories, in order."""
    found: list[str] = []
    for pattern in patterns:
        for directory in FONT_DIRS:
            for match in sorted(glob.glob(os.path.join(directory, pattern))):
                if os.path.isfile(match):
                    found.append(match)
    return found


def _register(mgr, path: str) -> str | None:
    """Register one font file, returning the family name OCCT recorded."""
    from OCP.TCollection import TCollection_AsciiString

    try:
        font = mgr.CheckFont(TCollection_AsciiString(path).ToCString())
    except Exception:  # noqa: BLE001
        return None
    if font is None:
        return None
    try:
        mgr.RegisterFont(font, True)
        return font.FontName().ToCString()
    except Exception:  # noqa: BLE001
        return None


def available_families() -> list[str]:
    """Family names currently in OCCT's database (diagnostics)."""
    from OCP.Font import Font_FontMgr
    from OCP.TColStd import TColStd_SequenceOfHAsciiString

    seq = TColStd_SequenceOfHAsciiString()
    Font_FontMgr.GetInstance_s().GetAvailableFontsNames(seq)
    return [seq.Value(i).ToCString() for i in range(1, seq.Size() + 1)]
