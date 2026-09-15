"""Desktop launches return to the existing SimpleCAD window."""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree
from uuid import uuid4

from PySide6.QtGui import QIcon

from simplecad.ui.single_instance import SingleInstance, activate_window


def test_second_instance_requests_activation(qtbot):
    name = f"simplecad-test-{uuid4().hex}"
    primary = SingleInstance(name)
    secondary = SingleInstance(name)
    try:
        assert primary.claim()
        with qtbot.waitSignal(primary.activate_requested, timeout=1000):
            assert not secondary.claim()
    finally:
        secondary.close()
        primary.close()


def test_activation_restores_and_focuses_the_window():
    calls: list[str] = []

    class Handle:
        def requestActivate(self):  # noqa: N802 - mirrors Qt
            calls.append("request")

    class Window:
        def isMinimized(self):  # noqa: N802 - mirrors Qt
            return True

        def showNormal(self):  # noqa: N802 - mirrors Qt
            calls.append("restore")

        def show(self):
            calls.append("show")

        def raise_(self):
            calls.append("raise")

        def activateWindow(self):  # noqa: N802 - mirrors Qt
            calls.append("activate")

        def windowHandle(self):  # noqa: N802 - mirrors Qt
            return Handle()

    activate_window(Window())

    assert calls == ["restore", "raise", "activate", "request"]


def test_desktop_entry_declares_one_main_window():
    entry = Path(__file__).parents[1] / "simplecad.desktop"
    text = entry.read_text()
    assert "SingleMainWindow=true" in text
    assert "X-GNOME-SingleWindow=true" in text


def test_desktop_icon_is_scalable_and_loadable(qtbot):
    root = Path(__file__).parents[1]
    asset = root / "assets" / "simplecad.svg"
    svg = ElementTree.parse(asset).getroot()
    assert svg.attrib["viewBox"] == "0 0 128 128"
    assert not QIcon(str(asset)).isNull()
    assert "Icon=simplecad" in (root / "simplecad.desktop").read_text()
