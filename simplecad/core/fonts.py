"""Font discovery shared by the viewport and solid-text geometry.

OCCT in the bundled OCP build is not connected to fontconfig, so applications
must seed its font database explicitly.  This module deliberately has no Qt
dependency: it is imported by the persistent geometry process as well as by
the viewport.
"""

from __future__ import annotations

import glob
import os
from functools import lru_cache


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

# Fonts are programs as much as they are outlines.  Some variable fonts render
# overlapping faces through StdPrs_BRepFont (notably a capital A), which is fine
# for display but creates an invalid solid.  Prefer conventional outline fonts
# that have been exercised as B-Rep geometry, then fall back through the list
# and let the text feature validate the requested glyphs.
MODELING_FAMILY_FILES: dict[str, tuple[str, ...]] = {
    "sans-serif": (
        "liberation-sans-fonts/LiberationSans-Regular.ttf",
        "open-sans/OpenSans-Regular.ttf",
        "google-noto*/NotoSans-Regular.ttf",
        "adwaita-sans-fonts/AdwaitaSans-Regular.ttf",
    ),
    "serif": (
        "liberation-serif-fonts/LiberationSerif-Regular.ttf",
        "google-noto*/NotoSerif-Regular.ttf",
        "dejavu-serif-fonts/DejaVuSerif.ttf",
    ),
    "monospace": (
        "liberation-mono-fonts/LiberationMono-Regular.ttf",
        "adwaita-mono-fonts/AdwaitaMono-Regular.ttf",
        "adobe-source-code-pro-fonts/SourceCodePro-Regular.otf",
        "dejavu-sans-mono-fonts/DejaVuSansMono.ttf",
    ),
}

FONT_DIRS = (
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    os.path.expanduser("~/.local/share/fonts"),
)


def _resolve(patterns: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for pattern in patterns:
        for directory in FONT_DIRS:
            for match in sorted(glob.glob(os.path.join(directory, pattern))):
                if os.path.isfile(match):
                    found.append(match)
    return found


def _register(mgr, path: str) -> str | None:
    from OCP.TCollection import TCollection_AsciiString

    try:
        font = mgr.CheckFont(TCollection_AsciiString(path).ToCString())
    except Exception:  # noqa: BLE001 - an unavailable font is an ordinary fallback
        return None
    if font is None:
        return None
    try:
        mgr.RegisterFont(font, True)
        return font.FontName().ToCString()
    except Exception:  # noqa: BLE001
        return None


@lru_cache(maxsize=1)
def init_fonts() -> dict[str, str]:
    """Populate OCCT's font database and bind its generic aliases."""
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

    try:
        Font_FontMgr.EmbedFallbackFont_s()
    except Exception:  # noqa: BLE001
        pass
    return bound


@lru_cache(maxsize=3)
def modeling_families(generic: str) -> tuple[str, ...]:
    """Concrete family names to try when turning *generic* into a solid."""
    from OCP.Font import Font_FontMgr

    if generic not in MODELING_FAMILY_FILES:
        return ()
    init_fonts()
    mgr = Font_FontMgr.GetInstance_s()
    found: list[str] = []
    for path in _resolve(MODELING_FAMILY_FILES[generic]):
        family = _register(mgr, path)
        if family and family not in found:
            found.append(family)
    # The generic alias and embedded fallback remain a last resort.
    bound = init_fonts().get(generic)
    if bound and bound not in found:
        found.append(bound)
    return tuple(found)


def available_families() -> list[str]:
    """Family names currently in OCCT's database (diagnostics)."""
    from OCP.Font import Font_FontMgr
    from OCP.TColStd import TColStd_SequenceOfHAsciiString

    init_fonts()
    seq = TColStd_SequenceOfHAsciiString()
    Font_FontMgr.GetInstance_s().GetAvailableFontsNames(seq)
    return [seq.Value(i).ToCString() for i in range(1, seq.Size() + 1)]
