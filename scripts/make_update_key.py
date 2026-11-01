#!/usr/bin/env python
"""Make the Ed25519 key pair that signs SimpleCAD releases.

The public half is written into the app (simplecad/update_key.pub) and is what
every installed copy checks updates against. The private half is written
outside the repository with owner-only permissions; add its contents to the
GitHub repository secret UPDATE_SIGNING_KEY, and keep a backup somewhere safe.
Losing it means installed copies can no longer verify updates; leaking it
means someone else could sign one. It refuses to overwrite an existing key.

    .venv/bin/python scripts/make_update_key.py [--private PATH]
"""

from __future__ import annotations

import argparse
import base64
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC = os.path.join(ROOT, "simplecad", "update_key.pub")
DEFAULT_PRIVATE = os.path.expanduser("~/.config/simplecad-release/update_signing_key.pem")


def main() -> int:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--private", default=DEFAULT_PRIVATE)
    args = parser.parse_args()
    for path in (args.private, PUBLIC):
        if os.path.exists(path):
            print(f"{path} already exists; not overwriting it.", file=sys.stderr)
            return 1
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    raw = key.public_key().public_bytes(serialization.Encoding.Raw,
                                        serialization.PublicFormat.Raw)
    os.makedirs(os.path.dirname(args.private), mode=0o700, exist_ok=True)
    descriptor = os.open(args.private, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(pem)
    with open(PUBLIC, "w") as handle:
        handle.write(base64.b64encode(raw).decode() + "\n")
    print(f"public key  -> {PUBLIC} (commit this)")
    print(f"private key -> {args.private} (add to the UPDATE_SIGNING_KEY secret; back it up)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
