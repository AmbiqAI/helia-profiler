"""Typed, security-conscious loading for portable validation bundles."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ..errors import ValidationBundleError

_REPEAT_SUFFIX = re.compile(r"-run(?P<attempt>[0-9]+)$")
_WINDOWS_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:|[\\/]{2})")
_STATUSES = {"pass", "fail", "skip"}


@dataclass(frozen=True)
class ArtifactRef:
    """One bundle-relative artifact reference."""

    path: str
    available: bool


@dataclass(frozen=True, order=True)
class ValidationCaseIdentity:
    """Fields that determine whether two validation cases are comparable."""

    model_id: str
    engine: str
    board: str
    toolchain: str
    transport: str
    requested_memory: tuple[tuple[str, str], ...]
    requested_power: tuple[tuple[str, str], ...]
    attempt: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "engine": self.engine,
            "board": self.board,
            "toolchain": self.toolchain,
            "transport": self.transport,
            "requested_memory": {key: json.loads(value) for key, value in self.requested_memory},
            "requested_power": {key: json.loads(value) for key, value in self.requested_power},
            "attempt": self.attempt,
        }


@dataclass(frozen=True)
class ValidationBundleCase:
    """One validated case entry from a bundle manifest."""

    identity: ValidationCaseIdentity
    case_id: str
    status: str
    health_issues: tuple[str, ...]
    artifacts: tuple[tuple[str, ArtifactRef], ...]
    provenance: tuple[tuple[str, Any], ...]

    def artifact(self, name: str) -> ArtifactRef | None:
        return dict(self.artifacts).get(name)


@dataclass(frozen=True)
class ValidationBundleMetadata:
    """Portable top-level provenance copied from a validation manifest."""

    generated_at: str | None
    hpx_version: str | None
    repo_sha: str | None
    repo_branch: str | None
    repo_dirty: bool | None


@dataclass(frozen=True)
class ValidationBundle:
    """A loaded validation bundle and any compatibility warnings."""

    root: Path
    schema_version: int
    cases: tuple[ValidationBundleCase, ...]
    warnings: tuple[str, ...]
    metadata: ValidationBundleMetadata


def load_validation_bundle(root: Path) -> ValidationBundle:
    """Load schema v1/v2, validate identities, and contain artifact paths."""

    bundle_root = root.expanduser().resolve()
    if not bundle_root.is_dir():
        raise ValidationBundleError(f"Validation bundle is not a directory: {bundle_root}")
    manifest_path = bundle_root / "validation_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except FileNotFoundError as exc:
        raise ValidationBundleError(f"Missing validation_manifest.json in {bundle_root}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationBundleError(
            f"Cannot parse validation manifest {manifest_path}: {exc}"
        ) from exc
    if not isinstance(manifest, dict):
        raise ValidationBundleError(
            f"Validation manifest must contain a JSON object: {manifest_path}"
        )

    version = manifest.get("schema_version")
    if version not in (1, 2):
        raise ValidationBundleError(f"Unsupported validation manifest schema_version: {version!r}")
    raw_cases = manifest.get("cases")
    if not isinstance(raw_cases, list):
        raise ValidationBundleError("Validation manifest field 'cases' must be a list")

    warnings: list[str] = []
    if version == 1:
        warnings.append("Loaded validation manifest schema v1 using compatibility inference.")
    cases: list[ValidationBundleCase] = []
    identities: set[ValidationCaseIdentity] = set()
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, dict):
            raise ValidationBundleError(f"Validation case {index} must be a JSON object")
        case = _load_case(raw_case, version=version, root=bundle_root, index=index)
        if case.identity in identities:
            raise ValidationBundleError(
                f"Duplicate validation case identity: {case.identity.to_dict()}"
            )
        identities.add(case.identity)
        cases.append(case)

    repo = manifest.get("repo") if isinstance(manifest.get("repo"), dict) else {}
    return ValidationBundle(
        root=bundle_root,
        schema_version=version,
        cases=tuple(sorted(cases, key=lambda case: case.identity)),
        warnings=tuple(warnings),
        metadata=ValidationBundleMetadata(
            generated_at=_optional_string(manifest.get("generated_at")),
            hpx_version=_optional_string(manifest.get("hpx_version")),
            repo_sha=_optional_string(repo.get("sha")),
            repo_branch=_optional_string(repo.get("branch")),
            repo_dirty=repo.get("dirty") if isinstance(repo.get("dirty"), bool) else None,
        ),
    )


def _load_case(
    raw: dict[str, Any], *, version: int, root: Path, index: int
) -> ValidationBundleCase:
    case_id = _required_string(raw, "case_id", index)
    status = _required_string(raw, "status", index)
    if status not in _STATUSES:
        raise ValidationBundleError(f"Validation case {case_id!r} has invalid status {status!r}")

    if version == 2:
        identity_raw = raw.get("identity")
        if not isinstance(identity_raw, dict):
            raise ValidationBundleError(f"Validation case {case_id!r} has no identity object")
        attempt = identity_raw.get("attempt")
        memory = identity_raw.get("requested_memory")
        power = identity_raw.get("requested_power")
        health = raw.get("health_issues", [])
        provenance = raw.get("provenance", {})
    else:
        identity_raw = raw
        match = _REPEAT_SUFFIX.search(case_id)
        attempt = int(match.group("attempt")) if match else 1
        memory = {"preset": raw.get("memory")}
        power = {"enabled": raw.get("power")}
        health = []
        provenance = {"jlink_serial": raw.get("jlink_serial")}

    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        raise ValidationBundleError(f"Validation case {case_id!r} has invalid repeat attempt")
    if not isinstance(memory, dict) or not isinstance(power, dict):
        raise ValidationBundleError(
            f"Validation case {case_id!r} has invalid requested configuration"
        )
    if not isinstance(health, list) or not all(isinstance(issue, str) for issue in health):
        raise ValidationBundleError(f"Validation case {case_id!r} has invalid health_issues")
    if not isinstance(provenance, dict):
        raise ValidationBundleError(f"Validation case {case_id!r} has invalid provenance")

    identity = ValidationCaseIdentity(
        model_id=_required_string(identity_raw, "model_id", index),
        engine=_required_string(identity_raw, "engine", index),
        board=_required_string(identity_raw, "board", index),
        toolchain=_required_string(identity_raw, "toolchain", index),
        transport=_required_string(identity_raw, "transport", index),
        requested_memory=_freeze_mapping(memory),
        requested_power=_freeze_mapping(power),
        attempt=attempt,
    )
    raw_artifacts = raw.get("artifacts")
    if not isinstance(raw_artifacts, dict):
        raise ValidationBundleError(f"Validation case {case_id!r} has invalid artifacts")
    artifacts: list[tuple[str, ArtifactRef]] = []
    for name, value in raw_artifacts.items():
        if not isinstance(name, str):
            raise ValidationBundleError(
                f"Validation case {case_id!r} has a non-string artifact name"
            )
        if version == 1:
            path_value = value
            available = False
        elif isinstance(value, dict):
            path_value = value.get("path")
            available = value.get("available", False)
        else:
            raise ValidationBundleError(f"Validation case {case_id!r} artifact {name!r} is invalid")
        if not isinstance(path_value, str) or not isinstance(available, bool):
            raise ValidationBundleError(f"Validation case {case_id!r} artifact {name!r} is invalid")
        _resolve_safe_artifact(root, path_value, case_id=case_id, name=name)
        if version == 1:
            available = (root / PurePosixPath(path_value)).exists()
        artifacts.append((name, ArtifactRef(path=path_value, available=available)))

    return ValidationBundleCase(
        identity=identity,
        case_id=case_id,
        status=status,
        health_issues=tuple(health),
        artifacts=tuple(sorted(artifacts)),
        provenance=tuple(sorted(provenance.items())),
    )


def resolve_artifact(bundle: ValidationBundle, artifact: ArtifactRef) -> Path:
    """Resolve an already-validated artifact reference within its bundle."""

    resolved = (bundle.root / PurePosixPath(artifact.path)).resolve()
    try:
        resolved.relative_to(bundle.root)
    except ValueError as exc:
        raise ValidationBundleError(f"Artifact path escapes bundle root: {artifact.path!r}") from exc
    return resolved

def _resolve_safe_artifact(root: Path, raw: str, *, case_id: str, name: str) -> Path:
    if not raw or "\x00" in raw or "\\" in raw or _WINDOWS_ABSOLUTE.match(raw):
        raise ValidationBundleError(
            f"Unsafe artifact path for case {case_id!r}, artifact {name!r}: {raw!r}"
        )
    pure = PurePosixPath(raw)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in raw.split("/")):
        raise ValidationBundleError(
            f"Unsafe artifact path for case {case_id!r}, artifact {name!r}: {raw!r}"
        )
    resolved = (root / pure).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValidationBundleError(
            f"Artifact path escapes bundle for case {case_id!r}, artifact {name!r}: {raw!r}"
        ) from exc
    return resolved


def _required_string(value: dict[str, Any], key: str, index: int) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValidationBundleError(f"Validation case {index} field {key!r} must be a string")
    return item


def _freeze_mapping(value: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (str(key), json.dumps(item, sort_keys=True, separators=(",", ":")))
            for key, item in value.items()
            if item is not None
        )
    )


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None
