"""An invalid run blocks the metrics its errors broke, not every metric.

A power run whose gate disagreed with the firmware's own window clock used to
produce no comparison at all: the INVALID verdict blocked cycles, latency,
memory and per-layer deltas that no failing check had anything to say about.
The disagreement is between the host's gate and the device's STIMER window, so
it confines itself to power — the cycle counts came off a different binary in
an earlier stage, on a different clock.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from helia_profiler.evaluation import ComparabilitySeverity, assess_comparability, RunArtifacts
from helia_profiler.evaluation import comparability
from helia_profiler.results import (
    METRIC_BLOCKING_CODE_BY_GROUP,
    ComparabilityCode,
    IssueCode,
    ResultManifest,
    ResultValidity,
    RunStatus,
    Severity,
    error_metric_group,
)
from helia_profiler.results.manifest import (
    RESULT_MANIFEST_SCHEMA,
    RESULT_MANIFEST_SCHEMA_VERSION,
    ResultIssue,
)

POWER = "power"


def _issue(code: str, severity: str = Severity.ERROR) -> ResultIssue:
    return ResultIssue(code=code, severity=severity, message="m")


def _manifest(*issues: ResultIssue, validity: ResultValidity = ResultValidity.INVALID):
    return ResultManifest(
        schema=RESULT_MANIFEST_SCHEMA,
        schema_version=RESULT_MANIFEST_SCHEMA_VERSION,
        run_id="r",
        timestamp="2026-09-10T00:00:00Z",
        hpx_version="0.1.0",
        status=RunStatus.COMPLETE,
        validity=validity,
        issues=tuple(issues),
        provenance={},
        comparability={},
        artifacts=(),
    )


def _run(manifest: ResultManifest | None = None) -> RunArtifacts:
    return RunArtifacts(
        path=Path("results"),
        summary={"schema_version": 1, "total_cycles": 100},
        metadata={"schema_version": 1, "model": {"sha256": "abc"}, "engine": {"type": "helia-rt"}},
        layers=[{"id": 0, "op": "CONV_2D"}],
        manifest=manifest,
    )


def _assess(*issues: ResultIssue):
    """One valid baseline against a candidate carrying ``issues``."""
    baseline = _run(_manifest(validity=ResultValidity.VALID))
    return assess_comparability(baseline, _run(_manifest(*issues)))


# ---------------------------------------------------------------------------
# The registry tag
# ---------------------------------------------------------------------------


def test_the_observer_mismatch_is_confined_to_power():
    assert error_metric_group(IssueCode.POWER_WINDOW_OBSERVER_MISMATCH.value) == POWER


def test_a_run_wide_error_is_confined_to_nothing():
    assert error_metric_group(IssueCode.PMU_MISSING.value) is None


def test_a_code_from_a_newer_hpx_confines_nothing():
    """Fail closed: an unrecognized code blocks everything rather than
    licensing a partial comparison this build cannot reason about."""
    assert error_metric_group("power.invented_by_a_later_version") is None


def test_every_tagged_group_has_a_code_that_can_express_it():
    """A tag with no METRIC_BLOCKING code would silently fall back to
    blocking everything — the safe direction, but not the intended one."""
    from helia_profiler.results.issues import ISSUE_REGISTRY

    tagged = {spec.metric_group for spec in ISSUE_REGISTRY.values() if spec.metric_group}
    assert tagged <= set(METRIC_BLOCKING_CODE_BY_GROUP)


# ---------------------------------------------------------------------------
# What a comparison does with it
# ---------------------------------------------------------------------------


def test_a_power_only_invalidity_still_compares_cycles_and_latency():
    assessment = _assess(_issue(IssueCode.POWER_WINDOW_OBSERVER_MISMATCH.value))

    assert assessment.run_metrics_comparable
    assert assessment.layers_comparable
    assert not assessment.power_metrics_comparable
    assert assessment.memory_metrics_comparable


def test_the_emitted_code_names_the_group_it_blocks():
    assessment = _assess(_issue(IssueCode.POWER_WINDOW_OBSERVER_MISMATCH.value))

    blocking = [
        issue
        for issue in assessment.issues
        if issue.severity is ComparabilitySeverity.METRIC_BLOCKING
    ]
    assert [issue.code for issue in blocking] == [
        str(ComparabilityCode.METRIC_POWER_INTEGRITY_INVALID)
    ]
    assert blocking[0].context["metric_group"] == POWER
    assert str(ComparabilityCode.RESULT_INVALID) not in {i.code for i in assessment.issues}


def test_a_run_wide_error_still_blocks_everything():
    assessment = _assess(_issue(IssueCode.PMU_MISSING.value))

    assert not assessment.run_metrics_comparable
    assert str(ComparabilityCode.RESULT_INVALID) in {i.code for i in assessment.issues}


def test_one_unconfined_error_alongside_a_confined_one_blocks_everything():
    """Partial comparison is reached only when EVERY error is confined."""
    assessment = _assess(
        _issue(IssueCode.POWER_WINDOW_OBSERVER_MISMATCH.value),
        _issue(IssueCode.PMU_MISSING.value),
    )

    assert not assessment.run_metrics_comparable


def test_an_unrecognized_error_code_blocks_everything():
    assessment = _assess(_issue("power.invented_by_a_later_version"))

    assert not assessment.run_metrics_comparable


def test_warnings_do_not_license_a_partial_comparison():
    """An INVALID run whose only *warning* is confined still has some error
    elsewhere; scoping must key on errors, never on the issue list at large."""
    assessment = _assess(
        _issue(IssueCode.POWER_WINDOW_OBSERVER_MISMATCH.value, severity=Severity.WARNING)
    )

    assert not assessment.run_metrics_comparable


def test_a_valid_run_is_unaffected():
    valid = _run(_manifest(validity=ResultValidity.VALID))

    assessment = assess_comparability(valid, valid)

    assert assessment.run_metrics_comparable
    assert assessment.power_metrics_comparable


def test_a_group_with_no_code_to_express_it_blocks_everything(monkeypatch):
    """The guard behind ``test_every_tagged_group_has_a_code…``. That
    invariant keeps this branch unreachable today, so exercise it directly
    rather than leaving the fallback unproven until someone adds a group.
    """
    monkeypatch.setattr(comparability, "METRIC_BLOCKING_CODE_BY_GROUP", {})

    assessment = _assess(_issue(IssueCode.POWER_WINDOW_OBSERVER_MISMATCH.value))

    assert not assessment.run_metrics_comparable
    assert str(ComparabilityCode.RESULT_INVALID) in {i.code for i in assessment.issues}


def test_an_invalid_run_with_no_recorded_errors_blocks_everything():
    """Nothing to confine it to, so the conservative verdict stands rather
    than a partial comparison inferred from an empty issue list."""
    assessment = _assess()

    assert not assessment.run_metrics_comparable
    assert str(ComparabilityCode.RESULT_INVALID) in {i.code for i in assessment.issues}


def test_both_sides_invalid_for_power_still_compare_cycles():
    issue = _issue(IssueCode.POWER_WINDOW_OBSERVER_MISMATCH.value)
    assessment = assess_comparability(_run(_manifest(issue)), _run(_manifest(issue)))

    assert assessment.run_metrics_comparable
    assert not assessment.power_metrics_comparable
    assert len([i for i in assessment.issues if i.context.get("metric_group") == POWER]) == 2


def test_the_run_is_still_invalid_the_verdict_is_untouched():
    """Scoping changes what a COMPARISON may use, never the run's verdict —
    fail_on_invalid must keep tripping."""
    manifest = _manifest(_issue(IssueCode.POWER_WINDOW_OBSERVER_MISMATCH.value))

    assert manifest.validity is ResultValidity.INVALID
    assert replace(manifest, run_id="other").validity is ResultValidity.INVALID
