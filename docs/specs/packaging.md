# Cross-platform packaging

| Platform | Package | Built by | Verified here |
|---|---|---|---|
| Linux x86_64 | AppImage, .deb, .rpm, Flatpak tarball (wrapping the bundle) | `packaging/build.py bundle appimage deb rpm flatpak-tarball` on ubuntu-22.04 | The bundle is built and `--self-test`ed locally; the window starts from the bundle under Xvfb. In CI each package is installed and self-tested, then opens its real window under Xvfb for 30 s (`scripts/window_smoke.sh`): the AppImage and .deb on Ubuntu 22.04, the .deb on Ubuntu 24.04, the .rpm and the AppImage on Fedora 44, whose desktop entry and AppStream data are also validated |
| Windows x64 | Inno Setup installer with uninstaller and `.scad3` association | `build.py bundle windows` on windows-2025 (Inno Setup 6.7.1) | CI only: installed silently and the installed program run with `--self-test` |
| macOS arm64 and x86_64 | `.app` in a `.dmg` with an Applications link | `build.py bundle macos` on macos-15 / macos-15-intel | CI only: the DMG mounted, the app copied out and run with `--self-test` |

The Flatpak is not built in CI: the manifest wraps the released tarball for a
local `flatpak-builder` install.

## Icons and metadata (v0.3.0)

- One source, `simplecad/assets/simplecad.svg`, with flat shapes only: QtSvg
  ignores SVG filters.
- `build.py icons` renders PNGs from 16 to 1024 px and uses Pillow to write a
  seven-size `.ico` (16–256) and a full `.icns`.
- deb and rpm install every hicolor size and the SVG.
- `packaging/io.github.fleetwoodjohnr.SimpleCAD.metainfo.xml` (AppStream) goes
  into deb, rpm, the AppImage and the Flatpak.
- `tests/test_packaging_assets.py` checks the icon sizes, the metainfo and the
  release notes.

## What a Linux package takes from the system

The bundle is built on Ubuntu 22.04 and carries its own Qt, Python and OCCT,
but **not** the C++ runtime (`libstdc++`, `libgcc_s`). The system's Mesa GL
driver needs the system's newer C++ runtime, and a bundled older one is found
first: the driver fails to load and Qt aborts with "Could not initialize GLX"
(v0.3.0's first build, on Fedora 44). The bundle's own code needs at most
`GLIBCXX_3.4.29`, which every distribution with glibc 2.35 or later has.

The .deb and .rpm declare what the bundle loads from the system:
`SYSTEM_LIBRARIES` in `packaging/build.py`, found with `ldd` in a bare
Fedora 44 (the C++ runtime, GL through libglvnd, and the xcb libraries Qt's
xcb plugin needs). The AppImage relies on the same libraries being installed,
as every desktop has them.

## Portability changes made to the code

| Before | After |
|---|---|
| The viewport read the GL context with `glXGetCurrentContext` only | `_current_gl_context_pointer` uses `wglGetCurrentContext` on Windows and `[NSOpenGLContext currentContext]` (objc runtime) on macOS. `effectiveWinId` and `Aspect_NeutralWindow` were already portable |
| `QT_QPA_PLATFORM=xcb` was forced everywhere | Linux only |
| The single-instance socket name used `os.getuid` | Falls back to the user name |
| No `freeze_support()` | In `__main__` and the frozen entry point, so the geometry worker starts in a frozen build |
| `data/` and the icon sat beside the package | Moved inside it (`simplecad/data`, `simplecad/assets`) and found relative to the module |
| Settings, recovery and calibration folders were XDG paths everywhere | `core/paths.py`: unchanged XDG paths on Linux (existing settings survive); `%APPDATA%\SimpleCAD` on Windows; `~/Library/Application Support/SimpleCAD` on macOS |
| Fonts were searched in Fedora's layout | Also Debian/Ubuntu, Windows and macOS system fonts |
| Export-and-open-in-slicer searched the PATH | On Windows and macOS it uses the system's 3MF handler |

## The bundle

- **PyInstaller, one folder** (`packaging/simplecad.spec`), from
  `packaging/entry.py`.
- **OCP** is collected whole, because its submodules are imported lazily.
- **`simplecad` submodules** are listed as hidden imports, because tools and
  features are loaded by name.
- **Data, the icon and the update public key** are included.
- **Every bundle runs `--self-test`** before it is packaged. Without a display,
  the self-test:
  - rebuilds a cut box and checks its volume
  - loads the thread and fit data
  - writes STEP and 3MF
  - starts a geometry worker process

## Install locations

| Package | Program | Notes |
|---|---|---|
| deb / rpm | `/opt/simplecad` | `/usr/bin/simplecad`, a desktop entry and icons; an `install-kind` marker tells the updater to hand updates to the system |
| AppImage | Wherever the user keeps the file | The updater replaces the file |
| Windows | Per-user Program Files by default (no administrator needed) or all users | |
| macOS | `/Applications`, dragged from the DMG | |

On every system, user data lives outside the program folder, so upgrading or
uninstalling never touches it.
