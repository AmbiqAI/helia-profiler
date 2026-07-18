"""Result bundle manifest generation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..result_manifest import (
    RESULT_MANIFEST_SCHEMA,
    RESULT_MANIFEST_SCHEMA_VERSION,
    ResultArtifact,
    ResultManifest,
    RunStatus,
    _sha256,
)
from ..evaluation import evaluate_run

if TYPE_CHECKING:
    from ..pipeline import PipelineContext


_MEDIA_TYPES = {
    ".csv": "text/csv",
    ".json": "application/json",
}


def _write_result_manifest(
    ctx: PipelineContext,
    paths: list[Path],
    output_dir: Path,
) -> Path:
    """Write the publication marker after every other result artifact."""
    evaluation = evaluate_run(ctx)
    artifacts = tuple(
        _result_artifact(path, output_dir)
        for path in paths
    )
    manifest = ResultManifest(
        schema=RESULT_MANIFEST_SCHEMA,
        schema_version=RESULT_MANIFEST_SCHEMA_VERSION,
        run_id=ctx.run_metadata.run_id,
        timestamp=ctx.run_metadata.timestamp,
        hpx_version=ctx.run_metadata.hpx_version,
        status=RunStatus.COMPLETE,
        validity=evaluation.validity,
        issues=evaluation.issues,
        provenance=_provenance(ctx),
        comparability=_comparability(ctx),
        artifacts=artifacts,
        bundle_type="profile",
    )
    return manifest.write(output_dir / "result_manifest.json")


def _result_artifact(path: Path, output_dir: Path) -> ResultArtifact:
    relative = path.relative_to(output_dir).as_posix()
    metadata = _artifact_metadata(relative)
    return ResultArtifact(
        path=relative,
        media_type=_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        size_bytes=path.stat().st_size,
        sha256=_sha256(path),
        **metadata,
    )


def _artifact_metadata(relative: str) -> dict[str, Any]:
    """Classify known products without closing the manifest to new artifacts."""
    name = Path(relative).name
    if name == "summary.json":
        return _artifact_fields("core", "hpx.summary", optional=False)
    if name == "run_metadata.json":
        return _artifact_fields("core", "hpx.run-metadata", optional=False)
    if name in {"profile_results.csv", "profile_results.json"}:
        return _artifact_fields("core", "hpx.profile-layers", optional=False)
    if relative.startswith("model_explorer/"):
        return _artifact_fields(
            "export",
            "model-explorer.overlay",
            producer="hpx.model-explorer-exporter",
            optional=True,
        )
    if name == "aot_operator_manifest.json":
        return _artifact_fields("extension", "helia-aot.operators", optional=True)
    if name == "aot_memory_layers.csv":
        return _artifact_fields("extension", "helia-aot.memory-layers", optional=True)
    if name == "power_summary.csv":
        return _artifact_fields("diagnostic", "hpx.power-summary", optional=True)
    if relative.startswith("detailed/"):
        return _artifact_fields("projection", None, optional=True)
    return _artifact_fields("extension", None, optional=True)


def _artifact_fields(
    role: str,
    name: str | None,
    *,
    producer: str = "hpx",
    optional: bool,
) -> dict[str, Any]:
    return {
        "role": role,
        "name": name,
        "schema": None,
        "schema_version": None,
        "producer": producer,
        "optional": optional,
    }

def _provenance(ctx: PipelineContext) -> dict[str, Any]:
    config_json = json.dumps(
        ctx.run_metadata.config_snapshot,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    provenance: dict[str, Any] = {
        "config_sha256": hashlib.sha256(config_json).hexdigest(),
    }
    if ctx.run_metadata.model is not None:
        provenance["model"] = asdict(ctx.run_metadata.model)
    if ctx.run_metadata.toolchain is not None:
        provenance["toolchain"] = asdict(ctx.run_metadata.toolchain)
    return provenance


def _comparability(ctx: PipelineContext) -> dict[str, Any]:
    config = ctx.run_metadata.config_snapshot
    model = ctx.run_metadata.model
    platform = ctx.run_metadata.platform
    dimensions = {
        "model_sha256": model.sha256 if model is not None else None,
        "engine": _nested(config, "engine", "type"),
        "board": platform.board if platform is not None else None,
        "soc": platform.soc if platform is not None else None,
        "cpu_clock": platform.cpu_clock_name if platform is not None else None,
        "toolchain": _nested(config, "target", "toolchain"),
        "transport": _nested(config, "target", "transport"),
        "arena_location": _nested(config, "model", "arena_location"),
        "weights_location": _nested(config, "model", "weights_location"),
    }
    if ctx.power_result is not None:
        dimensions.update(
            {
                "power_scope": ctx.power_result.metadata.get("measurement_scope"),
                "power_integrity": ctx.power_result.metadata.get("integrity"),
                "power_mode": ctx.config.power.mode.value,
                "power_firmware": ctx.power_run.plan.firmware_mode if ctx.power_run else None,
            }
        )
    return dimensions


def _nested(value: dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
