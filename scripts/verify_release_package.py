#!/usr/bin/env python3
"""Check the signed manifest before running a published installer in a drill."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def verify(package: Path) -> None:
    from simplecad.core.updates import SIGNATURE, SUMS, expected_hash, sha256_of, verify_signature

    manifest = (package.parent / SUMS).read_bytes()
    signature = (package.parent / SIGNATURE).read_bytes()
    if not verify_signature(manifest, signature):
        raise SystemExit("The published manifest signature could not be verified.")
    expected = expected_hash(manifest.decode("utf-8"), package.name)
    if not expected or sha256_of(str(package)) != expected:
        raise SystemExit(f"The checksum for {package.name} does not match the signed manifest.")
    print(f"Verified {package.name}")


if __name__ == "__main__":
    verify(Path(sys.argv[1]))
