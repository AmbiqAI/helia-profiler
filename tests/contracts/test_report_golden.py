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

from tests.pipeline_context_helpers import set_power_result, set_profile_result

import dataclasses
import hashlib
import json
import os
from pathlib import Path

import pytest

from helia_profiler.power.diagnostics import (
    GateTransitionTiming,
    SyncHandshakeMetadata,
)
from helia_profiler.power.metadata import MeasurementScope, PowerMetadata
from helia_profiler.target.lifecycle import (
    CapturePhase,
    ResetAction,
    ResetStrategy,
    TargetLifecyclePlan,
)
from helia_profiler.config import load_config
from helia_profiler.engines.base import HeliaAotArtifacts
from helia_profiler.engines import EngineType
from helia_profiler.modelcost import LayerOps, ModelAnalysis
from helia_profiler.pipeline import PipelineContext
from helia_profiler.hostenv.toolchain_probe import SymbolEntry
from helia_profiler.placement import MemoryRegion
from helia_profiler.power.base import GatedPowerWindow, PowerResult, PowerSummary
from helia_profiler.report import write_report
from helia_profiler.results import (
    BinarySections,
    ConsumerKind,
    FirmwareMeta,
    LayerResult,
    MemoryConsumer,
    ConsumerReconciliation,
    MeasuredMemoryRegions,
    MeasuredRegion,
    MemoryReconciliation,
    RegionReconciliation,
    UnattributedSection,
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
        clean_infer_total_cycles=7_680_000,
        clean_infer_avg_cycles=960_000,
        clean_infer_avg_us=10_000,
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
        # Well-formed typed metadata. The previous fixture deliberately held
        # the degenerate shapes shipped before #154 Phase 2 ("sync": True,
        # "target_lifecycle": "flashed", "sync_timing_s": 0.002) — the exact
        # bool-`sync` state behind the #135 crash. The typed PowerMetadata
        # makes those states unrepresentable, so this fixture (and the golden
        # digests derived from it) moved to the shapes production actually
        # writes. Reader-side tolerance for old on-disk artifacts lives in
        # evaluation/comparability.py's _nested and is tested separately.
        metadata=PowerMetadata(
            measurement_scope=MeasurementScope.GPIO_GATED_CLEAN_WINDOW,
            # #240: plan count (10) differs from the profile phase's
            # clean_infer_count (8) so the golden digest pins plan-preference
            # -- the per-inference denominator fixed-window runs need.
            # reference_inference_us (8000) x 10 == the 0.08s window, so the
            # gate-duration check stays healthy and energy/inference + TOPS
            # are emitted (not suppressed) with the plan count.
            power_plan={"inference_count": 10, "reference_inference_us": 8000},
            sync_input_index=0,
            gating_method="gpio_edge",
            target_lifecycle=TargetLifecyclePlan(
                phase=CapturePhase.POWER,
                power_cycle_attempted=True,
                power_cycle_succeeded=True,
                reset_strategy=ResetStrategy.AUTO,
                reset_action=ResetAction.DEBUG_RESET,
                actions=(),
                timings_s={},
            ),
            sync=SyncHandshakeMetadata(lockstep=True, ready_wait_s=0.012, ready_observed=True),
            sync_timing_s=GateTransitionTiming(capture_to_gate_rise_s=0.002),
            whole_capture_summary={
                "avg_current_a": 0.009,
                "avg_power_w": 0.0324,
                "peak_current_a": 0.045,
                "energy_j": 0.0009,
                "duration_s": 0.25,
                "sample_count": 12000,
            },
        ),
    )


def _sample_memory_regions() -> MeasuredMemoryRegions:
    """The measured block (#133 Phase 2), with every emission path live:
    a reserved figure, a nonzero load_image, and one unattributed section
    (with those at defaults the corresponding summary/console lines are
    dead and the digests could not see them — the #24 lesson)."""
    return MeasuredMemoryRegions(
        link_family="gnu",
        linker_profile="default",
        regions=(
            MeasuredRegion(
                region=MemoryRegion.MRAM,
                window_start=0x00410000,
                window_length=4_128_768,
                app_start=0x00410000,
                app_length=4_128_768,
                used=45_000,
                reserved=0,
                load_image=46_200,
                window_provenance="linker-app-origin",
            ),
            MeasuredRegion(
                region=MemoryRegion.DTCM,
                window_start=0x20000000,
                window_length=524_288,
                app_start=0x20000000,
                app_length=507_904,
                used=77_664,
                reserved=430_240,
                load_image=0,
            ),
        ),
        unattributed=(UnattributedSection(name=".mystery", address=0x30000000, size=64),),
    )


def _sample_memory_reconciliation() -> MemoryReconciliation:
    """Every emission path live: matched-with-delta, missing,
    unmatchable, and a nonzero region delta (the #24 lesson — a default
    leaves its console/summary line dead and invisible to digests)."""
    return MemoryReconciliation(
        consumers=(
            ConsumerReconciliation(
                name="tensor_arena",
                kind="arena",
                region="DTCM",
                planned_size=61000,
                status="matched",
                matched_symbols=("_ZL15g_arena_storage",),
                measured_size=61440,
                delta=440,
            ),
            ConsumerReconciliation(
                name="usb_buffers",
                kind="other",
                region="DTCM",
                planned_size=5120,
                status="missing",
            ),
            ConsumerReconciliation(
                name="model_psram_blob",
                kind="weights",
                region="PSRAM",
                planned_size=4096,
                status="unmatchable",
            ),
        ),
        regions=(
            RegionReconciliation(region="DTCM", planned_used=77664, measured_used=77664),
            RegionReconciliation(region="SRAM", planned_used=0, measured_used=98304),
        ),
    )


def _sample_memory_symbols() -> tuple[SymbolEntry, ...]:
    return (
        SymbolEntry(name="_ZL15g_arena_storage", address=0x20012000, size=61440, type="b"),
        SymbolEntry(name="_ZL10model_data", address=0x20004000, size=45000, type="d"),
        SymbolEntry(name="g_pui32Stack", address=0x20000000, size=16384, type="b"),
        SymbolEntry(name="main_loop", address=0x00411000, size=2048, type="T"),
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
                    MemoryConsumer(name="model_flatbuffer", size=4096, kind=ConsumerKind.WEIGHTS),
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
            "output": {
                "format": fmt,
                "detailed": True,
                "model_explorer": True,
                "dir": str(tmp_path),
            },
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    set_profile_result(ctx, _sample_pmu())
    set_power_result(ctx, _sample_power())
    ctx.memory_plan = _sample_memory_plan(engine)
    ctx.memory_regions = _sample_memory_regions()
    ctx.memory_reconciliation = _sample_memory_reconciliation()
    ctx.memory_symbols = _sample_memory_symbols()
    # reserved is non-zero on purpose: with it at the default 0 every
    # `if bs.reserved:` emission is dead in the golden path, so the digests
    # could not see the summary.json / memory.json / console additions at all
    # (issue #24). total stays the tool's own inclusive sum.
    ctx.binary_sections = BinarySections(
        text=45000, data=1200, bss=8000, total=54200, reserved=32000
    )
    ctx.model_analysis = _sample_model_analysis()
    ctx.run_metadata = _sample_run_metadata()
    dependency_lock = tmp_path / "_workspace" / "nsx.lock"
    dependency_lock.parent.mkdir(parents=True)
    dependency_lock.write_bytes(b"schema_version: 4\ntargets: {}\n")
    ctx.dependency_lock_path = dependency_lock
    return ctx


def _make_aot_ctx(tmp_path: Path) -> PipelineContext:
    ctx = _make_ctx(tmp_path, EngineType.HELIA_AOT, "csv")
    # #218: the AOT fixture is deliberately SKEWED — execution positions
    # (0, 1) map to original tflite indices (0, 3) and the analysis carries
    # original ids (0, 3, 5). A positional join would hand position 1 the
    # macs of analysis.layers[1] (original id 3 happens to match here, so
    # the ops are ALSO reordered vs the analysis list: position 1 is the
    # zero-mac SOFTMAX). The golden digest pins the manifest join.
    pmu = ctx.captured_pmu
    skewed = [
        dataclasses.replace(pmu.layers[0], op="CONV_2D:0"),
        dataclasses.replace(pmu.layers[1], op="SOFTMAX:5"),
        dataclasses.replace(pmu.layers[2], op="DEPTHWISE_CONV_2D:3"),
    ]
    set_profile_result(ctx, dataclasses.replace(pmu, layers=skewed))
    ctx.model_analysis = ModelAnalysis(
        layers=[
            LayerOps(id=0, op="CONV_2D", macs=100_000, ops=200_000, original_id=0),
            LayerOps(id=1, op="SOFTMAX", macs=0, ops=500, original_id=5),
            LayerOps(id=2, op="DEPTHWISE_CONV_2D", macs=20_000, ops=40_000, original_id=3),
        ],
        total_macs=120_000,
        total_ops=240_500,
        num_parameters=5000,
        engine="helia-aot",
    )
    ctx.engine_artifacts = HeliaAotArtifacts(
        engine_type=EngineType.HELIA_AOT,
        engine_header="model_model.h",
        aot_prefix="model",
        aot_module_name="aot-model",
        aot_cmake_target="nsx::aot_model",
        helia_aot_version="0.18.4",
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
                "id": 5,
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
            # Position 2 (DEPTHWISE_CONV_2D:3) is deliberately ABSENT from
            # the manifest: the manifest is authoritative, so that layer
            # must get NO mac attribution despite its parsable :3 suffix.
        ],
    )
    return ctx


def _digest_file(path: Path) -> str:
    # Strip CR before hashing to make digests platform-neutral: some writers
    # emit \r\n even on POSIX (csv module default lineterminator), and
    # Windows text-mode translation turns those into \r\r\n. The golden
    # contract pins content, not platform line endings.
    data = path.read_bytes().replace(b"\r", b"")
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
                rel = p.relative_to(out_dir.resolve()).as_posix()
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
    assert _SNAPSHOTS, (
        "no report golden snapshot committed — generate it with HPX_UPDATE_SNAPSHOTS=1"
    )


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
