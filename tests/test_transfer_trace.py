"""Chronological records use received evidence, without reconstructing missing events."""

import json
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator

from helia_profiler.capture.parser import parse_firmware_output
from helia_profiler.capture.transfer_diagnostics import validate_transfer_diagnostics
from helia_profiler.config import load_config
from helia_profiler.errors import CaptureError, ConfigError
from helia_profiler.report.json_writer import _write_json
from helia_profiler.results import RunMetadata
from tests.test_transfer_evidence import SCHEMA, _session

TRACE = [
    "HPX_TRANSFER_DIAGNOSTIC_BEGIN=trace,0,96000000,configured,1,1",
    "HPX_TRANSFER_TRACE_BEGIN=1,1,0,2,256,1",
    "HPX_TRANSFER_TRACE_SPAN=issue,0,1,10,0",
    "HPX_TRANSFER_TRACE_SPAN=operator,0,20,40,0",
    "HPX_TRANSFER_TRACE_SPAN=wait,0,50,60,0",
    "HPX_TRANSFER_TRACE_PIECE=0,0,64,0,5",
    "HPX_TRANSFER_TRACE_END=1,0,1,1",
    "HPX_TRANSFER_DIAGNOSTIC_STATS=1,1,0,1,0,0",
    "HPX_TRANSFER_DIAGNOSTIC_END=96000,0,1",
]


def _parse(lines):
    return parse_firmware_output(_session("HPX_SYSTEM_CLOCK_HZ=96000000", *lines))


def test_actual_timestamps_and_early_completion_round_trip_native_schema(tmp_path):
    result = _parse(TRACE)
    baseline = _parse([])
    assert result.meta == baseline.meta and result.presets == baseline.presets
    (diagnostic,) = result.transfer_diagnostics
    trace = diagnostic.trace
    assert trace is not None and diagnostic.complete
    assert trace.completions[0].cycles == 5 < trace.spans[0].end_cycles
    assert trace.spans[1].start_cycles == 20
    document = json.loads(_write_json(result, None, RunMetadata(), tmp_path).read_text())
    validator = Draft202012Validator(json.loads(SCHEMA.read_text()))
    validator.validate(document)
    raw = document["transfer_diagnostics"][0]["trace"]
    assert raw["schema"] == "hpx.transfer-trace" and raw["schema_version"] == 1
    raw["completions"][0]["cycles"] = -1
    assert not validator.is_valid(document)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("TRACE_BEGIN=1,1,0,2,256,1", "TRACE_BEGIN=2,1,0,2,256,1"),
        ("TRACE_BEGIN=1,1,0,2,256,1", "TRACE_BEGIN=1,0,0,2,256,1"),
        ("TRACE_BEGIN=1,1,0,2,256,1", "TRACE_BEGIN=1,1,0,4294967295,256,1"),
        ("SPAN=issue,0,1,10,0", "SPAN=issue,1,1,10,0"),
        ("SPAN=issue,0,1,10,0", "SPAN=issue,0,10,1,0"),
        ("SPAN=issue,0,1,10,0", "SPAN=issue,0,x,10,0"),
        ("SPAN=issue,0,1,10,0", "SPAN=issue,0,1,10,-1"),
        ("SPAN=operator,0,20,40,0", "SPAN=operator,0,20,96001,0"),
        ("TRACE_PIECE=0,0,64,0,5", "TRACE_PIECE=0,0,64,0,0"),
        ("TRACE_PIECE=0,0,64,0,5", "TRACE_PIECE=0,0,64,0,61"),
        ("TRACE_PIECE=0,0,64,0,5", "TRACE_PIECE=1,0,64,0,5"),
        ("TRACE_PIECE=0,0,64,0,5", "TRACE_PIECE=0,0,0,0,5"),
        ("TRACE_PIECE=0,0,64,0,5", "TRACE_PIECE=0,0,64,7,5"),
        ("TRACE_END=1,0,1,1", "TRACE_END=1,0,0,1"),
        ("TRACE_END=1,0,1,1", "TRACE_END=1,0,1,0"),
        ("TRACE_END=1,0,1,1", "TRACE_END=2,0,1,1"),
        ("STATS=1,1,0,1,0,0", "STATS=2,1,0,1,0,0"),
    ],
)
def test_wrong_variants_cannot_qualify(before, after):
    with pytest.raises(CaptureError):
        _parse([line.replace(before, after) for line in TRACE])


@pytest.mark.parametrize("index", [1, 2, 3, 4, 5, 6])
def test_missing_or_duplicate_trace_records_fail(index):
    with pytest.raises(CaptureError):
        _parse(TRACE[:index] + TRACE[index + 1 :])
    with pytest.raises(CaptureError):
        _parse(TRACE[:index] + [TRACE[index]] + TRACE[index:])


def test_incomplete_trace_retains_loss_and_failed_unpaired_evidence():
    lines = [
        line.replace("TRACE_END=1,0,1,1", "TRACE_END=2,1,1,1")
        .replace("SPAN=issue,0,1,10,0", "SPAN=issue,0,1,x,x")
        .replace("96000,0,1", "96000,-1,0")
        for line in TRACE
    ]
    (record,) = _parse(lines).transfer_diagnostics
    assert not record.complete
    assert record.trace.dropped_count == 1 and record.trace.spans[0].end_cycles is None


def test_sequence_wrap_and_out_of_order_notifications_retain_arrival_order():
    lines = [
        line.replace("TRACE_BEGIN=1,1,0,2", "TRACE_BEGIN=1,1,4294967295,2")
        .replace("TRACE_END=1,0,1,1", "TRACE_END=2,0,1,1")
        .replace("STATS=1,1,0,1,0,0", "STATS=2,1,0,1,0,0")
        for line in TRACE
    ]
    lines.insert(6, "HPX_TRANSFER_TRACE_PIECE=4294967295,0,64,0,6")
    trace = _parse(lines).transfer_diagnostics[0].trace
    assert [p.sequence for p in trace.completions] == [0, 4294967295]


def test_live_trace_capacity_must_match_requested_config():
    config = load_config(
        None,
        {
            "model": {"path": "m.tflite"},
            "engine": {"type": "helia-aot"},
            "profiling": {"transfer_diagnostics": "trace", "transfer_trace_max_pieces": 2},
        },
    )
    result = _parse(TRACE)
    validate_transfer_diagnostics(result, config.profiling)
    with pytest.raises(CaptureError):
        validate_transfer_diagnostics(
            result, replace(config.profiling, transfer_trace_max_pieces=3)
        )


@pytest.mark.parametrize("capacity", [True, False, 0, -1, 4097, "2", 2.0])
def test_capacity_is_an_explicit_bounded_integer(capacity):
    with pytest.raises(ConfigError, match="transfer_trace_max_pieces"):
        load_config(
            None,
            {"model": {"path": "m.tflite"}, "profiling": {"transfer_trace_max_pieces": capacity}},
        )


def test_unadopted_observer_release_is_refused_before_firmware_render(tmp_path):
    from helia_profiler.engines.base import HeliaAotArtifacts
    from helia_profiler.errors import FirmwareError
    from helia_profiler.firmware.context import FirmwareRenderContext
    from tests.test_model_only import _ctx

    ctx = _ctx(tmp_path, per_layer=True)
    ctx.config = replace(
        ctx.config, profiling=replace(ctx.config.profiling, transfer_diagnostics="trace")
    )
    assert isinstance(ctx.engine_artifacts, HeliaAotArtifacts)
    ctx.engine_artifacts = replace(ctx.engine_artifacts, aot_transfer_cmake_target="nsx::transfer")
    with pytest.raises(FirmwareError, match="recorded released AOT completion observer"):
        FirmwareRenderContext.from_pipeline_context(ctx)


def test_huge_operator_declaration_never_drives_allocation_or_range():
    lines = [
        s.replace("TRACE_BEGIN=1,1,0,2,256,1", "TRACE_BEGIN=1,1,0,2,256,4294967295") for s in TRACE
    ]
    with pytest.raises(CaptureError, match="missing boundaries"):
        _parse(lines)


def test_released_observer_fact_enables_only_the_requested_trace(tmp_path, monkeypatch):
    from helia_profiler.engines.base import HeliaAotArtifacts
    from helia_profiler.firmware.context import FirmwareRenderContext
    from helia_profiler.runtime import records
    from tests.test_model_only import _ctx

    ctx = _ctx(tmp_path, per_layer=True)
    ctx.config = replace(
        ctx.config,
        profiling=replace(
            ctx.config.profiling, transfer_diagnostics="trace", transfer_trace_max_pieces=7
        ),
    )
    assert isinstance(ctx.engine_artifacts, HeliaAotArtifacts)
    ctx.engine_artifacts = replace(ctx.engine_artifacts, aot_transfer_cmake_target="nsx::transfer")
    current = records.runtime("helia-aot", ctx.engine_artifacts.helia_aot_version)
    assert current is not None and not current.transfer_observer
    monkeypatch.setattr(records, "runtime", lambda *args: replace(current, transfer_observer=True))
    variables = FirmwareRenderContext.from_pipeline_context(ctx).to_template_vars()
    assert variables["transfer_diagnostics"] == "trace"
    assert variables["transfer_trace_max_pieces"] == 7
