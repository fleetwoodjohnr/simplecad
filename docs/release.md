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

1. Set the version in `simplecad/__init__.py` **and** `pyproject.toml`. A test
   checks they agree.
2. Commit, then tag and push:
   ```sh
   git tag v0.3.0 && git push origin v0.3.0
   ```
3. `release.yml` then:
   1. runs CI (lint, audit, Linux tests with real-window checks, headless
      Windows/macOS tests)
   2. builds on Linux (AppImage, .deb, .rpm, Flatpak tarball), Windows
      (installer) and macOS (arm64 and x86_64 DMGs)
   3. runs `--self-test` on every bundle
   4. writes and signs `SHA256SUMS`, failing if the secret doesn't match the
      key built into the app
   5. uploads everything to a **draft** release
4. Read the draft, edit the notes, and **Publish**. Installed copies see it on
   their next check.

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
  ("right-click → Open" the first time) and SmartScreen ("More info → Run
  anyway") respectively.
- **Testing**: the Windows and macOS 3D viewport has not been run by anyone
  yet. CI proves those builds start, compute and self-test headless, not that
  the viewport draws.
