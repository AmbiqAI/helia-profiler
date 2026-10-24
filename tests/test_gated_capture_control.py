"""Capture-level controls for gate completion and latest-sample READY waiting."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from helia_profiler.power.joulescope import capture_gated as capture
from helia_profiler.power.joulescope.driver import JoulescopeDriver
from tests import test_gated_capture_lifecycle as _lifecycle
from tests.test_power import (
    TestImplausibleGateRejection as _CaptureCases,
)
from tests.test_power import (
    _install_fake_capture_clock,
    _packed_gpi_frame,
)

instrument = _lifecycle.instrument


@pytest.mark.parametrize("invalid_at,expected_stop_ms", [(None, 10), (9, 14)])
def test_completion_guard_restarts_after_invalid_retained_evidence(
    monkeypatch, invalid_at, expected_stop_ms
):
    class Instrument(_CaptureCases._LiveInstrument):
        def poll(self):
            t = self.now_ms
            state = int(t == 1 or t >= 6)
            state_cb = self._cb("/s/gpi/1/!data")
            if state_cb is not None:
                frame = _packed_gpi_frame([state], t)
                frame["index"] = 1
                state_cb("u/js320/test/s/gpi/1/!data", frame)
            return super().poll() | (state << 1)

    fake = Instrument([(2, 4)])
    clock = _install_fake_capture_clock(monkeypatch)
    monkeypatch.setattr(capture, "_open_device", lambda _serial: (fake, "u/js320/test", "js320"))
    monkeypatch.setattr(capture, "_close_device", lambda *_args: None)
    monkeypatch.setattr(capture, "_read_gpi_snapshot", lambda *_args: fake.poll())
    resolve = capture.resolve_counter_completion
    validations = []

    def validate(gate, state):
        tick = fake.now_ms - 1
        validations.append(tick)
        if tick == invalid_at:
            raise ValueError("retained evidence temporarily invalid")
        return resolve(gate, state)

    monkeypatch.setattr(capture, "resolve_counter_completion", validate)
    result = JoulescopeDriver().capture_gated(
        duration_s=0.030,
        io_voltage=1.8,
        sync_input_index=0,
        state_input_index=1,
        require_done_state=True,
        poll_interval_s=0.001,
        guard_s=0.004,
    )
    assert result.metadata.early_stopped
    assert clock.elapsed_s == expected_stop_ms / 1000
    assert result.gated_windows[0].duration_s == 0.004
    assert 6 in validations and 7 in validations
    if invalid_at is not None:
        assert invalid_at in validations and invalid_at + 1 in validations


@pytest.mark.parametrize(
    "groups,expected_waits",
    [([(2, 0, 2), (2,), (2,)], 3), ([(2,), (0,), (2,), (2,), (2,)], 5)],
)
def test_ready_consumes_latest_values_without_replaying_skipped_polls(
    instrument, monkeypatch, groups, expected_waits
):
    permits = threading.Semaphore(0)
    levels = iter(value for group in groups for value in group)
    conditions = []
    stop_polling = threading.Event()

    class Condition(threading.Condition):
        def __init__(self):
            super().__init__()
            self.delivered = 0
            self.waits = 0
            conditions.append(self)

        def notify_all(self):
            self.delivered += 1
            super().notify_all()

        def wait_for(self, predicate, timeout=None):
            assert self.waits < len(groups), "READY failed to consume expected observations"
            count = len(groups[self.waits])
            self.waits += 1
            target = self.delivered + count
            for _ in range(count):
                permits.release()
            return super().wait_for(lambda: self.delivered >= target and predicate(), timeout)

    def read(*_args):
        assert permits.acquire(timeout=5), "test did not release the fake read"
        if stop_polling.is_set():
            return 0
        return next(levels)

    def started(wait):
        try:
            assert wait(1, True, 2)
            assert conditions[0].waits == expected_waits
            instrument.emit()
        finally:
            stop_polling.set()
            permits.release()
        # End via the unchanged hook-error path; no measurement loop is needed.
        raise LookupError("READY observed")

    monkeypatch.setattr(capture, "_read_gpi_snapshot", read)
    monkeypatch.setattr(
        capture, "threading", SimpleNamespace(Event=threading.Event, Condition=Condition)
    )
    try:
        with pytest.raises(LookupError, match="READY observed"):
            instrument.run(on_started=started, phase_getter=lambda: "waiting_ready")
    finally:
        stop_polling.set()
        permits.release()
