#!/usr/bin/env python3
"""Fail a commit that adds a bare marker with no linked reference.

Accepted forms: ``TODO(#123)``, ``TODO(verify)``, ``TODO(name)`` — the marker
must be followed by a parenthesized, non-empty reference. ``FIXME(...)`` and
``HACK(...)`` follow the same rule. Binary and undecodable files are skipped.

This module is excluded from its own hook in .pre-commit-config.yaml: the
regex below necessarily spells out the bare marker names it matches against,
which would otherwise flag itself.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MARKER_RE = re.compile(r"\b(TODO|FIXME|HACK)\b(\([^)]+\))?")
GIT_LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/"


def is_probably_binary(data: bytes) -> bool:
    """Heuristic: a NUL byte in the first chunk means "not text"."""
    return b"\x00" in data[:8192]


def find_bare_markers(text: str) -> list[tuple[int, str, str]]:
    """Return (line_number, marker, line_text) for every unreferenced marker."""
    hits: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for match in MARKER_RE.finditer(line):
            marker, ref = match.group(1), match.group(2)
            if not ref or ref == "()":
                hits.append((lineno, marker, line.strip()))
    return hits


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    try:
        data = path.read_bytes()
    except OSError:
        return []
    if not data or is_probably_binary(data):
        return []
    if data.startswith(GIT_LFS_POINTER_PREFIX.encode("ascii")):
        return []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return []
    return find_bare_markers(text)


def main(argv: list[str]) -> int:
    violations: list[str] = []
    for arg in argv:
        path = Path(arg)
        for lineno, marker, line in scan_file(path):
            violations.append(f"{path}:{lineno}: {line}")
    if not violations:
        return 0
    print("Bare TODO(...)/FIXME(...)/HACK(...) markers need a reference:")
    print("\n".join(violations))
    print(
        "\nAccepted forms: TODO(#123), TODO(verify), TODO(name) — same for FIXME(...) and HACK(...)."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
