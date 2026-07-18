from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from helia_profiler.config import load_config
from helia_profiler.artifacts import (
    OnDevicePowerSummary,
    PowerRun,
    PowerRunPlan,
    PowerTerminalRecord,
)
from helia_profiler.pipeline import PipelineContext
from helia_profiler.errors import ReportError
from helia_profiler.power.base import GatedPowerWindow, PowerResult, PowerSummary
from helia_profiler.report import (
    _metadata_to_dict,
    _write_csv,
    _write_json,
    _write_run_metadata,
    _write_summary,
    write_report,
)
from helia_profiler.result_manifest import load_result_manifest
from helia_profiler.model_analysis import ModelAnalysis
from helia_profiler.results import (
    FirmwareMeta,
    LayerResult,
    PmuResult,
    PresetResult,
    RunMetadata,
    TimingInfo,
)


def test_metadata_to_dict_includes_timing():
    meta = RunMetadata(
        hpx_version="0.1.0",
        run_id="run-1",
        timestamp="2026-06-10T00:00:00+00:00",
        timing=TimingInfo(
            capture_duration_s=1.5,
            hpx_start_latency_s=0.25,
            protocol_duration_s=0.9,
        ),
    )

    data = _metadata_to_dict(meta)

    assert data["timing"] == {
        "capture_duration_s": 1.5,
        "hpx_start_latency_s": 0.25,
        "protocol_duration_s": 0.9,
    }


def test_write_summary_includes_device_profiled_infer_latency(tmp_path: Path):
    config = load_config(
        None,
        {
            "model": {"path": "test.tflite"},
            "engine": {"type": "helia-rt"},
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ctx.pmu_result = PmuResult(
        meta=FirmwareMeta(
            profiled_infer_count=6,
            profiled_infer_total_us=48000,
            profiled_infer_avg_us=8000,
        ),
        layers=[LayerResult(id=0, op="CONV_2D", cycles=1000.0)],
    )

    out_path = _write_summary(ctx, tmp_path)
    summary = json.loads(out_path.read_text())

    assert summary["latency"] == {
        "device_profiled_infer_count": 6,
        "device_profiled_infer_total_us": 48000,
        "device_profiled_infer_avg_us": 8000,
    }
    assert summary["validity"] == "valid"
    assert summary["issues"] == []


def test_write_run_metadata_includes_target_lifecycle(tmp_path: Path):
    config = load_config(
        None,
        {
            "model": {"path": "test.tflite"},
            "engine": {"type": "helia-rt"},
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ctx.pmu_result = PmuResult(
        meta=FirmwareMeta(),
        layers=[LayerResult(id=0, op="CONV_2D", cycles=1000.0)],
    )
    ctx.power_result = PowerResult(
        summary=PowerSummary(0.0, 0.0, 0.0, 0.0, 0.0, 0),
        metadata={
            "target_lifecycle": {
                "phase": "power",
                "power_cycle_attempted": True,
                "power_cycle_succeeded": True,
                "reset_action": "debug_reset",
            },
        },
    )

    out_path = _write_run_metadata(ctx, tmp_path)
    metadata = json.loads(out_path.read_text())

    assert metadata["target_lifecycle"] == {
        "phase": "power",
        "power_cycle_attempted": True,
        "power_cycle_succeeded": True,
        "reset_action": "debug_reset",
    }


def test_write_csv_includes_layer_cycle_percentages(tmp_path: Path):
    pmu = PmuResult(
        meta=FirmwareMeta(),
        layers=[
            LayerResult(id=0, op="CONV_2D", cycles=25.0),
            LayerResult(id=1, op="DEPTHWISE_CONV_2D", cycles=75.0),
        ],
    )

    out_path = _write_csv(pmu, tmp_path)
    with open(out_path, newline="") as f:
        rows = list(csv.DictReader(f))

    assert rows[0]["cycles_pct"] == "25.0"
    assert rows[1]["cycles_pct"] == "75.0"


def test_write_json_includes_layer_cycle_percentages(tmp_path: Path):
    pmu = PmuResult(
        meta=FirmwareMeta(),
        layers=[
            LayerResult(id=0, op="CONV_2D", cycles=20.0),
            LayerResult(id=1, op="FULLY_CONNECTED", cycles=30.0),
        ],
        presets={
            "cpu_0": PresetResult(
                name="cpu_0",
                layers=[
                    LayerResult(id=0, op="CONV_2D", cycles=10.0),
                    LayerResult(id=1, op="FULLY_CONNECTED", cycles=30.0),
                ],
            )
        },
    )

    out_path = _write_json(pmu, None, RunMetadata(), tmp_path)
    data = json.loads(out_path.read_text())

    assert data["layers"][0]["cycles_pct"] == 40.0
    assert data["layers"][1]["cycles_pct"] == 60.0
    assert data["presets"]["cpu_0"]["layers"][0]["cycles_pct"] == 25.0
    assert data["presets"]["cpu_0"]["layers"][1]["cycles_pct"] == 75.0


def test_write_report_publishes_verifiable_manifest_last(tmp_path: Path):
    config = load_config(
        None,
        {
            "model": {"path": "test.tflite"},
            "engine": {"type": "helia-rt"},
            "output": {"dir": tmp_path, "model_explorer": False},
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ctx.run_metadata = RunMetadata(
        hpx_version="0.1.0",
        run_id="run-1",
        timestamp="2026-07-18T00:00:00+00:00",
        config_snapshot={"engine": {"type": "helia-rt"}},
    )
    ctx.pmu_result = PmuResult(
        meta=FirmwareMeta(),
        layers=[LayerResult(id=0, op="CONV_2D", cycles=1000.0)],
    )

    paths = write_report(ctx)

    assert paths[-1].name == "result_manifest.json"
    manifest = load_result_manifest(paths[-1], verify=True)
    assert [artifact.path for artifact in manifest.artifacts] == [
        path.relative_to(tmp_path).as_posix() for path in paths[:-1]
    ]
    artifacts = {artifact.path: artifact for artifact in manifest.artifacts}
    assert manifest.bundle_type == "profile"
    assert artifacts["summary.json"].role == "core"
    assert artifacts["summary.json"].name == "hpx.summary"
    assert artifacts["summary.json"].schema is None
    assert artifacts["summary.json"].optional is False
    assert artifacts["profile_results.csv"].name == "hpx.profile-layers"


def test_manifest_classifies_model_explorer_as_optional_export(tmp_path: Path):
    config = load_config(
        None,
        {
            "model": {"path": "test.tflite"},
            "engine": {"type": "helia-rt"},
            "output": {"dir": tmp_path, "model_explorer": True},
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ctx.run_metadata = RunMetadata(
        hpx_version="0.1.0",
        run_id="run-1",
        timestamp="2026-07-18T00:00:00+00:00",
    )
    ctx.pmu_result = PmuResult(
        meta=FirmwareMeta(),
        layers=[
            LayerResult(
                id=0,
                op="CONV_2D",
                cycles=1000.0,
                counters={"ARM_PMU_CPU_CYCLES": 1000.0},
            )
        ],
    )

    paths = write_report(ctx)
    manifest = load_result_manifest(paths[-1], verify=True)
    overlay = next(
        artifact
        for artifact in manifest.artifacts
        if artifact.path.startswith("model_explorer/")
    )

    assert overlay.role == "export"
    assert overlay.name == "model-explorer.overlay"
    assert overlay.schema is None
    assert overlay.producer == "hpx.model-explorer-exporter"
    assert overlay.optional is True


def test_write_report_invalidates_previous_manifest_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = load_config(
        None,
        {
            "model": {"path": "test.tflite"},
            "engine": {"type": "helia-rt"},
            "output": {"dir": tmp_path, "model_explorer": False},
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ctx.pmu_result = PmuResult(
        meta=FirmwareMeta(),
        layers=[LayerResult(id=0, op="CONV_2D", cycles=1000.0)],
    )
    stale = tmp_path / "result_manifest.json"
    stale.write_text('{"status": "complete"}\n')

    def fail_summary(*args, **kwargs):
        raise ReportError("forced report failure")

    monkeypatch.setattr("helia_profiler.report._write_summary", fail_summary)

    with pytest.raises(ReportError, match="forced report failure"):
        write_report(ctx)
    assert not stale.exists()


def test_write_summary_prefers_gpio_gated_power_when_present(tmp_path: Path):
    config = load_config(
        None,
        {
            "model": {"path": "test.tflite"},
            "engine": {"type": "helia-rt"},
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ctx.model_analysis = ModelAnalysis(
        layers=[],
        total_macs=1000,
        total_ops=2000,
        num_parameters=10,
    )
    ctx.pmu_result = PmuResult(
        meta=FirmwareMeta(clean_infer_count=10, clean_infer_avg_us=25000),
        layers=[LayerResult(id=0, op="CONV_2D", cycles=1000.0)],
    )
    ctx.power_result = PowerResult(
        summary=PowerSummary(
            avg_current_a=0.01,
            avg_power_w=0.02,
            peak_current_a=0.03,
            energy_j=0.5,
            duration_s=0.25,
            sample_count=100,
        ),
        gated_windows=[
            GatedPowerWindow(
                start_s=0.0,
                end_s=0.25,
                duration_s=0.25,
                charge_c=0.0025,
                energy_j=0.5,
                avg_current_a=0.01,
                avg_power_w=0.02,
                peak_current_a=0.03,
                sample_count=100,
            )
        ],
        metadata={
            "measurement_scope": "gpio_gated_clean_window",
            "sync_input_index": 0,
            "gating_method": "gpi_snapshot_poll",
            "target_lifecycle": {
                "phase": "power",
                "power_cycle_attempted": True,
                "power_cycle_succeeded": True,
                "reset_action": "debug_reset",
            },
            "sync": {"lockstep": True, "ready_wait_s": 0.012},
            "sync_timing_s": {"go_release_to_gate_rise_s": 0.004},
            "short_gate_pulses_ignored": 3,
            "whole_capture_summary": {
                "avg_current_a": 0.003,
                "avg_power_w": 0.006,
                "peak_current_a": 0.02,
                "energy_j": 0.04,
                "duration_s": 7.0,
                "sample_count": 14,
            },
        },
    )

    out_path = _write_summary(ctx, tmp_path)
    summary = json.loads(out_path.read_text())

    assert summary["power"]["measurement_scope"] == "gpio_gated_clean_window"
    assert summary["power"]["gated_window_count"] == 1
    assert summary["power"]["energy_per_inference_j"] == 0.05
    # High-level summary is inference-only: the non-inference whole-capture
    # window must NOT leak into summary.json (it belongs in the detailed CSV).
    assert "whole_capture_window" not in summary["power"]
    assert summary["power"]["sync_input_index"] == 0
    assert summary["power"]["target_lifecycle"] == {
        "phase": "power",
        "power_cycle_attempted": True,
        "power_cycle_succeeded": True,
        "reset_action": "debug_reset",
    }
    assert summary["power"]["sync"] == {"lockstep": True, "ready_wait_s": 0.012}
    assert summary["power"]["sync_timing_s"] == {"go_release_to_gate_rise_s": 0.004}
    assert summary["power"]["short_gate_pulses_ignored"] == 3
    assert summary["model_analysis"]["tops"] == 0.0


def _gated_power_ctx(
    tmp_path: Path, *, clean_infer_count: int, clean_infer_avg_us: int, duration_s: float
) -> PipelineContext:
    config = load_config(
        None,
        {
            "model": {"path": "test.tflite"},
            "engine": {"type": "helia-rt"},
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ctx.pmu_result = PmuResult(
        meta=FirmwareMeta(
            clean_infer_count=clean_infer_count,
            clean_infer_avg_us=clean_infer_avg_us,
        ),
        layers=[LayerResult(id=0, op="CONV_2D", cycles=1000.0)],
    )
    ctx.power_result = PowerResult(
        summary=PowerSummary(
            avg_current_a=0.004,
            avg_power_w=0.008,
            peak_current_a=0.006,
            energy_j=0.0016,
            duration_s=duration_s,
            sample_count=100,
        ),
        gated_windows=[
            GatedPowerWindow(
                start_s=0.0,
                end_s=duration_s,
                duration_s=duration_s,
                charge_c=0.0002,
                energy_j=0.0016,
                avg_current_a=0.004,
                avg_power_w=0.008,
                peak_current_a=0.006,
                sample_count=100,
            )
        ],
        metadata={"measurement_scope": "gpio_gated_clean_window"},
    )
    return ctx


def test_write_summary_flags_truncated_gated_window(tmp_path: Path):
    # 11 inferences at 21ms each should take ~0.231s; a 0.210s observed
    # window is ~10% short (missing roughly one inference's worth) --
    # dividing correctly-measured energy by the full count of 11 would
    # silently understate energy_per_inference_j with no other symptom.
    ctx = _gated_power_ctx(
        tmp_path, clean_infer_count=11, clean_infer_avg_us=21000, duration_s=0.210
    )

    out_path = _write_summary(ctx, tmp_path)
    summary = json.loads(out_path.read_text())

    assert summary["power"]["gated_window_duration_suspect"] is True
    assert summary["power"]["gated_window_expected_duration_s"] == 0.231
    assert summary["power"]["gated_window_duration_ratio"] < 0.95
    assert "energy_per_inference_j" not in summary["power"]


def test_write_summary_does_not_flag_normal_gated_window(tmp_path: Path):
    # Same expected duration (~0.231s), but the observed window matches
    # within normal GPIO-edge/packet-boundary jitter -- no flag expected.
    ctx = _gated_power_ctx(
        tmp_path, clean_infer_count=11, clean_infer_avg_us=21000, duration_s=0.230
    )

    out_path = _write_summary(ctx, tmp_path)
    summary = json.loads(out_path.read_text())

    assert "gated_window_duration_suspect" not in summary["power"]
    assert summary["power"]["gated_window_duration_ratio"] > 0.95


def test_write_summary_uses_fixed_power_plan_count(tmp_path: Path):
    ctx = _gated_power_ctx(
        tmp_path, clean_infer_count=10, clean_infer_avg_us=10000, duration_s=0.08
    )
    ctx.power_result.metadata["power_plan"] = {
        "inference_count": 8,
        "reference_inference_us": 10000,
        "target_duration_ms": 80,
        "count_source": "profile_guided",
    }
    ctx.power_result.metadata["gate_duration_integrity"] = {
        "measured_s": 0.08,
        "expected_s": 0.08,
        "tolerance_s": 0.008,
        "minimum_s": 0.0,
        "valid": True,
    }

    out_path = _write_summary(ctx, tmp_path)
    summary = json.loads(out_path.read_text())

    assert summary["power"]["energy_per_inference_j"] == 0.0002
    assert summary["power"]["power_plan"]["inference_count"] == 8


def test_degraded_free_form_capture_suppresses_derived_efficiency(tmp_path: Path):
    config = load_config(
        None,
        {
            "model": {"path": "test.tflite"},
            "engine": {"type": "helia-rt"},
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ctx.pmu_result = PmuResult(
        meta=FirmwareMeta(
            profiled_infer_count=3,
            profiled_infer_total_us=3000,
        ),
        layers=[LayerResult(id=0, op="CONV_2D", cycles=1000.0)],
    )
    ctx.model_analysis = ModelAnalysis(
        layers=[],
        total_macs=100,
        total_ops=200,
        num_parameters=10,
    )
    ctx.power_result = PowerResult(
        summary=PowerSummary(0.01, 0.018, 0.02, 0.18, 10.0, 10000),
        metadata={
            "measurement_scope": "free_form_capture",
            "observation_mode": "free_form",
            "integrity": "degraded",
            "gate_failure": {"kind": "no_gate_rise"},
        },
    )
    path = _write_summary(ctx, tmp_path)
    summary = json.loads(path.read_text())

    assert summary["power"]["observation_mode"] == "free_form"
    assert summary["power"]["integrity"] == "degraded"
    assert summary["power"]["gate_failure"]["kind"] == "no_gate_rise"
    assert "energy_per_inference_j" not in summary["power"]
    assert "active_window_estimated_energy_j" not in summary["power"]
    assert "tops_per_watt" not in summary.get("model_analysis", {})
    assert summary["validity"] == "degraded"
    assert [issue["code"] for issue in summary["issues"]] == [
        "power.observation_degraded"
    ]

    json_path = _write_json(ctx.pmu_result, ctx.power_result, ctx.run_metadata, tmp_path)
    full = json.loads(json_path.read_text())
    assert full["power"]["observation"] == {
        "measurement_scope": "free_form_capture",
        "observation_mode": "free_form",
        "integrity": "degraded",
        "gate_failure": {"kind": "no_gate_rise"},
    }


def test_summary_serializes_power_terminal_status(tmp_path: Path):
    config = load_config(
        None,
        {
            "model": {"path": "test.tflite"},
            "engine": {"type": "helia-rt"},
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ctx.pmu_result = PmuResult(meta=FirmwareMeta(), layers=[])
    terminal = PowerTerminalRecord(
        version=1,
        status="ok",
        requested_count=237,
        completed_count=237,
        elapsed_us=4_987_792,
        final_phase="complete",
        error_code=0,
        gate_asserted=True,
        gate_lowered=True,
    )
    ctx.power_run = PowerRun(
        plan=PowerRunPlan(firmware_mode="dedicated", inference_count=237),
        terminal=terminal,
        on_device_summary=OnDevicePowerSummary(
            source="ina228",
            scope="fixed_n_inference",
            energy_nj=90_123_456,
            duration_us=4_987_792,
            inference_count=237,
            overflow=False,
            charge_nc=50_000_000,
            bus_voltage_uv=1_800_000,
            sample_count=1000,
            calibration_id="board-rev-a",
        ),
    )
    ctx.power_result = PowerResult(
        summary=PowerSummary(0.01, 0.018, 0.02, 0.09, 5.0, 5000),
        metadata={"measurement_scope": "gpio_gated_clean_window"},
    )

    path = _write_summary(ctx, tmp_path)
    summary = json.loads(path.read_text())

    assert summary["power"]["terminal"] == {
        "version": 1,
        "status": "ok",
        "requested_count": 237,
        "completed_count": 237,
        "elapsed_us": 4_987_792,
        "final_phase": "complete",
        "error_code": 0,
        "gate_asserted": True,
        "gate_lowered": True,
    }
    assert summary["power"]["on_device_summary"] == {
        "source": "ina228",
        "scope": "fixed_n_inference",
        "energy_nj": 90_123_456,
        "duration_us": 4_987_792,
        "inference_count": 237,
        "overflow": False,
        "charge_nc": 50_000_000,
        "bus_voltage_uv": 1_800_000,
        "sample_count": 1000,
        "calibration_id": "board-rev-a",
    }


def test_write_summary_handles_sub_inference_dedicated_gate(tmp_path: Path):
    ctx = _gated_power_ctx(
        tmp_path, clean_infer_count=235, clean_infer_avg_us=21159, duration_s=0.008
    )
    ctx.power_result.metadata["power_firmware"] = "dedicated"

    out_path = _write_summary(ctx, tmp_path)
    summary = json.loads(out_path.read_text())

    assert summary["power"]["gated_window_duration_suspect"] is True
    assert summary["power"]["gated_window_expected_duration_s"] == 4.972365
    assert "clean_infer_count_source" not in summary["power"]
    assert "energy_per_inference_j" not in summary["power"]


def test_write_summary_flags_zero_device_cycles_as_suspect(tmp_path: Path):
    # clean_infer_count > 0 but the device reported clean_infer_avg_us=0 --
    # an inference cannot take zero time, so this means the device-side
    # DWT-based clean-window cycle measurement was corrupted (known cause:
    # a debugger/RTT attach racing the one-shot DWT->CYCCNT read). Previously
    # this silently skipped the duration sanity check with no warning at
    # all; it should now flag the run as suspect instead.
    ctx = _gated_power_ctx(tmp_path, clean_infer_count=11, clean_infer_avg_us=0, duration_s=0.230)

    out_path = _write_summary(ctx, tmp_path)
    summary = json.loads(out_path.read_text())

    assert summary["power"]["gated_window_duration_suspect"] is True
    assert "energy_per_inference_j" not in summary["power"]
    assert "gated_window_duration_ratio" not in summary["power"]
