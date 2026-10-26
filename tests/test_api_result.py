"""Public API result mapping for grouped profile and power workflows."""

from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler import (
    OnDevicePowerSummary,
    PowerObservation,
    PowerTerminalRecord,
)
from helia_profiler.api import profile
from helia_profiler.config import load_config
from helia_profiler.pipeline import PipelineContext
from helia_profiler.power.base import PowerResult, PowerSummary
from helia_profiler.power.metadata import (
    MeasurementScope,
    ObservationMode,
    PowerIntegrity,
    PowerMetadata,
)
from helia_profiler.results import FirmwareMeta, PmuResult, PowerRun, PowerRunPlan, ResultValidity
from helia_profiler.wire import POWER_TERMINAL_VERSION
from tests.pipeline_context_helpers import set_power_result, set_profile_result


def test_profile_result_exposes_grouped_power_contract(tmp_path: Path, monkeypatch) -> None:
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "helia-rt"},
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    set_profile_result(ctx, PmuResult(meta=FirmwareMeta(), layers=[]))
    power = PowerResult(
        summary=PowerSummary(0.01, 0.018, 0.02, 0.09, 5.0, 5000),
        metadata=PowerMetadata(measurement_scope=MeasurementScope.GPIO_GATED_CLEAN_WINDOW),
    )
    observation = PowerObservation(
        mode=ObservationMode.GPIO_GATED,
        result=power,
        gate_rise_observed=True,
        gate_fall_observed=True,
        deadline_s=20.0,
        integrity=PowerIntegrity.VALID,
    )
    terminal = PowerTerminalRecord(
        version=POWER_TERMINAL_VERSION,
        status="ok",
        requested_count=5,
        completed_count=5,
        elapsed_us=5000,
        gate_elapsed_us=5000,
        final_phase="complete",
        error_code=0,
        gate_asserted=True,
        gate_lowered=True,
    )
    on_device = OnDevicePowerSummary(
        source="ina228",
        scope="fixed_n_inference",
        energy_nj=90_000_000,
        duration_us=5000,
        inference_count=5,
        overflow=False,
    )
    set_power_result(ctx, power)
    ctx.power_run = PowerRun(
        plan=PowerRunPlan(firmware_mode="dedicated", inference_count=5),
        observation=observation,
        terminal=terminal,
        on_device_summary=on_device,
    )
    monkeypatch.setattr("helia_profiler.profiler.run_profile", lambda _config, **_kwargs: ctx)

    result = profile(config)

    assert result.pmu is ctx.pmu_result
    assert result.power is power
    assert result.power_observation is observation
    assert result.power_terminal is terminal
    assert result.on_device_power is on_device


def test_profile_forwards_optional_progress_sink(tmp_path: Path, monkeypatch) -> None:
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    config = load_config(None, {"model": {"path": str(model)}})
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    set_profile_result(ctx, PmuResult(meta=FirmwareMeta(), layers=[]))
    seen: dict[str, object] = {}

    def fake_run_profile(_config, **kwargs):
        seen.update(kwargs)
        return ctx

    monkeypatch.setattr("helia_profiler.profiler.run_profile", fake_run_profile)
    updates = []

    profile(config, progress_sink=updates.append)

    assert seen == {"console": None, "progress_sink": updates.append}


@pytest.mark.parametrize(
    "validity", [ResultValidity.VALID, ResultValidity.DEGRADED, ResultValidity.INVALID, None]
)
def test_profile_preserves_stored_evaluation(tmp_path, monkeypatch, capsys, validity):
    from helia_profiler.evaluation import RunEvaluation

    config = load_config(
        None, {"model": {"path": "model.tflite"}, "output": {"fail_on_invalid": True}}
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    set_profile_result(ctx, PmuResult(meta=FirmwareMeta(), layers=[]))
    ctx.run_evaluation = RunEvaluation(validity=validity) if validity is not None else None
    ctx.report_paths = [tmp_path / "summary.json"]
    monkeypatch.setattr("helia_profiler.profiler.run_profile", lambda *args, **kwargs: ctx)

    result = profile(config)

    assert result.evaluation is ctx.run_evaluation
    assert result.metadata is ctx.run_metadata
    assert result.report_paths == ctx.report_paths
    assert result.report_paths is not ctx.report_paths
    assert capsys.readouterr() == ("", "")


def test_profile_result_existing_construction():
    from dataclasses import fields

    from helia_profiler.results import ProfileResult, RunMetadata

    pmu = PmuResult(meta=FirmwareMeta(), layers=[])
    metadata = RunMetadata()
    paths = [Path("summary.json")]
    assert ProfileResult(pmu).evaluation is None
    result = ProfileResult(pmu, None, None, None, None, metadata, paths)
    assert result.metadata is metadata
    assert result.report_paths is paths
    assert result.evaluation is None
    assert fields(ProfileResult)[-1].name == "evaluation"
