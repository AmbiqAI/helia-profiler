from __future__ import annotations

import csv
import json
from pathlib import Path

from helia_profiler.config import load_config
from helia_profiler.pipeline import PipelineContext
from helia_profiler.report import _metadata_to_dict, _write_csv, _write_json, _write_summary
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
