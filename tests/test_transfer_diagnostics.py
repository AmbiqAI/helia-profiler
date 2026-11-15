"""Standalone transfer frames preserve their own identity and native evidence."""

import json
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator

from helia_profiler.capture.parser import parse_firmware_output
from helia_profiler.config import load_config
from helia_profiler.engines.base import HeliaAotArtifacts
from helia_profiler.errors import CaptureError, ConfigError, FirmwareError
from helia_profiler.firmware.context import FirmwareRenderContext
from helia_profiler.pipeline import serialize_config
from helia_profiler.report.json_writer import _write_json
from helia_profiler.results import RunMetadata
from tests.test_model_only import _ctx
from tests.test_transfer_evidence import SCHEMA, _session

_TIMING = [
    "HPX_TRANSFER_DIAGNOSTIC_BEGIN=timing,0,96000000,configured,2,1",
    "HPX_TRANSFER_TIMING=0,32,0,0,0",
    "HPX_TRANSFER_TIMING=1,10,0,x,x",
    "HPX_TRANSFER_DIAGNOSTIC_STATS=2,1,0,1,4,0",
    "HPX_TRANSFER_DIAGNOSTIC_END=96000,0,1",
]


def test_diagnostics_do_not_change_pmu_counts_or_layer_averages(tmp_path):
    baseline = parse_firmware_output(_session("HPX_PROFILED_INFER_COUNT=1"))
    result = parse_firmware_output(_session("HPX_PROFILED_INFER_COUNT=1", *_TIMING))
    assert result.presets == baseline.presets
    assert result.meta == baseline.meta
    assert result.transfer_evidence == ()
    (record,) = result.transfer_diagnostics
    assert record.units[0].wait_cycles == 0
    assert record.units[1].wait_cycles is None
    payload = json.loads(_write_json(result, None, RunMetadata(), tmp_path).read_text())
    validator = Draft202012Validator(json.loads(SCHEMA.read_text()))
    validator.validate(payload)
    assert payload["transfer_diagnostics"][0]["units"][1]["wait_status"] is None
    payload["transfer_diagnostics"][0]["units"][0]["issue_cycles"] = -1
    assert not validator.is_valid(payload)


def test_overlap_is_diagnostic_and_keeps_sampled_unknown_flags():
    lines = [
        "HPX_TRANSFER_DIAGNOSTIC_BEGIN=overlap,0,250000000,cold,2,1",
        "HPX_TRANSFER_DIAGNOSTIC_OVERLAP=0:x/1,1:0/0;0:1/0",
        "HPX_TRANSFER_DIAGNOSTIC_STATS=2,1,0,1,4,1",
        "HPX_TRANSFER_DIAGNOSTIC_END=250000,0,1",
    ]
    result = parse_firmware_output(_session(*lines))
    (record,) = result.transfer_diagnostics
    assert record.purpose == "overlap" and record.preparation == "cold"
    assert record.overlap is not None
    assert record.overlap.units[0].dma_in_progress is None
    assert record.overlap.units[0].reads_pending is True
    assert result.transfer_evidence == ()


@pytest.mark.parametrize(
    "lines",
    [
        _TIMING[:-1],
        _TIMING[1:],
        [_TIMING[0], _TIMING[0], *_TIMING[1:]],
        [_TIMING[0], _TIMING[1], *_TIMING[1:]],
        [_TIMING[0], *_TIMING[2:]],
        [_TIMING[0], "--- HPX_ITER 1 ---", *_TIMING[1:]],
        [*_TIMING, *_TIMING],
        [line.replace("0,32,0,0,0", "0,32,0,x,0") for line in _TIMING],
        [line.replace("0,32,0,0,0", "2,32,0,0,0") for line in _TIMING],
        [line.replace("0,32,0,0,0", "0,32,-1,0,0") for line in _TIMING],
        [line.replace("96000,0,1", "96000,-1,1") for line in _TIMING],
        [line.replace("96000,0,1", "0,0,1") for line in _TIMING],
        [line.replace("96000000,configured,2,1", "0,configured,2,1") for line in _TIMING],
        [line.replace("96000000,configured,2,1", "96000000,configured,2,0") for line in _TIMING],
        [line.replace("96000,0,1", "96000,0,2") for line in _TIMING],
        [
            line.replace("HPX_TRANSFER_TIMING=0,32,0,0,0", "HPX_TRANSFER_DIAGNOSTIC_OVERLAP=0:1/1;")
            for line in _TIMING
        ],
    ],
)
def test_invalid_or_truncated_diagnostics_fail_explicitly(lines):
    with pytest.raises(CaptureError, match="[Dd]iagnostic|PMU boundary"):
        parse_firmware_output(_session(*lines))


def test_incomplete_records_retain_missing_and_failure_evidence():
    lines = [
        line.replace("0,32,0,0,0", "0,x,x,x,x").replace("96000,0,1", "96000,-1,0")
        for line in _TIMING
    ]
    (record,) = parse_firmware_output(_session(*lines)).transfer_diagnostics
    assert not record.complete and record.run_status == -1
    assert record.units[0].issue_cycles is None


@pytest.mark.parametrize("mode", ["off", "timing", "overlap"])
def test_config_modes_and_default_serialization(mode):
    config = load_config(
        None,
        {
            "model": {"path": "m.tflite"},
            "engine": {"type": "helia-aot"},
            "profiling": {"transfer_diagnostics": mode},
        },
    )
    frozen = serialize_config(config)["profiling"]
    assert frozen.get("transfer_diagnostics", "off") == mode
    assert "transfer_diagnostic_iterations" not in frozen


@pytest.mark.parametrize("value", [True, 0, -1, 1001, 1.5, "2"])
def test_diagnostic_count_is_a_bounded_explicit_integer(value):
    with pytest.raises(ConfigError, match="transfer_diagnostic_iterations"):
        load_config(
            None,
            {
                "model": {"path": "m.tflite"},
                "engine": {"type": "helia-aot"},
                "profiling": {
                    "transfer_diagnostics": "timing",
                    "transfer_diagnostic_iterations": value,
                },
            },
        )


@pytest.mark.parametrize("engine", ["litert", "helia-rt", "executorch"])
def test_other_engines_cannot_enable_diagnostics(engine):
    with pytest.raises(ConfigError, match="transfer_diagnostics applies"):
        load_config(
            None,
            {
                "model": {"path": "m.tflite"},
                "engine": {"type": engine},
                "profiling": {"transfer_diagnostics": "timing"},
            },
        )


@pytest.mark.parametrize(
    "artifact_fields",
    [
        {"per_layer_profiling": False, "aot_transfer_cmake_target": "nsx::transfer"},
        {"per_layer_profiling": True, "aot_transfer_cmake_target": None},
        {
            "per_layer_profiling": True,
            "aot_transfer_cmake_target": "nsx::transfer",
            "engine_backend": "ethos_u",
        },
    ],
)
def test_render_rejects_unavailable_diagnostic_contracts(tmp_path, artifact_fields):
    ctx = _ctx(tmp_path, per_layer=True)
    ctx.config = replace(
        ctx.config, profiling=replace(ctx.config.profiling, transfer_diagnostics="timing")
    )
    assert isinstance(ctx.engine_artifacts, HeliaAotArtifacts)
    ctx.engine_artifacts = replace(ctx.engine_artifacts, **artifact_fields)
    with pytest.raises(FirmwareError, match="Transfer diagnostics need"):
        FirmwareRenderContext.from_pipeline_context(ctx)


def test_supported_render_passes_explicit_mode_and_count(tmp_path):
    ctx = _ctx(tmp_path, per_layer=True)
    ctx.config = replace(
        ctx.config,
        profiling=replace(
            ctx.config.profiling, transfer_diagnostics="timing", transfer_diagnostic_iterations=2
        ),
    )
    assert isinstance(ctx.engine_artifacts, HeliaAotArtifacts)
    ctx.engine_artifacts = replace(ctx.engine_artifacts, aot_transfer_cmake_target="nsx::transfer")
    variables = FirmwareRenderContext.from_pipeline_context(ctx).to_template_vars()
    assert variables["transfer_diagnostics"] == "timing"
    assert variables["transfer_diagnostic_iterations"] == 2


@pytest.mark.parametrize("mismatch", ["missing", "extra", "mode", "preparation", "clock"])
def test_live_capture_requires_requested_diagnostics(mismatch):
    from helia_profiler.capture.transfer_diagnostics import validate_transfer_diagnostics
    from helia_profiler.config.profiling import ProfilingConfig

    result = parse_firmware_output(_session("HPX_SYSTEM_CLOCK_HZ=96000000", *_TIMING))
    config = ProfilingConfig(transfer_diagnostics="timing")
    validate_transfer_diagnostics(result, config)
    if mismatch == "missing":
        result = replace(result, transfer_diagnostics=())
    elif mismatch == "extra":
        config = ProfilingConfig()
    elif mismatch == "mode":
        config = replace(config, transfer_diagnostics="overlap")
    elif mismatch == "preparation":
        config = replace(config, stream_evidence=True)
    else:
        result = replace(result, meta=replace(result.meta, system_clock_hz=250000000))
    with pytest.raises(CaptureError, match="do not match"):
        validate_transfer_diagnostics(result, config)
