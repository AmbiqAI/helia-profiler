"""Tests for hpx compare result diffs."""

from __future__ import annotations

import csv
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from helia_profiler.evaluation import (
    ConfigDiffRow,
    CounterDiff,
    LayerDiffRow,
    compare_runs,
    write_compare_artifacts,
)
from helia_profiler.evaluation import (
    ComparisonProfile,
    MetricDirection,
    MetricPolicy,
)
from helia_profiler.errors import ReportError
from helia_profiler.results import (
    ResultArtifact,
    ResultManifest,
    ResultValidity,
    RunStatus,
)


def _write_run(
    path: Path,
    *,
    toolchain: str,
    total_cycles: float,
    avg_us: int,
    layer_cycles: list[float],
    power: dict[str, float | str] | None = None,
    memory_regions: dict | None = None,
    link_family: str | None = None,
) -> None:
    path.mkdir(parents=True)
    platform: dict = {"soc": "apollo510", "cpu_clock_name": "lp"}
    if link_family is not None:
        platform["link_family"] = link_family
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
                **({"memory_regions": memory_regions} if memory_regions is not None else {}),
            }
        )
    )
    (path / "run_metadata.json").write_text(
        json.dumps(
            {
                "hpx_version": "0.1.0",
                "model": {"sha256": "abc123"},
                "platform": platform,
                "config": {
                    "model": {
                        "path": "model.tflite",
                        "arena_size": 131072,
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
                        "pmu_counters": {"cpu": "default"},
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


def _write_aot_memory_layers(
    path: Path,
    memory: str,
    source_memory: str | None = None,
    *,
    layer_idx: int = 0,
    layer_id: int = 0,
    extra_rows: list[dict] | None = None,
) -> None:
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
                "layer_idx": layer_idx,
                "layer_id": layer_id,
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
        for extra in extra_rows or []:
            writer.writerow(extra)


def _replace_csv_with_json(path: Path) -> None:
    csv_path = path / "profile_results.csv"
    with open(csv_path, newline="") as stream:
        layers = [dict(row) for row in csv.DictReader(stream)]
    (path / "profile_results.json").write_text(json.dumps({"layers": layers}))
    csv_path.unlink()


def _publish_manifest(path: Path) -> Path:
    import hashlib

    artifacts = []
    for artifact_path in sorted(item for item in path.iterdir() if item.is_file()):
        artifacts.append(
            ResultArtifact(
                path=artifact_path.name,
                media_type=("application/json" if artifact_path.suffix == ".json" else "text/csv"),
                size_bytes=artifact_path.stat().st_size,
                sha256=hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
            )
        )
    return ResultManifest(
        schema="hpx.result-manifest",
        schema_version=1,
        run_id=path.name,
        timestamp="2026-07-18T00:00:00+00:00",
        hpx_version="0.1.0",
        status=RunStatus.COMPLETE,
        validity=ResultValidity.VALID,
        issues=(),
        provenance={},
        comparability={},
        artifacts=tuple(artifacts),
    ).write(path / "result_manifest.json")


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


def test_compare_loads_json_profile_results(tmp_path: Path):
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
        candidate,
        toolchain="arm-none-eabi-gcc",
        total_cycles=900,
        avg_us=9,
        layer_cycles=[700, 200],
    )
    _replace_csv_with_json(baseline)
    _replace_csv_with_json(candidate)
    _publish_manifest(baseline)
    _publish_manifest(candidate)

    result = compare_runs(baseline, candidate)

    assert result.layer_rows[0].delta_cycles == -100


def test_compare_verifies_manifest_before_loading(tmp_path: Path):
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    for path in (baseline, candidate):
        _write_run(
            path,
            toolchain="arm-none-eabi-gcc",
            total_cycles=1000,
            avg_us=10,
            layer_cycles=[800],
        )
        _publish_manifest(path)
    (candidate / "summary.json").write_text('{"total_cycles": 1}')

    with pytest.raises(ReportError, match="size mismatch|digest mismatch"):
        compare_runs(baseline, candidate)


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


def test_compare_suppresses_power_metrics_when_scope_differs(tmp_path: Path):
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    _write_run(
        baseline,
        toolchain="arm-none-eabi-gcc",
        total_cycles=1000,
        avg_us=10,
        layer_cycles=[800],
        power={
            "measurement_scope": "gpio_gated_clean_window",
            "integrity": "valid",
            "energy_j": 0.002,
        },
    )
    _write_run(
        candidate,
        toolchain="arm-none-eabi-gcc",
        total_cycles=900,
        avg_us=9,
        layer_cycles=[700],
        power={
            "measurement_scope": "free_form_capture",
            "integrity": "degraded",
            "energy_j": 0.1,
        },
    )

    result = compare_runs(baseline, candidate)

    assert result.comparability.run_metrics_comparable
    assert not result.comparability.power_metrics_comparable
    assert not any(metric.name.startswith("power.") for metric in result.metrics)
    assert any("Power metrics omitted" in warning for warning in result.warnings)


def test_compare_power_gate_leaves_region_rows_alone(tmp_path: Path):
    """#213 1: end to end through compare_runs -- a closed power gate
    withholds power rows only; the memory group is gated independently."""
    _write_run(
        tmp_path / "baseline",
        toolchain="arm-none-eabi-gcc",
        total_cycles=1000,
        avg_us=10,
        layer_cycles=[800],
        power={"measurement_scope": "gpio_gated_clean_window", "integrity": "valid", "energy_j": 1},
        memory_regions=_memory_regions_block("gnu", DTCM=(1000, 9000)),
        link_family="gnu",
    )
    _write_run(
        tmp_path / "candidate",
        toolchain="arm-none-eabi-gcc",
        total_cycles=1000,
        avg_us=10,
        layer_cycles=[800],
        power={"measurement_scope": "free_form_capture", "integrity": "valid", "energy_j": 2},
        memory_regions=_memory_regions_block("gnu", DTCM=(1200, 8800)),
        link_family="gnu",
    )

    result = compare_runs(tmp_path / "baseline", tmp_path / "candidate")

    names = [m.name for m in result.metrics]
    assert not result.comparability.power_metrics_comparable
    assert result.comparability.memory_metrics_comparable
    assert not any(n.startswith("power.") for n in names)
    assert "memory_regions.DTCM.used" in names


def test_compare_memory_gate_leaves_power_rows_alone(tmp_path: Path):
    power: dict[str, float | str] = {
        "measurement_scope": "gpio_gated_clean_window",
        "integrity": "valid",
        "energy_j": 1,
    }
    _write_run(
        tmp_path / "baseline",
        toolchain="arm-none-eabi-gcc",
        total_cycles=1000,
        avg_us=10,
        layer_cycles=[800],
        power=power,
        memory_regions=_memory_regions_block("gnu", DTCM=(1000, 9000)),
        link_family="gnu",
    )
    _write_run(
        tmp_path / "candidate",
        toolchain="armclang",
        total_cycles=1000,
        avg_us=10,
        layer_cycles=[800],
        power=power,
        memory_regions=_memory_regions_block("armlink", DTCM=(300, 9700)),
        link_family="armlink",
    )

    result = compare_runs(tmp_path / "baseline", tmp_path / "candidate")

    names = [m.name for m in result.metrics]
    assert result.comparability.power_metrics_comparable
    assert not result.comparability.memory_metrics_comparable
    assert "power.energy_j" in names
    assert not any(n.startswith("memory_regions.") for n in names)
    assert any("linked by different linker families" in w for w in result.warnings)
    paths = write_compare_artifacts(result, tmp_path / "diff")
    summary = json.loads(next(p for p in paths if p.name == "compare_summary.json").read_text())
    assert summary["comparability"]["memory_metrics_comparable"] is False
    assert summary["comparability"]["power_metrics_comparable"] is True


def test_compare_emits_no_dash_rows_for_a_group_neither_run_measured(tmp_path: Path):
    """Group rows absent on both sides are skipped, not rendered as dashes."""
    for name in ("baseline", "candidate"):
        _write_run(
            tmp_path / name,
            toolchain="arm-none-eabi-gcc",
            total_cycles=1000,
            avg_us=10,
            layer_cycles=[800],
        )

    result = compare_runs(tmp_path / "baseline", tmp_path / "candidate")

    assert not any(m.group is not None for m in result.metrics)


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
    assert "verdict" not in summary
    assert summary["comparability"]["run_metrics_comparable"] is True
    assert isinstance(summary["comparability"]["issues"], list)
    with open(tmp_path / "diff" / "layer_diff.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["baseline_op"] == "CONV_2D"
    assert rows[0]["delta_cycles"] == "-100.0"


def test_compare_profile_verdict_is_serialized_with_identity(tmp_path: Path):
    baseline = tmp_path / "gcc"
    candidate = tmp_path / "atfe"
    _write_run(
        baseline, toolchain="arm-none-eabi-gcc", total_cycles=1000, avg_us=10, layer_cycles=[800]
    )
    _write_run(candidate, toolchain="atfe", total_cycles=1100, avg_us=11, layer_cycles=[900])
    profile = ComparisonProfile(
        schema="hpx.comparison-profile",
        schema_version=1,
        name="cycles-smoke",
        metrics={
            "total_cycles": MetricPolicy(
                direction=MetricDirection.SMALLER,
                unit="cycles",
                max_regression_pct=5,
            )
        },
    )

    result = compare_runs(baseline, candidate, profile=profile)
    write_compare_artifacts(result, tmp_path / "diff")
    summary = json.loads((tmp_path / "diff" / "compare_summary.json").read_text())

    assert summary["verdict"]["status"] == "fail"
    assert summary["verdict"]["profile_name"] == "cycles-smoke"
    assert summary["verdict"]["profile_schema"] == "hpx.comparison-profile"
    assert len(summary["verdict"]["profile_sha256"]) == 64


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
    assert row.baseline_memory is not None
    assert row.candidate_memory is not None
    assert row.memory_diff is not None
    assert "constants: 1 buffer in DTCM" in row.baseline_memory
    assert "constants: 1 buffer staged MRAM to SRAM" in row.candidate_memory
    assert "->" in row.memory_diff

    paths = write_compare_artifacts(result, tmp_path / "diff")
    assert {p.name for p in paths} == {"compare_summary.json", "layer_diff.csv"}
    rows = list(csv.DictReader(open(tmp_path / "diff" / "layer_diff.csv")))
    assert rows[0]["memory_changed"] == "True"
    assert "staged MRAM to SRAM" in rows[0]["candidate_memory"]


def test_memory_rows_join_on_the_source_index_not_position(tmp_path: Path):
    """#223: the memory CSV row for original op 5 sits at position 0 after
    fusion. The old dual layer_id/layer_idx key matched EITHER, so position
    0's row could attach to whichever layer probed first; the join must key
    on the layer's resolved source index only."""
    baseline = tmp_path / "gcc"
    candidate = tmp_path / "atfe"
    for d in (baseline, candidate):
        _write_run(
            d, toolchain="arm-none-eabi-gcc", total_cycles=1000, avg_us=10, layer_cycles=[800]
        )
        # Rewrite the profile CSV with an AOT-style skewed layer: execution
        # position 0, ORIGINAL index 5.
        rows = list(csv.DictReader(open(d / "profile_results.csv")))
        rows[0]["op"] = "CONV_2D:5"
        with open(d / "profile_results.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    # Memory row belongs to original op 5, stored at position 0 — and a
    # DIFFERENT placement on each side so a successful join is visible.
    # Alongside op 5's row sits a DECOY whose layer_idx collides with 5:
    # a dual-key index would file the decoy under key 5 too and hand this
    # layer two buffers instead of one.
    decoy = {
        "layer_idx": 5,
        "layer_id": 9,
        "op_type": "SOFTMAX",
        "op_name": "softmax_9",
        "tensor_role": "local",
        "tensor_id": 44,
        "tensor_name": "decoy",
        "tensor_kind": "constant",
        "memory": "sram",
        "source_memory": "sram",
        "staged": False,
        "arena_role": "constant",
        "arena_region_id": 1,
        "offset": 0,
        "size": 64,
        "shape": "[4]",
    }
    _write_aot_memory_layers(baseline, "dtcm", layer_idx=0, layer_id=5, extra_rows=[decoy])
    _write_aot_memory_layers(candidate, "sram", layer_idx=0, layer_id=5, extra_rows=[decoy])

    row = compare_runs(baseline, candidate).layer_rows[0]

    assert row.memory_changed is True
    assert row.baseline_memory is not None and "DTCM" in row.baseline_memory
    # The decoy (SRAM) must not join: under a dual-key index it files under
    # key 5 via its layer_idx and shows up as a second placement here.
    assert "SRAM" not in row.baseline_memory

    # Now the adversarial shape: the memory CSV holds only original op 3's
    # row, whose POSITION (layer_idx=0) matches this layer's id. The old
    # dual key attached it; the source-index join must not.
    for d, mem in ((baseline, "dtcm"), (candidate, "sram")):
        _write_aot_memory_layers(d, mem, layer_idx=0, layer_id=3)

    row = compare_runs(baseline, candidate).layer_rows[0]

    assert row.baseline_memory is None
    assert row.memory_changed is None


def test_unresolvable_source_gets_no_memory_rows(tmp_path: Path):
    """A ':'-labelled op with no integer suffix (ExecuTorch-style) names no
    tflite operator: honest absence, never a positional guess."""
    baseline = tmp_path / "gcc"
    candidate = tmp_path / "atfe"
    for d in (baseline, candidate):
        _write_run(
            d, toolchain="arm-none-eabi-gcc", total_cycles=1000, avg_us=10, layer_cycles=[800]
        )
        rows = list(csv.DictReader(open(d / "profile_results.csv")))
        rows[0]["op"] = "OPERATOR_CALL:c3i12"
        with open(d / "profile_results.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        _write_aot_memory_layers(d, "dtcm", layer_idx=0, layer_id=0)

    row = compare_runs(baseline, candidate).layer_rows[0]

    assert row.baseline_memory is None


def test_recorded_source_index_outranks_the_label_suffix(tmp_path: Path):
    """#227: the post-#222 source_index column is the strongest
    evidence — where it disagrees with the op-label suffix (manifest vs
    firmware-label version skew) the recorded value wins."""
    baseline = tmp_path / "gcc"
    candidate = tmp_path / "atfe"
    decoy = {
        "layer_idx": 1,
        "layer_id": 5,
        "op_type": "CONV_2D",
        "op_name": "conv_5",
        "tensor_role": "local",
        "tensor_id": 9,
        "tensor_name": "suffix-decoy",
        "tensor_kind": "constant",
        "memory": "sram",
        "source_memory": "sram",
        "staged": False,
        "arena_role": "constant",
        "arena_region_id": 1,
        "offset": 0,
        "size": 64,
        "shape": "[4]",
    }
    for d in (baseline, candidate):
        _write_run(
            d, toolchain="arm-none-eabi-gcc", total_cycles=1000, avg_us=10, layer_cycles=[800]
        )
        rows = list(csv.DictReader(open(d / "profile_results.csv")))
        rows[0]["op"] = "CONV_2D:5"
        rows[0]["source_index"] = "7"
        with open(d / "profile_results.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        # Rows exist under BOTH keys: 7 (recorded, dtcm) and 5 (suffix, sram).
        _write_aot_memory_layers(d, "dtcm", layer_idx=0, layer_id=7, extra_rows=[decoy])

    row = compare_runs(baseline, candidate).layer_rows[0]

    assert row.baseline_memory is not None
    assert "DTCM" in row.baseline_memory and "SRAM" not in row.baseline_memory


def test_malformed_recorded_source_index_is_unresolvable_not_downgraded(tmp_path: Path):
    """A corrupt recorded value must not silently fall through to weaker
    evidence; an integral float (spreadsheet round-trip) still counts."""
    from helia_profiler.evaluation.compare import _layer_source_index

    assert _layer_source_index({"source_index": 7.0, "op": "CONV_2D:5"}) == 7
    assert _layer_source_index({"source_index": "junk", "op": "CONV_2D:5", "id": 0}) is None
    assert _layer_source_index({"source_index": 7.5, "op": "CONV_2D:5", "id": 0}) is None
    assert _layer_source_index({"source_index": "", "op": "CONV_2D:5"}) == 5


def test_zero_join_memory_artifacts_warn(tmp_path: Path):
    """#227: file present but nothing joined must be distinguishable
    from 'no placement change'."""
    baseline = tmp_path / "gcc"
    candidate = tmp_path / "atfe"
    for d in (baseline, candidate):
        _write_run(
            d, toolchain="arm-none-eabi-gcc", total_cycles=1000, avg_us=10, layer_cycles=[800]
        )
        rows = list(csv.DictReader(open(d / "profile_results.csv")))
        rows[0]["op"] = "CONV_2D:5"
        with open(d / "profile_results.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        _write_aot_memory_layers(d, "dtcm", layer_idx=0, layer_id=3)  # never matches

    result = compare_runs(baseline, candidate)

    assert result.layer_rows[0].baseline_memory is None
    assert any("no layer matched" in w for w in result.warnings)


def test_derived_analysis_fields_are_not_counter_diffs(tmp_path: Path):
    """#218 D6: macs/ops/cycles_per_mac are derived enrichments — a delta
    between two derived values is noise, not measurement."""
    baseline = tmp_path / "gcc"
    candidate = tmp_path / "atfe"
    for d, cycles in ((baseline, 800), (candidate, 700)):
        _write_run(
            d, toolchain="arm-none-eabi-gcc", total_cycles=1000, avg_us=10, layer_cycles=[cycles]
        )
        rows = list(csv.DictReader(open(d / "profile_results.csv")))
        rows[0].update({"macs": "1000", "ops": "2000", "cycles_per_mac": "0.8"})
        with open(d / "profile_results.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    row = compare_runs(baseline, candidate).layer_rows[0]

    assert "ARM_PMU_CPU_CYCLES" in row.counters
    assert not {"macs", "ops", "cycles_per_mac"} & set(row.counters)


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
        row.delta_cycles = 0  # ty: ignore[invalid-assignment]  # the illegal write IS the test

    flat = row.to_flat_dict()
    assert flat["delta_cycles"] == row.delta_cycles
    assert flat["baseline_ARM_PMU_CPU_CYCLES"] == row.counters["ARM_PMU_CPU_CYCLES"].baseline
    # Rows without memory placement data omit the memory_* keys entirely,
    # matching the original dict-based producer's conditional insertion.
    assert "baseline_memory" not in flat


def _memory_regions_block(link_family: str, **regions: tuple[int, int]) -> dict:
    """Minimal memory_regions block: regions={NAME: (used, free)}."""
    return {
        "link_family": link_family,
        "linker_profile": "default",
        "regions": [
            {
                "region": name,
                "window": {"start": 0, "length": used + free},
                "app_window": {"start": 0, "length": used + free},
                "used": used,
                "reserved": 0,
                "free": free,
                "load_image": 0,
                "window_provenance": "hardware-aperture",
                "app_provenance": "linker-script",
            }
            for name, (used, free) in regions.items()
        ],
        "unattributed": [],
        "unattributed_load_bytes": 0,
    }


class TestMemoryRegionRows:
    """#206: per-region used/free become compare rows, gated on link family."""

    def test_config_row_shows_the_family_the_gate_judged(self, tmp_path):
        """#213: a pre-#206 pair must not render 'Link family — — same'
        while the gate (reading the summary fallback) withholds the rows."""
        from helia_profiler.evaluation.compare import RunArtifacts, _compare_config

        def run(measured: str) -> RunArtifacts:
            return RunArtifacts(
                path=tmp_path,
                summary={"memory_regions": _memory_regions_block(measured, DTCM=(1, 1))},
                metadata={"platform": {"board": "apollo510_evb"}},
                layers=[],
            )

        row = next(r for r in _compare_config(run("gnu"), run("armlink")) if r.key == "link_family")

        assert (row.baseline, row.candidate, row.status) == ("gnu", "armlink", "diff")

    def test_config_row_shows_the_manifest_merged_value_the_gate_judged(self, tmp_path):
        """#213: the manifest merges last for the gate; a Config
        row reading artifacts by path could disagree with it. One reader."""
        from helia_profiler.evaluation.compare import RunArtifacts, _compare_config
        from helia_profiler.results import ResultManifest

        manifest = ResultManifest.from_dict(
            {
                "schema": "hpx.result-manifest",
                "schema_version": 1,
                "run_id": "r",
                "timestamp": "2026-08-20T00:00:00+00:00",
                "hpx_version": "0.0.0",
                "status": "complete",
                "validity": "valid",
                "issues": [],
                "provenance": {},
                "comparability": {"link_family": "armlink"},
                "artifacts": [],
            }
        )

        def run(manifest=None) -> RunArtifacts:
            return RunArtifacts(
                path=tmp_path,
                summary={"memory_regions": _memory_regions_block("gnu", DTCM=(1, 1))},
                metadata={"platform": {"board": "apollo510_evb", "link_family": "gnu"}},
                layers=[],
                manifest=manifest,
            )

        row = next(r for r in _compare_config(run(manifest), run()) if r.key == "link_family")

        assert (row.baseline, row.candidate, row.status) == ("armlink", "gnu", "diff")

    def test_percent_is_relative_to_the_baseline_magnitude(self):
        """#213: ``free`` is unclamped, so a negative baseline must not
        flip the sign of the percentage against the delta."""
        from helia_profiler.evaluation.compare import _compare_metrics

        base = {"memory_regions": _memory_regions_block("gnu", DTCM=(1000, -100))}
        cand = {"memory_regions": _memory_regions_block("gnu", DTCM=(1000, -50))}

        row = next(m for m in _compare_metrics(base, cand) if m.name == "memory_regions.DTCM.free")

        assert row.delta == 50
        assert row.delta_pct == 50.0

    def test_a_repeated_region_name_keeps_the_first_row(self):
        from helia_profiler.evaluation.compare import _compare_metrics

        block = _memory_regions_block("gnu", DTCM=(1000, 9000))
        block["regions"].append(dict(block["regions"][0], used=2000, free=8000))
        base = {"memory_regions": block}
        cand = {"memory_regions": _memory_regions_block("gnu", DTCM=(1000, 9000))}

        row = next(m for m in _compare_metrics(base, cand) if m.name == "memory_regions.DTCM.used")

        assert row.baseline == 1000

    def test_rows_emit_in_canonical_order_with_declared_direction(self):
        from helia_profiler.evaluation.compare import _compare_metrics

        # Summary lists regions alphabetically; rows must follow the
        # canonical ITCM, MRAM, DTCM, SRAM order (not alphabetical).
        base = {
            "memory_regions": _memory_regions_block(
                "gnu", DTCM=(1000, 9000), ITCM=(1, 1), MRAM=(2, 2), SRAM=(500, 500)
            )
        }
        cand = {
            "memory_regions": _memory_regions_block(
                "gnu", DTCM=(1200, 8800), ITCM=(1, 1), MRAM=(2, 2), SRAM=(500, 500)
            )
        }

        rows = {m.name: m for m in _compare_metrics(base, cand)}

        assert list(n for n in rows if n.startswith("memory_regions.")) == [
            "memory_regions.ITCM.used",
            "memory_regions.ITCM.free",
            "memory_regions.MRAM.used",
            "memory_regions.MRAM.free",
            "memory_regions.DTCM.used",
            "memory_regions.DTCM.free",
            "memory_regions.SRAM.used",
            "memory_regions.SRAM.free",
        ]
        assert rows["memory_regions.DTCM.used"].delta == 200
        assert rows["memory_regions.DTCM.used"].lower_is_better is True
        assert rows["memory_regions.DTCM.free"].delta == -200
        assert rows["memory_regions.DTCM.free"].lower_is_better is False
        assert rows["memory_regions.DTCM.free"].group == "memory"

    def test_rows_are_withheld_when_the_memory_gate_is_closed(self):
        from helia_profiler.evaluation.compare import _compare_metrics

        base = {"memory_regions": _memory_regions_block("gnu", DTCM=(1000, 9000))}
        cand = {"memory_regions": _memory_regions_block("armlink", DTCM=(300, 9700))}

        rows = _compare_metrics(base, cand, include_groups=frozenset({"power"}))

        assert not any(m.name.startswith("memory_regions.") for m in rows)

    def test_one_sided_region_stays_visible(self):
        """ITCM exists on AP5 only: an SoC-axis change renders, not hides."""
        from helia_profiler.evaluation.compare import _compare_metrics

        base = {"memory_regions": _memory_regions_block("gnu", ITCM=(100, 900), DTCM=(1, 1))}
        cand = {"memory_regions": _memory_regions_block("gnu", DTCM=(1, 1))}

        rows = {m.name: m for m in _compare_metrics(base, cand)}

        assert rows["memory_regions.ITCM.used"].baseline == 100
        assert rows["memory_regions.ITCM.used"].candidate is None
        assert rows["memory_regions.ITCM.used"].delta is None

    def test_pre_v3_pair_emits_no_region_rows(self):
        from helia_profiler.evaluation.compare import _compare_metrics

        rows = _compare_metrics({"total_cycles": 1}, {"total_cycles": 2})

        assert not any(m.name.startswith("memory_regions.") for m in rows)

    def test_declared_direction_replaces_the_name_hacks(self):
        """The static rows carry their direction too: layers and
        inferences_per_joule are higher-is-better, everything else lower."""
        from helia_profiler.evaluation.run_metrics import _METRIC_FIELDS

        directions = {f.name: f.lower_is_better for f in _METRIC_FIELDS}
        # The complete table, so a direction cannot change unnoticed. Note
        # power.inferences_per_joule: main's ``name != "layers"`` hack
        # coloured a throughput DROP green; the declaration corrects it.
        assert {n for n, lower in directions.items() if not lower} == {
            "layers",
            "power.inferences_per_joule",
        }
        assert {n for n, lower in directions.items() if lower} == {
            "total_cycles",
            "device_profiled_infer_avg_us",
            "device_profiled_infer_total_us",
            "binary.text",
            "binary.data",
            "binary.bss",
            "binary.reserved",
            "binary.total",
            "memory.arena_size",
            "memory.allocated_arena",
            "memory.model_size",
            "power.avg_current_a",
            "power.avg_power_w",
            "power.peak_current_a",
            "power.energy_j",
            "power.duration_s",
            "power.energy_per_inference_j",
        }
        groups = {f.name: f.group for f in _METRIC_FIELDS}
        assert all((g == "power") == n.startswith("power.") for n, g in groups.items())


def test_compare_survives_a_wider_than_header_layer_row(tmp_path: Path):
    """#243 C1: a profile_results.csv row with more fields than the header
    (foreign / other-version / hand-edited artifact) must not crash compare
    with a raw TypeError -- the surplus column is dropped."""
    baseline = tmp_path / "gcc"
    candidate = tmp_path / "atfe"
    for d in (baseline, candidate):
        _write_run(
            d, toolchain="arm-none-eabi-gcc", total_cycles=1000, avg_us=10, layer_cycles=[800]
        )
    # Append a stray extra column to the baseline's first data row.
    rows = (baseline / "profile_results.csv").read_text().splitlines()
    rows[1] = rows[1] + ",SURPRISE"
    (baseline / "profile_results.csv").write_text("\n".join(rows) + "\n")

    from helia_profiler.evaluation.compare import _read_layer_csv

    # The surplus column is dropped (not filed under DictReader's None key),
    # so the scalar coercer never sees a list -- pins the C1 fix directly.
    parsed = _read_layer_csv(baseline / "profile_results.csv")
    assert None not in parsed[0]
    assert all(not isinstance(v, list) for v in parsed[0].values())

    result = compare_runs(baseline, candidate)  # and end-to-end: no TypeError
    assert result.layer_rows


def test_compare_summary_json_is_valid_when_a_metric_is_non_finite(tmp_path: Path):
    """#243 C2: a foreign summary carrying NaN/Infinity must not make
    compare_summary.json invalid RFC-8259 JSON -- non-finite coerces to
    null at the emission boundary, and a strict parser must accept it."""
    baseline = tmp_path / "gcc"
    candidate = tmp_path / "atfe"
    for d in (baseline, candidate):
        _write_run(
            d, toolchain="arm-none-eabi-gcc", total_cycles=1000, avg_us=10, layer_cycles=[800]
        )
    # Inject a non-finite headline metric into one summary.json.
    summ = json.loads((candidate / "summary.json").read_text())
    summ["total_cycles"] = float("inf")
    (candidate / "summary.json").write_text(json.dumps(summ))

    paths = write_compare_artifacts(compare_runs(baseline, candidate), tmp_path / "diff")
    text = next(p for p in paths if p.name == "compare_summary.json").read_text()

    assert "Infinity" not in text and "NaN" not in text
    # A strict parser (rejecting the JS5 constants) must accept it.
    json.loads(text, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))


def test_compare_console_survives_a_non_finite_metric(tmp_path: Path):
    """#244: C2 sanitized the JSON but the console formatters did
    int(nan) -> ValueError -- the same raw-traceback crash class on a
    foreign artifact. print_compare must render (non-finite -> em-dash),
    not crash."""
    from helia_profiler.console import HpxConsole
    from helia_profiler.console.compare import print_compare

    baseline = tmp_path / "gcc"
    candidate = tmp_path / "atfe"
    for d in (baseline, candidate):
        _write_run(
            d, toolchain="arm-none-eabi-gcc", total_cycles=1000, avg_us=10, layer_cycles=[800]
        )
    summ = json.loads((candidate / "summary.json").read_text())
    summ["total_cycles"] = float("nan")
    (candidate / "summary.json").write_text(json.dumps(summ))

    result = compare_runs(baseline, candidate)
    print_compare(HpxConsole(verbosity=0), result)  # must not raise ValueError
