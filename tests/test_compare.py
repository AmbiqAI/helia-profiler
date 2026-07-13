"""Tests for hpx compare result diffs."""

from __future__ import annotations

import csv
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from helia_profiler.compare import (
    ConfigDiffRow,
    CounterDiff,
    LayerDiffRow,
    compare_runs,
    render_compare,
    write_compare_artifacts,
)


def _write_run(
    path: Path,
    *,
    toolchain: str,
    total_cycles: float,
    avg_us: int,
    layer_cycles: list[float],
    power: dict[str, float] | None = None,
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
                "power": power,
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


def _write_aot_memory_layers(path: Path, memory: str, source_memory: str | None = None) -> None:
    with open(path / "aot_memory_layers.csv", "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "layer_idx",
                "layer_id",
                "op_type",
                "op_name",
                "tensor_role",
                "tensor_id",
                "tensor_name",
                "tensor_kind",
                "memory",
                "source_memory",
                "staged",
                "arena_role",
                "arena_region_id",
                "offset",
                "size",
                "shape",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "layer_idx": 0,
                "layer_id": 0,
                "op_type": "CONV_2D",
                "op_name": "conv_2d_0",
                "tensor_role": "local",
                "tensor_id": 17,
                "tensor_name": "weights",
                "tensor_kind": "constant",
                "memory": memory,
                "source_memory": source_memory or memory,
                "staged": source_memory is not None and source_memory != memory,
                "arena_role": "constant",
                "arena_region_id": 1,
                "offset": 0,
                "size": 1024,
                "shape": "[64, 1, 5, 1]",
            }
        )


def test_compare_runs_computes_run_and_layer_deltas(tmp_path: Path):
    baseline = tmp_path / "gcc"
    candidate = tmp_path / "atfe"
    _write_run(
        baseline,
        toolchain="arm-none-eabi-gcc",
        total_cycles=1000,
        avg_us=10,
        layer_cycles=[800, 200],
    )
    _write_run(candidate, toolchain="atfe", total_cycles=750, avg_us=8, layer_cycles=[600, 150])

    result = compare_runs(baseline, candidate)

    total = next(m for m in result.metrics if m.name == "total_cycles")
    assert total.delta == -250
    assert total.delta_pct == -25
    assert isinstance(result.layer_rows[0], LayerDiffRow)
    assert result.layer_rows[0].delta_cycles == -200
    assert result.layer_rows[0].speedup == 800 / 600
    assert isinstance(result.config_rows[0], ConfigDiffRow)
    assert any(row.field == "Toolchain" and row.status == "diff" for row in result.config_rows)


def test_compare_includes_power_metrics_when_available(tmp_path: Path):
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    _write_run(
        baseline,
        toolchain="arm-none-eabi-gcc",
        total_cycles=1000,
        avg_us=10,
        layer_cycles=[800],
        power={"energy_per_inference_j": 0.002, "inferences_per_joule": 500},
    )
    _write_run(
        candidate,
        toolchain="arm-none-eabi-gcc",
        total_cycles=900,
        avg_us=9,
        layer_cycles=[700],
        power={"energy_per_inference_j": 0.0015, "inferences_per_joule": 600},
    )

    result = compare_runs(baseline, candidate)

    energy = next(
        metric for metric in result.metrics if metric.name == "power.energy_per_inference_j"
    )
    assert energy.delta == pytest.approx(-0.0005)
    throughput = next(
        metric for metric in result.metrics if metric.name == "power.inferences_per_joule"
    )
    assert throughput.delta == 100


def test_compare_omits_layers_when_operation_sequence_differs(tmp_path: Path):
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    _write_run(
        baseline,
        toolchain="arm-none-eabi-gcc",
        total_cycles=1000,
        avg_us=10,
        layer_cycles=[800, 200],
    )
    _write_run(
        candidate, toolchain="arm-none-eabi-gcc", total_cycles=900, avg_us=9, layer_cycles=[700]
    )

    result = compare_runs(baseline, candidate)

    assert result.layer_rows == []
    assert any("Per-layer deltas omitted" in warning for warning in result.warnings)


def test_compare_layer_rows_type_dynamic_pmu_counters_as_counter_diffs(tmp_path: Path):
    baseline = tmp_path / "gcc"
    candidate = tmp_path / "atfe"
    _write_run(
        baseline,
        toolchain="arm-none-eabi-gcc",
        total_cycles=1000,
        avg_us=10,
        layer_cycles=[800, 200],
    )
    _write_run(candidate, toolchain="atfe", total_cycles=750, avg_us=8, layer_cycles=[600, 150])

    result = compare_runs(baseline, candidate)

    row = result.layer_rows[0]
    assert "ARM_PMU_CPU_CYCLES" in row.counters
    counter = row.counters["ARM_PMU_CPU_CYCLES"]
    assert isinstance(counter, CounterDiff)
    assert counter.baseline == 800
    assert counter.candidate == 600
    assert counter.delta == -200
    # Rows with no memory placement data leave the memory fields unset.
    assert row.baseline_memory is None
    assert row.memory_changed is None


def test_render_compare_starts_with_config_then_run_then_layers(tmp_path: Path):
    baseline = tmp_path / "gcc"
    candidate = tmp_path / "atfe"
    _write_run(
        baseline, toolchain="arm-none-eabi-gcc", total_cycles=1000, avg_us=10, layer_cycles=[800]
    )
    _write_run(candidate, toolchain="atfe", total_cycles=900, avg_us=9, layer_cycles=[700])

    text = render_compare(compare_runs(baseline, candidate), top_layers=1)

    assert text.index("Config") < text.index("Run") < text.index("Layers")
    assert "Toolchain" in text
    assert "total_cycles" in text
    assert "CONV_2D" in text


def test_write_compare_artifacts(tmp_path: Path):
    baseline = tmp_path / "gcc"
    candidate = tmp_path / "atfe"
    _write_run(
        baseline, toolchain="arm-none-eabi-gcc", total_cycles=1000, avg_us=10, layer_cycles=[800]
    )
    _write_run(candidate, toolchain="atfe", total_cycles=900, avg_us=9, layer_cycles=[700])

    paths = write_compare_artifacts(compare_runs(baseline, candidate), tmp_path / "diff")

    assert {p.name for p in paths} == {"compare_summary.json", "layer_diff.csv"}
    summary = json.loads((tmp_path / "diff" / "compare_summary.json").read_text())
    assert summary["metrics"][0]["name"] == "total_cycles"
    with open(tmp_path / "diff" / "layer_diff.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["baseline_op"] == "CONV_2D"
    assert rows[0]["delta_cycles"] == "-100.0"


def test_compare_includes_aot_memory_placement_diffs(tmp_path: Path):
    baseline = tmp_path / "dtcm"
    candidate = tmp_path / "sram"
    _write_run(
        baseline, toolchain="arm-none-eabi-gcc", total_cycles=1000, avg_us=10, layer_cycles=[800]
    )
    _write_run(
        candidate, toolchain="arm-none-eabi-gcc", total_cycles=900, avg_us=9, layer_cycles=[700]
    )
    _write_aot_memory_layers(baseline, "dtcm")
    _write_aot_memory_layers(candidate, "sram", source_memory="mram")

    result = compare_runs(baseline, candidate)

    row = result.layer_rows[0]
    assert row.memory_changed is True
    assert "constants: 1 buffer in DTCM" in row.baseline_memory
    assert "constants: 1 buffer staged MRAM to SRAM" in row.candidate_memory
    assert "->" in row.memory_diff

    paths = write_compare_artifacts(result, tmp_path / "diff")
    assert {p.name for p in paths} == {"compare_summary.json", "layer_diff.csv"}
    rows = list(csv.DictReader(open(tmp_path / "diff" / "layer_diff.csv")))
    assert rows[0]["memory_changed"] == "True"
    assert "staged MRAM to SRAM" in rows[0]["candidate_memory"]


def test_layer_diff_row_is_frozen_and_flattens_for_csv(tmp_path: Path):
    """LayerDiffRow is immutable and its to_flat_dict() output drives the CSV writer."""
    baseline = tmp_path / "gcc"
    candidate = tmp_path / "atfe"
    _write_run(
        baseline, toolchain="arm-none-eabi-gcc", total_cycles=1000, avg_us=10, layer_cycles=[800]
    )
    _write_run(candidate, toolchain="atfe", total_cycles=900, avg_us=9, layer_cycles=[700])

    result = compare_runs(baseline, candidate)
    row = result.layer_rows[0]

    with pytest.raises(FrozenInstanceError):
        row.delta_cycles = 0  # type: ignore[misc]

    flat = row.to_flat_dict()
    assert flat["delta_cycles"] == row.delta_cycles
    assert flat["baseline_ARM_PMU_CPU_CYCLES"] == row.counters["ARM_PMU_CPU_CYCLES"].baseline
    # Rows without memory placement data omit the memory_* keys entirely,
    # matching the original dict-based producer's conditional insertion.
    assert "baseline_memory" not in flat
