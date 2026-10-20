"""The hashes a :class:`~helia_profiler.identity.RunIdentity` states, shared by every run kind.

A fixture build and a profile run hash the same inputs the same way, so equal
builds give equal ``build_hash`` values whichever path produced them. The
hashes are semantic hashes; :mod:`._semantic_hash` owns their kinds and form.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any

import yaml

from ._semantic_hash import canonical_json, semantic_hash
from .identity import Unknown

_NOT_A_LOCK = Unknown("the dependency lock is not an NSX lock")


def build_hash(
    *, image_sha256: str, lock: bytes, runtime_archive: str | None, model_sha256: str
) -> str | Unknown:
    """The hash of what runs: the flashed image, the resolved lock, the runtime archive, the model."""
    resolved = _resolved_lock(lock)
    if isinstance(resolved, Unknown):
        return resolved
    return semantic_hash(
        "build",
        {
            "image": image_sha256,
            "lock": resolved,
            "runtime_archive": runtime_archive,
            "model": model_sha256,
        },
    )


def _resolved_lock(lock: bytes) -> Any:
    """What the dependency lock resolved, without the times and tool version NSX stamps on it."""
    try:
        data = yaml.safe_load(lock)
        targets = data["targets"]
        return {
            name: {
                "target": target["target"],
                "modules": {
                    module: _resolved_module(entry) for module, entry in target["modules"].items()
                },
            }
            for name, target in targets.items()
        }
    except (yaml.YAMLError, KeyError, TypeError, AttributeError):
        return _NOT_A_LOCK


def _resolved_module(entry: dict[str, Any]) -> dict[str, Any]:
    resolved = entry.get("resolved", {})
    pinned = {"project": entry.get("project"), "kind": entry.get("kind")}
    # A git module is its URL and commit; its checkout hash varies with line endings.
    if entry.get("kind") == "git":
        return {**pinned, "url": resolved.get("url"), "commit": resolved.get("commit")}
    # Packaged tooling is identified by content. Vendored and local modules are
    # generated per run (dates, host paths); the flashed image already reflects them.
    if entry.get("kind") == "packaged":
        return {**pinned, "content_hash": resolved.get("content_hash")}
    return pinned


def measured_facts(value: Any, leaf: Callable[[Any], Any] | None = None) -> Any:
    """A record's facts as plain data, with host paths left out.

    ``leaf`` reduces a value the caller knows how to state, such as a pinned
    file to its content hash; it returns ``NotImplemented`` for anything else.
    """
    if leaf is not None:
        reduced = leaf(value)
        if reduced is not NotImplemented:
            return reduced
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: measured_facts(getattr(value, f.name), leaf) for f in fields(value)}
    if isinstance(value, Mapping):
        return {key: measured_facts(item, leaf) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [measured_facts(item, leaf) for item in value]
    return value


def digest(value: Any) -> str:
    """The bare sha256 of ``value``'s canonical JSON, for a digest that is not a semantic hash."""
    return hashlib.sha256(canonical_json(value)).hexdigest()
