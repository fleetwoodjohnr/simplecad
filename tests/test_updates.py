"""The updater: finding a release, trusting it, and installing it -- no network."""

from __future__ import annotations

import base64
import hashlib
import json

import pytest

from simplecad.core import updates


@pytest.fixture
def keys():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw,
                                               serialization.PublicFormat.Raw)
    return private, public


def _release(version="9.9.0", assets=()):
    return updates.Release(version=version, tag=f"v{version}", notes="Notes", page="https://x",
                           published="", assets={name: f"https://dl/{name}" for name in assets})


def _signed(private, files: dict[str, bytes]):
    sums = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n"
                   for name, data in files.items()).encode()
    return sums, base64.b64encode(private.sign(sums))


# -- versions ---------------------------------------------------------------
@pytest.mark.parametrize("candidate,current,newer", [
    ("v0.3.0", "0.2.0", True),
    ("0.2.1", "0.2.0", True),
    ("v0.10.0", "0.9.9", True),       # numeric, not string, comparison
    ("v0.2.0", "0.2.0", False),
    ("v0.1.9", "0.2.0", False),
    ("v1.0.0rc1", "0.2.0", False),    # prereleases are never offered
    ("nightly", "0.2.0", False),      # nor anything that is not a version
    ("", "0.2.0", False),
])
def test_version_comparison(candidate, current, newer):
    assert updates.is_newer(candidate, current) is newer


def test_the_app_version_is_one_number_everywhere():
    import tomllib
    from pathlib import Path

    from simplecad import __version__

    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert pyproject["project"]["version"] == __version__
    assert updates.parse_version(__version__) is not None


# -- finding a release ----------------------------------------------------
def test_latest_release_is_read_from_github_json():
    payload = {"tag_name": "v0.4.0", "body": "Fixes", "html_url": "https://page",
               "published_at": "2026-11-01T00:00:00Z", "draft": False, "prerelease": False,
               "assets": [{"name": "SHA256SUMS", "browser_download_url": "https://dl/s"}]}
    release = updates.latest_release(fetch=lambda _url: payload)
    assert release.version == "0.4.0"
    assert release.assets == {"SHA256SUMS": "https://dl/s"}


@pytest.mark.parametrize("payload", [
    {"tag_name": "v0.4.0", "draft": True},
    {"tag_name": "v0.4.0", "prerelease": True},
    {"tag_name": "latest-build"},
    [],
])
def test_drafts_prereleases_and_garbage_are_not_releases(payload):
    assert updates.latest_release(fetch=lambda _url: payload) is None


def test_no_network_is_a_message_not_a_crash():
    def offline(_url):
        raise OSError("Name or service not known")

    with pytest.raises(updates.UpdateError, match="Could not reach GitHub"):
        updates.latest_release(fetch=offline)


# -- which file -------------------------------------------------------------
def test_each_install_gets_its_own_package():
    assert updates.asset_name("1.2.3", "appimage", "x86_64") == "SimpleCAD-1.2.3-linux-x86_64.AppImage"
    assert updates.asset_name("1.2.3", "rpm", "x86_64") == "SimpleCAD-1.2.3-linux-x86_64.rpm"
    assert updates.asset_name("1.2.3", "windows", "x86_64") == "SimpleCAD-1.2.3-windows-x86_64-setup.exe"
    assert updates.asset_name("1.2.3", "macos", "arm64") == "SimpleCAD-1.2.3-macos-arm64.dmg"
    assert updates.asset_name("1.2.3", "flatpak") is None
    assert updates.asset_name("1.2.3", "source") is None


def test_install_kind_detection(monkeypatch, tmp_path):
    monkeypatch.delenv("APPIMAGE", raising=False)
    assert updates.install_kind() == "source"
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "SimpleCAD.AppImage"))
    assert updates.install_kind() == "appimage"
    monkeypatch.delenv("APPIMAGE")
    monkeypatch.setattr(updates.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updates.sys, "executable", str(tmp_path / "simplecad"))
    if updates.sys.platform.startswith("linux"):
        (tmp_path / "install-kind").write_text("rpm\n")
        assert updates.install_kind() == "rpm"


def test_flatpak_and_source_copies_are_only_told():
    assert not updates.can_install("flatpak") and "software centre" in updates.notify_only_reason("flatpak")
    assert not updates.can_install("source") and "git pull" in updates.notify_only_reason("source")
    with pytest.raises(updates.UpdateError):
        updates.install("/nowhere", "flatpak")


# -- trusting it ------------------------------------------------------------
def _serve(files, sums, signature):
    blobs = {f"https://dl/{name}": data for name, data in files.items()}
    blobs["https://dl/SHA256SUMS"] = sums
    blobs["https://dl/SHA256SUMS.sig"] = signature

    def fetch(url):
        return blobs[url]

    def download(url, path, _progress=None):
        with open(path, "wb") as handle:
            handle.write(blobs[url])

    return fetch, download


def test_a_signed_matching_download_is_accepted(keys, tmp_path):
    private, public = keys
    name = updates.asset_name("9.9.0", "appimage")
    files = {name: b"new appimage"}
    sums, signature = _signed(private, files)
    fetch, download = _serve(files, sums, signature)
    release = _release(assets=[name, "SHA256SUMS", "SHA256SUMS.sig"])
    path = updates.download_verified(release, "appimage", str(tmp_path), fetch=fetch,
                                     download=download, key=public)
    assert open(path, "rb").read() == b"new appimage"


def test_a_wrong_signature_is_refused_before_downloading(keys, tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    _private, public = keys
    name = updates.asset_name("9.9.0", "appimage")
    files = {name: b"new appimage"}
    sums, signature = _signed(Ed25519PrivateKey.generate(), files)   # someone else's key
    fetch, download = _serve(files, sums, signature)
    fetched = []
    release = _release(assets=[name, "SHA256SUMS", "SHA256SUMS.sig"])
    with pytest.raises(updates.UpdateError, match="signature does not match"):
        updates.download_verified(release, "appimage", str(tmp_path), fetch=fetch,
                                  download=lambda *a: fetched.append(a), key=public)
    assert not fetched, "nothing is downloaded once the signature fails"


def test_a_tampered_file_is_deleted(keys, tmp_path):
    private, public = keys
    name = updates.asset_name("9.9.0", "appimage")
    sums, signature = _signed(private, {name: b"the real thing"})
    fetch, download = _serve({name: b"something else"}, sums, signature)
    release = _release(assets=[name, "SHA256SUMS", "SHA256SUMS.sig"])
    with pytest.raises(updates.UpdateError, match="does not match its signed checksum"):
        updates.download_verified(release, "appimage", str(tmp_path), fetch=fetch,
                                  download=download, key=public)
    assert not (tmp_path / name).exists()


def test_unsigned_releases_and_keyless_builds_install_nothing(keys, tmp_path):
    _private, public = keys
    name = updates.asset_name("9.9.0", "appimage")
    with pytest.raises(updates.UpdateError, match="not signed"):
        updates.download_verified(_release(assets=[name]), "appimage", str(tmp_path), key=public)
    release = _release(assets=[name, "SHA256SUMS", "SHA256SUMS.sig"])
    import simplecad.core.updates as module

    original = module.public_key
    module.public_key = lambda: None
    try:
        with pytest.raises(updates.UpdateError, match="no update signing key"):
            updates.download_verified(release, "appimage", str(tmp_path))
    finally:
        module.public_key = original


def test_a_release_without_this_platform_says_so(keys, tmp_path):
    _private, public = keys
    with pytest.raises(updates.UpdateError, match="has no appimage package"):
        updates.download_verified(_release(assets=["SHA256SUMS", "SHA256SUMS.sig"]),
                                  "appimage", str(tmp_path), key=public)


# -- installing it ----------------------------------------------------------
def test_an_appimage_is_swapped_atomically_and_can_be_rolled_back(tmp_path, monkeypatch):
    current = tmp_path / "SimpleCAD.AppImage"
    current.write_bytes(b"old version")
    new = tmp_path / "download.AppImage"
    new.write_bytes(b"new version")
    monkeypatch.setenv("APPIMAGE", str(current))
    assert updates.install(str(new), "appimage") == "restart"
    assert current.read_bytes() == b"new version"
    if not updates.sys.platform.startswith("win"):
        assert current.stat().st_mode & 0o111, "the new AppImage is executable"
    assert (tmp_path / "SimpleCAD.AppImage.bak").read_bytes() == b"old version"
    assert updates.rollback_appimage()
    assert current.read_bytes() == b"old version"


def test_packages_are_handed_to_the_system_installer(tmp_path):
    opened, ran = [], []
    package = str(tmp_path / "SimpleCAD.rpm")
    assert updates.install(package, "rpm", opener=opened.append) == "handed-off"
    assert updates.install(package, "deb", opener=opened.append) == "handed-off"
    assert updates.install(package, "windows", runner=ran.append) == "quit-to-install"
    assert opened == [package, package] and ran == [package]


def test_background_checks_are_at_most_daily():
    assert updates.check_due(1000.0, None)
    assert not updates.check_due(1000.0 + 3600, 1000.0)
    assert updates.check_due(1000.0 + 25 * 3600, 1000.0)


def test_the_shipped_key_if_present_is_a_valid_ed25519_key():
    key = updates.public_key()
    if key is None:
        pytest.skip("this checkout has no update signing key yet")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    Ed25519PublicKey.from_public_bytes(key)


def test_sign_release_script_signs_what_the_app_verifies(keys, tmp_path, monkeypatch):
    import subprocess
    import sys
    from pathlib import Path

    from cryptography.hazmat.primitives import serialization

    private, public = keys
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    (tmp_path / "SimpleCAD-1.0.0-linux-x86_64.AppImage").write_bytes(b"app")
    key_file = tmp_path / "update_key.pub"
    key_file.write_text(base64.b64encode(public).decode())
    script = Path(__file__).parents[1] / "scripts" / "sign_release.py"
    code = ("import runpy, sys; import simplecad.core.updates as u; "
            f"u.KEY_PATH = {str(key_file)!r}; sys.argv = ['sign', {str(tmp_path)!r}]; "
            f"runpy.run_path({str(script)!r}, run_name='__main__')")
    result = subprocess.run([sys.executable, "-c", code], env={
        **__import__("os").environ, "UPDATE_SIGNING_KEY": pem,
        "PYTHONPATH": str(Path(__file__).parents[1])}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    sums = (tmp_path / "SHA256SUMS").read_bytes()
    assert updates.verify_signature(sums, (tmp_path / "SHA256SUMS.sig").read_bytes(), public)
    assert json.dumps(updates.expected_hash(sums.decode(), "SimpleCAD-1.0.0-linux-x86_64.AppImage"))
