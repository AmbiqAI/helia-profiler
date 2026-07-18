from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from helia_profiler.artifacts import (
    OnDevicePowerSummary,
    PowerObservation,
    PowerRun,
    PowerRunPlan,
    PowerTerminalRecord,
)
from helia_profiler.config import load_config
from helia_profiler.pipeline import PipelineContext
from helia_profiler.power.base import PowerResult, PowerSummary
from helia_profiler.result_manifest import ResultValidity
from helia_profiler.results import FirmwareMeta, PmuResult
from helia_profiler.evaluation import evaluate_run


def _context(tmp_path: Path) -> PipelineContext:
    config = load_config(
        None,
        {
            "model": {"path": "test.tflite"},
            "engine": {"type": "helia-rt"},
            "power": {"enabled": True},
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ctx.pmu_result = PmuResult(meta=FirmwareMeta(), layers=[])
    result = PowerResult(
        summary=PowerSummary(0.01, 0.02, 0.03, 0.1, 1.0, 10),
        metadata={"measurement_scope": "gpio_gated_clean_window"},
    )
    observation = PowerObservation(
        mode="gpio_gated",
        result=result,
        gate_rise_observed=True,
        gate_fall_observed=True,
        deadline_s=2.0,
        integrity="valid",
    )
    terminal = PowerTerminalRecord(
        version=1,
        status="ok",
        requested_count=10,
        completed_count=10,
        elapsed_us=1_000_000,
        final_phase="done",
        error_code=0,
        gate_asserted=True,
        gate_lowered=True,
    )
    ctx.power_run = PowerRun(
        plan=PowerRunPlan(firmware_mode="dedicated", inference_count=10),
        observation=observation,
        terminal=terminal,
    )
    return ctx


def test_valid_run_has_no_issues(tmp_path: Path):
    evaluation = evaluate_run(_context(tmp_path))

    assert evaluation.validity is ResultValidity.VALID
    assert evaluation.issues == ()


def test_degraded_observation_and_duration_mismatch_are_structured(tmp_path: Path):
    ctx = _context(tmp_path)
    assert ctx.power_run is not None and ctx.power_run.observation is not None
    observation = ctx.power_run.observation
    observation.result.metadata["gate_duration_integrity"] = {
        "measured_s": 0.1,
        "expected_s": 1.0,
        "tolerance_s": 0.01,
        "minimum_s": 0.5,
    }
    ctx.power_run = PowerRun(
        plan=ctx.power_run.plan,
        observation=PowerObservation(
            mode="free_form",
            result=observation.result,
            gate_rise_observed=False,
            gate_fall_observed=False,
            deadline_s=2.0,
            integrity="degraded",
        ),
        terminal=ctx.power_run.terminal,
    )

    evaluation = evaluate_run(ctx)

    assert evaluation.validity is ResultValidity.DEGRADED
    assert {issue.code for issue in evaluation.issues} == {
        "power.observation_degraded",
        "power.gate_duration_mismatch",
    }


def test_terminal_plan_and_on_device_mismatches_are_invalid(tmp_path: Path):
    ctx = _context(tmp_path)
    assert ctx.power_run is not None
    terminal = PowerTerminalRecord(
        version=1,
        status="ok",
        requested_count=9,
        completed_count=9,
        elapsed_us=1_000_000,
        final_phase="done",
        error_code=0,
        gate_asserted=True,
        gate_lowered=True,
    )
    ctx.power_run = PowerRun(
        plan=ctx.power_run.plan,
        observation=ctx.power_run.observation,
        terminal=terminal,
        on_device_summary=OnDevicePowerSummary(
            source="ina228",
            scope="fixed_n_inference",
            energy_nj=100,
            duration_us=1000,
            inference_count=8,
            overflow=False,
        ),
    )

    evaluation = evaluate_run(ctx)

    assert evaluation.validity is ResultValidity.INVALID
    assert {issue.code for issue in evaluation.issues} == {
        "power.plan_count_mismatch",
        "power.on_device_count_mismatch",
    }


def test_pmu_overflow_is_invalid_without_power(tmp_path: Path):
    ctx = _context(tmp_path)
    ctx.power_run = None
    ctx.pmu_result = PmuResult(meta=FirmwareMeta(), overflow_detected=True)

    evaluation = evaluate_run(ctx)

    assert evaluation.validity is ResultValidity.INVALID
    assert [issue.code for issue in evaluation.issues] == ["pmu.counter_overflow"]


def test_duration_fallback_matches_summary_policy(tmp_path: Path):
    ctx = _context(tmp_path)
    assert ctx.power_run is not None and ctx.power_run.observation is not None
    observation = ctx.power_run.observation
    result = PowerResult(
        summary=replace(observation.result.summary, duration_s=0.1),
        metadata=observation.result.metadata,
    )
    ctx.power_run = PowerRun(
        plan=PowerRunPlan(
            firmware_mode="dedicated",
            inference_count=10,
            reference_inference_us=100_000,
        ),
        observation=replace(observation, result=result),
        terminal=ctx.power_run.terminal,
    )

    evaluation = evaluate_run(ctx)

    assert evaluation.validity is ResultValidity.DEGRADED
    assert any(issue.code == "power.gate_duration_mismatch" for issue in evaluation.issues)
