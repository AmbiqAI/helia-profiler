"""Typed policy for deciding which result deltas are meaningful."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..results import (
    COMPARABILITY_REGISTRY,
    DIMENSION_DIFFERS,
    POWER_DIMENSION_MISMATCH,
    ComparabilityCode,
    ComparabilitySeverity,
    ComparisonDimension,
    ResultValidity,
    RunStatus,
)
from ..results.dimensions import DIMENSION_REGISTRY, ArtifactSource

# Not re-exported by the results package (construction shape changes in #154
# Phase 3); imported from the registry module directly.
from ..results.issues import ComparabilityCodeFamily

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
    """Construction chokepoint for static codes: severity and metric group
    come from the registry, so a code's effect on comparison output cannot
    drift from its declaration."""
    spec = COMPARABILITY_REGISTRY[code]
    if spec.metric_group is not None:
        context = {"metric_group": spec.metric_group, **context}
    return ComparabilityIssue(
        code=str(code),
        severity=spec.severity,
        message=message,
        context=context,
    )


def _family_issue(
    family: ComparabilityCodeFamily,
    dimension: ComparisonDimension,
    message: str,
    **context: Any,
) -> ComparabilityIssue:
    """The same chokepoint for parameterized families: code, severity, and
    metric group are taken from one family object, so pairing a family's code
    with another family's effect cannot compile its way in."""
    if family.metric_group is not None:
        context = {"metric_group": family.metric_group, **context}
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
    baseline_model = baseline_dimensions.get(ComparisonDimension.MODEL_SHA256)
    candidate_model = candidate_dimensions.get(ComparisonDimension.MODEL_SHA256)
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
        # A spec may scope itself to other dimensions (registry data, not
        # comparator special-casing): it is consulted only when every scope
        # dimension is present AND equal on both sides. The firmware
        # fingerprint is the user: cross-platform renders trivially differ,
        # and board/SoC differences are documented visible-not-blocking, so
        # its mismatch only means something on a matching platform (#138).
        scoped_to = DIMENSION_REGISTRY[dimension].scoped_to
        if scoped_to and any(
            baseline_dimensions.get(scope) is None
            or candidate_dimensions.get(scope) is None
            or baseline_dimensions.get(scope) != candidate_dimensions.get(scope)
            for scope in scoped_to
        ):
            continue
        baseline_value = baseline_dimensions.get(dimension)
        candidate_value = candidate_dimensions.get(dimension)
        if baseline_value is not None and candidate_value is not None and baseline_value != candidate_value:
            issues.append(
                _family_issue(
                    POWER_DIMENSION_MISMATCH,
                    dimension,
                    f"Power metrics omitted because {dimension} differs.",
                    baseline=baseline_value,
                    candidate=candidate_value,
                )
            )
    for role, dimensions in (("baseline", baseline_dimensions), ("candidate", candidate_dimensions)):
        integrity = dimensions.get(ComparisonDimension.POWER_INTEGRITY)
        if integrity not in (None, "valid"):
            issues.append(
                _issue(
                    ComparabilityCode.METRIC_POWER_INTEGRITY_INVALID,
                    f"Power metrics omitted because the {role} power result is {integrity}.",
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
    """Registry-driven read of every dimension from the run artifacts.

    Each spec declares its source and path (``results/dimensions.py``); the
    ``_nested`` traversal is deliberately crash-tolerant because artifacts
    from other HPX versions may hold shapes this build would not write —
    pre-#154-Phase-2 artifacts on disk store ``summary.power.sync`` as a
    bare bool, which an unguarded ``.get()`` chain dies on with an
    ``AttributeError`` that is not an ``HpxError``, aborting a whole
    multi-case validation compare instead of recording one COMPARE_ERROR.

    The manifest merges last as the authoritative record — except for the
    dimensions whose spec says ``manifest_authoritative=False``
    (``power_lockstep``): those record the RUNTIME state of the rail, config
    intent answers the wrong question, and a manifest value must never
    override them (the #115 phantom-comparability rule; rationale on the
    spec).
    """
    dimensions: dict[str, Any] = {}
    power = run.summary.get("power")
    power_dict = power if isinstance(power, dict) else None
    for spec in DIMENSION_REGISTRY.values():
        if spec.source is ArtifactSource.RUN_METADATA:
            dimensions[spec.dimension] = _nested(run.metadata, *spec.path)
        elif spec.source is ArtifactSource.SUMMARY:
            dimensions[spec.dimension] = _nested(run.summary, *spec.path)
        elif spec.source is ArtifactSource.SUMMARY_POWER:
            if power_dict is not None:
                dimensions[spec.dimension] = (
                    spec.derive(power_dict)
                    if spec.derive is not None
                    else _nested(power_dict, *spec.path)
                )
        elif spec.source is not ArtifactSource.MANIFEST_ONLY:
            # A new ArtifactSource must be dispatched here explicitly — a
            # silently dropped dimension is the failure mode this registry
            # exists to prevent.
            raise AssertionError(f"Unhandled artifact source: {spec.source}")
    if run.manifest is not None:
        protected = {
            spec.dimension
            for spec in DIMENSION_REGISTRY.values()
            if not spec.manifest_authoritative
        }
        dimensions.update(
            {
                key: value
                for key, value in run.manifest.comparability.items()
                if value is not None and key not in protected
            }
        )
    return dimensions


def _nested(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
