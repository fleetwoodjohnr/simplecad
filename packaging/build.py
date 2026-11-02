#!/usr/bin/env python
"""Build SimpleCAD's installable packages.

    python packaging/build.py icons            PNG / ICO / ICNS from the SVG
    python packaging/build.py bundle           PyInstaller folder, then --self-test it
    python packaging/build.py appimage         Linux AppImage    (needs appimagetool)
    python packaging/build.py deb rpm          Linux packages    (needs nfpm)
    python packaging/build.py windows          Inno Setup installer (needs iscc)
    python packaging/build.py macos            .dmg              (needs hdiutil)

Everything lands in dist/release/ named the way the updater looks for it:
SimpleCAD-<version>-<os>-<arch>.<ext>. See docs/release.md.
"""

from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
BUILD = os.path.join(ROOT, "build")
DIST = os.path.join(ROOT, "dist")
RELEASE = os.path.join(DIST, "release")
BUNDLE = os.path.join(DIST, "SimpleCAD")
APP_ID = "io.github.fleetwoodjohnr.SimpleCAD"
METAINFO = os.path.join(ROOT, "packaging", f"{APP_ID}.metainfo.xml")


def version() -> str:
    from simplecad import __version__

    return __version__


def arch() -> str:
    from simplecad.core.updates import machine

    return machine()


def run(*command, **kwargs) -> None:
    print("+", " ".join(map(str, command)), flush=True)
    subprocess.run([str(c) for c in command], check=True, **kwargs)


def tool(name: str, env: str | None = None) -> str:
    found = os.environ.get(env or "") or shutil.which(name)
    if not found:
        sys.exit(f"{name} is needed for this package and was not found.")
    return found


def desktop_entry(executable: str) -> str:
    with open(os.path.join(ROOT, "simplecad.desktop")) as handle:
        text = handle.read()
    return text.replace("SIMPLECAD_LAUNCHER", executable)


# -- steps ------------------------------------------------------------------
#: Every size the platforms ask for. Windows picks 16-256 out of one .ico,
#: macOS 16-1024 out of one .icns, Linux takes the PNGs and the SVG.
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256, 512, 1024)
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def icons() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PIL import Image
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    QGuiApplication.instance() or QGuiApplication([])
    folder = os.path.join(BUILD, "icons")
    os.makedirs(folder, exist_ok=True)
    renderer = QSvgRenderer(os.path.join(ROOT, "simplecad", "assets", "simplecad.svg"))
    if not renderer.isValid():
        sys.exit("simplecad.svg could not be read")
    for size in ICON_SIZES:
        image = QImage(size, size, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing, True)
        renderer.render(painter)
        painter.end()
        path = os.path.join(folder, f"simplecad-{size}.png")
        if not image.save(path):
            sys.exit(f"could not write {path}")
    # Qt writes a one-image .ico and .icns; Pillow writes every size into
    # one file, which is what keeps the icon sharp in a taskbar and a Dock.
    largest = Image.open(os.path.join(folder, "simplecad-1024.png"))
    largest.save(os.path.join(folder, "simplecad.ico"), sizes=[(s, s) for s in ICO_SIZES])
    largest.save(os.path.join(folder, "simplecad.icns"))
    for name in ("simplecad.ico", "simplecad.icns"):
        if os.path.getsize(os.path.join(folder, name)) < 1024:
            sys.exit(f"{name} came out empty")
    print("icons ->", folder)


def bundle() -> None:
    icons()
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", DIST, "--workpath", os.path.join(BUILD, "pyinstaller"),
        os.path.join(ROOT, "packaging", "simplecad.spec"))
    executable = (os.path.join(DIST, "SimpleCAD.app", "Contents", "MacOS", "SimpleCAD")
                  if sys.platform == "darwin" else
                  os.path.join(BUNDLE, "SimpleCAD.exe" if sys.platform == "win32" else "simplecad"))
    run(executable, "--self-test")


def appimage() -> None:
    appdir = os.path.join(BUILD, "AppDir")
    shutil.rmtree(appdir, ignore_errors=True)
    shutil.copytree(BUNDLE, os.path.join(appdir, "usr", "lib", "simplecad"), symlinks=True)
    with open(os.path.join(appdir, "AppRun"), "w") as handle:
        handle.write('#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\n'
                     'exec "$HERE/usr/lib/simplecad/simplecad" "$@"\n')
    os.chmod(os.path.join(appdir, "AppRun"), 0o755)
    with open(os.path.join(appdir, "simplecad.desktop"), "w") as handle:
        handle.write(desktop_entry("simplecad"))
    shutil.copy(os.path.join(BUILD, "icons", "simplecad-256.png"),
                os.path.join(appdir, "simplecad.png"))
    metainfo = os.path.join(appdir, "usr", "share", "metainfo")
    os.makedirs(metainfo, exist_ok=True)
    shutil.copy(METAINFO, metainfo)
    os.makedirs(RELEASE, exist_ok=True)
    out = os.path.join(RELEASE, f"SimpleCAD-{version()}-linux-{arch()}.AppImage")
    run(tool("appimagetool", "APPIMAGETOOL"), "--no-appstream", appdir, out,
        env={**os.environ, "ARCH": platform.machine()})


def linux_package(kind: str) -> None:
    """A .deb or .rpm through nfpm, installing under /opt/simplecad."""
    stage = os.path.join(BUILD, f"pkg-{kind}")
    shutil.rmtree(stage, ignore_errors=True)
    shutil.copytree(BUNDLE, os.path.join(stage, "simplecad"), symlinks=True)
    with open(os.path.join(stage, "simplecad", "install-kind"), "w") as handle:
        handle.write(kind + "\n")       # tells the updater to hand updates to the system
    with open(os.path.join(stage, "simplecad.desktop"), "w") as handle:
        handle.write(desktop_entry("/opt/simplecad/simplecad"))
    config = os.path.join(stage, "nfpm.yaml")
    with open(config, "w") as handle:
        handle.write(f"""name: simplecad
version: {version()}
arch: {"amd64" if kind == "deb" and arch() == "x86_64" else arch()}
maintainer: SimpleCAD <noreply@github.com>
description: Parametric CAD for mechanical design and 3D printing
homepage: https://github.com/fleetwoodjohnr/simplecad
license: Proprietary
contents:
  - src: {stage}/simplecad
    dst: /opt/simplecad
    type: tree
  - src: /opt/simplecad/simplecad
    dst: /usr/bin/simplecad
    type: symlink
  - src: {stage}/simplecad.desktop
    dst: /usr/share/applications/simplecad.desktop
  - src: {ROOT}/simplecad/assets/simplecad.svg
    dst: /usr/share/icons/hicolor/scalable/apps/simplecad.svg
  - src: {METAINFO}
    dst: /usr/share/metainfo/{APP_ID}.metainfo.xml
""" + "".join(f"""  - src: {BUILD}/icons/simplecad-{size}.png
    dst: /usr/share/icons/hicolor/{size}x{size}/apps/simplecad.png
""" for size in (16, 24, 32, 48, 64, 128, 256, 512)))
    os.makedirs(RELEASE, exist_ok=True)
    out = os.path.join(RELEASE, f"SimpleCAD-{version()}-linux-{arch()}.{kind}")
    run(tool("nfpm"), "package", "--config", config, "--packager", kind, "--target", out)


def windows() -> None:
    os.makedirs(RELEASE, exist_ok=True)
    run(tool("iscc", "ISCC"), f"/DAppVersion={version()}", f"/DSourceDir={BUNDLE}",
        f"/DIconFile={os.path.join(BUILD, 'icons', 'simplecad.ico')}",
        f"/DOutputDir={RELEASE}", f"/DOutputName=SimpleCAD-{version()}-windows-{arch()}-setup",
        os.path.join(ROOT, "packaging", "simplecad.iss"))


def macos() -> None:
    os.makedirs(RELEASE, exist_ok=True)
    out = os.path.join(RELEASE, f"SimpleCAD-{version()}-macos-{arch()}.dmg")
    # The app beside a link to /Applications: the usual drag-to-install window.
    stage = os.path.join(BUILD, "dmg")
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    shutil.copytree(os.path.join(DIST, "SimpleCAD.app"), os.path.join(stage, "SimpleCAD.app"),
                    symlinks=True)
    os.symlink("/Applications", os.path.join(stage, "Applications"))
    run(tool("hdiutil"), "create", "-volname", "SimpleCAD", "-srcfolder", stage,
        "-ov", "-format", "UDZO", out)


def flatpak_tarball() -> None:
    """The bundle as a tarball, for the Flatpak manifest to wrap."""
    os.makedirs(RELEASE, exist_ok=True)
    shutil.make_archive(os.path.join(BUILD, f"simplecad-bundle-{arch()}"), "gztar",
                        root_dir=DIST, base_dir="SimpleCAD")


STEPS = {"icons": icons, "bundle": bundle, "appimage": appimage,
         "deb": lambda: linux_package("deb"), "rpm": lambda: linux_package("rpm"),
         "windows": windows, "macos": macos, "flatpak-tarball": flatpak_tarball}


def main() -> int:
    for name in sys.argv[1:] or ["bundle"]:
        if name not in STEPS:
            sys.exit(f"unknown step {name!r}; one of {', '.join(STEPS)}")
        STEPS[name]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
