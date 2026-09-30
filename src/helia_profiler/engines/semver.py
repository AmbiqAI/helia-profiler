"""Semver-ish version parsing shared by the engine version checks."""

from __future__ import annotations

import re


def parse_semver(version: str) -> tuple[int, int, int]:
    """Parse a semver-ish string into (major, minor, patch); (0, 0, 0) on failure."""
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", version)
    if not m:
        return (0, 0, 0)
    return int(m.group(1)), int(m.group(2)), int(m.group(3))
