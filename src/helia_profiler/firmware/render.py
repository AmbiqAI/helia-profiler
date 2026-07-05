"""Jinja rendering primitives for generated firmware files."""

from __future__ import annotations

from pathlib import Path

import jinja2

_jinja_env = jinja2.Environment(
    loader=jinja2.PackageLoader("helia_profiler.firmware", "templates"),
    keep_trailing_newline=True,
    undefined=jinja2.StrictUndefined,
)


def _write_text(path: Path, text: str) -> None:
    """Write generated source text with deterministic cross-platform encoding."""
    path.write_text(text, encoding="utf-8")
