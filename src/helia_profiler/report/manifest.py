"""Result bundle manifest generation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..results import (
    RESULT_MANIFEST_SCHEMA,
    RESULT_MANIFEST_SCHEMA_VERSION,
    ComparisonDimension,
    ResultArtifact,
    ResultManifest,
    RunStatus,
)
from ..firmware import measured_power_fingerprint
from ..results.serde import nested_get, sha256_file
from ..evaluation import evaluate_run
from .contracts import (
    PROFILE_RESULTS_SCHEMA,
    PROFILE_RESULTS_SCHEMA_VERSION,
    RUN_METADATA_SCHEMA,
    RUN_METADATA_SCHEMA_VERSION,
    RUN_SUMMARY_SCHEMA,
    RUN_SUMMARY_SCHEMA_VERSION,
)

if TYPE_CHECKING:
    from ..evaluation import RunEvaluation
    from ..pipeline import PipelineContext


_MEDIA_TYPES = {
    ".csv": "text/csv",
    ".json": "application/json",
    ".lock": "application/yaml",
}


def _write_result_manifest(
    ctx: PipelineContext,
    paths: list[Path],
    output_dir: Path,
    evaluation: RunEvaluation | None = None,
) -> Path:
    """Write the publication marker after every other result artifact.

    ``write_report`` passes the run's single :class:`RunEvaluation` -- the
    same one the summary rendered, so the two artifacts cannot disagree.
    The ``None`` default evaluates on demand for direct callers (tests).
    """
    if evaluation is None:
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
        sha256=sha256_file(path),
        **metadata,
    )


def _artifact_metadata(relative: str) -> dict[str, Any]:
    """Classify known products without closing the manifest to new artifacts."""
    name = Path(relative).name
    if name == "summary.json":
        return _artifact_fields(
            "core",
            "hpx.summary",
            schema=RUN_SUMMARY_SCHEMA,
            schema_version=RUN_SUMMARY_SCHEMA_VERSION,
            optional=False,
        )
    if name == "run_metadata.json":
        return _artifact_fields(
            "core",
            "hpx.run-metadata",
            schema=RUN_METADATA_SCHEMA,
            schema_version=RUN_METADATA_SCHEMA_VERSION,
            optional=False,
        )
    if name == "nsx.lock":
        return _artifact_fields(
            "core",
            "hpx.nsx-lock",
            producer="neuralspotx",
            optional=False,
        )
    if name == "profile_results.json":
        return _artifact_fields(
            "core",
            "hpx.profile-layers",
            schema=PROFILE_RESULTS_SCHEMA,
            schema_version=PROFILE_RESULTS_SCHEMA_VERSION,
            optional=False,
        )
    if name == "profile_results.csv":
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
    schema: str | None = None,
    schema_version: int | None = None,
    producer: str = "hpx",
    optional: bool,
) -> dict[str, Any]:
    return {
        "role": role,
        "name": name,
        "schema": schema,
        "schema_version": schema_version,
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
    if ctx.run_metadata.compatibility is not None:
        provenance["compatibility"] = ctx.run_metadata.compatibility.to_dict()
    if ctx.run_metadata.dependencies is not None:
        provenance["dependencies"] = ctx.run_metadata.dependencies.to_dict()
    return provenance


def _comparability(ctx: PipelineContext) -> dict[str, Any]:
    """Authoritative comparability record, one entry per registry dimension.

    Extraction from the typed context stays here (declaring extractors in
    ``results/`` would couple it to the pipeline), but the key set is
    contract-tested against ``DIMENSION_REGISTRY``: every dimension is
    recorded except those whose spec says ``manifest_authoritative=False``
    (``power_lockstep`` — the runtime value in ``summary.power.sync.lockstep``
    is the record; a config-derived value here would be merged last by the
    reader and silently overwrite it — see the spec's rationale).
    """
    config = ctx.run_metadata.config_snapshot
    model = ctx.run_metadata.model
    platform = ctx.run_metadata.platform
    toolchain = ctx.run_metadata.toolchain
    firmware = ctx.pmu_result.meta if ctx.pmu_result is not None else None
    values: dict[ComparisonDimension, Any] = {
        ComparisonDimension.MODEL_SHA256: model.sha256 if model is not None else None,
        ComparisonDimension.HPX_VERSION: ctx.run_metadata.hpx_version,
        ComparisonDimension.ENGINE: nested_get(config, "engine", "type"),
        ComparisonDimension.BOARD: platform.board if platform is not None else None,
        ComparisonDimension.SOC: platform.soc if platform is not None else None,
        ComparisonDimension.CPU_CLOCK: platform.cpu_clock_name if platform is not None else None,
        ComparisonDimension.TOOLCHAIN: nested_get(config, "target", "toolchain"),
        ComparisonDimension.COMPILER_VERSION: (
            toolchain.compiler_version if toolchain is not None else None
        ),
        ComparisonDimension.SYSTEM_CLOCK_HZ: (
            firmware.system_clock_hz if firmware is not None else None
        ),
        ComparisonDimension.RUN_SUMMARY_SCHEMA_VERSION: RUN_SUMMARY_SCHEMA_VERSION,
        ComparisonDimension.RUN_METADATA_SCHEMA_VERSION: RUN_METADATA_SCHEMA_VERSION,
        ComparisonDimension.TRANSPORT: nested_get(config, "target", "transport"),
        ComparisonDimension.ARENA_LOCATION: nested_get(config, "model", "arena_location"),
        ComparisonDimension.WEIGHTS_LOCATION: nested_get(config, "model", "weights_location"),
        ComparisonDimension.ENGINE_VERSION: (
            ctx.run_metadata.engine.version if ctx.run_metadata.engine is not None else None
        ),
        ComparisonDimension.LINK_FAMILY: (
            platform.link_family if platform is not None else None
        ),
    }
    if ctx.power_result is not None:
        # A run that measured no power has nothing to say about how it
        # measured it; a value on this side would block a power-vs-no-power
        # comparison that used to work.
        values.update(
            {
                ComparisonDimension.POWER_SCOPE: ctx.power_result.metadata.measurement_scope,
                ComparisonDimension.POWER_INTEGRITY: ctx.power_result.metadata.integrity,
                ComparisonDimension.POWER_MODE: ctx.config.power.mode.value,
                ComparisonDimension.POWER_FIRMWARE: (
                    ctx.power_run.plan.firmware_mode if ctx.power_run else None
                ),
                # An on-target monitor keeps its IOM powered on the measured
                # rail for the whole run — a double-digit-percent current
                # adder on a low-power target. A block/no-block pair is
                # therefore not power-comparable even when every other
                # dimension matches.
                ComparisonDimension.POWER_MONITOR: (
                    "ina228" if ctx.config.power.monitor_selected else "none"
                ),
                # What ran inside the measured window: the busy_loop probe
                # replaces the model with a calibrated CPU spin (#125).
                ComparisonDimension.POWER_CLEAN_WINDOW_PROBE: (
                    ctx.config.profiling.clean_window_probe.value
                ),
                # Code hash of the measured binary (#138/#115) — same helper
                # summary.py writes into summary.power, so the manifest and
                # artifact values cannot disagree.
                ComparisonDimension.POWER_FIRMWARE_FINGERPRINT: (
                    measured_power_fingerprint(ctx)
                ),
            }
        )
    return {dimension.value: value for dimension, value in values.items()}


