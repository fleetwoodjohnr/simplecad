#!/usr/bin/env python
"""Write SHA256SUMS for a folder of release files and sign it.

Run by the release workflow. The Ed25519 private key comes from the
UPDATE_SIGNING_KEY environment variable (PEM text), never from a file in the
repository. Writes SHA256SUMS and SHA256SUMS.sig (base64) into the folder and
then checks the signature against simplecad/update_key.pub, so a release
signed with the wrong key fails here rather than on users' machines.

    UPDATE_SIGNING_KEY="$(cat key.pem)" python scripts/sign_release.py dist/release
"""

from __future__ import annotations

import base64
import hashlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main() -> int:
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    from simplecad.core.updates import SIGNATURE, SUMS, verify_signature

    folder = sys.argv[1]
    secret = os.environ.get("UPDATE_SIGNING_KEY", "").strip()
    if not secret:
        print("UPDATE_SIGNING_KEY is not set; refusing to publish unsigned files.",
              file=sys.stderr)
        return 1
    lines = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if name in (SUMS, SIGNATURE) or not os.path.isfile(path):
            continue
        with open(path, "rb") as handle:
            lines.append(f"{hashlib.sha256(handle.read()).hexdigest()}  {name}\n")
    sums = "".join(lines).encode()
    key = load_pem_private_key(secret.encode(), password=None)
    signature = key.sign(sums)
    with open(os.path.join(folder, SUMS), "wb") as handle:
        handle.write(sums)
    with open(os.path.join(folder, SIGNATURE), "w") as handle:
        handle.write(base64.b64encode(signature).decode() + "\n")
    if not verify_signature(sums, base64.b64encode(signature)):
        print("The signature does not verify against simplecad/update_key.pub: the secret "
              "and the key built into the app do not match.", file=sys.stderr)
        return 1
    print(sums.decode(), end="")
    print(f"signed {len(lines)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
