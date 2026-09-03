"""Typed, versioned manifest for one ``hpx doctor --bundle`` support archive.

Distinct from :class:`~helia_profiler.results.manifest.ResultManifest`: a
support bundle is a host/environment snapshot for troubleshooting, not a
profiling run, so it has no ``RunStatus``/``ResultValidity``/comparability
concept. It reuses :class:`~helia_profiler.results.manifest.ResultArtifact`
for its member-file entries (content-addressed path/size/sha256 is exactly
the same shape) but is deliberately its own schema so the two bundle kinds
can evolve independently.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Self

from ..errors import ReportError
from .manifest import ResultArtifact
from .serde import sha256_file

SUPPORT_BUNDLE_SCHEMA = "hpx.support-bundle-manifest"
SUPPORT_BUNDLE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SupportBundleSection:
    """One diagnostic section the collector attempted.

    ``available=False`` records *why* a section was skipped (missing
    workspace, offline, optional tool absent, ...) rather than failing the
    whole bundle — see ``docs/architecture/field-diagnostics.md``.
    """

    name: str
    available: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ReportError("Support bundle section name must not be empty.")
        if not isinstance(self.available, bool):
            raise ReportError("Support bundle section availability must be a boolean.")
        if self.reason is not None and (not isinstance(self.reason, str) or not self.reason):
            raise ReportError("Support bundle section reason must be a non-empty string or null.")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        if not isinstance(data, dict):
            raise ReportError("Support bundle section must be a JSON object.")
        try:
            return cls(name=data["name"], available=data["available"], reason=data.get("reason"))
        except KeyError as exc:
            raise ReportError(f"Support bundle section is missing field: {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        data = {"name": self.name, "available": self.available}
        if self.reason is not None:
            data["reason"] = self.reason
        return data


@dataclass(frozen=True)
class SupportBundleManifest:
    """Stable envelope describing one support-bundle archive's contents."""

    schema: str
    schema_version: int
    hpx_version: str
    generated_at: str
    host: dict[str, Any]
    sections: tuple[SupportBundleSection, ...]
    redaction: dict[str, Any]
    artifacts: tuple[ResultArtifact, ...]
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.schema != SUPPORT_BUNDLE_SCHEMA:
            raise ReportError(f"Unsupported support bundle schema: {self.schema!r}")
        if (
            isinstance(self.schema_version, bool)
            or self.schema_version != SUPPORT_BUNDLE_SCHEMA_VERSION
        ):
            raise ReportError(
                f"Unsupported support bundle schema version: {self.schema_version!r}",
                hint=f"This HPX version supports schema v{SUPPORT_BUNDLE_SCHEMA_VERSION}.",
            )
        if not isinstance(self.hpx_version, str):
            raise ReportError("Support bundle hpx_version must be a string.")
        if not isinstance(self.generated_at, str) or not self.generated_at:
            raise ReportError("Support bundle generated_at must be a non-empty string.")
        if not isinstance(self.host, dict):
            raise ReportError("Support bundle host must be an object.")
        if not isinstance(self.sections, tuple) or not isinstance(self.artifacts, tuple):
            raise ReportError("Support bundle sections and artifacts must be arrays.")
        if not isinstance(self.redaction, dict):
            raise ReportError("Support bundle redaction must be an object.")

    def section(self, name: str) -> SupportBundleSection | None:
        for section in self.sections:
            if section.name == name:
                return section
        return None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            **self.extra,
            "schema": self.schema,
            "schema_version": self.schema_version,
            "hpx_version": self.hpx_version,
            "generated_at": self.generated_at,
            "host": self.host,
            "sections": [section.to_dict() for section in self.sections],
            "redaction": self.redaction,
            "artifacts": [_artifact_to_dict(artifact) for artifact in self.artifacts],
        }
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        if not isinstance(data, dict):
            raise ReportError("Support bundle manifest must be a JSON object.")
        known = {item.name for item in fields(cls) if item.name != "extra"}
        try:
            values: dict[str, Any] = {key: value for key, value in data.items() if key in known}
            values["sections"] = tuple(
                SupportBundleSection.from_dict(item) for item in data.get("sections", [])
            )
            values["artifacts"] = tuple(
                ResultArtifact.from_dict(item) for item in data.get("artifacts", [])
            )
            values["extra"] = {key: value for key, value in data.items() if key not in known}
            return cls(**values)
        except ReportError:
            raise
        except (TypeError, ValueError, KeyError) as exc:
            raise ReportError(f"Invalid support bundle manifest: {exc}") from exc

    @classmethod
    def load(cls, path: str | Path) -> Self:
        manifest_path = Path(path)
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReportError(
                f"Cannot load support bundle manifest {manifest_path}: {exc}"
            ) from exc
        return cls.from_dict(data)

    def verify(self, bundle_dir: str | Path) -> None:
        """Verify every declared artifact path, size, and SHA-256 digest.

        Rejects absolute paths and any path that escapes *bundle_dir* so a
        hostile/corrupted manifest cannot be used to read or overwrite files
        outside the extracted bundle (zip-slip style attacks).
        """
        root = Path(bundle_dir).resolve()
        for artifact in self.artifacts:
            if Path(artifact.path).is_absolute():
                raise ReportError(f"Support bundle artifact path must be relative: {artifact.path}")
            artifact_path = (root / artifact.path).resolve()
            if not artifact_path.is_relative_to(root):
                raise ReportError(
                    f"Support bundle artifact escapes bundle directory: {artifact.path}"
                )
            if not artifact_path.is_file():
                raise ReportError(f"Support bundle artifact is missing: {artifact.path}")
            if artifact_path.stat().st_size != artifact.size_bytes:
                raise ReportError(f"Support bundle artifact size mismatch: {artifact.path}")
            if sha256_file(artifact_path) != artifact.sha256:
                raise ReportError(f"Support bundle artifact digest mismatch: {artifact.path}")


def _artifact_to_dict(artifact: ResultArtifact) -> dict[str, Any]:
    data = asdict(artifact)
    extra = data.pop("extra", {})
    data = {key: item for key, item in data.items() if item is not None}
    return {**extra, **data}


__all__ = [
    "SUPPORT_BUNDLE_SCHEMA",
    "SUPPORT_BUNDLE_SCHEMA_VERSION",
    "SupportBundleManifest",
    "SupportBundleSection",
]
