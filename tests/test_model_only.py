"""Model-only captures retain whole-model evidence without fabricating layers."""

import json
from dataclasses import replace

import pytest

from helia_profiler.capture import capture_pmu
from helia_profiler.capture.parser import parse_firmware_output
from helia_profiler.engines.base import HeliaAotArtifacts
from helia_profiler.errors import CaptureError, FirmwareError
from helia_profiler.evaluation.compare import compare_runs, load_run_artifacts
from helia_profiler.firmware.context import FirmwareRenderContext
from helia_profiler.report import write_report
from helia_profiler.results import DeploymentRecord, load_result_manifest
from helia_profiler.results.run_summary import load_run_summary
from tests.contracts.conftest import make_pmu_ctx
from tests.contracts.test_firmware_render_snapshots import _render
from tests.pipeline_context_helpers import set_profile_result


def _lines():
    return [
        "--- HPX_START ---",
        "HPX_PER_LAYER_PROFILING=0",
        "HPX_NUM_PRESETS=0",
        "HPX_CLEAN_INFER_COUNT=10",
        "HPX_CLEAN_INFER_TOTAL_CYCLES=10000",
        "HPX_CLEAN_INFER_AVG_CYCLES=1000",
        "HPX_CLEAN_INFER_AVG_US=4",
        "HPX_INPUT_0_FNV1A64=00000000000000ff",
        "HPX_OUTPUT_0_HEX=0001",
        "--- HPX_END ---",
    ]


def _ctx(tmp_path, *, fmt="csv", per_layer=False):
    ctx = make_pmu_ctx(
        tmp_path,
        board="apollo510_evb",
        transport="uart",
        engine="helia-aot",
        extra={"output": {"dir": str(tmp_path / "bundle"), "format": fmt, "detailed": True}},
    )
    ctx.engine_artifacts = HeliaAotArtifacts(
        engine_header="fake_model.h",
        aot_prefix="fake",
        aot_module_name="fake_module",
        aot_cmake_target="nsx::fake",
        helia_aot_version="0.33.0",
        per_layer_profiling=per_layer,
    )
    ctx.run_metadata.run_id = "model-only-test"
    ctx.run_metadata.timestamp = "2026-11-13T00:00:00+00:00"
    ctx.run_metadata.hpx_version = "0.6.0"
    return ctx


def test_model_only_capture_publishes_at_the_existing_boundary(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(
        "helia_profiler.transport.uart.capture_uart_output", lambda **kwargs: _lines()
    )
    result = capture_pmu(ctx)
    assert ctx.profile_run is not None
    ctx.publish_profile_deployment(
        DeploymentRecord(firmware=ctx.profile_run.firmware, target_id="test", deployed_at="test")
    )
    ctx.publish_profile_result(result)
    assert ctx.captured_pmu.meta.per_layer_profiling is False
    assert ctx.captured_pmu.layers == []
    assert ctx.captured_pmu.meta.clean_infer_total_cycles == 10000


@pytest.mark.parametrize("per_layer,reported", [(True, False), (False, None), (False, True)])
def test_prepared_engine_and_report_must_agree(tmp_path, per_layer, reported):
    ctx = _ctx(tmp_path, per_layer=per_layer)
    assert ctx.profile_run is not None
    ctx.publish_profile_deployment(
        DeploymentRecord(firmware=ctx.profile_run.firmware, target_id="test", deployed_at="test")
    )
    result = parse_firmware_output(_lines())
    result = replace(result, meta=replace(result.meta, per_layer_profiling=reported))
    with pytest.raises(CaptureError, match="capability"):
        ctx.publish_profile_result(result)
    assert ctx.profile_run.result is None


@pytest.mark.parametrize(
    "replacement",
    ["HPX_PER_LAYER_PROFILING=2", "HPX_PER_LAYER_PROFILING=false", "HPX_PER_LAYER_PROFILING=model"],
)
def test_unknown_capability_is_rejected(replacement):
    lines = _lines()
    lines[1] = replacement
    with pytest.raises(CaptureError, match="PER_LAYER_PROFILING"):
        parse_firmware_output(lines)


@pytest.mark.parametrize(
    "drop",
    [
        "HPX_NUM_PRESETS=0",
        "HPX_CLEAN_INFER_COUNT=10",
        "HPX_CLEAN_INFER_TOTAL_CYCLES=10000",
        "HPX_CLEAN_INFER_AVG_CYCLES=1000",
        "HPX_CLEAN_INFER_AVG_US=4",
        "--- HPX_END ---",
    ],
)
def test_model_only_stream_requires_complete_evidence(drop):
    with pytest.raises(CaptureError):
        parse_firmware_output([line for line in _lines() if line != drop])


@pytest.mark.parametrize(
    "field,value",
    [
        ("COUNT", "bad"),
        ("TOTAL_CYCLES", "bad"),
        ("AVG_CYCLES", "bad"),
        ("AVG_US", "bad"),
        ("COUNT", "0"),
        ("TOTAL_CYCLES", "0"),
        ("AVG_CYCLES", "0"),
        ("AVG_US", "-1"),
    ],
)
def test_invalid_model_timing_is_a_capture_error(field, value):
    key = f"HPX_CLEAN_INFER_{field}="
    lines = [key + value if line.startswith(key) else line for line in _lines()]
    with pytest.raises(CaptureError, match="clean-window timing"):
        parse_firmware_output(lines)


def test_sub_microsecond_model_timing_retains_positive_cycle_measurement():
    lines = [
        "HPX_CLEAN_INFER_AVG_US=0" if line.startswith("HPX_CLEAN_INFER_AVG_US=") else line
        for line in _lines()
    ]
    result = parse_firmware_output(lines)
    assert result.meta.clean_infer_avg_us == 0
    assert result.meta.clean_infer_avg_cycles == 1000


def test_model_only_stream_does_not_accept_an_empty_announced_pass():
    lines = _lines()
    lines[2] = "HPX_NUM_PRESETS=1"
    lines[-1:-1] = ["--- HPX_PRESET memory_0 ---", "--- HPX_ITER 0 ---"]
    with pytest.raises(CaptureError):
        parse_firmware_output(lines)


def test_layer_capable_truncation_is_still_an_error(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, per_layer=True)
    monkeypatch.setattr(
        "helia_profiler.transport.uart.capture_uart_output",
        lambda **kwargs: [
            line for line in _lines() if not line.startswith("HPX_PER_LAYER_PROFILING")
        ],
    )
    with pytest.raises(CaptureError, match="No layer data"):
        capture_pmu(ctx)


@pytest.mark.parametrize("fmt", ["csv", "json"])
def test_model_only_bundle_retains_timing_and_omits_layer_projections(tmp_path, fmt):
    ctx = _ctx(tmp_path, fmt=fmt)
    result = parse_firmware_output(_lines())
    set_profile_result(ctx, result)
    lock = tmp_path / "source.lock"
    lock.write_text("schema_version: 4\ntargets: {}\n")
    ctx.dependency_lock_path = lock
    out = ctx.config.output.dir
    for name in (
        "profile_results.csv",
        "profile_results.json",
        "detailed/profile_cpu.csv",
        "model_explorer/me_overlay_cycles.json",
    ):
        stale = out / name
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text("stale")
    (out / "keep.txt").write_text("unrelated")
    paths = write_report(ctx)
    assert all(not path.name.startswith(("profile_", "me_overlay_")) for path in paths)
    assert not (out / "profile_results.csv").exists()
    assert not (out / "profile_results.json").exists()
    assert not (out / "detailed/profile_cpu.csv").exists()
    assert not (out / "model_explorer/me_overlay_cycles.json").exists()
    assert (out / "keep.txt").read_text() == "unrelated"
    summary = load_run_summary(out / "summary.json")
    assert summary.per_layer_profiling is False
    assert summary.layers == 0 and summary.total_cycles is None
    assert summary.latency is not None
    assert summary.latency.device_clean_infer_avg_cycles == 1000
    meta = json.loads((out / "run_metadata.json").read_text())
    assert meta["firmware"]["per_layer_profiling"] is False
    manifest = load_result_manifest(out / "result_manifest.json", verify=True)
    assert manifest.bundle_type == "profile-model-only"
    raw = (out / "firmware_output.txt").read_text()
    assert "HPX_INPUT_0_FNV1A64=00000000000000ff" in raw
    assert "HPX_OUTPUT_0_HEX=0001" in raw
    assert manifest.validity.value == "valid"
    assert load_run_artifacts(out).layers == []
    comparison = compare_runs(out, out)
    assert comparison.comparability.run_metrics_comparable
    assert not comparison.comparability.layers_comparable
    assert not comparison.layer_rows
    assert any(metric.name == "device_clean_infer_avg_cycles" for metric in comparison.metrics)
    assert not any(metric.name == "layers" for metric in comparison.metrics)


@pytest.mark.parametrize("power", [False, True])
def test_model_only_render_has_no_callback_storage_or_passes(power):
    text = _render(
        "apollo510",
        "rtt",
        "helia-aot",
        power_only=power,
        overrides={"per_layer_profiling": False, "pmu_passes": [], "pmu_pass_names": []},
    )
    assert "model_operator_cb" not in text
    assert "g_layers[" not in text
    assert "--- HPX_PRESET" not in text
    assert ".callback = nullptr" in text
    assert ("HPX_PER_LAYER_PROFILING=0" in text) is not power
    assert "HPX_CLEAN_INFER_TOTAL_CYCLES" in text or power
    assert "fake_model_run" in text


def test_model_only_production_render_context_drops_passes(tmp_path):
    ctx = _ctx(tmp_path)
    rendered = FirmwareRenderContext.from_pipeline_context(ctx)
    assert not rendered.pmu.pmu_passes
    assert rendered.to_template_vars()["per_layer_profiling"] is False


def test_instrument_only_request_is_not_silently_accepted(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.config = replace(ctx.config, profiling=replace(ctx.config.profiling, stream_evidence=True))
    with pytest.raises(FirmwareError):
        FirmwareRenderContext.from_pipeline_context(ctx)


def test_diagnostic_text_does_not_change_measured_observation_identity():
    from helia_profiler.profile_identity import _observation_hash

    result = parse_firmware_output(_lines())
    changed_text = replace(result, raw_output=result.raw_output + ("diagnostic",))
    assert _observation_hash(result) == _observation_hash(changed_text)
    changed_measurement = replace(result, meta=replace(result.meta, clean_infer_total_cycles=10001))
    assert _observation_hash(result) != _observation_hash(changed_measurement)


def test_validation_health_accepts_missing_layers_only_for_model_only():
    from helia_profiler.validation.runner import CaseResult, validation_health_issues

    case = CaseResult(
        case_id="model-only",
        status="pass",
        duration_s=1,
        engine="helia-aot",
        model_id="test",
        board="apollo510_evb",
        power=False,
        toolchain="gcc",
        transport="uart",
        memory="tcm",
        layers=0,
        total_cycles=1000,
        aot_operator_count=13,
        per_layer_profiling=False,
    )
    assert not validation_health_issues(case)
    assert "summary.json reports no layers" in validation_health_issues(
        replace(case, per_layer_profiling=True)
    )
    assert any(
        "total_cycles" in issue
        for issue in validation_health_issues(replace(case, total_cycles=None))
    )


def test_model_only_summary_rejects_layer_measurements():
    from helia_profiler.results.run_summary import (
        RUN_SUMMARY_SCHEMA,
        RUN_SUMMARY_SCHEMA_VERSION,
        RunSummary,
    )

    with pytest.raises(ValueError, match="Model-only"):
        RunSummary.from_dict(
            {
                "schema": RUN_SUMMARY_SCHEMA,
                "schema_version": RUN_SUMMARY_SCHEMA_VERSION,
                "per_layer_profiling": False,
                "layers": 0,
                "total_cycles": 0,
            }
        )
