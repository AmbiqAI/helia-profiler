"""Writing files without exposing a partial one."""

from __future__ import annotations

import os
import uuid
from pathlib import Path


def write_text_atomically(target: Path, text: str) -> None:
    """Replace *target* with *text*.

    An interrupted write leaves the previous file intact, and replacing the
    name rather than writing through it leaves any other link to that file
    unchanged.
    """
    write_bytes_atomically(target, text.encode("utf-8"))


def write_bytes_atomically(target: Path, data: bytes) -> None:
    """Replace *target* with *data*, as :func:`write_text_atomically` does."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.partial")
    try:
        partial.write_bytes(data)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
