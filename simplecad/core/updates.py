"""Updates from the project's GitHub releases, installed only when asked.

The rules, in order of importance:

* Nothing is installed without the user clicking Install, and nothing restarts
  without them agreeing.
* Nothing is installed unless it is signed. Every release carries a
  ``SHA256SUMS`` file and an Ed25519 signature of it, made in CI with a key
  whose public half is built into the app (``simplecad/update_key.pub``). The
  signature is checked first, then the downloaded file's hash against it. A
  build without the public key can still say an update exists, but refuses
  to install one.
* Each installation goes through its own platform's mechanism, never around
  it: an AppImage is swapped for the new one (the old kept for rollback);
  Flatpak and source checkouts are told, not touched; a .deb, .rpm, Windows
  installer or macOS disk image is verified and then handed to the system,
  which asks for whatever permission it needs.
* Settings and projects live outside the install (``core.paths``) and are
  never touched.

See docs/specs/updates.md.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import shutil
import sys
from dataclasses import dataclass, field

REPOSITORY = "fleetwoodjohnr/simplecad"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases"
SUMS = "SHA256SUMS"
SIGNATURE = "SHA256SUMS.sig"
KEY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "update_key.pub")
#: Background checks happen at most this often.
CHECK_INTERVAL_S = 24 * 3600


class UpdateError(Exception):
    """Something the user should be told, in words."""


@dataclass
class Release:
    version: str
    tag: str
    notes: str
    page: str
    published: str
    assets: dict[str, str] = field(default_factory=dict)   # name -> download URL


# -- versions ---------------------------------------------------------------
def parse_version(text):
    """A comparable version from a tag like ``v0.3.1``; None for anything else."""
    from packaging.version import InvalidVersion, Version

    try:
        return Version(str(text).strip().lstrip("vV"))
    except InvalidVersion:
        return None


def current_version() -> str:
    from .. import __version__

    return __version__


def is_newer(candidate: str, current: str | None = None) -> bool:
    """True only for a proper, final release later than the running one."""
    new, old = parse_version(candidate), parse_version(current or current_version())
    return bool(new and old and not new.is_prerelease and new > old)


# -- finding out ------------------------------------------------------------
def _fetch_json(url: str, timeout: float = 15.0):
    from urllib.request import Request, urlopen

    request = Request(url, headers={"Accept": "application/vnd.github+json",
                                    "User-Agent": f"SimpleCAD/{current_version()}"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https URL
        return json.loads(response.read().decode("utf-8"))


def latest_release(fetch=_fetch_json) -> Release | None:
    """The newest published, non-draft, non-prerelease release, or None."""
    try:
        data = fetch(LATEST_URL)
    except Exception as exc:  # noqa: BLE001 - network, DNS, HTTP 404/403, bad JSON
        raise UpdateError(f"Could not reach GitHub to check for updates ({exc}).") from exc
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = str(data.get("tag_name") or "")
    if parse_version(tag) is None:
        return None
    return Release(
        version=str(parse_version(tag)), tag=tag, notes=str(data.get("body") or ""),
        page=str(data.get("html_url") or RELEASES_PAGE),
        published=str(data.get("published_at") or ""),
        assets={str(a.get("name")): str(a.get("browser_download_url"))
                for a in data.get("assets") or [] if a.get("name")},
    )


# -- what this copy is, and which file it wants -------------------------------
def install_kind() -> str:
    """How this copy was installed: appimage, flatpak, deb, rpm, windows, macos, source."""
    if os.path.exists("/.flatpak-info"):
        return "flatpak"
    if os.environ.get("APPIMAGE"):
        return "appimage"
    if not getattr(sys, "frozen", False):
        return "source"
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    marker = os.path.join(os.path.dirname(sys.executable), "install-kind")
    try:
        with open(marker) as handle:
            kind = handle.read().strip()
        return kind if kind in ("deb", "rpm") else "source"
    except OSError:
        return "source"


def machine() -> str:
    arch = platform.machine().lower()
    return {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}.get(arch, arch)


def asset_name(version: str, kind: str, arch: str | None = None) -> str | None:
    arch = arch or machine()
    names = {
        "appimage": f"SimpleCAD-{version}-linux-{arch}.AppImage",
        "deb": f"SimpleCAD-{version}-linux-{arch}.deb",
        "rpm": f"SimpleCAD-{version}-linux-{arch}.rpm",
        "windows": f"SimpleCAD-{version}-windows-{arch}-setup.exe",
        "macos": f"SimpleCAD-{version}-macos-{arch}.dmg",
    }
    return names.get(kind)


def can_install(kind: str) -> bool:
    return kind in ("appimage", "deb", "rpm", "windows", "macos")


def notify_only_reason(kind: str) -> str:
    if kind == "flatpak":
        return "This copy is a Flatpak: updates come from your software centre or `flatpak update`."
    if kind == "source":
        return "This copy runs from source: update it with `git pull`."
    return ""


# -- trusting it ------------------------------------------------------------
def public_key() -> bytes | None:
    try:
        with open(KEY_PATH, "rb") as handle:
            text = handle.read().strip()
    except OSError:
        return None
    try:
        key = base64.b64decode(text, validate=True)
    except ValueError:
        return None
    return key if len(key) == 32 else None


def verify_signature(message: bytes, signature: bytes, key: bytes | None = None) -> bool:
    """Is *signature* (raw or base64) an Ed25519 signature of *message* by our key?"""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    key = key if key is not None else public_key()
    if key is None:
        return False
    signature = signature.strip()
    if len(signature) != 64:
        try:
            signature = base64.b64decode(signature, validate=True)
        except ValueError:
            return False
    try:
        Ed25519PublicKey.from_public_bytes(key).verify(signature, message)
        return True
    except (InvalidSignature, ValueError):
        return False


def expected_hash(sums: str, name: str) -> str | None:
    """The SHA-256 a ``sha256sum``-format list gives for *name*."""
    for line in sums.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[1].lstrip("*") == name:
            return parts[0].lower()
    return None


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# -- getting it -------------------------------------------------------------
def _fetch_bytes(url: str, timeout: float = 30.0) -> bytes:
    from urllib.request import Request, urlopen

    request = Request(url, headers={"User-Agent": f"SimpleCAD/{current_version()}"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - GitHub release URL
        return response.read()


def _download(url: str, destination: str, progress=None, timeout: float = 60.0) -> None:
    from urllib.request import Request, urlopen

    request = Request(url, headers={"User-Agent": f"SimpleCAD/{current_version()}"})
    partial = destination + ".part"
    with urlopen(request, timeout=timeout) as response, open(partial, "wb") as out:  # noqa: S310
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while True:
            block = response.read(1 << 16)
            if not block:
                break
            out.write(block)
            done += len(block)
            if progress and total:
                progress(int(done * 100 / total))
    os.replace(partial, destination)


def download_verified(release: Release, kind: str, folder: str, *, fetch=_fetch_bytes,
                      download=_download, progress=None, key: bytes | None = None) -> str:
    """Download this platform's file and prove it is ours. Returns its path.

    The signature over SHA256SUMS is checked before the large download, and the
    file's hash after it; a file that fails either is deleted, never kept.
    """
    name = asset_name(release.version, kind)
    if name is None or name not in release.assets:
        raise UpdateError(f"Release {release.version} has no {kind} package for "
                          f"{machine()}. Download it from {release.page}.")
    if SUMS not in release.assets or SIGNATURE not in release.assets:
        raise UpdateError(f"Release {release.version} is not signed, so it will not be "
                          "installed automatically.")
    key = key if key is not None else public_key()
    if key is None:
        raise UpdateError("This build has no update signing key, so it cannot check that an "
                          f"update is genuine. Download it from {release.page}.")
    try:
        sums = fetch(release.assets[SUMS])
        signature = fetch(release.assets[SIGNATURE])
    except Exception as exc:  # noqa: BLE001
        raise UpdateError(f"Could not download the release's checksums ({exc}).") from exc
    if not verify_signature(sums, signature, key):
        raise UpdateError("The release's signature does not match SimpleCAD's key. "
                          "Nothing was installed.")
    expected = expected_hash(sums.decode("utf-8", "replace"), name)
    if expected is None:
        raise UpdateError(f"The signed checksums do not list {name}. Nothing was installed.")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    try:
        download(release.assets[name], path, progress)
    except Exception as exc:  # noqa: BLE001
        raise UpdateError(f"The download failed ({exc}). Nothing was installed.") from exc
    if sha256_of(path) != expected:
        os.remove(path)
        raise UpdateError("The download does not match its signed checksum and was deleted. "
                          "Nothing was installed.")
    return path


# -- installing it ----------------------------------------------------------
def replace_appimage(new_file: str, target: str | None = None) -> str:
    """Swap the running AppImage for *new_file*, keeping the old one as ``.bak``.

    The swap is a rename on the same filesystem, so the file at *target* is
    always either the old AppImage or the complete new one. On any failure the
    backup is put back. Returns the backup's path (the rollback).
    """
    target = target or os.environ.get("APPIMAGE")
    if not target:
        raise UpdateError("This copy is not an AppImage.")
    backup = target + ".bak"
    staged = target + ".new"
    shutil.copyfile(new_file, staged)
    os.chmod(staged, 0o755)
    shutil.copy2(target, backup)
    try:
        os.replace(staged, target)
    except OSError as exc:
        try:
            os.replace(backup, target)
        finally:
            if os.path.exists(staged):
                os.remove(staged)
        raise UpdateError(f"Could not replace the AppImage ({exc}); the old one is kept.") from exc
    return backup


def rollback_appimage(target: str | None = None) -> bool:
    target = target or os.environ.get("APPIMAGE")
    backup = (target or "") + ".bak"
    if not target or not os.path.exists(backup):
        return False
    os.replace(backup, target)
    return True


def install(path: str, kind: str, *, opener=None, runner=None) -> str:
    """Install a verified file the way this platform installs things.

    Returns what happens next: ``"restart"`` (the new version runs after a
    restart), ``"handed-off"`` (the system's installer has it), or
    ``"quit-to-install"`` (the installer needs this app closed).
    """
    import subprocess

    if kind == "appimage":
        replace_appimage(path)
        return "restart"
    if kind in ("deb", "rpm", "macos"):
        # The software centre (or Finder, for a disk image) takes it from here
        # and asks for whatever permission it needs. No privileges from us.
        opener = opener or _open_with_system
        opener(path)
        return "handed-off"
    if kind == "windows":
        runner = runner or (lambda p: subprocess.Popen([p]))  # noqa: S603 - verified installer
        runner(path)
        return "quit-to-install"
    raise UpdateError(notify_only_reason(kind) or f"Cannot install updates for a {kind} copy.")


def _open_with_system(path: str) -> None:
    import subprocess

    if sys.platform == "darwin":
        subprocess.Popen(["open", path])  # noqa: S603, S607
    elif sys.platform == "win32":
        os.startfile(path)  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", path])  # noqa: S603, S607


# -- remembered state -------------------------------------------------------
def check_due(now: float, last_checked: float | None) -> bool:
    return last_checked is None or now - last_checked >= CHECK_INTERVAL_S


def default_auto_check() -> bool:
    """Packaged builds check by default; a source checkout is updated by git."""
    return install_kind() not in ("source",)
