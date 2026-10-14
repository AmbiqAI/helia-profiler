"""Pure packed-stream controls: no instrument import, connection or model call."""

import json
from copy import deepcopy
from dataclasses import asdict

import numpy as np
import pytest

from helia_profiler.power.counter_gate import PackedGateRecorder, counter_gate_trace


def frame(data, *, first=0, count=None, step=1, rate=1000, channel=0):
    value = {
        "sample_id": first,
        "sample_rate": rate,
        "decimate_factor": step,
        "field_id": 5,
        "index": channel,
        "utc": first * 1000,
        "time_map": {"offset_counter": 0, "offset_time": 0, "counter_rate": rate},
        "data": np.array(data, dtype=np.uint8),
    }
    if count is not None:
        value["element_count"] = count
    return value


def packets(count=16, *, rate=1000):
    return [
        {
            "time": {
                "samples": {"value": [i, i + 1]},
                "sample_freq": {"value": rate},
                "utc": {"value": [i * 1000, (i + 1) * 1000]},
                "delta": {"value": 1 / rate},
                "time_map": {"offset_counter": 0, "offset_time": 0, "counter_rate": rate},
            },
            "signals": {
                "power": {"integral": {"value": 2 / rate}},
                "current": {"integral": {"value": 1 / rate}},
            },
        }
        for i in range(count)
    ]


def retained(*values):
    recorder = PackedGateRecorder(0, 100000, sample_mode="first")
    for value in values:
        recorder.append(value)
    assert recorder.error is None
    return recorder.frames


def test_within_byte_little_endian_pulse_and_known_integral_survive_json():
    # 0b00111100 is low, low, four high samples, then low, low.
    frames = retained(frame([0x3C, 0], count=16))
    frames = json.loads(json.dumps(frames))
    stats = packets()
    trace = counter_gate_trace(frames, stats, channel=0)
    assert trace.windows == ((2, 6),)
    evidence = trace.window_evidence(2, 6)
    assert evidence["first_packet"] == 2 and evidence["last_packet"] == 5
    assert evidence["packet_count"] == 4
    assert evidence["integrated_duration_s"] == 0.004
    assert sum(p["signals"]["power"]["integral"]["value"] for p in stats[2:6]) == 0.008
    assert evidence["boundary_uncertainty_s"] == 0.002


def test_valid_count_excludes_padding_and_cross_frame_edge_is_preserved():
    # Two high bits then six padding bits in the second frame; padding MUST NOT
    # invent a fall. The fall is the first real sample of the following frame.
    frames = retained(
        frame([0xC0], count=8), frame([0x03], first=8, count=2), frame([0], first=10, count=6)
    )
    assert counter_gate_trace(frames, packets(), channel=0).windows == ((6, 10),)


def test_decimation_uses_native_counter_not_byte_count():
    frames = retained(frame([0x3C], count=8, step=2))
    trace = counter_gate_trace(frames, packets(), channel=0)
    assert trace.windows == ((4, 12),)
    assert trace.window_evidence(4, 12)["integrated_duration_s"] == 0.008


@pytest.mark.parametrize("change", ["missing", "duplicate", "reverse", "rate", "delta"])
def test_counter_discontinuities_refuse(change):
    stats = packets()
    if change == "missing":
        stats.pop(4)
    elif change == "duplicate":
        stats.insert(4, deepcopy(stats[4]))
    elif change == "reverse":
        stats[4], stats[5] = stats[5], stats[4]
    elif change == "rate":
        stats[4]["time"]["sample_freq"]["value"] *= 2
    else:
        stats[4]["time"]["delta"]["value"] = 4
    with pytest.raises(ValueError):
        counter_gate_trace(retained(frame([0x3C, 0], count=16)), stats, channel=0)


def test_fitted_map_update_does_not_create_missing_device_samples():
    stats = packets()
    for p in stats[4:]:
        p["time"]["time_map"]["offset_time"] = 535
        p["time"]["utc"]["value"] = [v + 535 for v in p["time"]["utc"]["value"]]
    trace = counter_gate_trace(retained(frame([0x3C, 0], count=16)), stats, channel=0)
    assert trace.clock_diagnostics["utc_boundary_discontinuities_ticks"] == [535]
    assert trace.clock_diagnostics["fitted_map_changes"] == 1
    assert trace.window_evidence(2, 6)["integrated_duration_s"] == 0.004


@pytest.mark.parametrize("change", ["gap", "overlap", "channel", "rate", "order", "count"])
def test_gate_stream_format_and_continuity_refuse(change):
    frames = retained(frame([0xF0], count=8), frame([0x03], first=8, count=8))
    target = frames[1]
    if change == "gap":
        target["sample_id"] += 1
    elif change == "overlap":
        target["sample_id"] -= 1
    elif change == "channel":
        target["index"] = 1
    elif change == "rate":
        target["sample_rate"] = 2000
    elif change == "order":
        target["bit_order"] = "big"
    else:
        target["element_count"] = 9
    with pytest.raises(ValueError):
        counter_gate_trace(frames, packets(), channel=0)


def test_unknown_padding_is_not_inferred_from_next_frame():
    recorder = PackedGateRecorder(0, 10000, sample_mode="first")
    recorder.append(frame([0xF0]))
    assert recorder.error and not recorder.frames


def test_native_word_contract_retains_exact_bytes_and_count():
    recorder = PackedGateRecorder(0, 10000, sample_mode="first")
    recorder.append(frame([0x3C, 0, 0, 0]), word_aligned=True)
    assert recorder.error is None
    saved = json.loads(json.dumps(asdict(recorder)))
    assert saved["frames"][0]["data_hex"] == "3c000000"
    assert saved["frames"][0]["element_count"] == 32
    assert saved["frames"][0]["count_source"] == "native_u32_payload"


def test_retention_failure_is_sticky_and_preserves_prior_frames():
    recorder = PackedGateRecorder(0, 513, sample_mode="first")
    recorder.append(frame([0], count=8))
    recorder.append(frame([0], first=8, count=8))
    first_error = recorder.error
    recorder.append(frame([0], first=16, count=8))
    assert first_error and recorder.error == first_error and len(recorder.frames) == 1


def test_missing_edge_or_statistics_coverage_cannot_yield_a_gate():
    trace = counter_gate_trace(retained(frame([0xFC, 0xFF], count=16)), packets(), channel=0)
    assert not trace.windows
    with pytest.raises(ValueError, match="not observed"):
        trace.window_evidence(2, 16)
    trace = counter_gate_trace(retained(frame([0x3C, 0], count=16)), packets()[3:], channel=0)
    with pytest.raises(ValueError, match="cover gate"):
        trace.window_evidence(2, 6)


@pytest.mark.parametrize("mode", [None, "toggle", "majority"])
def test_gate_mode_must_preserve_actual_sampled_levels(mode):
    recorder = PackedGateRecorder(0, 10000, sample_mode=mode)
    recorder.append(frame([0x3C], count=8))
    assert recorder.error and not recorder.frames


def test_source_array_mutation_does_not_change_retained_evidence():
    source = frame([0x3C], count=8)
    recorder = PackedGateRecorder(0, 10000, sample_mode="first")
    recorder.append(source)
    source["data"][0] = 0
    source["time_map"]["offset_counter"] = 999
    assert recorder.frames[0]["data_hex"] == "3c"
    assert recorder.frames[0]["time_map"]["offset_counter"] == 0
