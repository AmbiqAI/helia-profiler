"""Shared serde helpers for permissive, versioned result documents.

Result documents (result manifests, comparison profiles) share the same
forward-compatible parse contract: known dataclass fields are populated
(with optional per-key transforms) and unknown keys are preserved verbatim
in an ``extra`` bucket so newer writers round-trip through older readers.
This module is the single implementation of that contract — and, since
#229 D6, the home of the small general-purpose helpers those documents
and their producers share (file digests, nested reads, float coercion),
each previously duplicated across packages.
"""

from __future__ import annotations

import hashlib
from dataclasses import fields
from pathlib import Path
from typing import Any, Callable

from ..errors import ReportError


def sha256_file(path: Path) -> str:
    """Streaming sha256 of a file (1 MiB chunks).

    The one implementation behind artifact digests, lock stamps, and
    workspace fingerprints — previously three identical copies (#229 D6).
    """
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def nested_get(mapping: Any, *keys: str) -> Any:
    """Walk nested dicts; ``None`` on any missing key or non-dict step.

    The shared crash-tolerant read for artifacts written by other hpx
    versions — previously four identical copies (#229 D6).
    """
    current = mapping
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def to_float(value: Any) -> float | None:
    """Bool-rejecting float coercion; ``None`` on anything unconvertible.

    Bools are not measurements. Previously two identical copies (#229 D6).
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def dataclass_from_dict(
    cls,
    data: dict[str, Any],
    transforms: dict[str, Callable[[Any], Any]] | None = None,
):
    """Build ``cls`` from ``data``, routing unknown keys into ``extra``."""
    if not isinstance(data, dict):
        raise ReportError(f"Expected JSON object for {cls.__name__}.")
    transforms = transforms or {}
    known = {item.name for item in fields(cls) if item.name != "extra"}
    try:
        values = {
            key: transforms.get(key, lambda value: value)(value)
            for key, value in data.items()
            if key in known
        }
        values["extra"] = {key: value for key, value in data.items() if key not in known}
        return cls(**values)
    except ReportError:
        raise
    except (TypeError, ValueError) as exc:
        raise ReportError(f"Invalid {cls.__name__}: {exc}") from exc
