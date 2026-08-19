"""Typed policy for deciding which result deltas are meaningful."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from ..results import ResultValidity, RunStatus

if TYPE_CHECKING:
    from .compare import RunArtifacts


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

    # power_monitor: an on-target monitor's IOM stays powered on the measured
    # rail, so block-present vs block-absent runs differ by a real,
    # bench-measurable current adder and must not be power-compared.
    # Baselines predating the dimension carry None and are skipped, like
    # every other dimension here.
    # power_window_semantics: a digest of everything the firmware render makes
    # the measured window DO -- which probe runs inside it, which clock times
    # it, whether the radio and crypto blocks are shut down. A busy_loop run
    # measures a CPU spin and an infer run measures the model; comparing them
    # reports the difference between two different quantities as a regression.
    # Keyed on a digest of PowerWindowContext's whole field set rather than on
    # a hand-listed subset, because that list is what kept being incomplete
    # (#125).
    for dimension in (
        "power_scope",
        "power_mode",
        "power_firmware",
        "power_monitor",
        "power_lockstep",
        "power_window_semantics",
    ):
        baseline_value = baseline_dimensions.get(dimension)
        candidate_value = candidate_dimensions.get(dimension)
        if baseline_value is not None and candidate_value is not None and baseline_value != candidate_value:
            context: dict[str, Any] = {
                "metric_group": "power",
                "baseline": baseline_value,
                "candidate": candidate_value,
            }
            message = f"Power metrics omitted because {dimension} differs."
            if dimension == "power_window_semantics":
                # A digest pair tells the user nothing actionable, so name the
                # properties that actually differ.
                changed = _window_semantics_diff(baseline, candidate)
                if changed:
                    context["changed"] = changed
                    named = ", ".join(
                        f"{key} {old!r} -> {new!r}" for key, (old, new) in changed.items()
                    )
                    message = (
                        "Power metrics omitted because the measured window "
                        f"differs: {named}."
                    )
            issues.append(
                ComparabilityIssue(
                    # The doubled "power_" is pre-existing and load-bearing:
                    # these codes are documented and asserted. Renaming them
                    # here would be a silent break of a public surface.
                    code=f"metric.power_{dimension}_mismatch",
                    severity=ComparabilitySeverity.METRIC_BLOCKING,
                    message=message,
                    context=context,
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


def _window_semantics_diff(
    baseline: RunArtifacts, candidate: RunArtifacts
) -> dict[str, tuple[Any, Any]]:
    """Which window properties differ, from the manifests' provenance."""
    base = _window_semantics_fields(baseline)
    cand = _window_semantics_fields(candidate)
    if not base or not cand:
        return {}
    return {
        key: (base.get(key), cand.get(key))
        for key in sorted(set(base) | set(cand))
        if base.get(key) != cand.get(key)
    }


def _window_semantics_fields(run: RunArtifacts) -> dict[str, Any]:
    if run.manifest is None:
        return {}
    fields = run.manifest.provenance.get("power_window")
    return fields if isinstance(fields, dict) else {}


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
