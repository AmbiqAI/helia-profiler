"""Shared serde helpers for permissive, versioned result documents.

Result documents (result manifests, comparison profiles) share the same
forward-compatible parse contract: known dataclass fields are populated
(with optional per-key transforms) and unknown keys are preserved verbatim
in an ``extra`` bucket so newer writers round-trip through older readers.
This module is the single implementation of that contract.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any, Callable

from ..errors import ReportError


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
