"""Jinja rendering primitives for generated firmware files."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import jinja2

from ..wire import POWER_TERMINAL_VERSION

_jinja_env = jinja2.Environment(
    loader=jinja2.PackageLoader("helia_profiler.firmware", "templates"),
    keep_trailing_newline=True,
    undefined=jinja2.StrictUndefined,
)
# The envelope version the host parser accepts, so the firmware cannot drift
# from it. jinja2 types ``globals`` as its own builtin helpers only.
cast("dict[str, Any]", _jinja_env.globals)["power_terminal_version"] = POWER_TERMINAL_VERSION


def _write_text(path: Path, text: str) -> None:
    """Write generated source text with deterministic cross-platform encoding.

    Skips the write when the file already holds exactly *text*: regeneration
    into a cached workspace must not bump mtimes on unchanged sources, or the
    incremental firmware build recompiles and relinks the whole app (and
    CMake re-runs configure) on every run.
    """
    try:
        if path.read_text(encoding="utf-8") == text:
            return
    except (OSError, UnicodeDecodeError):
        pass
    path.write_text(text, encoding="utf-8")
