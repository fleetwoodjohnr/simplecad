"""The application icon and the metadata that ships with the packages."""

from __future__ import annotations

import importlib.util
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "simplecad" / "assets" / "simplecad.svg"


def _build_module():
    spec = importlib.util.spec_from_file_location("simplecad_build", ROOT / "packaging" / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_icon_uses_nothing_qt_cannot_draw():
    """QtSvg ignores filters and blurs: the old icon's glow never rendered."""
    text = SVG.read_text()
    for unsupported in ("<filter", "feGaussianBlur", "feDropShadow", "mask="):
        assert unsupported not in text
    ElementTree.fromstring(text)


def test_icon_build_writes_every_size(tmp_path, monkeypatch, qapp):
    pytest.importorskip("PIL")
    from PIL import Image

    build = _build_module()
    monkeypatch.setattr(build, "BUILD", str(tmp_path))
    build.icons()
    folder = tmp_path / "icons"
    for size in build.ICON_SIZES:
        assert (folder / f"simplecad-{size}.png").stat().st_size > 0
    with Image.open(folder / "simplecad.ico") as ico:
        assert set(ico.ico.sizes()) >= {(s, s) for s in build.ICO_SIZES}
    with Image.open(folder / "simplecad.icns") as icns:
        assert icns.size[0] >= 512


def test_metainfo_is_valid_xml_with_this_version():
    from simplecad import __version__

    build = _build_module()
    tree = ElementTree.parse(build.METAINFO)
    root = tree.getroot()
    assert root.findtext("id") == build.APP_ID
    versions = [release.get("version") for release in root.iter("release")]
    assert __version__ in versions, "add this version to the metainfo <releases>"


def test_window_icon_resolves_from_the_package():
    from simplecad.ui import main_window

    path = Path(main_window.__file__).resolve().parents[1] / "assets" / "simplecad.svg"
    assert path == SVG and path.is_file()
