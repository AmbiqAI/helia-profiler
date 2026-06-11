from __future__ import annotations

import json
from pathlib import Path

from helia_profiler.config import load_config
from helia_profiler.pipeline import PipelineContext
from helia_profiler.report import _metadata_to_dict, _write_summary
from helia_profiler.results import FirmwareMeta, LayerResult, PmuResult, RunMetadata, TimingInfo


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
