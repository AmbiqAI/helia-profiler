"""Typed policy for deciding which result deltas are meaningful."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from ..result_manifest import ResultValidity, RunStatus

if TYPE_CHECKING:
    from ..compare import RunArtifacts


class ComparabilitySeverity(StrEnum):
    """Effect of one compatibility issue on comparison output."""

    BLOCKING = "blocking"
    LAYER_BLOCKING = "layer_blocking"
    METRIC_BLOCKING = "metric_blocking"
    INFORMATIVE = "informative"


@dataclass(frozen=True)
class ComparabilityIssue:
    """One machine-readable compatibility decision."""

    code: str
    severity: ComparabilitySeverity
    message: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ComparabilityAssessment:
    """Whether run-level and per-layer deltas may be computed."""

    issues: tuple[ComparabilityIssue, ...] = ()

    @property
    def run_metrics_comparable(self) -> bool:
        return not any(issue.severity is ComparabilitySeverity.BLOCKING for issue in self.issues)

    @property
    def layers_comparable(self) -> bool:
        return self.run_metrics_comparable and not any(
            issue.severity is ComparabilitySeverity.LAYER_BLOCKING for issue in self.issues
        )

    @property
    def power_metrics_comparable(self) -> bool:
        return self.run_metrics_comparable and not any(
            issue.severity is ComparabilitySeverity.METRIC_BLOCKING
            and issue.context.get("metric_group") == "power"
            for issue in self.issues
        )


_INFORMATIVE_DIMENSIONS = (
    "hpx_version",
    "engine",
    "board",
    "soc",
    "cpu_clock",
    "toolchain",
    "compiler_version",
    "system_clock_hz",
    "run_summary_schema_version",
    "run_metadata_schema_version",
    "transport",
    "arena_location",
    "weights_location",
)


def assess_comparability(
    baseline: RunArtifacts,
    candidate: RunArtifacts,
) -> ComparabilityAssessment:
    """Compare identity, validity, topology, and intentional run dimensions."""
    issues: list[ComparabilityIssue] = []
    for role, run in (("baseline", baseline), ("candidate", candidate)):
        manifest = run.manifest
        if manifest is not None and manifest.status is not RunStatus.COMPLETE:
            issues.append(
                ComparabilityIssue(
                    code="result.incomplete",
                    severity=ComparabilitySeverity.BLOCKING,
                    message=f"The {role} result bundle is {manifest.status.value}.",
                    context={"role": role, "run_id": manifest.run_id},
                )
            )
        if manifest is not None and manifest.validity is ResultValidity.INVALID:
            issues.append(
                ComparabilityIssue(
                    code="result.invalid",
                    severity=ComparabilitySeverity.BLOCKING,
                    message=f"The {role} result is invalid and cannot be compared.",
                    context={"role": role, "run_id": manifest.run_id},
                )
            )
        elif manifest is None and run.summary.get("overflow_detected"):
            issues.append(
                ComparabilityIssue(
                    code="result.invalid_pmu_overflow",
                    severity=ComparabilitySeverity.BLOCKING,
                    message=f"The legacy {role} result has PMU counter overflow.",
                    context={"role": role},
                )
            )
        elif manifest is not None and manifest.validity is ResultValidity.DEGRADED:
            issues.append(
                ComparabilityIssue(
                    code="result.degraded",
                    severity=ComparabilitySeverity.INFORMATIVE,
                    message=f"The {role} result is degraded; interpret affected metrics cautiously.",
                    context={"role": role, "run_id": manifest.run_id},
                )
            )

    baseline_dimensions = _dimensions(baseline)
    candidate_dimensions = _dimensions(candidate)
    baseline_model = baseline_dimensions.get("model_sha256")
    candidate_model = candidate_dimensions.get("model_sha256")
    if baseline_model and candidate_model and baseline_model != candidate_model:
        issues.append(
            ComparabilityIssue(
                code="identity.model_mismatch",
                severity=ComparabilitySeverity.BLOCKING,
                message="Model SHA-256 differs; run-level performance deltas are not comparable.",
                context={"baseline": baseline_model, "candidate": candidate_model},
            )
        )

    for dimension in ("power_scope", "power_mode", "power_firmware"):
        baseline_value = baseline_dimensions.get(dimension)
        candidate_value = candidate_dimensions.get(dimension)
        if baseline_value is not None and candidate_value is not None and baseline_value != candidate_value:
            issues.append(
                ComparabilityIssue(
                    code=f"metric.power_{dimension}_mismatch",
                    severity=ComparabilitySeverity.METRIC_BLOCKING,
                    message=f"Power metrics omitted because {dimension} differs.",
                    context={
                        "metric_group": "power",
                        "baseline": baseline_value,
                        "candidate": candidate_value,
                    },
                )
            )
    for role, dimensions in (("baseline", baseline_dimensions), ("candidate", candidate_dimensions)):
        integrity = dimensions.get("power_integrity")
        if integrity not in (None, "valid"):
            issues.append(
                ComparabilityIssue(
                    code="metric.power_integrity_invalid",
                    severity=ComparabilitySeverity.METRIC_BLOCKING,
                    message=f"Power metrics omitted because the {role} power result is {integrity}.",
                    context={"metric_group": "power", "role": role, "integrity": integrity},
                )
            )

    baseline_ops = [row.get("op") for row in baseline.layers]
    candidate_ops = [row.get("op") for row in candidate.layers]
    if len(baseline.layers) != len(candidate.layers):
        issues.append(
            ComparabilityIssue(
                code="topology.layer_count_mismatch",
                severity=ComparabilitySeverity.LAYER_BLOCKING,
                message=(
                    "Per-layer deltas omitted because layer counts differ "
                    f"(baseline={len(baseline.layers)}, candidate={len(candidate.layers)})."
                ),
            )
        )
    elif baseline_ops != candidate_ops:
        issues.append(
            ComparabilityIssue(
                code="topology.operation_sequence_mismatch",
                severity=ComparabilitySeverity.LAYER_BLOCKING,
                message="Per-layer deltas omitted because operation sequences differ.",
            )
        )

    for dimension in _INFORMATIVE_DIMENSIONS:
        baseline_value = baseline_dimensions.get(dimension)
        candidate_value = candidate_dimensions.get(dimension)
        if baseline_value is not None and candidate_value is not None and baseline_value != candidate_value:
            issues.append(
                ComparabilityIssue(
                    code=f"dimension.{dimension}_differs",
                    severity=ComparabilitySeverity.INFORMATIVE,
                    message=f"Comparison dimension differs: {dimension}.",
                    context={"baseline": baseline_value, "candidate": candidate_value},
                )
            )
    return ComparabilityAssessment(issues=tuple(issues))


def _dimensions(run: RunArtifacts) -> dict[str, Any]:
    metadata = run.metadata
    config = metadata.get("config", {})
    platform = metadata.get("platform", {})
    model = metadata.get("model", {})
    toolchain = metadata.get("toolchain", {})
    firmware = metadata.get("firmware", {})
    dimensions = {
        "model_sha256": model.get("sha256"),
        "hpx_version": metadata.get("hpx_version"),
        "engine": _nested(config, "engine", "type"),
        "board": _nested(config, "target", "board"),
        "soc": platform.get("soc"),
        "cpu_clock": platform.get("cpu_clock_name"),
        "toolchain": _nested(config, "target", "toolchain"),
        "compiler_version": toolchain.get("compiler_version"),
        "system_clock_hz": firmware.get("system_clock_hz"),
        "run_summary_schema_version": run.summary.get("schema_version"),
        "run_metadata_schema_version": metadata.get("schema_version"),
        "transport": _nested(config, "target", "transport"),
        "arena_location": _nested(config, "model", "arena_location"),
        "weights_location": _nested(config, "model", "weights_location"),
    }
    power = run.summary.get("power")
    if isinstance(power, dict):
        dimensions.update(
            {
                "power_scope": power.get("measurement_scope"),
                "power_integrity": power.get("integrity"),
                "power_firmware": power.get("power_firmware"),
            }
        )
    if run.manifest is not None:
        dimensions.update(
            {key: value for key, value in run.manifest.comparability.items() if value is not None}
        )
    return dimensions


def _nested(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
