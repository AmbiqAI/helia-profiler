"""Capture-level characterization of post-drain measurement decisions and effects."""

from __future__ import annotations

import pytest

from helia_profiler.errors import PowerError
from helia_profiler.power.joulescope import capture_gated as capture
from helia_profiler.power.joulescope.fullrate import FullRateRecorder
from tests.test_power import TestImplausibleGateRejection as _CaptureCases
from tests.test_power import _install_fake_capture_clock, _packed_gpi_frame


class _Instrument(_CaptureCases._LiveInstrument):
    def __init__(self, *, failure=None):
        super().__init__([(2, 4)])
        self.failure = failure

    def subscribe(self, topic, flags, callback):
        if self.failure == "gate_setup" and "/s/gpi/0/" in topic:
            raise RuntimeError("gate setup failed")
        if self.failure == "state_setup" and "/s/gpi/1/" in topic:
            raise RuntimeError("state setup failed")
        return super().subscribe(topic, flags, callback)

    def poll(self):
        t = self.now_ms
        state = int(t == 1 or t >= 6)
        state_cb = self._cb("/s/gpi/1/!data")
        if state_cb is not None:
            frame = _packed_gpi_frame([state], t)
            frame["index"] = 1 if self.failure != "state_invalid" else 2
            state_cb("u/js320/test/s/gpi/1/!data", frame)
        return super().poll() | (state << 1)


@pytest.fixture
def measurement(monkeypatch):
    monkeypatch.delenv("HPX_POWER_FULLRATE_XCHECK", raising=False)
    monkeypatch.delenv("HPX_GATE_DEBUG_DUMP", raising=False)
    clock = _install_fake_capture_clock(monkeypatch)
    events = []

    def run(*, failure=None, required=False):
        fake = _Instrument(failure=failure)
        monkeypatch.setattr(
            capture, "_open_device", lambda _serial: (fake, "u/js320/test", "js320")
        )
        monkeypatch.setattr(capture, "_close_device", lambda *_args: events.append("close"))
        monkeypatch.setattr(capture, "_read_gpi_snapshot", lambda *_args: fake.poll())
        return capture.capture_gated(
            type("Driver", (), {"_serial": None})(),
            duration_s=0.020,
            io_voltage=1.8,
            sync_input_index=0,
            require_done_state=required,
            poll_interval_s=0.001,
            guard_s=0,
        )

    return run, clock, events


@pytest.mark.parametrize("failure", [None, "gate_setup", "state_setup", "state_invalid"])
def test_required_done_keeps_stream_setup_and_validation_outcomes_distinct(measurement, failure):
    run, _clock, _events = measurement
    result = run(failure=failure, required=True)
    if failure in (None, "gate_setup"):
        assert result.gated_windows[0].duration_s == 0.004
        assert result.metadata.window_count == 1
        assert bool(result.counter_gate_evidence) is (failure is None)
        assert result.metadata.gating_method == (
            "gpi_stream_sample_counter+host_stats_integral"
            if failure is None
            else "gpi_snapshot_poll+host_stats_integral"
        )
    else:
        assert result.gated_windows == []
        assert result.metadata.gate_failure is not None
        assert result.gate_stream_error == "Invalid retained completion stream"
        assert result.state_stream_error
    if failure == "gate_setup":
        assert result.state_stream_error == "Required state stream could not be enabled"


def test_capture_duration_includes_reduction_but_excludes_fullrate(measurement, monkeypatch):
    run, clock, events = measurement
    original = capture._process_gated_stats

    def reduce(**kwargs):
        events.append("reduce")
        result = original(**kwargs)
        clock.now_us += 2000
        return result

    def integrate(self, **kwargs):
        events.append("fullrate")
        clock.now_us += 3000
        return None, "no_integrable_gate_samples"

    monkeypatch.setattr(capture, "_process_gated_stats", reduce)
    monkeypatch.setenv("HPX_POWER_FULLRATE_XCHECK", "1")
    monkeypatch.setattr(FullRateRecorder, "start", lambda self: True)
    monkeypatch.setattr(FullRateRecorder, "integrate", integrate)
    result = run()
    assert result.metadata.capture_window_s == 0.008
    assert clock.elapsed_s == 0.011
    assert events == ["reduce", "fullrate", "close"]


def test_debug_dump_precedes_reduction_error_and_device_release(measurement, monkeypatch):
    run, _clock, events = measurement
    error = PowerError("reduction failed")

    def reduce(**kwargs):
        events.append("reduce")
        raise error

    monkeypatch.setenv("HPX_GATE_DEBUG_DUMP", "unused-by-fake")
    monkeypatch.setattr(capture, "_write_gate_dump", lambda *args, **kwargs: events.append("dump"))
    monkeypatch.setattr(capture, "_process_gated_stats", reduce)
    with pytest.raises(PowerError) as raised:
        run()
    assert raised.value is error
    assert events == ["dump", "reduce", "close"]
