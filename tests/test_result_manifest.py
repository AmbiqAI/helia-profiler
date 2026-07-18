from __future__ import annotations

import json
from pathlib import Path

import pytest

from helia_profiler.errors import ReportError
from helia_profiler.result_manifest import (
    ResultArtifact,
    ResultManifest,
    ResultValidity,
    RunStatus,
    load_result_manifest,
)


def _manifest_data(artifact: Path) -> dict:
    import hashlib

    return {
        "schema": "hpx.result-manifest",
        "schema_version": 1,
        "run_id": "run-1",
        "timestamp": "2026-07-18T00:00:00+00:00",
        "hpx_version": "0.1.0",
        "status": "complete",
        "validity": "valid",
        "issues": [],
        "provenance": {"future": {"value": 1}},
        "comparability": {"model_sha256": "abc"},
        "artifacts": [
            {
                "path": artifact.name,
                "media_type": "application/json",
                "size_bytes": artifact.stat().st_size,
                "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                "future_artifact_field": True,
            }
        ],
        "extensions": {"vendor.example": {"metric": 42}},
        "future_root_field": {"enabled": True},
    }


def test_manifest_preserves_unknown_fields(tmp_path: Path):
    artifact = tmp_path / "summary.json"
    artifact.write_text("{}\n")
    data = _manifest_data(artifact)

    manifest = ResultManifest.from_dict(data)
    written = manifest.write(tmp_path / "result_manifest.json")

    assert json.loads(written.read_text()) == data


def test_known_fields_override_colliding_extra_values(tmp_path: Path):
    artifact = tmp_path / "summary.json"
    artifact.write_text("{}\n")
    data = _manifest_data(artifact)
    manifest = ResultManifest.from_dict(data)
    object.__setattr__(manifest, "extra", {"run_id": "forged", "future": True})

    serialized = manifest.to_dict()

    assert serialized["run_id"] == "run-1"
    assert serialized["future"] is True


def test_load_result_manifest_can_verify_artifacts(tmp_path: Path):
    artifact = tmp_path / "summary.json"
    artifact.write_text("{}\n")
    path = tmp_path / "result_manifest.json"
    path.write_text(json.dumps(_manifest_data(artifact)))

    loaded = load_result_manifest(path, verify=True)

    assert loaded.artifacts[0].path == "summary.json"


def test_manifest_verification_detects_tampering(tmp_path: Path):
    artifact = tmp_path / "summary.json"
    artifact.write_text("{}\n")
    manifest = ResultManifest.from_dict(_manifest_data(artifact))
    artifact.write_text('{"changed": true}\n')

    with pytest.raises(ReportError, match="size mismatch|digest mismatch"):
        manifest.verify(tmp_path)


def test_manifest_rejects_artifact_outside_bundle(tmp_path: Path):
    artifact = tmp_path / "summary.json"
    artifact.write_text("{}\n")
    data = _manifest_data(artifact)
    data["artifacts"][0]["path"] = "../summary.json"
    manifest = ResultManifest.from_dict(data)

    with pytest.raises(ReportError, match="escapes bundle"):
        manifest.verify(tmp_path)


def test_manifest_rejects_absolute_artifact_path(tmp_path: Path):
    artifact = tmp_path / "summary.json"
    artifact.write_text("{}\n")
    data = _manifest_data(artifact)
    data["artifacts"][0]["path"] = str(artifact)

    with pytest.raises(ReportError, match="relative path"):
        ResultManifest.from_dict(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [("status", "unknown"), ("issues", None), ("artifacts", None)],
)
def test_manifest_normalizes_malformed_fields(field: str, value, tmp_path: Path):
    artifact = tmp_path / "summary.json"
    artifact.write_text("{}\n")
    data = _manifest_data(artifact)
    data[field] = value

    with pytest.raises(ReportError, match="Invalid ResultManifest"):
        ResultManifest.from_dict(data)


@pytest.mark.parametrize("size", [True, 1.5, -1])
def test_artifact_rejects_schema_invalid_sizes(size):
    with pytest.raises(ReportError, match="non-negative integer"):
        ResultArtifact(
            path="summary.json",
            media_type="application/json",
            size_bytes=size,
            sha256="0" * 64,
        )


def test_manifest_direct_construction_requires_enum_values():
    with pytest.raises(ReportError, match="RunStatus"):
        ResultManifest(
            schema="hpx.result-manifest",
            schema_version=1,
            run_id="run-1",
            timestamp="2026-07-18T00:00:00+00:00",
            hpx_version="0.1.0",
            status="complete",  # type: ignore[arg-type]
            validity=ResultValidity.VALID,
            issues=(),
            provenance={},
            comparability={},
            artifacts=(),
        )

    with pytest.raises(ReportError, match="ResultValidity"):
        ResultManifest(
            schema="hpx.result-manifest",
            schema_version=1,
            run_id="run-1",
            timestamp="2026-07-18T00:00:00+00:00",
            hpx_version="0.1.0",
            status=RunStatus.COMPLETE,
            validity="valid",  # type: ignore[arg-type]
            issues=(),
            provenance={},
            comparability={},
            artifacts=(),
        )


def test_result_manifest_schema_is_open_at_extension_boundaries():
    schema_path = (
        Path(__file__).parents[1]
        / "src"
        / "helia_profiler"
        / "data"
        / "result_manifest.schema.v1.json"
    )
    schema = json.loads(schema_path.read_text())

    assert schema["additionalProperties"] is True
    assert schema["properties"]["provenance"]["additionalProperties"] is True
    assert schema["properties"]["comparability"]["additionalProperties"] is True
    assert schema["properties"]["extensions"]["additionalProperties"] is True
    assert schema["$defs"]["artifact"]["additionalProperties"] is True


def test_optional_artifact_semantics_are_omitted_for_legacy_records(tmp_path: Path):
    artifact = tmp_path / "summary.json"
    artifact.write_text("{}\n")

    serialized = ResultArtifact.from_dict(_manifest_data(artifact)["artifacts"][0]).to_dict()

    assert "role" not in serialized
    assert "name" not in serialized
    assert "schema" not in serialized
    assert "schema_version" not in serialized
    assert "producer" not in serialized
    assert "optional" not in serialized


def test_complete_profile_requires_named_core_artifacts(tmp_path: Path):
    artifact = tmp_path / "summary.json"
    artifact.write_text("{}\n")
    data = _manifest_data(artifact)
    data["bundle_type"] = "profile"
    manifest = ResultManifest.from_dict(data)

    with pytest.raises(ReportError, match="missing required core artifacts"):
        manifest.verify(tmp_path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("timestamp", "", "timestamp"),
        ("issues", [{"code": 1, "severity": "error", "message": "bad"}], "code"),
        ("issues", [{"code": "bad", "severity": "error", "message": 1}], "message"),
    ],
)
def test_python_validation_matches_required_schema_fields(
    field: str, value, message: str, tmp_path: Path
):
    artifact = tmp_path / "summary.json"
    artifact.write_text("{}\n")
    data = _manifest_data(artifact)
    data[field] = value

    with pytest.raises(ReportError, match=message):
        ResultManifest.from_dict(data)
