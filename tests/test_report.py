from __future__ import annotations

import csv
import json
from pathlib import Path

from helia_profiler.config import load_config
from helia_profiler.pipeline import PipelineContext
from helia_profiler.power.base import GatedPowerWindow, PowerResult, PowerSummary
from helia_profiler.report import _metadata_to_dict, _write_csv, _write_json, _write_summary
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
        meta=FirmwareMeta(clean_infer_count=10),
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
    # energy_per_inference_j itself is still computed (Joulescope energy /
    # exact clean_infer_count) -- only the duration cross-check is skipped.
    assert "energy_per_inference_j" in summary["power"]
    assert "gated_window_duration_ratio" not in summary["power"]

