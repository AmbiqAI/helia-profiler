"""Public API result mapping for grouped profile and power workflows."""

from __future__ import annotations

from pathlib import Path

from helia_profiler import (
    OnDevicePowerSummary,
    PowerObservation,
    PowerTerminalRecord,
)
from helia_profiler.api import profile
from helia_profiler.results import PowerRun, PowerRunPlan
from helia_profiler.config import load_config
from helia_profiler.pipeline import PipelineContext
from helia_profiler.power.base import PowerResult, PowerSummary
from helia_profiler.results import FirmwareMeta, PmuResult


def test_profile_result_exposes_grouped_power_contract(
    tmp_path: Path, monkeypatch
) -> None:
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
    ctx.pmu_result = PmuResult(meta=FirmwareMeta(), layers=[])
    power = PowerResult(
        summary=PowerSummary(0.01, 0.018, 0.02, 0.09, 5.0, 5000),
        metadata={"measurement_scope": "gpio_gated_clean_window"},
    )
    observation = PowerObservation(
        mode="gpio_gated",
        result=power,
        gate_rise_observed=True,
        gate_fall_observed=True,
        deadline_s=20.0,
        integrity="valid",
    )
    terminal = PowerTerminalRecord(
        version=1,
        status="ok",
        requested_count=5,
        completed_count=5,
        elapsed_us=5000,
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
    ctx.power_result = power
    ctx.power_run = PowerRun(
        plan=PowerRunPlan(firmware_mode="dedicated", inference_count=5),
        observation=observation,
        terminal=terminal,
        on_device_summary=on_device,
    )
    monkeypatch.setattr("helia_profiler.profiler.run_profile", lambda _config: ctx)

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
    ctx.pmu_result = PmuResult(meta=FirmwareMeta(), layers=[])
    seen: dict[str, object] = {}

    def fake_run_profile(_config, **kwargs):
        seen.update(kwargs)
        return ctx

    monkeypatch.setattr("helia_profiler.profiler.run_profile", fake_run_profile)
    updates = []

    profile(config, progress_sink=updates.append)

    assert seen == {"progress_sink": updates.append}
