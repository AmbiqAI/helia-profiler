"""Gate counter duration stays observable independently of fitted UTC mapping."""

from copy import deepcopy

import pytest

from helia_profiler.power.counter_gate import counter_gate_trace


def _evidence():
    clock = {"offset_counter": 0, "offset_time": 0, "counter_rate": 1000}
    frames = [
        {
            "sample_id": 0,
            "sample_rate": 1000,
            "decimate_factor": 1,
            "field_id": 5,
            "index": 0,
            "utc": 0,
            "time_map": clock,
            "data_hex": "3c",
            "element_count": 8,
            "byte_length": 1,
            "count_source": "element_count",
            "bit_order": "little",
            "sample_mode": "first",
        }
    ]
    packets = [
        {
            "time": {
                "samples": {"value": [i, i + 1]},
                "sample_freq": {"value": 1000},
                "utc": {"value": [i * 1000, (i + 1) * 1000]},
                "time_map": deepcopy(clock),
            }
        }
        for i in range(8)
    ]
    return frames, packets


def test_steady_counter_stream_reports_duration_and_gate_resolution():
    trace = counter_gate_trace(*_evidence(), channel=0)
    assert trace.rate == 1000 and trace.edge_resolution == 1
    assert trace.clock_diagnostics["fitted_map_changes"] == 0
    assert trace.window_evidence(2, 6)["gate_duration_s"] == 0.004


def test_map_change_is_reported_without_rescaling_sample_duration():
    frames, packets = _evidence()
    for p in packets[4:]:
        p["time"]["time_map"]["offset_time"] = 535
        p["time"]["utc"]["value"] = [v + 535 for v in p["time"]["utc"]["value"]]
    trace = counter_gate_trace(frames, packets, channel=0)
    assert trace.clock_diagnostics["utc_boundary_discontinuities_ticks"] == [535]
    assert trace.window_evidence(2, 6)["integrated_duration_s"] == 0.004


def test_dropped_counter_interval_refuses_instead_of_median_smoothing():
    frames, packets = _evidence()
    packets.pop(4)
    with pytest.raises(ValueError, match="Discontinuous"):
        counter_gate_trace(frames, packets, channel=0)


def test_empty_frames_and_missing_clock_metadata_refuse():
    frames, packets = _evidence()
    with pytest.raises(ValueError, match="Missing"):
        counter_gate_trace([], packets, channel=0)
    frames[0]["time_map"]["counter_rate"] = float("nan")
    with pytest.raises(ValueError, match="clock"):
        counter_gate_trace(frames, packets, channel=0)
