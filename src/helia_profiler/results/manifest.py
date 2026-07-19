"""Versioned, permissive manifest for one completed HPX result bundle."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

from ..errors import ReportError

RESULT_MANIFEST_SCHEMA = "hpx.result-manifest"
RESULT_MANIFEST_SCHEMA_VERSION = 1


class RunStatus(StrEnum):
    """Publication status of a result bundle."""

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    FAILED = "failed"


class ResultValidity(StrEnum):
    """Whether measurements in a completed bundle are authoritative."""

    VALID = "valid"
    DEGRADED = "degraded"
    INVALID = "invalid"


@dataclass(frozen=True)
class ResultIssue:
    """One stable machine-readable issue with optional open context."""

    code: str
    severity: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code:
            raise ReportError("Result issue code must not be empty.")
        if not isinstance(self.severity, str) or not self.severity:
            raise ReportError("Result issue severity must not be empty.")
        if not isinstance(self.message, str):
            raise ReportError("Result issue message must be a string.")
        if not isinstance(self.context, dict) or not isinstance(self.extra, dict):
            raise ReportError("Result issue context and extra fields must be objects.")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return _from_dict(cls, data)

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


@dataclass(frozen=True)
class ResultArtifact:
    """One content-addressed file in a result bundle."""

    path: str
    media_type: str
    size_bytes: int
    sha256: str
    role: str | None = None
    name: str | None = None
    schema: str | None = None
    schema_version: int | None = None
    producer: str | None = None
    optional: bool | None = None
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path or Path(self.path).is_absolute():
            raise ReportError("Result artifact path must be a non-empty relative path.")
        if not isinstance(self.media_type, str) or not self.media_type:
            raise ReportError("Result artifact media type must not be empty.")
        if (
            not isinstance(self.size_bytes, int)
            or isinstance(self.size_bytes, bool)
            or self.size_bytes < 0
        ):
            raise ReportError("Result artifact size must be a non-negative integer.")
        if (
            not isinstance(self.sha256, str)
            or len(self.sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.sha256)
        ):
            raise ReportError("Result artifact SHA-256 must contain 64 lowercase hex characters.")
        if not isinstance(self.extra, dict):
            raise ReportError("Result artifact extra fields must be an object.")
        for name in ("role", "name", "schema", "producer"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ReportError(f"Result artifact {name} must be a non-empty string.")
        if self.schema_version is not None and (
            not isinstance(self.schema_version, int)
            or isinstance(self.schema_version, bool)
            or self.schema_version < 1
        ):
            raise ReportError("Result artifact schema_version must be a positive integer.")
        if self.optional is not None and not isinstance(self.optional, bool):
            raise ReportError("Result artifact optional must be a boolean.")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return _from_dict(cls, data)

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


@dataclass(frozen=True)
class ResultManifest:
    """Stable result envelope with open provenance and extension payloads."""

    schema: str
    schema_version: int
    run_id: str
    timestamp: str
    hpx_version: str
    status: RunStatus
    validity: ResultValidity
    issues: tuple[ResultIssue, ...]
    provenance: dict[str, Any]
    comparability: dict[str, Any]
    artifacts: tuple[ResultArtifact, ...]
    bundle_type: str | None = None
    extensions: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.schema != RESULT_MANIFEST_SCHEMA:
            raise ReportError(f"Unsupported result manifest schema: {self.schema!r}")
        if (
            isinstance(self.schema_version, bool)
            or self.schema_version != RESULT_MANIFEST_SCHEMA_VERSION
        ):
            raise ReportError(
                f"Unsupported result manifest schema version: {self.schema_version!r}",
                hint=f"This HPX version supports schema v{RESULT_MANIFEST_SCHEMA_VERSION}.",
            )
        if not isinstance(self.run_id, str) or not self.run_id:
            raise ReportError("Result manifest run_id must not be empty.")
        if not isinstance(self.status, RunStatus):
            raise ReportError("Result manifest status must be a RunStatus value.")
        if not isinstance(self.validity, ResultValidity):
            raise ReportError("Result manifest validity must be a ResultValidity value.")
        if not isinstance(self.timestamp, str) or not self.timestamp:
            raise ReportError("Result manifest timestamp must be a non-empty string.")
        if not isinstance(self.hpx_version, str):
            raise ReportError("Result manifest hpx_version must be a string.")
        if self.bundle_type is not None and (
            not isinstance(self.bundle_type, str) or not self.bundle_type
        ):
            raise ReportError("Result manifest bundle_type must be a non-empty string.")
        if not isinstance(self.issues, tuple) or not isinstance(self.artifacts, tuple):
            raise ReportError("Result manifest issues and artifacts must be arrays.")
        for name in ("provenance", "comparability", "extensions", "extra"):
            if not isinstance(getattr(self, name), dict):
                raise ReportError(f"Result manifest {name} must be an object.")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        parsed = _from_dict(
            cls,
            data,
            transforms={
                "status": RunStatus,
                "validity": ResultValidity,
                "issues": lambda values: tuple(ResultIssue.from_dict(value) for value in values),
                "artifacts": lambda values: tuple(
                    ResultArtifact.from_dict(value) for value in values
                ),
            },
        )
        return parsed

    @classmethod
    def load(cls, path: str | Path) -> Self:
        """Load a manifest while preserving unknown fields."""
        manifest_path = Path(path)
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReportError(f"Cannot load result manifest {manifest_path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ReportError(f"Result manifest must contain a JSON object: {manifest_path}")
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)

    def write(self, path: str | Path) -> Path:
        """Write the manifest without discarding unknown fields."""
        manifest_path = Path(path)
        temporary_path = manifest_path.with_name(f".{manifest_path.name}.tmp")
        temporary_path.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary_path.replace(manifest_path)
        return manifest_path

    def verify(self, bundle_dir: str | Path) -> None:
        """Verify all declared artifact paths, sizes, and SHA-256 digests."""
        root = Path(bundle_dir).resolve()
        self._verify_required_artifacts()
        for artifact in self.artifacts:
            if Path(artifact.path).is_absolute():
                raise ReportError(f"Result artifact path must be relative: {artifact.path}")
            artifact_path = (root / artifact.path).resolve()
            if not artifact_path.is_relative_to(root):
                raise ReportError(f"Result artifact escapes bundle directory: {artifact.path}")
            if not artifact_path.is_file():
                raise ReportError(f"Result artifact is missing: {artifact.path}")
            if artifact_path.stat().st_size != artifact.size_bytes:
                raise ReportError(f"Result artifact size mismatch: {artifact.path}")
            if _sha256(artifact_path) != artifact.sha256:
                raise ReportError(f"Result artifact digest mismatch: {artifact.path}")

    def _verify_required_artifacts(self) -> None:
        if self.status is not RunStatus.COMPLETE or self.bundle_type != "profile":
            return
        required = {"hpx.summary", "hpx.run-metadata", "hpx.profile-layers"}
        declared = {artifact.name for artifact in self.artifacts if artifact.optional is False}
        missing = sorted(required - declared)
        if missing:
            raise ReportError(
                "Complete profile manifest is missing required core artifacts: "
                + ", ".join(missing)
            )


def load_result_manifest(path: str | Path, *, verify: bool = False) -> ResultManifest:
    """Load a result manifest and optionally verify its sibling artifacts."""
    manifest_path = Path(path)
    manifest = ResultManifest.load(manifest_path)
    if verify:
        manifest.verify(manifest_path.parent)
    return manifest


def _from_dict(cls, data: dict[str, Any], transforms: dict[str, Any] | None = None):
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


def _to_dict(value: Any) -> dict[str, Any]:
    data = asdict(value)
    extra = data.pop("extra", {})
    if isinstance(value, ResultArtifact):
        data = {key: item for key, item in data.items() if item is not None}
    if isinstance(value, ResultManifest):
        if value.bundle_type is None:
            data.pop("bundle_type", None)
        data["status"] = value.status.value
        data["validity"] = value.validity.value
        data["issues"] = [issue.to_dict() for issue in value.issues]
        data["artifacts"] = [artifact.to_dict() for artifact in value.artifacts]
    return {**extra, **data}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
