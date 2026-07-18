from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from helia_profiler.evaluation import (
    ComparisonProfile,
    MetricDirection,
    MetricPolicy,
    MissingMetricPolicy,
    VerdictStatus,
    evaluate_comparison_profile,
)
from helia_profiler.compare import CompareResult, ConfigDiffRow, MetricDiff, RunArtifacts
from helia_profiler.errors import ReportError


def _result(*metrics: MetricDiff) -> CompareResult:
    run = RunArtifacts(path=Path("results"), summary={}, metadata={}, layers=[])
    return CompareResult(
        baseline=run,
        candidate=run,
        config_rows=[ConfigDiffRow("Engine", "helia-rt", "helia-rt", "same", "engine")],
        metrics=list(metrics),
        layer_rows=[],
    )


def _profile(policy: MetricPolicy, **kwargs) -> ComparisonProfile:
    return ComparisonProfile(
        schema="hpx.comparison-profile",
        schema_version=1,
        metrics={"total_cycles": policy},
        **kwargs,
    )


def test_smaller_metric_fails_above_relative_tolerance():
    result = _result(MetricDiff("total_cycles", 100, 106, 6, 6, "cycles"))
    profile = _profile(
        MetricPolicy(
            direction=MetricDirection.SMALLER,
            unit="cycles",
            max_regression_pct=5,
        )
    )

    verdict = evaluate_comparison_profile(result, profile)

    assert verdict.status is VerdictStatus.FAIL
    assert verdict.metrics[0].regression == 6
    assert verdict.metrics[0].allowed_regression == 5


def test_larger_metric_allows_absolute_or_relative_tolerance():
    result = _result(MetricDiff("total_cycles", 100, 96, -4, -4, "cycles"))
    profile = _profile(
        MetricPolicy(
            direction=MetricDirection.LARGER,
            unit="cycles",
            max_regression_pct=2,
            max_regression_abs=5,
        )
    )

    assert evaluate_comparison_profile(result, profile).status is VerdictStatus.PASS


def test_missing_policy_warns_and_unknown_fields_round_trip():
    data = {
        "schema": "hpx.comparison-profile",
        "schema_version": 1,
        "name": "smoke",
        "missing": "fail",
        "metrics": {
            "total_cycles": {
                "direction": "smaller",
                "unit": "cycles",
                "missing": "warn",
                "future_metric_option": 7,
            }
        },
        "future_root_option": True,
    }
    profile = ComparisonProfile.from_dict(data)

    verdict = evaluate_comparison_profile(_result(), profile)

    assert verdict.status is VerdictStatus.WARN
    assert profile.to_dict() == data


def test_unit_and_required_dimension_mismatches_fail():
    result = _result(MetricDiff("total_cycles", 100, 100, 0, 0, "cycles"))
    result = replace(
        result,
        config_rows=[ConfigDiffRow("Engine", "helia-rt", "helia-aot", "diff", "engine")],
    )
    profile = _profile(
        MetricPolicy(direction=MetricDirection.EQUAL, unit="us"),
        required_dimensions=("engine",),
    )

    verdict = evaluate_comparison_profile(result, profile)

    assert verdict.status is VerdictStatus.FAIL
    assert verdict.dimension_mismatches == ("engine",)
    assert "expected 'us'" in verdict.metrics[0].message


def test_all_ignored_metrics_produce_skip():
    profile = _profile(
        MetricPolicy(
            direction=MetricDirection.SMALLER,
            unit="cycles",
            missing=MissingMetricPolicy.IGNORE,
        )
    )

    verdict = evaluate_comparison_profile(_result(), profile)

    assert verdict.status is VerdictStatus.SKIP
    assert verdict.metrics[0].status is VerdictStatus.SKIP


def test_profile_identity_is_stable_and_defaults_round_trip():
    data = {
        "schema": "hpx.comparison-profile",
        "schema_version": 1,
        "metrics": {"total_cycles": {"direction": "smaller", "unit": "cycles"}},
    }
    profile = ComparisonProfile.from_dict(data)
    result = _result(MetricDiff("total_cycles", 100, 100, 0, 0, "cycles"))

    verdict = evaluate_comparison_profile(result, profile)

    assert profile.to_dict() == data
    assert len(verdict.profile_sha256) == 64
    assert verdict.profile_schema == "hpx.comparison-profile"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_tolerances_must_be_finite_and_non_negative(value):
    with pytest.raises(ReportError, match="non-negative number"):
        MetricPolicy(
            direction=MetricDirection.SMALLER,
            unit="cycles",
            max_regression_abs=value,
        )


def test_non_finite_metric_values_fail():
    profile = _profile(MetricPolicy(direction=MetricDirection.SMALLER, unit="cycles"))
    result = _result(MetricDiff("total_cycles", 100, float("nan"), None, None, "cycles"))

    verdict = evaluate_comparison_profile(result, profile)

    assert verdict.status is VerdictStatus.FAIL
    assert verdict.metrics[0].message == "Metric values are not numeric."


@pytest.mark.parametrize("value", ["engine", ["engine", "engine"]])
def test_required_dimensions_follow_schema(value):
    data = {
        "schema": "hpx.comparison-profile",
        "schema_version": 1,
        "metrics": {"total_cycles": {"direction": "smaller", "unit": "cycles"}},
        "required_dimensions": value,
    }

    with pytest.raises(ReportError, match="required_dimensions"):
        ComparisonProfile.from_dict(data)
