"""Typed dependency workspace and lock provenance."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ContentDigest:
    """A typed content digest."""

    algorithm: str
    value: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class DependencyOverride:
    """One explicit module, project, or engine source override."""

    scope: str
    name: str
    mode: str
    requested: str
    content_hash: ContentDigest | None = None


@dataclass(frozen=True)
class DependencyWorkspace:
    """Deterministic identity of one isolated dependency workspace."""

    schema_version: int
    fingerprint: str
    baseline_id: str
    baseline_fingerprint: str
    registry_hash: ContentDigest
    inputs: dict[str, Any]
    root: Path

    def to_dict(self, *, include_root: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "fingerprint": self.fingerprint,
            "baseline_id": self.baseline_id,
            "baseline_fingerprint": self.baseline_fingerprint,
            "registry_hash": self.registry_hash.to_dict(),
            "inputs": self.inputs,
        }
        if include_root:
            result["root"] = self.root.as_posix()
        return result


class DependencyLockMode(StrEnum):
    """How the exact lock used by a run was obtained."""

    REUSED = "reused"
    RESOLVED = "resolved"
    UPDATED = "updated"


@dataclass(frozen=True)
class DependencyModule:
    """One exact module resolution copied from ``nsx.lock``."""

    name: str
    project: str
    kind: str
    requested_ref: str
    requested_tag: str | None
    peeled_commit: str | None
    content_hash: ContentDigest
    url: str | None
    vendored_at: str


@dataclass(frozen=True)
class DependencyLockState:
    """Operation state for the exact lock used by a run."""

    mode: DependencyLockMode
    update_requested: bool
    offline: bool
    frozen_sync: bool
    schema_version: int
    sha256: ContentDigest
    manifest_hash: ContentDigest


@dataclass(frozen=True)
class DependencyProvenance:
    """Complete deterministic dependency provenance for one profile."""

    workspace: DependencyWorkspace
    lock: DependencyLockState
    modules: tuple[DependencyModule, ...]
    overrides: tuple[DependencyOverride, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": self.workspace.to_dict(),
            "lock": {
                **asdict(self.lock),
                "mode": self.lock.mode.value,
            },
            "modules": [asdict(module) for module in self.modules],
            "overrides": [asdict(override) for override in self.overrides],
        }
