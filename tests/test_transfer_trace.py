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


def test_replayed_binding_cannot_become_a_second_qualified_iteration():
    replay = [line.replace("BEGIN=trace,0,", "BEGIN=trace,1,") for line in TRACE]
    with pytest.raises(CaptureError, match="repeated trace binding epoch"):
        _parse([*TRACE, *replay])
    fresh = [line.replace("TRACE_BEGIN=1,1,0,", "TRACE_BEGIN=1,2,0,") for line in replay]
    result = _parse([*TRACE, *fresh])
    config = load_config(
        None,
        {
            "model": {"path": "m.tflite"},
            "engine": {"type": "helia-aot"},
            "profiling": {
                "transfer_diagnostics": "trace",
                "transfer_diagnostic_iterations": 2,
                "transfer_trace_max_pieces": 2,
            },
        },
    )
    validate_transfer_diagnostics(result, config.profiling)
    second = result.transfer_diagnostics[1]
    corrupted = replace(second, trace=replace(second.trace, binding_epoch=1))
    with pytest.raises(CaptureError):
        validate_transfer_diagnostics(
            replace(result, transfer_diagnostics=(result.transfer_diagnostics[0], corrupted)),
            config.profiling,
        )


@pytest.mark.parametrize(
    "extra, change",
    [
        (
            ["HPX_TRANSFER_TRACE_SPAN=operator,1,30,45,0"],
            ("TRACE_BEGIN=1,1,0,2,256,1", "TRACE_BEGIN=1,1,0,2,256,2"),
        ),
        (
            ["HPX_TRANSFER_TRACE_SPAN=operator,1,11,19,0"],
            ("TRACE_BEGIN=1,1,0,2,256,1", "TRACE_BEGIN=1,1,0,2,256,2"),
        ),
        ([], ("SPAN=wait,0,50,60,0", "SPAN=wait,0,6,60,0")),
    ],
)
def test_impossible_foreground_chronology_is_not_qualified(extra, change):
    lines = [line.replace(*change) for line in TRACE]
    lines[5:5] = extra
    with pytest.raises(CaptureError, match="sequential|execution|waits before"):
        _parse(lines)
    incomplete = [line.replace("96000,0,1", "96000,0,0") for line in lines]
    assert not _parse(incomplete).transfer_diagnostics[0].complete


def test_callbacks_cannot_arrive_backwards_in_time_but_may_share_a_cycle():
    lines = [
        line.replace("TRACE_END=1,0,1,1", "TRACE_END=2,0,1,1").replace(
            "STATS=1,1,0,1,0,0", "STATS=2,1,0,1,0,0"
        )
        for line in TRACE
    ]
    lines.insert(6, "HPX_TRANSFER_TRACE_PIECE=1,0,64,0,4")
    with pytest.raises(CaptureError, match="notification time"):
        _parse(lines)
    lines[6] = "HPX_TRANSFER_TRACE_PIECE=1,0,64,0,5"
    assert _parse(lines).transfer_diagnostics[0].complete


def test_complete_trace_operator_count_matches_captured_layers():
    config = load_config(
        None,
        {
            "model": {"path": "m.tflite"},
            "engine": {"type": "helia-aot"},
            "profiling": {"transfer_diagnostics": "trace", "transfer_trace_max_pieces": 2},
        },
    )
    without_ops = [
        s.replace("TRACE_BEGIN=1,1,0,2,256,1", "TRACE_BEGIN=1,1,0,2,256,0")
        for s in TRACE
        if "SPAN=operator" not in s
    ]
    result = _parse(without_ops)
    with pytest.raises(CaptureError):
        validate_transfer_diagnostics(result, config.profiling)
    incomplete = _parse([s.replace("96000,0,1", "96000,0,0") for s in without_ops])
    validate_transfer_diagnostics(incomplete, config.profiling)
    assert not incomplete.transfer_diagnostics[0].complete


TWO_UNITS = [
    "HPX_TRANSFER_DIAGNOSTIC_BEGIN=trace,0,96000000,configured,2,2",
    "HPX_TRANSFER_TRACE_BEGIN=1,1,0,2,512,1",
    "HPX_TRANSFER_TRACE_SPAN=issue,0,1,10,0",
    "HPX_TRANSFER_TRACE_SPAN=issue,1,11,20,0",
    "HPX_TRANSFER_TRACE_SPAN=operator,0,21,30,0",
    "HPX_TRANSFER_TRACE_SPAN=wait,0,40,50,0",
    "HPX_TRANSFER_TRACE_SPAN=wait,1,51,60,0",
    "HPX_TRANSFER_TRACE_PIECE=0,0,64,0,5",
    "HPX_TRANSFER_TRACE_PIECE=1,1,64,0,15",
    "HPX_TRANSFER_TRACE_END=2,0,1,1",
    "HPX_TRANSFER_DIAGNOSTIC_STATS=2,2,0,2,0,0",
    "HPX_TRANSFER_DIAGNOSTIC_END=96000,0,1",
]


def test_each_waited_dma_unit_needs_its_own_completion_evidence():
    assert _parse(TWO_UNITS).transfer_diagnostics[0].complete
    missing = [s.replace("PIECE=1,1,64,0,15", "PIECE=1,0,64,0,15") for s in TWO_UNITS]
    with pytest.raises(CaptureError, match="waited DMA unit"):
        _parse(missing)
    assert (
        not _parse([s.replace("96000,0,1", "96000,0,0") for s in missing])
        .transfer_diagnostics[0]
        .complete
    )


def test_acceptance_sequence_cannot_reverse_nonoverlapping_issue_windows():
    impossible = [
        s.replace("PIECE=0,0,64,0,5", "PIECE=1,0,64,0,5").replace(
            "PIECE=1,1,64,0,15", "PIECE=0,1,64,0,15"
        )
        for s in TWO_UNITS
    ]
    with pytest.raises(CaptureError, match="sequence contradicts issue windows"):
        _parse(impossible)
    assert (
        not _parse([s.replace("96000,0,1", "96000,0,0") for s in impossible])
        .transfer_diagnostics[0]
        .complete
    )
    delayed_first = list(TWO_UNITS)
    # Notifications can be delivered out of accepted-read order.
    delayed_first[7:9] = [
        "HPX_TRANSFER_TRACE_PIECE=1,1,64,0,15",
        "HPX_TRANSFER_TRACE_PIECE=0,0,64,0,35",
    ]
    assert _parse(delayed_first).transfer_diagnostics[0].complete
