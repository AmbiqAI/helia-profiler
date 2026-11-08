"""Content identity for SDK source files across checkout line endings."""

from __future__ import annotations

import hashlib
from pathlib import Path


def source_sha256(path: Path) -> str:
    """SHA256 of UTF-8 source with LF line endings, independent of Git checkout style."""
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
