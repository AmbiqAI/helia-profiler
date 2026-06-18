"""Tests for hpx compare result diffs."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from helia_profiler.compare import compare_runs, render_compare, write_compare_artifacts


def _write_run(
    path: Path,
    *,
    toolchain: str,
    total_cycles: float,
    avg_us: int,
    layer_cycles: list[float],
) -> None:
    path.mkdir(parents=True)
    (path / "summary.json").write_text(
        json.dumps(
            {
                "engine": "helia-rt",
                "layers": len(layer_cycles),
                "total_cycles": total_cycles,
                "overflow_detected": False,
                "memory": {
                    "arena_size": 131072,
                    "allocated_arena": 29780,
                    "model_size": 53744,
                },
                "binary": {
                    "text": 1000,
                    "data": 200,
                    "bss": 300,
                    "total": 1500,
                },
                "latency": {
                    "device_profiled_infer_avg_us": avg_us,
                    "device_profiled_infer_total_us": avg_us * 100,
                },
            }
        )
    )
    (path / "run_metadata.json").write_text(
        json.dumps(
            {
                "hpx_version": "0.1.0",
                "model": {"sha256": "abc123"},
                "platform": {
                    "soc": "apollo510",
                    "cpu_clock_name": "lp",
                },
                "config": {
                    "model": {
                        "path": "model.tflite",
                        "arena_size": 131072,
                        "model_location": "auto",
                    },
                    "engine": {"type": "helia-rt", "backend": None},
                    "target": {
                        "board": "apollo510_evb",
                        "toolchain": toolchain,
                        "transport": "rtt",
                    },
                    "profiling": {
                        "iterations": 100,
                        "warmup": 5,
                        "pmu_counters": None,
                        "pmu_presets": ["basic_cpu"],
                    },
                },
            }
        )
    )
    with open(path / "profile_results.csv", "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["id", "op", "ARM_PMU_CPU_CYCLES", "cycles", "overflow"],
        )
        writer.writeheader()
        for idx, cycles in enumerate(layer_cycles):
            writer.writerow(
                {
                    "id": idx,
                    "op": "CONV_2D" if idx == 0 else "SOFTMAX",
                    "ARM_PMU_CPU_CYCLES": cycles,
                    "cycles": cycles,
                    "overflow": False,
                }
            )


def test_compare_runs_computes_run_and_layer_deltas(tmp_path: Path):
    baseline = tmp_path / "gcc"
    candidate = tmp_path / "atfe"
    _write_run(baseline, toolchain="arm-none-eabi-gcc", total_cycles=1000, avg_us=10, layer_cycles=[800, 200])
    _write_run(candidate, toolchain="atfe", total_cycles=750, avg_us=8, layer_cycles=[600, 150])

    result = compare_runs(baseline, candidate)

    total = next(m for m in result.metrics if m.name == "total_cycles")
    assert total.delta == -250
    assert total.delta_pct == -25
    assert result.layer_rows[0]["delta_cycles"] == -200
    assert result.layer_rows[0]["speedup"] == 800 / 600
    assert any(row["field"] == "Toolchain" and row["status"] == "diff" for row in result.config_rows)


def test_render_compare_starts_with_config_then_run_then_layers(tmp_path: Path):
    baseline = tmp_path / "gcc"
    candidate = tmp_path / "atfe"
    _write_run(baseline, toolchain="arm-none-eabi-gcc", total_cycles=1000, avg_us=10, layer_cycles=[800])
    _write_run(candidate, toolchain="atfe", total_cycles=900, avg_us=9, layer_cycles=[700])

    text = render_compare(compare_runs(baseline, candidate), top_layers=1)

    assert text.index("Config") < text.index("Run") < text.index("Layers")
    assert "Toolchain" in text
    assert "total_cycles" in text
    assert "CONV_2D" in text


def test_write_compare_artifacts(tmp_path: Path):
    baseline = tmp_path / "gcc"
    candidate = tmp_path / "atfe"
    _write_run(baseline, toolchain="arm-none-eabi-gcc", total_cycles=1000, avg_us=10, layer_cycles=[800])
    _write_run(candidate, toolchain="atfe", total_cycles=900, avg_us=9, layer_cycles=[700])

    paths = write_compare_artifacts(compare_runs(baseline, candidate), tmp_path / "diff")

    assert {p.name for p in paths} == {"compare_summary.json", "layer_diff.csv"}
    summary = json.loads((tmp_path / "diff" / "compare_summary.json").read_text())
    assert summary["metrics"][0]["name"] == "total_cycles"
    with open(tmp_path / "diff" / "layer_diff.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["baseline_op"] == "CONV_2D"
    assert rows[0]["delta_cycles"] == "-100.0"
