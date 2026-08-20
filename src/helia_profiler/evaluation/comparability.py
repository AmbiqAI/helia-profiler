"""Typed policy for deciding which result deltas are meaningful."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..results import (
    COMPARABILITY_REGISTRY,
    DIMENSION_DIFFERS,
    POWER_DIMENSION_MISMATCH,
    ComparabilityCode,
    ComparabilityCodeFamily,
    ComparabilitySeverity,
    ComparisonDimension,
    ResultValidity,
    RunStatus,
)

if TYPE_CHECKING:
    from .compare import RunArtifacts


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


def _issue(code: ComparabilityCode, message: str, **context: Any) -> ComparabilityIssue:
    """Construction chokepoint for static codes: severity comes from the
    registry, so a code's effect on comparison output cannot drift from its
    declaration."""
    return ComparabilityIssue(
        code=str(code),
        severity=COMPARABILITY_REGISTRY[code].severity,
        message=message,
        context=context,
    )


def _family_issue(
    family: ComparabilityCodeFamily,
    dimension: ComparisonDimension,
    message: str,
    **context: Any,
) -> ComparabilityIssue:
    """The same chokepoint for parameterized families: code and severity are
    taken from one family object, so pairing a family's code with another
    family's severity cannot compile its way in."""
    return ComparabilityIssue(
        code=family.code_for(dimension),
        severity=family.severity,
        message=message,
        context=context,
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
                _issue(
                    ComparabilityCode.RESULT_INCOMPLETE,
                    f"The {role} result bundle is {manifest.status.value}.",
                    role=role,
                    run_id=manifest.run_id,
                )
            )
        if manifest is not None and manifest.validity is ResultValidity.INVALID:
            issues.append(
                _issue(
                    ComparabilityCode.RESULT_INVALID,
                    f"The {role} result is invalid and cannot be compared.",
                    role=role,
                    run_id=manifest.run_id,
                )
            )
        elif manifest is None and run.summary.get("overflow_detected"):
            issues.append(
                _issue(
                    ComparabilityCode.RESULT_INVALID_PMU_OVERFLOW,
                    f"The legacy {role} result has PMU counter overflow.",
                    role=role,
                )
            )
        elif manifest is not None and manifest.validity is ResultValidity.DEGRADED:
            issues.append(
                _issue(
                    ComparabilityCode.RESULT_DEGRADED,
                    f"The {role} result is degraded; interpret affected metrics cautiously.",
                    role=role,
                    run_id=manifest.run_id,
                )
            )

    baseline_dimensions = _dimensions(baseline)
    candidate_dimensions = _dimensions(candidate)
    baseline_model = baseline_dimensions.get("model_sha256")
    candidate_model = candidate_dimensions.get("model_sha256")
    if baseline_model and candidate_model and baseline_model != candidate_model:
        issues.append(
            _issue(
                ComparabilityCode.IDENTITY_MODEL_MISMATCH,
                "Model SHA-256 differs; run-level performance deltas are not comparable.",
                baseline=baseline_model,
                candidate=candidate_model,
            )
        )

    # power_monitor: an on-target monitor's IOM stays powered on the measured
    # rail, so block-present vs block-absent runs differ by a real,
    # bench-measurable current adder and must not be power-compared.
    # Baselines predating the dimension carry None and are skipped, like
    # every other dimension here.
    # power_clean_window_probe: WHAT ran inside the measured window. The
    # busy_loop probe replaces the model with a calibrated CPU spin, so an
    # infer baseline against a busy_loop candidate compares a model inference
    # against a CPU spin and reports the difference as a regression (#125).
    for dimension in POWER_DIMENSION_MISMATCH.dimensions:
        baseline_value = baseline_dimensions.get(dimension)
        candidate_value = candidate_dimensions.get(dimension)
        if baseline_value is not None and candidate_value is not None and baseline_value != candidate_value:
            issues.append(
                _family_issue(
                    POWER_DIMENSION_MISMATCH,
                    dimension,
                    f"Power metrics omitted because {dimension} differs.",
                    metric_group="power",
                    baseline=baseline_value,
                    candidate=candidate_value,
                )
            )
    for role, dimensions in (("baseline", baseline_dimensions), ("candidate", candidate_dimensions)):
        integrity = dimensions.get("power_integrity")
        if integrity not in (None, "valid"):
            issues.append(
                _issue(
                    ComparabilityCode.METRIC_POWER_INTEGRITY_INVALID,
                    f"Power metrics omitted because the {role} power result is {integrity}.",
                    metric_group="power",
                    role=role,
                    integrity=integrity,
                )
            )

    baseline_ops = [row.get("op") for row in baseline.layers]
    candidate_ops = [row.get("op") for row in candidate.layers]
    if len(baseline.layers) != len(candidate.layers):
        issues.append(
            _issue(
                ComparabilityCode.TOPOLOGY_LAYER_COUNT_MISMATCH,
                "Per-layer deltas omitted because layer counts differ "
                f"(baseline={len(baseline.layers)}, candidate={len(candidate.layers)}).",
            )
        )
    elif baseline_ops != candidate_ops:
        issues.append(
            _issue(
                ComparabilityCode.TOPOLOGY_OPERATION_SEQUENCE_MISMATCH,
                "Per-layer deltas omitted because operation sequences differ.",
            )
        )

    for dimension in DIMENSION_DIFFERS.dimensions:
        baseline_value = baseline_dimensions.get(dimension)
        candidate_value = candidate_dimensions.get(dimension)
        if baseline_value is not None and candidate_value is not None and baseline_value != candidate_value:
            issues.append(
                _family_issue(
                    DIMENSION_DIFFERS,
                    dimension,
                    f"Comparison dimension differs: {dimension}.",
                    baseline=baseline_value,
                    candidate=candidate_value,
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
                # Manifest-less fallback: a published on-device payload means
                # monitor firmware was live. The manifest's config-derived
                # value (merged below) is authoritative when present.
                "power_monitor": "ina228" if power.get("on_device_summary") else "none",
                # Lock-step is read from the RUNTIME record only -- the state
                # the rail was actually in, written by capture/__init__.py as
                # summary.power.sync.lockstep. Deliberately NOT carried in the
                # manifest alongside the config-derived dimensions above: the
                # manifest is merged last and would overwrite this, and config
                # intent is the wrong question. capture/__init__.py's own
                # comment says why -- "a driver with no GO output degrades to
                # the null controller even when the config resolved lock-step
                # on" -- so an intent-derived value would compare two runs as
                # equivalent while one of them actually free-ran its window,
                # which is the phantom-comparability failure this dimension
                # exists to prevent.
                #
                # _nested, not a hand-rolled .get chain: report/summary.py
                # copies power metadata's "sync" through on an is-not-None
                # check alone, so it reaches disk as whatever was stored --
                # the repo's own report golden fixture holds the bool True,
                # which an unguarded .get() dies on with AttributeError. That
                # is not an HpxError, so cli/compare_cmd.py would print a
                # traceback and validation/compare.py would abort a whole
                # multi-case run instead of recording one COMPARE_ERROR.
                "power_lockstep": _nested(power, "sync", "lockstep"),
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
