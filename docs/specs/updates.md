# Updates

`simplecad/core/updates.py`, plus Settings → Updates (`ui/dialogs/updates.py`).

## Rules

1. **Never install without a click, and never restart without agreement.**
2. **Never install anything unsigned.**
   - Every release carries `SHA256SUMS` and `SHA256SUMS.sig`, an Ed25519
     signature made in CI with the `UPDATE_SIGNING_KEY` secret.
   - The public key is built into the app (`simplecad/update_key.pub`).
   - The signature is checked before the package is downloaded, and the
     package's hash after. A mismatched file is deleted.
   - A build without the key can report an update, but will not install it.
3. **Install through the platform, never around it.**
4. **Settings and projects are never touched.** They live in `core.paths`
   folders, outside the install.

## Finding an update

- GitHub's `releases/latest` endpoint returns only the newest published
  release, never a draft or prerelease. Those are still rejected if seen,
  as are tags that are not versions.
- Versions are compared with `packaging.version`, so `0.10.0 > 0.9.9`, and a
  prerelease is never offered.
- The check runs on a background thread. Network errors become a sentence in
  the Updates tab, never a crash.
- **Background check:**
  - Runs 8 s after startup, at most once a day.
  - Is on by default for packaged builds and off for source checkouts.
  - Can be toggled in Settings → Updates.
  - When a newer release exists, it reports it in the status line.

## Installing, per install type

`install_kind()` decides how this copy was installed:

| Detected by | Kind | What Download and install does |
|---|---|---|
| `$APPIMAGE` | appimage | Copies the new file beside the old one, keeps the old as `.bak`, renames the new over it atomically, then offers a restart. `rollback_appimage()` puts `.bak` back |
| `/.flatpak-info` | flatpak | Notifies only: "updates come from your software centre" |
| `install-kind` file next to the executable (written by the package build) | deb / rpm | Hands the verified package to the desktop's installer (`xdg-open`), which asks for privileges itself |
| frozen on Windows | windows | Runs the verified installer (Windows asks for UAC if needed), then closes the app so the installer can replace it |
| frozen on macOS | macos | Opens the verified disk image |
| not frozen | source | Notifies only: "update with git pull" |

**Asset names** are `SimpleCAD-<version>-linux-<arch>.AppImage|.deb|.rpm`,
`SimpleCAD-<version>-windows-<arch>-setup.exe` and
`SimpleCAD-<version>-macos-<arch>.dmg`. The release build produces exactly
these (`packaging/build.py`).

## Settings → Updates

The tab shows:

- installed version, latest version, last checked
- release notes (Markdown)
- Check for updates
- Download and install (enabled only for a newer release on an installable
  kind)
- a progress bar
- the automatic-check toggle

## Acceptance (`tests/test_updates.py`, no network)

- Version comparison, including prereleases, garbage tags and `0.10 > 0.9`.
- The app and `pyproject.toml` versions match.
- Drafts, prereleases and non-version tags are ignored, and offline gives a
  message.
- Each install type gets its own asset name; Flatpak and source only notify.
- Signatures:
  - a correct signature and hash install
  - another key's signature is refused before anything downloads
  - a tampered file is deleted
  - an unsigned release, or a build without a key, installs nothing
- AppImage swap is atomic; the result is executable and rolls back.
- deb/rpm/macOS packages go to the system opener, and the Windows installer is
  run.
- `scripts/sign_release.py` signs exactly what the app verifies.

## v0.3.0: every failure leaves things as they were, and says so

- A failed download, from a dropped connection or a full disk, removes its
  `.part` file and any file it started.
- Earlier installers are removed from the updates folder before a new download,
  so it holds one at most.
- A disk or permission error anywhere in download or install is an
  `UpdateError` with a sentence for the user, not a stack trace:
  - an AppImage in a folder the user cannot write to is left untouched, and the
    user is told to move it
  - an installer that cannot be started reports where the verified file is
- The worker thread reports any exception, so the dialog never stays at
  "Checking…" or "Downloading…".
- After a failure, Install is enabled again only for a newer release that this
  copy can install.

Tested in `tests/test_updates.py`:
- a partial file is removed
- old downloads are pruned
- a full disk is an `UpdateError`
- a read-only AppImage folder gives a message, not a crash
- an installer that cannot start is reported
- 0.3.0 is offered to 0.2.0, with each platform's file

## Known limits

- **macOS notarisation and Windows Authenticode** are not done; they need paid
  certificates. macOS asks for right-click → Open the first time, and Windows
  SmartScreen warns. The Ed25519 signature still proves an update is from this
  project.
- **Flatpak** builds wrap the PyInstaller bundle, which is fine for a download
  but not for Flathub.
