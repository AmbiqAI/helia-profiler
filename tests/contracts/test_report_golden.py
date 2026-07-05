"""Contract: report artifacts are byte-identical across a report/ refactor.

Builds a fixed, representative :class:`PipelineContext` (multi-preset PMU
results with per-layer counters, a gated power capture, a memory plan, binary
sections, model analysis, run metadata, and — for the heliaAOT scenario — an
AOT operator manifest) and calls ``write_report`` into ``tmp_path`` for three
scenarios:

* ``helia_rt_csv`` — heliaRT engine, ``format=csv``, ``--detailed``, Model
  Explorer overlays enabled. Exercises ``_write_csv``, ``_write_preset_csv``
  (multiple presets and groups), ``_write_memory_breakdown``,
  ``_write_power_csv``, and the Model Explorer overlay writer.
* ``helia_rt_json`` — same context, ``format=json``. Exercises ``_write_json``.
* ``helia_aot`` — heliaAOT engine with an ``aot_op_manifest`` on
  ``engine_artifacts``. Exercises ``_write_aot_manifest`` and
  ``_write_aot_memory_layers``.

Every artifact's sha256 digest is pinned in
``tests/contracts/snapshots/report_golden.json`` (committed). This is a pure
byte-identity gate: after splitting ``report/__init__.py`` into per-writer
modules, every digest must be unchanged. Regenerate (only for an intentional
output-format change, never to paper over a split-introduced diff) with::

    HPX_UPDATE_SNAPSHOTS=1 pytest tests/contracts/test_report_golden.py
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from helia_profiler.config import load_config
from helia_profiler.engines.base import EngineArtifacts
from helia_profiler.engines import EngineType
from helia_profiler.model_analysis import LayerOps, ModelAnalysis
from helia_profiler.pipeline import PipelineContext
from helia_profiler.placement import MemoryRegion
from helia_profiler.power.base import GatedPowerWindow, PowerResult, PowerSummary
from helia_profiler.report import write_report
from helia_profiler.results import (
    BinarySections,
    ConsumerKind,
    FirmwareMeta,
    LayerResult,
    MemoryConsumer,
    MemoryPlan,
    MemoryRegionUsage,
    ModelInfo,
    PlatformInfo,
    PresetResult,
    PmuResult,
    RunMetadata,
    TimingInfo,
    ToolchainInfo,
)

_SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "report_golden.json"
_UPDATE = os.environ.get("HPX_UPDATE_SNAPSHOTS") == "1"


def _sample_layers() -> list[LayerResult]:
    return [
        LayerResult(
            id=0,
            op="CONV_2D",
            counters={
                "ARM_PMU_CPU_CYCLES": 12000.0,
                "ARM_PMU_L1D_CACHE_RD": 4000.0,
                "ARM_PMU_L1D_CACHE_MISS_RD": 200.0,
                "ARM_PMU_INST_RETIRED": 9000.0,
            },
            cycles=12000.0,
        ),
        LayerResult(
            id=1,
            op="DEPTHWISE_CONV_2D",
            counters={
                "ARM_PMU_CPU_CYCLES": 6000.0,
                "ARM_PMU_L1D_CACHE_RD": 1500.0,
                "ARM_PMU_L1D_CACHE_MISS_RD": 50.0,
                "ARM_PMU_INST_RETIRED": 4200.0,
            },
            cycles=6000.0,
        ),
        LayerResult(
            id=2,
            op="SOFTMAX",
            counters={
                "ARM_PMU_CPU_CYCLES": 800.0,
                "ARM_PMU_INST_RETIRED": 500.0,
            },
            cycles=800.0,
            overflow=True,
        ),
    ]


def _sample_pmu() -> PmuResult:
    layers = _sample_layers()
    meta = FirmwareMeta(
        model_size=4096,
        arena_size=65536,
        allocated_arena=61000,
        input_size=1024,
        output_size=256,
        num_tensors=12,
        num_inputs=1,
        num_outputs=1,
        num_presets=2,
        system_clock_hz=96_000_000,
        profiled_infer_count=10,
        profiled_infer_total_us=80_000,
        profiled_infer_avg_us=8000,
        clean_infer_count=8,
        clean_infer_total_cycles=152_000,
        clean_infer_avg_cycles=19000,
        clean_infer_avg_us=198,
        presets=("basic_cpu", "memory"),
    )
    presets = {
        "basic_cpu": PresetResult(
            name="basic_cpu",
            header=["op", "ARM_PMU_CPU_CYCLES"],
            iterations=[layers],
            layers=layers,
        ),
        "memory": PresetResult(
            name="memory",
            header=["op", "ARM_PMU_L1D_CACHE_RD"],
            iterations=[layers],
            layers=layers,
        ),
    }
    groups = {
        "cpu": layers,
        "memory": layers,
    }
    return PmuResult(meta=meta, presets=presets, layers=layers, groups=groups)


def _sample_power() -> PowerResult:
    return PowerResult(
        summary=PowerSummary(
            avg_current_a=0.012,
            avg_power_w=0.0432,
            peak_current_a=0.045,
            energy_j=0.000345,
            duration_s=0.08,
            sample_count=4000,
        ),
        gated_windows=[
            GatedPowerWindow(
                start_s=0.01,
                end_s=0.09,
                duration_s=0.08,
                charge_c=0.00096,
                energy_j=0.000345,
                avg_current_a=0.012,
                avg_power_w=0.0432,
                peak_current_a=0.045,
                sample_count=4000,
                median_current_a=0.0119,
                p95_current_a=0.021,
                p99_current_a=0.03,
                peak_current_p99_a=0.029,
                median_power_w=0.0428,
                p95_power_w=0.0756,
                p99_power_w=0.108,
            ),
        ],
        metadata={
            "measurement_scope": "gpio_gated_clean_window",
            "sync_input_index": 0,
            "gating_method": "gpio_edge",
            "target_lifecycle": "flashed",
            "sync": True,
            "sync_timing_s": 0.002,
            "whole_capture_summary": {
                "avg_current_a": 0.009,
                "avg_power_w": 0.0324,
                "peak_current_a": 0.045,
                "energy_j": 0.0009,
                "duration_s": 0.25,
                "sample_count": 12000,
            },
        },
    )


def _sample_memory_plan(engine: EngineType) -> MemoryPlan:
    return MemoryPlan(
        engine=engine,
        model_weight_bytes=4096,
        has_overflow=False,
        regions=(
            MemoryRegionUsage(
                region=MemoryRegion.MRAM,
                capacity=2_000_000,
                used=4096,
                consumers=(
                    MemoryConsumer(name="model_weights", size=4096, kind=ConsumerKind.WEIGHTS),
                ),
            ),
            MemoryRegionUsage(
                region=MemoryRegion.DTCM,
                capacity=384_000,
                used=61000,
                consumers=(
                    MemoryConsumer(name="tensor_arena", size=61000, kind=ConsumerKind.ARENA),
                ),
            ),
        ),
    )


def _sample_model_analysis() -> ModelAnalysis:
    return ModelAnalysis(
        layers=[
            LayerOps(id=0, op="CONV_2D", macs=100_000, ops=200_000),
            LayerOps(id=1, op="DEPTHWISE_CONV_2D", macs=20_000, ops=40_000),
            LayerOps(id=2, op="SOFTMAX", macs=0, ops=500),
        ],
        total_macs=120_000,
        total_ops=240_500,
        num_parameters=5000,
        engine="tflite",
    )


def _sample_run_metadata() -> RunMetadata:
    return RunMetadata(
        hpx_version="0.1.0",
        run_id="fixed-run-id",
        timestamp="2026-06-10T00:00:00+00:00",
        config_snapshot={"model": {"path": "test.tflite"}, "engine": {"type": "tflm"}},
        platform=PlatformInfo(
            board="apollo510_evb",
            soc="apollo510",
            core="cm55",
            pmu_tier="armv8m",
            has_mve=True,
            profiling_backends=["armv8m-pmu"],
            profiling_domains=["cpu", "memory"],
            cpu_clock_name="hp",
            cpu_clock_mhz=250,
            cpu_perf_tier="NSX_PERF_HIGH",
        ),
        model=ModelInfo(name="test.tflite", size_bytes=4096, sha256="a" * 64),
        toolchain=ToolchainInfo(
            compiler="arm-none-eabi-gcc",
            compiler_version="12.2.1",
            cmake_version="3.27.0",
        ),
        timing=TimingInfo(
            capture_duration_s=1.5,
            hpx_start_latency_s=0.25,
            protocol_duration_s=0.9,
            phases={"reset": 0.1, "sbl_settle": 0.05, "attach": 0.2},
        ),
    )


def _make_ctx(tmp_path: Path, engine: EngineType, fmt: str) -> PipelineContext:
    config = load_config(
        None,
        {
            "model": {"path": "test.tflite"},
            "engine": {"type": engine.value},
            "output": {"format": fmt, "detailed": True, "model_explorer": True, "dir": str(tmp_path)},
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ctx.pmu_result = _sample_pmu()
    ctx.power_result = _sample_power()
    ctx.memory_plan = _sample_memory_plan(engine)
    ctx.binary_sections = BinarySections(text=45000, data=1200, bss=8000, total=54200)
    ctx.model_analysis = _sample_model_analysis()
    ctx.run_metadata = _sample_run_metadata()
    return ctx


def _make_aot_ctx(tmp_path: Path) -> PipelineContext:
    ctx = _make_ctx(tmp_path, EngineType.HELIA_AOT, "csv")
    ctx.engine_artifacts = EngineArtifacts(
        engine_type=EngineType.HELIA_AOT,
        aot_op_manifest=[
            {
                "idx": 0,
                "id": 0,
                "op_type": "CONV_2D",
                "name": "conv1",
                "inputs": [
                    {
                        "id": 0,
                        "name": "input",
                        "kind": "activation",
                        "memory": "tcm",
                        "source_memory": "mram",
                        "staged": True,
                        "arena_role": "input",
                        "arena_region_id": 0,
                        "offset": 0,
                        "allocation_size": 1024,
                        "shape": [1, 28, 28, 1],
                    },
                ],
                "outputs": [
                    {
                        "id": 1,
                        "name": "conv1_out",
                        "kind": "activation",
                        "memory": "tcm",
                        "offset": 1024,
                        "size": 2048,
                        "shape": [1, 26, 26, 8],
                    },
                ],
                "local_tensors": [],
            },
            {
                "idx": 1,
                "id": 1,
                "op_type": "SOFTMAX",
                "name": "softmax1",
                "inputs": [],
                "outputs": [],
                "local_tensors": [
                    {
                        "id": 2,
                        "name": "scratch",
                        "kind": "scratch",
                        "memory": "tcm",
                        "nbytes": 128,
                    },
                ],
            },
        ],
    )
    return ctx


def _digest_file(path: Path) -> str:
    # Normalise CRLF -> LF before hashing: the report writers open files in
    # text mode, so Windows newline translation would otherwise change every
    # digest. The golden contract pins content, not platform line endings.
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def _build_digests() -> dict[str, dict[str, str]]:
    import tempfile

    result: dict[str, dict[str, str]] = {}
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)

        scenarios = {
            "helia_rt_csv": lambda d: _make_ctx(d, EngineType.HELIA_RT, "csv"),
            "helia_rt_json": lambda d: _make_ctx(d, EngineType.HELIA_RT, "json"),
            "helia_aot": _make_aot_ctx,
        }
        for scenario, factory in scenarios.items():
            out_dir = base / scenario
            out_dir.mkdir()
            ctx = factory(out_dir)
            paths = write_report(ctx)
            digests = {}
            for p in sorted(paths):
                rel = p.relative_to(out_dir).as_posix()
                digests[rel] = _digest_file(p)
            result[scenario] = digests
    return result


def _maybe_regenerate() -> None:
    if _UPDATE:
        _SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SNAPSHOT_PATH.write_text(json.dumps(_build_digests(), indent=2, sort_keys=True) + "\n")


_maybe_regenerate()

_SNAPSHOTS: dict = json.loads(_SNAPSHOT_PATH.read_text()) if _SNAPSHOT_PATH.exists() else {}

_REGEN_HINT = (
    "Report artifact bytes changed. If this is an intentional output-format "
    "change, review the diff then regenerate with:\n"
    "    HPX_UPDATE_SNAPSHOTS=1 pytest tests/contracts/test_report_golden.py"
)


def test_snapshot_exists():
    assert _SNAPSHOTS, "no report golden snapshot committed — generate it with HPX_UPDATE_SNAPSHOTS=1"


@pytest.mark.parametrize("scenario", ["helia_rt_csv", "helia_rt_json", "helia_aot"])
def test_report_artifacts_match_golden_digests(scenario, tmp_path):
    assert _SNAPSHOTS, "no report golden snapshot committed"
    expected = _SNAPSHOTS[scenario]

    factory = {
        "helia_rt_csv": lambda d: _make_ctx(d, EngineType.HELIA_RT, "csv"),
        "helia_rt_json": lambda d: _make_ctx(d, EngineType.HELIA_RT, "json"),
        "helia_aot": _make_aot_ctx,
    }[scenario]

    ctx = factory(tmp_path)
    paths = write_report(ctx)

    actual = {p.relative_to(tmp_path).as_posix(): _digest_file(p) for p in paths}

    assert set(actual) == set(expected), (
        f"[{scenario}] produced file set changed:\n"
        f"  expected: {sorted(expected)}\n"
        f"  actual:   {sorted(actual)}\n{_REGEN_HINT}"
    )
    for rel, digest in expected.items():
        assert actual[rel] == digest, f"[{scenario}] {rel} digest changed. {_REGEN_HINT}"


def test_snapshot_covers_exactly_the_current_scenarios():
    assert set(_SNAPSHOTS) == {"helia_rt_csv", "helia_rt_json", "helia_aot"}, _REGEN_HINT
