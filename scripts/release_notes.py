#!/usr/bin/env python
"""Print the CHANGELOG.md section for one version: the GitHub release notes.

    python scripts/release_notes.py 0.3.0 > notes.md

Fails if the section is missing or empty, so a release cannot go out with
the wrong notes -- they are also what the in-app updater shows.
"""

from __future__ import annotations

import os
import re
import sys

CHANGELOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "CHANGELOG.md")


def section(text: str, version: str) -> str:
    """The body under ``## <version>`` (with an optional date), up to the next ``## ``."""
    pattern = re.compile(rf"^## \[?v?{re.escape(version)}\]?(?:\s.*)?$", re.MULTILINE)
    match = pattern.search(text)
    if match is None:
        return ""
    rest = text[match.end():]
    following = re.search(r"^## ", rest, re.MULTILINE)
    body = (rest[:following.start()] if following else rest).strip()
    # A version tagged but never released had no notes anyone saw: carry it
    # into the next release's, so nothing new in it goes unannounced.
    if following:
        heading = rest[following.start():].splitlines()[0]
        if "not released" in heading:
            older = heading.removeprefix("## ").split()[0]
            body += f"\n\n{heading}\n\n{section(text, older)}"
    return body


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    version = argv[1].removeprefix("v")
    with open(CHANGELOG, encoding="utf-8") as handle:
        body = section(handle.read(), version)
    if not body:
        print(f"CHANGELOG.md has no notes for {version}", file=sys.stderr)
        return 1
    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
