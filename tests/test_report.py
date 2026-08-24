from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from helia_profiler.config import CleanWindowProbe, load_config
from helia_profiler.results import (
    OnDevicePowerSummary,
    PowerRun,
    PowerRunPlan,
    PowerTerminalRecord,
)
from helia_profiler.pipeline import PipelineContext
from helia_profiler.errors import ReportError
from helia_profiler.power.base import GatedPowerWindow, PowerResult, PowerSummary
from helia_profiler.power.diagnostics import WindowClockCeiling
from helia_profiler.power.diagnostics import (
    GateDurationIntegrity,
    GateFailure,
    GateFailureKind,
    GateTransitionTiming,
    SyncHandshakeMetadata,
)
from helia_profiler.power.metadata import (
    MeasurementScope,
    ObservationMode,
    PowerIntegrity,
    PowerMetadata,
)
from helia_profiler.target.lifecycle import (
    CapturePhase,
    ResetAction,
    ResetStrategy,
    TargetLifecyclePlan,
)

from helia_profiler.report import (
    _metadata_to_dict,
    _write_csv,
    _write_json,
    _write_run_metadata,
    _write_summary,
    write_report,
)
from helia_profiler.results import load_result_manifest
from helia_profiler.evaluation import ModelAnalysis
from helia_profiler.results.issues import IssueCode
from helia_profiler.results import (
    EngineInfo,
    FirmwareMeta,
    LayerResult,
    PmuResult,
    PresetResult,
    PsramInfo,
    RunMetadata,
    TimingInfo,
    ToolchainInfo,
)

# Built from the real producer type: the old hand-written lifecycle dicts
# were under-specified (four keys) relative to what production always wrote.
_LIFECYCLE_PLAN = TargetLifecyclePlan(
    phase=CapturePhase.POWER,
    power_cycle_attempted=True,
    power_cycle_succeeded=True,
    reset_strategy=ResetStrategy.AUTO,
    reset_action=ResetAction.DEBUG_RESET,
    actions=(),
    timings_s={},
)


def _attach_dependency_lock(ctx: PipelineContext, root: Path) -> bytes:
    source = root / "workspace" / "nsx.lock"
    source.parent.mkdir(parents=True, exist_ok=True)
    payload = b"schema_version: 4\ntargets: {}\n"
    source.write_bytes(payload)
    ctx.dependency_lock_path = source
    return payload


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

    assert data["schema"] == "hpx.run-metadata"
    assert data["schema_version"] == 1
    assert data["timing"] == {
        "capture_duration_s": 1.5,
        "hpx_start_latency_s": 0.25,
        "protocol_duration_s": 0.9,
    }


def test_metadata_to_dict_includes_runtime_versions():
    meta = RunMetadata(
        toolchain=ToolchainInfo(
            compiler="arm-none-eabi-gcc",
            compiler_version="14.2.1",
            cmake_version="3.31.6",
        ),
        engine=EngineInfo(type="helia-aot", version="0.18.4"),
    )

    data = _metadata_to_dict(meta)

    assert data["toolchain"] == {
        "compiler": "arm-none-eabi-gcc",
        "compiler_version": "14.2.1",
        "cmake_version": "3.31.6",
    }
    assert data["engine"] == {"type": "helia-aot", "version": "0.18.4"}


def test_write_summary_surfaces_the_clean_window_self_check(tmp_path: Path):
    """The window-clock self-check must reach summary.json (#121).

    Same reason ``window_clock_ceiling`` had to: a reader of summary.json
    cannot otherwise distinguish a build that checked its clean-window clock
    and found nothing from one that never checked at all. Both counters and the
    warm reference the partial-count floor was derived from are carried, so the
    threshold is auditable from the artifact.
    """
    config = load_config(
        None,
        {
            "model": {"path": "test.tflite"},
            "engine": {"type": "helia-rt"},
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    # Setting run_metadata.timing is load-bearing, not incidental: summary.py
    # has TWO latency branches, and every real capture takes the primary one
    # (capture/__init__.py always populates timing). A fixture that leaves it
    # None exercises only the fallback elif -- the three device_clean_* lines
    # could be deleted from the shipping branch with the whole suite green.
    ctx.run_metadata.timing = TimingInfo(capture_duration_s=1.0)
    ctx.pmu_result = PmuResult(
        meta=FirmwareMeta(
            clean_infer_count=1092,
            clean_infer_avg_us=684,
            clean_stalled_iters=0,
            clean_partial_iters=0,
            clean_ref_cycles=83300,
            clean_dwt_rate_cyc=96_000,
            clean_dwt_rate_us=1000,
        ),
        layers=[LayerResult(id=0, op="CONV_2D", cycles=1000.0)],
    )

    summary = json.loads(_write_summary(ctx, tmp_path).read_text())

    assert summary["latency"]["device_clean_stalled_iters"] == 0
    assert summary["latency"]["device_clean_partial_iters"] == 0
    assert summary["latency"]["device_clean_ref_cycles"] == 83300
    assert summary["latency"]["device_clean_dwt_rate_cyc"] == 96_000
    assert summary["latency"]["device_clean_dwt_rate_us"] == 1000

    # Firmware that never reported them omits the keys entirely rather than
    # publishing a 0 that would read as "checked, healthy".
    ctx.pmu_result = PmuResult(
        meta=FirmwareMeta(clean_infer_count=1092, clean_infer_avg_us=684),
        layers=[LayerResult(id=0, op="CONV_2D", cycles=1000.0)],
    )
    silent = json.loads(_write_summary(ctx, tmp_path).read_text())
    for key in (
        "device_clean_stalled_iters",
        "device_clean_partial_iters",
        "device_clean_ref_cycles",
        "device_clean_dwt_rate_cyc",
        "device_clean_dwt_rate_us",
    ):
        assert key not in silent["latency"], key

    # And the fallback branch carries them too, for a capture with no timing.
    ctx.run_metadata.timing = None
    ctx.pmu_result = PmuResult(
        meta=FirmwareMeta(clean_stalled_iters=4, clean_infer_count=1092),
        layers=[LayerResult(id=0, op="CONV_2D", cycles=1000.0)],
    )
    fallback = json.loads(_write_summary(ctx, tmp_path).read_text())
    assert fallback["latency"]["device_clean_stalled_iters"] == 4


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
    assert summary["schema"] == "hpx.run-summary"
    assert summary["schema_version"] == 3  # v2: #24 binary.bss; v3: #133 memory_regions
    assert summary["validity"] == "valid"
    assert summary["issues"] == []


def test_write_summary_includes_psram_diagnostics(tmp_path: Path):
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
            psram=PsramInfo(
                size_bytes=67_108_864,
                clock_hz=125_000_000,
                capabilities=7,
                state=1,
                last_init_status=0,
                xip_enabled=True,
                timing_status=2,
                rxdqs_delay=14,
            )
        ),
        layers=[LayerResult(id=0, op="CONV_2D", cycles=1000.0)],
    )

    summary = json.loads(_write_summary(ctx, tmp_path).read_text())

    assert summary["psram"] == {
        "size_bytes": 67_108_864,
        "clock_hz": 125_000_000,
        "capabilities": 7,
        "state": 1,
        "last_init_status": 0,
        "xip_enabled": True,
        "timing_status": 2,
        "rxdqs_delay": 14,
    }


def test_write_run_metadata_includes_psram_diagnostics(tmp_path: Path):
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
            psram=PsramInfo(
                size_bytes=67_108_864,
                clock_hz=125_000_000,
                capabilities=7,
                state=1,
                last_init_status=0,
                xip_enabled=True,
                timing_status=2,
                rxdqs_delay=14,
            )
        )
    )

    metadata = json.loads(_write_run_metadata(ctx, tmp_path).read_text())

    assert metadata["firmware"]["psram"] == {
        "size_bytes": 67_108_864,
        "clock_hz": 125_000_000,
        "capabilities": 7,
        "state": 1,
        "last_init_status": 0,
        "xip_enabled": True,
        "timing_status": 2,
        "rxdqs_delay": 14,
    }


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
        metadata=PowerMetadata(
            target_lifecycle=_LIFECYCLE_PLAN,
        ),
    )

    out_path = _write_run_metadata(ctx, tmp_path)
    metadata = json.loads(out_path.read_text())

    assert metadata["target_lifecycle"] == _LIFECYCLE_PLAN.to_metadata()


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

    assert data["schema"] == "hpx.profile-results"
    assert data["schema_version"] == 1
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
    expected_lock = _attach_dependency_lock(ctx, tmp_path)

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
    assert artifacts["summary.json"].schema == "hpx.run-summary"
    assert artifacts["summary.json"].schema_version == 3
    assert artifacts["summary.json"].optional is False
    assert artifacts["profile_results.csv"].name == "hpx.profile-layers"
    assert artifacts["profile_results.csv"].schema is None
    assert (tmp_path / "nsx.lock").read_bytes() == expected_lock
    assert artifacts["nsx.lock"].name == "hpx.nsx-lock"


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
    _attach_dependency_lock(ctx, tmp_path)

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
        metadata=PowerMetadata(
            measurement_scope=MeasurementScope.GPIO_GATED_CLEAN_WINDOW,
            sync_input_index=0,
            gating_method="gpi_snapshot_poll",
            target_lifecycle=_LIFECYCLE_PLAN,
            sync=SyncHandshakeMetadata(lockstep=True, ready_wait_s=0.012),
            sync_timing_s=GateTransitionTiming(go_release_to_gate_rise_s=0.004),
            short_gate_pulses_ignored=3,
            whole_capture_summary={
                "avg_current_a": 0.003,
                "avg_power_w": 0.006,
                "peak_current_a": 0.02,
                "energy_j": 0.04,
                "duration_s": 7.0,
                "sample_count": 14,
            },
        ),
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
    assert summary["power"]["target_lifecycle"] == _LIFECYCLE_PLAN.to_metadata()
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
        metadata=PowerMetadata(measurement_scope=MeasurementScope.GPIO_GATED_CLEAN_WINDOW),
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


def _attach_power_terminal(ctx: PipelineContext, *, elapsed_us: int, count: int) -> None:
    """Dedicated-mode terminal envelope, the observer that arbitrates the
    est*count band (#142/#181)."""
    ctx.power_run = PowerRun(
        plan=PowerRunPlan(
            firmware_mode="dedicated",
            inference_count=count,
        ),
        observation=None,
        terminal=PowerTerminalRecord(
            version=1,
            status="ok",
            requested_count=count,
            completed_count=count,
            elapsed_us=elapsed_us,
            final_phase="done",
            error_code=0,
            gate_asserted=True,
            gate_lowered=True,
        ),
    )


def test_write_summary_publishes_drift_note_when_firmware_confirms_gate(
    tmp_path: Path,
):
    """THE #181 scenario at summary level: est*count misses by 11.8% but the
    firmware's STIMER window agrees with the gate, so the reference is stale
    and the capture is sound. Per-inference metrics stay published (the
    denominator is the count, which drift cannot change) and the story is
    told next to the ratio instead of via suppression."""
    ctx = _gated_power_ctx(
        tmp_path, clean_infer_count=233, clean_infer_avg_us=21532, duration_s=4.427
    )
    _attach_power_terminal(ctx, elapsed_us=4_427_500, count=233)

    out_path = _write_summary(ctx, tmp_path)
    summary = json.loads(out_path.read_text())

    assert "gated_window_duration_suspect" not in summary["power"]
    assert "energy_per_inference_j" in summary["power"]
    assert "inferences_per_joule" in summary["power"]
    assert "HFRC" in summary["power"]["gated_window_reference_drift"]
    assert summary["power"]["gated_window_duration_ratio"] < 0.95


def test_write_summary_suppresses_when_observer_disagrees(tmp_path: Path):
    """When the firmware's own window clock disagrees with the gate, the gate
    did not bracket what the firmware timed -- suppression, with no drift
    note pretending otherwise."""
    ctx = _gated_power_ctx(
        tmp_path, clean_infer_count=233, clean_infer_avg_us=21532, duration_s=4.427
    )
    _attach_power_terminal(ctx, elapsed_us=5_017_000, count=233)

    out_path = _write_summary(ctx, tmp_path)
    summary = json.loads(out_path.read_text())

    assert summary["power"]["gated_window_duration_suspect"] is True
    assert "energy_per_inference_j" not in summary["power"]
    assert "gated_window_reference_drift" not in summary["power"]


def test_write_summary_observer_agreement_does_not_mask_the_floor(tmp_path: Path):
    """A sub-minimum gate suppresses even when the firmware clock agrees:
    the floor guards the stats integral, not the reference."""
    ctx = _gated_power_ctx(
        tmp_path, clean_infer_count=11, clean_infer_avg_us=21000, duration_s=0.210
    )
    ctx.power_result.metadata.gate_duration_integrity = GateDurationIntegrity(
        measured_s=0.210,
        expected_s=0.231,
        tolerance_s=0.0231,
        minimum_s=1.0,
    )
    _attach_power_terminal(ctx, elapsed_us=210_000, count=11)

    out_path = _write_summary(ctx, tmp_path)
    summary = json.loads(out_path.read_text())

    assert summary["power"]["gated_window_duration_suspect"] is True
    assert "energy_per_inference_j" not in summary["power"]


def test_write_summary_uses_fixed_power_plan_count(tmp_path: Path):
    ctx = _gated_power_ctx(
        tmp_path, clean_infer_count=10, clean_infer_avg_us=10000, duration_s=0.08
    )
    ctx.power_result.metadata.power_plan = {
        "inference_count": 8,
        "reference_inference_us": 10000,
        "target_duration_ms": 80,
        "count_source": "profile_guided",
    }
    ctx.power_result.metadata.gate_duration_integrity = GateDurationIntegrity(
        measured_s=0.08,
        expected_s=0.08,
        tolerance_s=0.008,
        minimum_s=0.0,
    )

    out_path = _write_summary(ctx, tmp_path)
    summary = json.loads(out_path.read_text())

    assert summary["power"]["energy_per_inference_j"] == 0.0002
    assert summary["power"]["power_plan"]["inference_count"] == 8


def test_write_summary_surfaces_window_clock_ceiling(tmp_path: Path):
    # #115: window_clock_ceiling (added by #107's collect_power_terminal
    # stage) must reach summary.json -- previously report/summary.py's power
    # metadata allowlist omitted it, so a power.window_clock_exceeds_host_time
    # warning had no envelope numbers a user could see outside the validity
    # issue's context.
    # Built from the real producer, not a hand-written literal: the point is
    # that whatever WindowClockCeiling emits reaches summary.json intact. A
    # fabricated dict would keep passing after to_metadata() renamed a key,
    # leaving the documented field silently wrong.
    ceiling = WindowClockCeiling(elapsed_us=6_027_000, host_envelope_s=0.9, slack_s=0.05)
    ctx = _gated_power_ctx(
        tmp_path, clean_infer_count=10, clean_infer_avg_us=10000, duration_s=0.1
    )
    ctx.power_result.metadata.window_clock_ceiling = ceiling

    out_path = _write_summary(ctx, tmp_path)
    summary = json.loads(out_path.read_text())

    assert summary["power"]["window_clock_ceiling"] == ceiling.to_metadata()


def test_write_summary_carries_the_power_firmware_fingerprint(tmp_path: Path):
    """#138: the measured binary's code hash must reach summary.power so the
    POWER_FIRMWARE_FINGERPRINT comparability dimension has an artifact value
    — and must be simply absent (legacy semantics) when no source exists."""
    from helia_profiler.firmware import measured_power_fingerprint
    from helia_profiler.results import PowerRunPlan

    ctx = _gated_power_ctx(
        tmp_path, clean_infer_count=10, clean_infer_avg_us=10000, duration_s=0.1
    )
    # Direct assignment (the same-file PowerRun precedent): publish_power_plan resets
    # power_result, which _gated_power_ctx already installed.
    from helia_profiler.results import PowerRun

    ctx.power_run = PowerRun(
        plan=PowerRunPlan(
            firmware_mode="dedicated", inference_count=10, count_source="configured"
        )
    )
    src = tmp_path / "fw" / "src"
    src.mkdir(parents=True)
    (src / "main_power.cc").write_text("int main(void) { return 7; }\n")
    ctx.firmware_dir = tmp_path / "fw"

    summary = json.loads(_write_summary(ctx, tmp_path).read_text())

    # The composite source-set hash (main + profiler TUs) — same helper the
    # writer uses, so this pins passthrough, not the hash construction
    # (tests/test_firmware_fingerprint.py owns that).
    assert summary["power"]["firmware_code_fingerprint"] == measured_power_fingerprint(
        ctx
    )

    # No rendered source (a hand-built or legacy context): key absent, run OK.
    bare = _gated_power_ctx(
        tmp_path, clean_infer_count=10, clean_infer_avg_us=10000, duration_s=0.1
    )
    bare_summary = json.loads(_write_summary(bare, tmp_path).read_text())
    assert "firmware_code_fingerprint" not in bare_summary["power"]


def test_window_clock_ceiling_metadata_keys_are_the_documented_set():
    # docs/guide/power.md names these fields for users reading summary.json,
    # and #115 put them in the summary's power block. Nothing else pins the
    # key set, so renaming or adding one in to_metadata() would leave the
    # guide describing a field that no longer exists while the whole suite
    # stayed green. Update the guide and this list together, deliberately.
    ceiling = WindowClockCeiling(elapsed_us=6_027_000, host_envelope_s=0.9, slack_s=0.05)

    assert set(ceiling.to_metadata()) == {
        "elapsed_us",
        "elapsed_s",
        "host_envelope_s",
        "slack_s",
        "ratio",
    }


def test_target_lifecycle_metadata_is_the_documented_shape():
    # Independent pin for TargetLifecyclePlan.to_metadata(): the summary and
    # run_metadata assertions above compare production output against
    # _LIFECYCLE_PLAN.to_metadata() — the same method on the same object —
    # which proves round-tripping, not the emitted shape. This literal is the
    # shape, hand-written, so a renamed or dropped key fails here even while
    # the round-trip assertions stay green (same rule as the
    # window_clock_ceiling key-set pin above).
    assert _LIFECYCLE_PLAN.to_metadata() == {
        "phase": "power",
        "power_cycle_attempted": True,
        "power_cycle_succeeded": True,
        "reset_strategy": "auto",
        "reset_action": "debug_reset",
        "actions": [],
    }


def test_busy_loop_probe_publishes_no_per_inference_power_metrics(tmp_path: Path):
    """A window that ran zero inferences must not report energy per inference.

    The busy_loop probe replaces the inference loop with a calibrated CPU spin.
    Dividing real gated energy by any count then yields a plausible-looking
    figure under an ordinary field name -- driving the pre-guard code with THIS
    fixture publishes `energy_per_inference_j: 0.0016` and
    `inferences_per_joule: 625.0` for a window with no inferences in it,
    alongside `gated_window_duration_ratio: 1.0` looking perfectly healthy
    (#125; an earlier draft quoted a reviewer's fixture's digits here, which
    was exactly the unreproducible-number discipline failure this arc keeps
    finding in others).

    The integrity check cannot catch this and never could: "N inferences" and
    "one spin of the same total length" are timing-identical by construction.
    So the report has to ask the probe, which is what collect_power_terminal
    and evaluation.validity already do via probe_runs_inferences().
    """
    ctx = _gated_power_ctx(
        tmp_path, clean_infer_count=1, clean_infer_avg_us=1_000_000, duration_s=1.0
    )
    object.__setattr__(
        ctx.config.profiling, "clean_window_probe", CleanWindowProbe.BUSY_LOOP
    )

    summary = json.loads(_write_summary(ctx, tmp_path).read_text())

    assert "energy_per_inference_j" not in summary["power"]
    assert "inferences_per_joule" not in summary["power"]
    # The gate-duration integrity fields go too, deliberately: for busy_loop,
    # expected == measured by construction (1 x the whole spin), so the ratio
    # is definitionally 1.0 and carries no information -- publishing it would
    # only lend false health to the fabricated figures it sat beside.
    assert "gated_window_duration_ratio" not in summary["power"]
    assert "gated_window_expected_duration_s" not in summary["power"]
    # and it says why, rather than silently omitting them
    assert "busy_loop" in summary["power"]["per_inference_metrics_omitted"]
    # the window's own measurements are untouched -- only the per-inference
    # division is withheld
    assert summary["power"]["energy_j"] == 0.0016


def test_busy_loop_probe_publishes_no_active_window_estimates_either(tmp_path: Path):
    """The OTHER fabrication branch (#125): internal-mode estimates.

    The first version of this guard covered only the gpio-gated branch.
    Review reproduced, on that version, `active_window_estimated_energy_per_
    inference_j` still publishing for a zero-inference internal-mode window.
    The estimated branch is worse than it looks: every figure in it scales
    `ps.avg_power_w` -- the WHOLE-CAPTURE average, which for busy_loop
    measured the CPU spin -- by real profiled inference time. Real time,
    wrong power, plausible number.
    """
    ctx = _gated_power_ctx(
        tmp_path, clean_infer_count=1, clean_infer_avg_us=1_000_000, duration_s=1.0
    )
    object.__setattr__(
        ctx.config.profiling, "clean_window_probe", CleanWindowProbe.BUSY_LOOP
    )
    ctx.power_result.metadata.measurement_scope = MeasurementScope.ON_DEVICE_GATED_INFERENCE
    object.__setattr__(ctx.pmu_result.meta, "profiled_infer_count", 200)
    object.__setattr__(ctx.pmu_result.meta, "profiled_infer_total_us", 18_000_000)

    summary = json.loads(_write_summary(ctx, tmp_path).read_text())

    for key in list(summary["power"]):
        assert not key.startswith("active_window_estimated"), key
    assert "busy_loop" in summary["power"]["per_inference_metrics_omitted"]


def test_infer_probe_still_publishes_per_inference_power_metrics(tmp_path: Path):
    """The guard above must not withhold the normal case."""
    ctx = _gated_power_ctx(
        tmp_path, clean_infer_count=10, clean_infer_avg_us=100_000, duration_s=1.0
    )

    summary = json.loads(_write_summary(ctx, tmp_path).read_text())

    assert summary["power"]["energy_per_inference_j"] > 0
    assert "per_inference_metrics_omitted" not in summary["power"]


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
        metadata=PowerMetadata(
            measurement_scope=MeasurementScope.FREE_FORM_CAPTURE,
            observation_mode=ObservationMode.FREE_FORM,
            integrity=PowerIntegrity.DEGRADED,
            gate_failure=GateFailure(kind=GateFailureKind.NO_GATE_RISE, message="", hint=""),
            gate_rise_observed=False,
            gate_fall_observed=False,
        ),
    )
    path = _write_summary(ctx, tmp_path)
    summary = json.loads(path.read_text())

    assert summary["power"]["observation_mode"] == "free_form"
    assert summary["power"]["integrity"] == "degraded"
    assert summary["power"]["gate_failure"]["kind"] == "no_gate_rise"
    assert summary["power"]["gate_rise_observed"] is False
    assert summary["power"]["gate_fall_observed"] is False
    assert "energy_per_inference_j" not in summary["power"]
    assert "active_window_estimated_energy_j" not in summary["power"]
    assert "tops_per_watt" not in summary.get("model_analysis", {})
    assert summary["validity"] == "degraded"
    assert [issue["code"] for issue in summary["issues"]] == [
        IssueCode.POWER_OBSERVATION_DEGRADED
    ]

    json_path = _write_json(ctx.pmu_result, ctx.power_result, ctx.run_metadata, tmp_path)
    full = json.loads(json_path.read_text())
    assert full["power"]["observation"] == {
        "measurement_scope": "free_form_capture",
        "observation_mode": "free_form",
        "integrity": "degraded",
        "gate_failure": {"kind": "no_gate_rise", "message": "", "hint": ""},
        "gate_rise_observed": False,
        "gate_fall_observed": False,
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
            calibration_id="board-rev-a",
        ),
    )
    ctx.power_result = PowerResult(
        summary=PowerSummary(0.01, 0.018, 0.02, 0.09, 5.0, 5000),
        metadata=PowerMetadata(measurement_scope=MeasurementScope.GPIO_GATED_CLEAN_WINDOW),
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
        "calibration_id": "board-rev-a",
    }


def test_write_summary_handles_sub_inference_dedicated_gate(tmp_path: Path):
    ctx = _gated_power_ctx(
        tmp_path, clean_infer_count=235, clean_infer_avg_us=21159, duration_s=0.008
    )
    ctx.power_result.metadata.power_firmware = "dedicated"

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
