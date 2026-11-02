# Cutting a release

## One-time setup

1. **The signing key.** `scripts/make_update_key.py` has already been run:
   - the public key is in `simplecad/update_key.pub` (committed)
   - the private key is in `~/.config/simplecad-release/update_signing_key.pem`
     (mode 600, never committed)

   Add that file's whole contents as a repository secret named
   `UPDATE_SIGNING_KEY` (GitHub → Settings → Secrets and variables → Actions),
   then **back the file up somewhere safe**:
   - Losing it means copies already installed can no longer verify updates. A
     new key would need a release that people install by hand.
   - Leaking it means someone else could sign an update. Replace it at once,
     and ship the new public key in a hand-installed release.
2. Nothing else. The workflows use only the built-in `GITHUB_TOKEN`.

## Each release

1. Set the version in `simplecad/__init__.py` **and** `pyproject.toml` (a test
   checks they agree). Then:
   - add a `## <version>` section to `CHANGELOG.md`; it becomes the release
     notes and what the in-app updater shows
   - add a `<release>` line to
     `packaging/io.github.fleetwoodjohnr.SimpleCAD.metainfo.xml`
2. Prove the release pipeline on the branch first, without tagging:
   `gh workflow run release.yml --ref <branch>` (or Actions → Release → Run
   workflow). It builds, installs and runs every package and signs the set,
   but creates no release. A tag is never moved once pushed, so this is the
   time to find out.
3. Merge to `main`, push it, and wait for CI to pass.
4. Tag that commit with an **annotated** tag and push the tag:
   ```sh
   git tag -a v0.3.0 -m "SimpleCAD 0.3.0" && git push origin v0.3.0
   ```
5. `release.yml` then:
   1. **preflight**: refuses a lightweight tag, a tag that differs from
      `__version__`, a commit that is not on `main`, or a missing CHANGELOG
      section
   2. runs CI in full: lint, audit, Linux tests with the real-window checks,
      and headless Windows and macOS tests
   3. builds on Linux (AppImage, .deb, .rpm, Flatpak tarball), Windows
      (installer) and macOS (arm64 and x86_64 DMGs); appimagetool and nfpm are
      pinned and checksum-checked
   4. **installs and runs every package**:
      - the AppImage, on Ubuntu 22.04 and Fedora 44
      - the .deb on Ubuntu 22.04 and 24.04
      - the .rpm on Fedora 44, with the desktop entry and AppStream validated
      - each Linux package also opens its real window under Xvfb for 30 s
        (`scripts/window_smoke.sh`); `--self-test` is headless and cannot
        catch a build whose window fails to start
      - the Windows installer, silently
      - the DMG: mounted, the app run from it
   5. checks the exact file list, writes and signs `SHA256SUMS` (failing if the
      secret doesn't match the key built into the app), and runs
      `sha256sum -c`
   6. refuses to touch an existing release, then uploads everything to a
      **draft** with the CHANGELOG notes
6. Read the draft and **Publish**. Installed copies see it on their next
   check.

## Building locally (Linux)

```sh
.venv/bin/pip install -r requirements-build.txt
.venv/bin/python packaging/build.py bundle          # dist/SimpleCAD/, self-tested
.venv/bin/python packaging/build.py appimage        # needs appimagetool on PATH
.venv/bin/python packaging/build.py deb rpm         # needs nfpm on PATH
```

## Known limits

- **Signing**: macOS builds are not notarised, and Windows builds are not
  Authenticode-signed; both need paid certificates. Users see Gatekeeper
  (System Settings → Privacy & Security → "Open Anyway" the first time) and
  SmartScreen ("More info → Run anyway") respectively.
- **Testing**: the Windows and macOS 3D viewport has not been run by anyone
  yet. CI proves those builds start, compute and self-test headless, not that
  the viewport draws.
