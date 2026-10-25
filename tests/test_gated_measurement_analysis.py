"""Device-free selection and reduction controls using retained packet integrals."""

from __future__ import annotations

import sys
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from helia_profiler.errors import PowerError
from helia_profiler.power.counter_gate import PackedGateRecorder
from helia_profiler.power.joulescope._gated_result import (
    _CaptureInfo,
    _MeasurementPlan,
    assemble_result,
    reduce_measurement,
)
from helia_profiler.power.joulescope._gated_selection import select_gate
from helia_profiler.power.joulescope._gated_state import _GateEvidence
from tests.test_power import _SECOND, _packed_gpi_frame
from tests.test_power import TestGatedStatsProcessing as _Packets


@pytest.fixture
def retained(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "pyjoulescope_driver", SimpleNamespace(time64=SimpleNamespace(SECOND=_SECOND))
    )
    packets = [_Packets._millisecond_packet(i) for i in range(10)]
    for packet in packets:
        a, b = packet["time"]["utc"]["value"]
        packet["_host_time64"] = (a + b) // 2
    recorder = PackedGateRecorder(0, 100_000, sample_mode="first")
    recorder.append(_packed_gpi_frame([0, 0, 1, 1, 1, 1, 0, 0, 0, 0], 0))
    assert recorder.error is None
    evidence = _GateEvidence(
        poll_samples=tuple((round(i * _SECOND / 1000), int(2 <= i < 6)) for i in range(10)),
        poll_reads=(),
        first_high_at=0.002,
        first_low_after_high_at=0.006,
        saw_any_gate_rise=True,
        saw_any_gate_fall=True,
        short_pulses_ignored=0,
        short_pulse_phases=(),
        short_pulse_first_s=None,
        short_pulse_last_s=None,
        below_plan_highs=0,
        below_plan_rise_at=None,
        below_plan_fall_at=None,
        early_stopped=True,
    )
    info = _CaptureInfo("js320", "retained", 1.8, 0, 1000, 16000, 0.020)
    plan = _MeasurementPlan(0, 0, None, None, 0.01, "inferences", False, False, 0)
    return packets, recorder.frames, evidence, info, plan


def select(packets, frames, evidence, info, *, enabled, error=None):
    return select_gate(
        packets=packets,
        gpi_stream_frames=frames,
        state_frames=[],
        poll_samples=list(evidence.poll_samples),
        family=info.family,
        sync_input_index=0,
        state_input_index=1,
        statistics_timebase_hz=_SECOND,
        minimum_gate_s=0,
        plan_floor_s=0,
        elapsed_high_s=None,
        saw_any_gate_fall=evidence.saw_any_gate_fall,
        gpi_stream_enabled=enabled,
        require_done_state=False,
        gate_error=error,
        state_error=None,
    )


def reduce(packets, selected, evidence, info, plan, enabled):
    return reduce_measurement(
        packets=packets,
        selection=selected,
        evidence=evidence,
        info=info,
        plan=plan,
        gpi_stream_enabled=enabled,
        gate_retained_bytes=0,
        gpi_poll_failures=0,
        wait_ended_at=0.010,
        fr_requested=False,
        fr_xcheck=False,
    )


@pytest.mark.parametrize("family,enabled", [("js110", False), ("js220", True), ("js320", True)])
def test_retained_analysis_preserves_packet_integrals_and_inputs(retained, family, enabled):
    packets, frames, evidence, info, plan = retained
    info = replace(info, family=family)
    before = deepcopy((packets, frames))
    selected = select(packets, frames, evidence, info, enabled=enabled)
    result = reduce(packets, selected, evidence, info, plan, enabled)
    assert len(result.windows) == 1
    assert result.summary.duration_s == 0.004
    assert result.summary.energy_j == pytest.approx(0.00072, rel=1e-12)
    assert result.windows[0].charge_c == pytest.approx(0.0004, rel=1e-12)
    assert result.summary.sample_count == 4
    assert result.diagnostics["selected_packets"] == 4
    assert bool(selected.counter_evidence) is enabled
    assert (packets, frames) == before


def test_invalid_stream_forces_empty_selection_instead_of_poll_fallback(retained):
    packets, frames, evidence, info, plan = retained
    unavailable = select(packets, [], evidence, info, enabled=False)
    invalid = select(packets, frames, evidence, info, enabled=True, error="retained gate invalid")
    assert unavailable.selected_windows is None
    assert invalid.selected_windows == []
    assert invalid.gate_error == "retained gate invalid"
    assert reduce(packets, unavailable, evidence, info, plan, False).windows
    reduced = reduce(packets, invalid, evidence, info, plan, True)
    assert not reduced.windows
    assert reduced.failure is not None
    result = assemble_result(
        packets=packets,
        gpi_stream_frames=frames,
        state_frames=[],
        state_error=None,
        selection=invalid,
        reduced=reduced,
        evidence=evidence,
        info=info,
        plan=plan,
        capture_start=0,
        go_release_at=0,
        captured_s=0.020,
        statistics_timebase_hz=_SECOND,
        fr_xcheck=False,
        fr=None,
        reason=None,
        fullrate_chunks=0,
        fullrate_anchors=0,
    )
    assert result.statistics is packets
    assert result.gate_frames is frames
    assert result.gate_stream_error == "retained gate invalid"
    assert result.metadata.sync_timing_s is None
    assert result.metadata.capture_window_s == 0.020


def test_missing_stats_retains_hard_failure(retained):
    _packets, _frames, evidence, info, plan = retained
    evidence = replace(
        evidence,
        poll_samples=(),
        saw_any_gate_rise=False,
        saw_any_gate_fall=False,
        first_high_at=None,
        first_low_after_high_at=None,
    )
    selected = select([], [], evidence, info, enabled=False)
    with pytest.raises(PowerError):
        reduce([], selected, evidence, info, plan, False)
