"""Shared serde helpers for permissive, versioned result documents.

Result documents (result manifests, comparison profiles) share the same
forward-compatible parse contract: known dataclass fields are populated
(with optional per-key transforms) and unknown keys are preserved verbatim
in an ``extra`` bucket so newer writers round-trip through older readers.
This module is the single implementation of that contract and the home of
the small helpers those documents and their producers share (file digests,
nested reads, number coercion, ``None`` stripping) (#229 D6).
"""

from __future__ import annotations

import csv
import hashlib
import math
from dataclasses import fields
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ..errors import ReportError


def sha256_file(path: Path) -> str:
    """Streaming sha256 of a file (1 MiB chunks).

    The one implementation behind artifact digests, lock stamps, and
    workspace fingerprints (#229 D6).
    """
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_dict_csv(
    path: Path, fieldnames: Iterable[str], rows: Iterable[Mapping[str, Any]]
) -> None:
    """Write *rows* as a UTF-8 CSV with a header row."""
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fieldnames))
        writer.writeheader()
        writer.writerows(rows)


def nested_get(mapping: Any, *keys: str) -> Any:
    """Walk nested dicts; ``None`` on any missing key or non-dict step.

    The shared crash-tolerant read for artifacts written by other hpx
    versions (#229 D6).
    """
    current = mapping
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def to_float(value: Any, *, finite: bool = False) -> float | None:
    """Bool-rejecting float coercion; ``None`` on anything unconvertible.

    Bools are not measurements (#229 D6). ``finite=True`` also rejects
    NaN and infinities, for arithmetic that must not propagate them.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if finite and not math.isfinite(number):
        return None
    return number


def to_int(value: Any) -> int | None:
    """Bool-rejecting int coercion; ``None`` on anything unconvertible.

    A boolean where a count belongs is garbage, not a 0/1 measurement.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def strip_none(value: Any) -> Any:
    """Recursively drop ``None`` values from dicts, including dicts in lists.

    ``None`` list items are kept: position is meaningful in a list.
    """
    if isinstance(value, dict):
        return {key: strip_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [strip_none(item) for item in value]
    return value


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
