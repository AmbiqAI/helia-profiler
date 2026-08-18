from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from helia_profiler.evaluation import ComparabilitySeverity, assess_comparability
from helia_profiler.evaluation import RunArtifacts


def _run(
    *,
    model: str = "abc",
    engine: str = "helia-rt",
    compiler_version: str = "12.2.1",
    system_clock_hz: int = 250_000_000,
    ops=("CONV_2D",),
    power: dict | None = None,
):
    summary: dict = {"schema_version": 1, "total_cycles": 100}
    if power is not None:
        summary["power"] = power
    return RunArtifacts(
        path=Path("results"),
        summary=summary,
        metadata={
            "schema_version": 1,
            "hpx_version": "0.1.0",
            "model": {"sha256": model},
            "toolchain": {"compiler_version": compiler_version},
            "firmware": {"system_clock_hz": system_clock_hz},
            "platform": {"soc": "apollo510", "cpu_clock_name": "hp"},
            "config": {
                "engine": {"type": engine},
                "target": {
                    "board": "apollo510_evb",
                    "toolchain": "arm-none-eabi-gcc",
                    "transport": "rtt",
                },
                "model": {"arena_location": "tcm", "weights_location": "mram"},
            },
        },
        layers=[{"id": index, "op": op, "cycles": 10} for index, op in enumerate(ops)],
    )


def test_engine_difference_is_informative():
    assessment = assess_comparability(_run(), _run(engine="helia-aot"))

    assert assessment.run_metrics_comparable
    assert assessment.layers_comparable
    issue = next(issue for issue in assessment.issues if issue.code == "dimension.engine_differs")
    assert issue.severity is ComparabilitySeverity.INFORMATIVE


def test_model_mismatch_blocks_all_deltas():
    assessment = assess_comparability(_run(model="abc"), _run(model="def"))

    assert not assessment.run_metrics_comparable
    assert not assessment.layers_comparable
    assert assessment.issues[0].code == "identity.model_mismatch"


def test_topology_mismatch_blocks_only_layer_deltas():
    assessment = assess_comparability(_run(), _run(ops=("CONV_2D", "SOFTMAX")))

    assert assessment.run_metrics_comparable
    assert not assessment.layers_comparable
    assert any(issue.code == "topology.layer_count_mismatch" for issue in assessment.issues)


def test_cross_machine_provenance_differences_are_structured():
    assessment = assess_comparability(
        _run(compiler_version="12.2.1", system_clock_hz=250_000_000),
        _run(compiler_version="14.3.1", system_clock_hz=96_000_000),
    )

    assert assessment.run_metrics_comparable
    assert {issue.code for issue in assessment.issues} >= {
        "dimension.compiler_version_differs",
        "dimension.system_clock_hz_differs",
    }


def test_cross_instrument_power_scopes_omit_power_metrics_only():
    """Joulescope (host-gated window) vs INA228 (on-device accumulators) are
    different-instrument measurements: power deltas are omitted with an
    explanatory issue, while run/layer performance deltas stay comparable."""
    joulescope = _run(
        power={"measurement_scope": "gpio_gated_clean_window", "integrity": "valid"}
    )
    ina228 = _run(
        power={"measurement_scope": "on_device_gated_inference", "integrity": "valid"}
    )

    assessment = assess_comparability(joulescope, ina228)

    assert assessment.run_metrics_comparable
    assert assessment.layers_comparable
    assert not assessment.power_metrics_comparable
    issue = next(
        issue
        for issue in assessment.issues
        if issue.code == "metric.power_power_scope_mismatch"
    )
    assert issue.severity is ComparabilitySeverity.METRIC_BLOCKING
    assert issue.context["baseline"] == "gpio_gated_clean_window"
    assert issue.context["candidate"] == "on_device_gated_inference"


def test_monitor_presence_mismatch_omits_power_metrics_only():
    """An on-target monitor keeps its IOM powered on the measured rail, so a
    block-present run draws measurably more than a block-absent one even when
    the instrument, mode, and firmware all match. Comparing the two as equals
    would report a phantom power regression."""
    without_monitor = _run(
        power={"measurement_scope": "gpio_gated_clean_window", "integrity": "valid"}
    )
    with_monitor = _run(
        power={
            "measurement_scope": "gpio_gated_clean_window",
            "integrity": "valid",
            "on_device_summary": {"source": "ina228", "energy_nj": 0},
        }
    )

    assessment = assess_comparability(without_monitor, with_monitor)

    assert assessment.run_metrics_comparable
    assert assessment.layers_comparable
    assert not assessment.power_metrics_comparable
    issue = next(
        issue
        for issue in assessment.issues
        if issue.code == "metric.power_power_monitor_mismatch"
    )
    assert issue.severity is ComparabilitySeverity.METRIC_BLOCKING
    assert issue.context["baseline"] == "none"
    assert issue.context["candidate"] == "ina228"


def test_lockstep_mismatch_omits_power_metrics_only():
    """#114 flips the lock-step default, so runs recorded either side of it
    differ in a baked firmware constant. Lock-step drives the state pin as an
    output and enables the GO pin's input buffer on the measured rail, and the
    host holds GO high into that input until gate rise -- the same class of
    real, rail-level difference that makes monitor-presence power-blocking.

    Adversarial review found both runs comparing clean with integrity: valid,
    which is #115's phantom-delta failure mode: only the runs that LOST the
    gate race are marked degraded, so the ones that won compare silently
    against post-change runs."""
    free_running = _run(
        power={
            "measurement_scope": "gpio_gated_clean_window",
            "integrity": "valid",
            "sync": {"lockstep": False},
        }
    )
    lockstepped = _run(
        power={
            "measurement_scope": "gpio_gated_clean_window",
            "integrity": "valid",
            "sync": {"lockstep": True},
        }
    )

    assessment = assess_comparability(free_running, lockstepped)

    assert assessment.run_metrics_comparable
    assert assessment.layers_comparable
    assert not assessment.power_metrics_comparable
    issue = next(
        issue
        for issue in assessment.issues
        if issue.code == "metric.power_power_lockstep_mismatch"
    )
    assert issue.severity is ComparabilitySeverity.METRIC_BLOCKING
    assert issue.context["baseline"] is False
    assert issue.context["candidate"] is True


def test_baselines_predating_the_lockstep_dimension_are_skipped():
    """A run recorded before #114 has no sync.lockstep key at all. Dimensions
    are skipped when either side is None, so old baselines must not start
    reporting a phantom mismatch against new runs."""
    legacy = _run(
        power={"measurement_scope": "gpio_gated_clean_window", "integrity": "valid"}
    )
    current = _run(
        power={
            "measurement_scope": "gpio_gated_clean_window",
            "integrity": "valid",
            "sync": {"lockstep": True},
        }
    )

    assessment = assess_comparability(legacy, current)

    assert assessment.power_metrics_comparable
    assert not any(
        issue.code == "metric.power_power_lockstep_mismatch"
        for issue in assessment.issues
    )


def test_matching_monitor_presence_stays_power_comparable():
    with_monitor = {
        "measurement_scope": "gpio_gated_clean_window",
        "integrity": "valid",
        "on_device_summary": {"source": "ina228", "energy_nj": 0},
    }
    assessment = assess_comparability(_run(power=with_monitor), _run(power=with_monitor))
    assert assessment.power_metrics_comparable


def test_matching_on_device_power_scopes_stay_comparable():
    ina228 = {"measurement_scope": "on_device_gated_inference", "integrity": "valid"}
    assessment = assess_comparability(_run(power=ina228), _run(power=ina228))

    assert assessment.power_metrics_comparable


def test_partial_manifest_dimensions_fall_back_to_metadata():
    baseline = _run(model="abc")
    candidate = _run(model="def")
    from helia_profiler.results import ResultManifest, ResultValidity, RunStatus

    candidate = replace(
        candidate,
        manifest=ResultManifest(
            schema="hpx.result-manifest",
            schema_version=1,
            run_id="candidate",
            timestamp="2026-07-18T00:00:00+00:00",
            hpx_version="0.1.0",
            status=RunStatus.COMPLETE,
            validity=ResultValidity.VALID,
            issues=(),
            provenance={},
            comparability={},
            artifacts=(),
        ),
    )

    assessment = assess_comparability(baseline, candidate)

    assert not assessment.run_metrics_comparable
