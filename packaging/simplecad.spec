# PyInstaller spec: one folder, the app and everything it loads at run time.
# Built by packaging/build.py; see docs/release.md.
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICONS = os.path.join(ROOT, "build", "icons")

datas, binaries, hidden = [], [], []
# OCP is one large extension module with lazily imported submodules.
for package in ("OCP",):
    found = collect_all(package)
    datas += found[0]
    binaries += found[1]
    hidden += found[2]
# Tools, panels and features are imported by name at run time.
hidden += collect_submodules("simplecad")
hidden += ["shapely", "scipy.optimize", "cryptography.hazmat.primitives.asymmetric.ed25519"]
datas += [
    (os.path.join(ROOT, "simplecad", "data"), "simplecad/data"),
    (os.path.join(ROOT, "simplecad", "assets"), "simplecad/assets"),
]
if os.path.exists(os.path.join(ROOT, "simplecad", "update_key.pub")):
    datas.append((os.path.join(ROOT, "simplecad", "update_key.pub"), "simplecad"))

analysis = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    # Pulled in by OCP's and scipy's optional imports, never used by SimpleCAD.
    excludes=["tkinter", "matplotlib", "PIL", "IPython", "pandas", "pytest",
              "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtMultimedia", "PySide6.QtQuick", "PySide6.QtQml"],
)
pyz = PYZ(analysis.pure)
icon = {"win32": os.path.join(ICONS, "simplecad.ico"),
        "darwin": os.path.join(ICONS, "simplecad.icns")}.get(sys.platform)
executable = EXE(
    pyz, analysis.scripts, [], exclude_binaries=True,
    name="simplecad" if sys.platform.startswith("linux") else "SimpleCAD",
    console=False, icon=icon if icon and os.path.exists(icon) else None,
)
bundle = COLLECT(executable, analysis.binaries, analysis.datas, name="SimpleCAD")
if sys.platform == "darwin":
    from simplecad import __version__

    app = BUNDLE(
        bundle, name="SimpleCAD.app", icon=icon,
        bundle_identifier="io.github.fleetwoodjohnr.SimpleCAD",
        info_plist={"CFBundleShortVersionString": __version__, "CFBundleVersion": __version__,
                    "NSHighResolutionCapable": True, "LSMinimumSystemVersion": "13.0"},
    )
