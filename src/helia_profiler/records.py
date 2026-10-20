"""Verified offline reading of native profiler result bundles.

These readers never run a model or acquire hardware. Verification establishes
consistency of retained evidence, not authenticity or numerical correctness.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from ._semantic_hash import Kind, require
from .errors import ReportError
from .results.dimensions import DIMENSION_REGISTRY, ComparisonDimension
from .results.manifest import (
    RESULT_PUBLICATION_INCOMPLETE,
    ResultArtifact,
    ResultIssue,
    ResultManifest,
    RunStatus,
)
from .results.run_summary import RUN_SUMMARY_SCHEMA, RunSummary
from .results.serde import nested_get


class ResultReadError(ReportError):
    """Invalid or unsupported native evidence, distinct from a recorded run failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ProfileResultRecord:
    """A verified snapshot; nested native mappings retain their existing read-only convention."""

    location: Path
    manifest_path: Path
    manifest_size: int
    manifest_sha256: str
    manifest: ResultManifest
    summary: RunSummary | None
    metadata: dict[str, Any] | None


@dataclass(frozen=True)
class ResultInspection:
    """Inspection does not promote an invalid bundle into a trusted result."""

    result: ProfileResultRecord | None
    code: str | None = None
    message: str | None = None


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ResultReadError("duplicate_key", f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ResultReadError("invalid_number", f"Non-finite JSON number: {value}")


def _object(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
    except (ValueError, UnicodeError) as exc:
        raise ResultReadError("invalid_json", str(exc)) from exc
    if not isinstance(value, dict):
        raise ResultReadError("invalid_json", "Native document must be a JSON object")
    _finite(value)
    return value


def _finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ResultReadError("invalid_number", "Non-finite JSON number")
    if isinstance(value, dict):
        for child in value.values():
            _finite(child)
    elif isinstance(value, list):
        for child in value:
            _finite(child)


def _schema(data: dict[str, Any], name: str, version: int) -> None:
    if (
        data.get("schema") != name
        or type(data.get("schema_version")) is not int
        or data["schema_version"] != version
    ):
        raise ResultReadError("unsupported_schema", f"Expected {name} version {version}")


def _paths(manifest: ResultManifest, root: Path) -> None:
    """Reject ambiguous names before selecting any semantic artifact."""
    paths: set[str] = set()
    resolved: set[Path] = set()
    names: set[str] = set()
    for artifact in manifest.artifacts:
        path = PurePosixPath(artifact.path)
        if (
            "\\" in artifact.path
            or PureWindowsPath(artifact.path).drive
            or path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != artifact.path
        ):
            raise ResultReadError("invalid_path", f"Nonportable artifact path: {artifact.path}")
        actual = (root / artifact.path).resolve()
        if artifact.path in paths or actual in resolved:
            raise ResultReadError("duplicate_artifact", f"Repeated artifact: {artifact.path}")
        paths.add(artifact.path)
        resolved.add(actual)
        # JSON and CSV profile tables may intentionally share the layer role.
        if artifact.name in {"hpx.summary", "hpx.run-metadata"}:
            if artifact.name in names:
                raise ResultReadError("duplicate_artifact", f"Repeated role: {artifact.name}")
            names.add(artifact.name)


def _document(root: Path, artifact: ResultArtifact) -> dict[str, Any]:
    raw = (root / artifact.path).read_bytes()
    # Parse the exact bytes checked here, rather than a second unchecked read.
    if len(raw) != artifact.size_bytes or hashlib.sha256(raw).hexdigest() != artifact.sha256:
        raise ResultReadError("artifact_changed", f"Artifact changed: {artifact.path}")
    data = _object(raw)
    if artifact.schema is not None and data.get("schema") != artifact.schema:
        raise ResultReadError("schema_binding", f"Artifact schema differs: {artifact.path}")
    if artifact.schema_version is not None and (
        type(data.get("schema_version")) is not int
        or data["schema_version"] != artifact.schema_version
    ):
        raise ResultReadError("schema_binding", f"Artifact version differs: {artifact.path}")
    return data


_SUMMARY_VERSIONS = (11, 12, 13, 14, 15, 16, 17, 18, 19)

#: Each identity hash, its kind, and the non-hash value it may take instead.
_IDENTITY_HASHES: tuple[tuple[str, Kind, str], ...] = (
    ("build_hash", "build", "unknown"),
    ("inputs_hash", "inputs", "unknown"),
    ("observation_hash", "observation", "null"),
    ("intent_hash", "intent", "null"),
)


def _identity_hashes(identity: Any) -> None:
    """Refuse a summary identity whose hashes are not semantic hashes of their kind."""
    if identity is None:
        return
    if not isinstance(identity, dict):
        raise ResultReadError("invalid_summary", "Summary identity must be an object")
    for name, kind, absent in _IDENTITY_HASHES:
        value = identity.get(name)
        if absent == "null" and value is None:
            continue
        if absent == "unknown" and isinstance(value, dict) and value.keys() == {"unknown"}:
            continue
        try:
            require(value, kind)
        except ValueError as exc:
            raise ResultReadError("invalid_summary", f"Summary identity.{name}: {exc}") from exc


def _reconcile_identities(
    manifest: ResultManifest, summary: RunSummary | None, metadata: dict[str, Any] | None
) -> None:
    facts = {
        ComparisonDimension.ENGINE: (
            summary.engine if summary is not None else None,
            nested_get(metadata, "engine", "type"),
        ),
        ComparisonDimension.MODEL_SHA256: (nested_get(manifest.provenance, "model", "sha256"),),
        ComparisonDimension.BOARD: (nested_get(metadata, "platform", "board"),),
        ComparisonDimension.ENGINE_VERSION: (),
    }
    for dimension, recorded in facts.items():
        values = (
            manifest.comparability.get(dimension),
            nested_get(metadata, *DIMENSION_REGISTRY[dimension].path),
            *recorded,
        )
        known = [value for value in values if value is not None]
        if known and any(value != known[0] for value in known[1:]):
            raise ResultReadError("identity_mismatch", f"Native identities differ for {dimension}")


def load_result(location: str | Path) -> ProfileResultRecord:
    """Strictly load an ordinary profile manifest or its containing directory.

    Failed native runs remain failed records. Missing manifests and interrupted
    publication are refusals, not legacy fallbacks. Summary versions 11 to 19
    retain the timing/memory fields read here; other versions remain available
    to native readers without receiving this verification guarantee. From
    version 19 the identity's hashes must be ``hpx`` semantic hashes.
    """
    try:
        return _load_result(Path(location))
    except ResultReadError:
        raise
    except (OSError, ReportError, TypeError, ValueError) as exc:
        raise ResultReadError("invalid_evidence", str(exc)) from exc


def _load_result(location: Path) -> ProfileResultRecord:
    path = location / "result_manifest.json" if location.is_dir() else location
    root = path.parent.resolve()
    marker = root / RESULT_PUBLICATION_INCOMPLETE
    if marker.exists() or marker.is_symlink():
        raise ResultReadError("incomplete_publication", "Native publication is incomplete")
    raw = path.read_bytes()
    data = _object(raw)
    _schema(data, "hpx.result-manifest", 1)
    manifest = ResultManifest.from_dict(data)
    if manifest.bundle_type != "profile":
        raise ResultReadError("unsupported_kind", "Expected an ordinary profile result bundle")
    if manifest.status is RunStatus.INCOMPLETE:
        raise ResultReadError("incomplete_result", "Native result is incomplete")
    _paths(manifest, root)
    manifest.verify(root)
    selected = {item.name: item for item in manifest.artifacts if item.name}
    summary_data = _document(root, selected["hpx.summary"]) if "hpx.summary" in selected else None
    metadata = (
        _document(root, selected["hpx.run-metadata"]) if "hpx.run-metadata" in selected else None
    )
    summary = None
    if summary_data is not None:
        version = summary_data.get("schema_version")
        if type(version) is not int or version not in _SUMMARY_VERSIONS:
            raise ResultReadError(
                "unsupported_schema",
                f"Supported summary versions: {', '.join(map(str, _SUMMARY_VERSIONS))}",
            )
        _schema(summary_data, RUN_SUMMARY_SCHEMA, version)
        if version >= 19:
            _identity_hashes(summary_data.get("identity"))
        if (
            summary_data.get("validity") is not None
            and summary_data["validity"] != manifest.validity.value
        ):
            raise ResultReadError("identity_mismatch", "Summary differs from manifest validity")
        if "issues" in summary_data:
            issues = summary_data["issues"]
            if not isinstance(issues, list):
                raise ResultReadError("invalid_summary", "Summary issues must be a list")
            if tuple(ResultIssue.from_dict(issue) for issue in issues) != manifest.issues:
                raise ResultReadError("identity_mismatch", "Summary differs from manifest issues")
        if (
            not isinstance(summary_data.get("engine"), str)
            or not summary_data["engine"]
            or type(summary_data.get("layers")) is not int
            or summary_data["layers"] < 0
            or type(summary_data.get("overflow_detected")) is not bool
        ):
            raise ResultReadError("invalid_summary", "Invalid native summary identity/count")
        for section in ("latency", "memory", "binary"):
            if section in summary_data and not isinstance(summary_data[section], dict):
                raise ResultReadError("invalid_summary", f"Expected object for {section}")
        for section in ("memory", "binary"):
            for name, value in summary_data.get(section, {}).items():
                if name in {
                    "arena_size",
                    "allocated_arena",
                    "text",
                    "data",
                    "bss",
                    "total",
                    "reserved",
                }:
                    if value is not None and (type(value) is not int or value < 0):
                        raise ResultReadError(
                            "invalid_summary", f"Invalid byte size: {section}.{name}"
                        )
        summary = RunSummary.from_dict(summary_data)
    if metadata is not None:
        _schema(metadata, "hpx.run-metadata", 1)
        if (
            metadata.get("run_id") != manifest.run_id
            or metadata.get("hpx_version") != manifest.hpx_version
        ):
            raise ResultReadError("identity_mismatch", "Metadata differs from manifest identity")
        declared_model = manifest.provenance.get("model")
        recorded_model = metadata.get("model")
        if isinstance(declared_model, dict) and isinstance(recorded_model, dict):
            for field in ("sha256", "name", "size_bytes"):
                if (
                    declared_model.get(field) is not None
                    and recorded_model.get(field) is not None
                    and declared_model[field] != recorded_model[field]
                ):
                    raise ResultReadError(
                        "identity_mismatch", f"Metadata differs from manifest model {field}"
                    )
    _reconcile_identities(manifest, summary, metadata)
    if manifest.status is RunStatus.COMPLETE and (summary is None or metadata is None):
        raise ResultReadError(
            "incomplete_result", "Completed profile lacks native summary/metadata"
        )
    if marker.exists() or marker.is_symlink() or path.read_bytes() != raw:
        raise ResultReadError("publication_changed", "Native publication changed during read")
    return ProfileResultRecord(
        root, path.resolve(), len(raw), hashlib.sha256(raw).hexdigest(), manifest, summary, metadata
    )


def inspect_result(location: str | Path) -> ResultInspection:
    """Return expected evidence problems without executing or repairing anything."""
    try:
        return ResultInspection(load_result(location))
    except ResultReadError as exc:
        return ResultInspection(None, exc.code, str(exc))
