# Cross-platform packaging

| Platform | Package | Built by | Verified here |
|---|---|---|---|
| Linux x86_64 | AppImage, .deb, .rpm, Flatpak (wrapping the bundle) | `packaging/build.py bundle appimage deb rpm flatpak-tarball` on ubuntu-22.04 | The bundle is built and `--self-test`ed locally; the window starts from the bundle under Xvfb |
| Windows x64 | Inno Setup installer with uninstaller and `.scad3` association | `build.py bundle windows` on windows-latest | No: CI only |
| macOS arm64 and x86_64 | `.app` in a `.dmg` | `build.py bundle macos` on macos-14 / macos-13 | No: CI only |

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
