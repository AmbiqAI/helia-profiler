"""Shared helpers used across ``hpx`` CLI command modules."""

from __future__ import annotations

import sys
from pathlib import Path


def _print_hpx_error(exc: Exception) -> None:
    print(f"Error: {exc}", file=sys.stderr)


def _find_repo_root() -> Path:
    """Locate the helia-profiler checkout root.

    Walks up from this file until a directory containing ``pyproject.toml``
    is found.  Falls back to the current working directory.
    """
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "pyproject.toml").is_file() and (parent / "tests").is_dir():
            return parent
    return Path.cwd()
