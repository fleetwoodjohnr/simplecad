"""Settings and data folders: unchanged on Linux, native on Windows and macOS."""

from __future__ import annotations

import os

from simplecad.core import paths


def test_linux_keeps_the_xdg_folders_existing_users_have(monkeypatch, tmp_path):
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    assert paths.config_dir() == str(tmp_path / "c" / "simplecad")
    assert paths.data_dir() == str(tmp_path / "d" / "simplecad")
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.delenv("XDG_DATA_HOME")
    assert paths.config_dir() == os.path.expanduser("~/.config/simplecad")
    assert paths.data_dir() == os.path.expanduser("~/.local/share/simplecad")


def test_windows_and_macos_use_their_own_places(monkeypatch, tmp_path):
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert paths.config_dir() == os.path.join(str(tmp_path), "SimpleCAD")
    assert paths.data_dir() == paths.config_dir()
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    assert paths.config_dir() == os.path.expanduser("~/Library/Application Support/SimpleCAD")


def test_the_bundled_data_and_icon_are_inside_the_package():
    from simplecad.kernel import thread_specs

    package = os.path.dirname(os.path.dirname(os.path.abspath(thread_specs.__file__)))
    assert os.path.isfile(os.path.join(thread_specs.DATA_DIR, "fits.json"))
    assert thread_specs.DATA_DIR.startswith(package)
    assert os.path.isfile(os.path.join(package, "assets", "simplecad.svg"))
