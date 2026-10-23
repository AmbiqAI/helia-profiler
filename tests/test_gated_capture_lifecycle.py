"""Real-thread lifecycle characterization; numerical timelines live in test_power."""

from __future__ import annotations

import sys
import threading
from types import SimpleNamespace

import pytest

from helia_profiler.errors import PowerError
from helia_profiler.power.joulescope import capture_gated as capture
from helia_profiler.power.joulescope.driver import JoulescopeDriver
from tests.test_power import TestGatedStatsProcessing as _StatsPackets
from tests.test_power import _packed_gpi_frame


class _Instrument:
    path = "u/js320/lifecycle"

    def __init__(self):
        self.events = []
        self.callbacks = {}
        self.fail_at = None
        self.cleanup_fails = False
        self.read_entered = threading.Event()
        self.release_read = threading.Event()
        self.poller_exited = threading.Event()
        self.threads = []
        self.reads = 0
        self.read_error = False

    def _record(self, action, topic, value=None):
        event = (action, topic.removeprefix(self.path + "/"), value)
        self.events.append(event)
        if event == self.fail_at or (
            self.cleanup_fails and (action == "unsubscribe" or action == "publish" and value == 0)
        ):
            raise RuntimeError("instrument operation failed")

    def query(self, topic):
        self._record("query", topic)
        return "first"

    def publish(self, topic, value, **kwargs):
        self._record("publish", topic, value)

    def subscribe(self, topic, flags, callback):
        self._record("subscribe", topic)
        self.callbacks[topic] = callback

    def unsubscribe(self, topic, callback):
        assert self.callbacks[topic] is callback
        self._record("unsubscribe", topic)
        del self.callbacks[topic]

    def read(self, *_args):
        self.reads += 1
        self.read_entered.set()
        assert self.release_read.wait(5), "test did not release the fake instrument"
        if self.read_error and self.reads == 1:
            raise RuntimeError("snapshot unavailable")
        return int(self.reads == 2)

    def thread(self, target, daemon=False):
        def run():
            self.events.append(("poller", "enter", None))
            try:
                target()
            finally:
                self.events.append(("poller", "exit", None))
                self.poller_exited.set()

        worker = threading.Thread(target=run, daemon=daemon)
        self.threads.append(worker)
        return worker

    def emit(self):
        for i in range(6):
            topic = self.path + "/s/stats/value"
            self.callbacks[topic](topic, _StatsPackets._millisecond_packet(i))
        topic = self.path + "/s/gpi/0/!data"
        if topic in self.callbacks:
            self.callbacks[topic](topic, _packed_gpi_frame([0, 1, 1, 1, 0, 0], 0))

    def started(self, _wait):
        assert self.read_entered.wait(5)
        self.events.append(("hook", "started", None))
        self.emit()
        self.release_read.set()

    def close(self, *_args):
        self.events.append(("device", "close", None))

    def run(self, **kwargs):
        return JoulescopeDriver().capture_gated(
            duration_s=2,
            io_voltage=1.8,
            sync_input_index=0,
            guard_s=0,
            poll_interval_s=0,
            on_started=kwargs.pop("on_started", self.started),
            **kwargs,
        )


@pytest.fixture
def instrument(monkeypatch):
    fake = _Instrument()
    monkeypatch.delenv("HPX_POWER_FULLRATE_XCHECK", raising=False)
    monkeypatch.delenv("HPX_GATE_DEBUG_DUMP", raising=False)
    monkeypatch.setitem(
        sys.modules, "pyjoulescope_driver", SimpleNamespace(time64=SimpleNamespace(SECOND=1 << 30))
    )
    monkeypatch.setattr(capture, "_open_device", lambda _serial: (fake, fake.path, "js320"))
    monkeypatch.setattr(capture, "_close_device", fake.close)
    monkeypatch.setattr(capture, "_read_gpi_snapshot", fake.read)
    monkeypatch.setattr(
        capture,
        "threading",
        SimpleNamespace(Thread=fake.thread, Event=threading.Event, Condition=threading.Condition),
    )
    try:
        yield fake
    finally:
        fake.release_read.set()
        for worker in fake.threads:
            worker.join(5)
            assert not worker.is_alive(), "fake poller leaked"
        fake.callbacks.clear()


def test_streams_and_poller_precede_start_hook_and_stop_before_close(instrument):
    result = instrument.run()
    assert result.gated_windows
    events = instrument.events
    assert events.index(("subscribe", "s/gpi/0/!data", None)) < events.index(
        ("poller", "enter", None)
    )
    assert events.index(("poller", "enter", None)) < events.index(("hook", "started", None))
    assert events[-6:] == [
        ("poller", "exit", None),
        ("publish", "s/stats/ctrl", 0),
        ("unsubscribe", "s/stats/value", None),
        ("publish", "s/gpi/0/ctrl", 0),
        ("unsubscribe", "s/gpi/0/!data", None),
        ("device", "close", None),
    ]
    assert not instrument.callbacks


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_start_hook_error_wins_over_callback_error_and_cleanup(instrument, cleanup_fails):
    start_error = LookupError("start hook")
    callback_error = ValueError("phase callback")
    instrument.cleanup_fails = cleanup_fails

    def phase():
        raise callback_error

    def started(_wait):
        assert instrument.poller_exited.wait(5)
        raise start_error

    with pytest.raises(LookupError) as raised:
        instrument.run(on_started=started, phase_getter=phase)
    assert raised.value is start_error
    assert instrument.events[-1] == ("device", "close", None)
    assert ("unsubscribe", "s/gpi/0/!data", None) in instrument.events


@pytest.mark.parametrize("source", ["phase", "rise"])
def test_poller_callback_error_propagates_unchanged(instrument, source):
    error = ValueError("callback failed")

    def fail():
        raise error

    if source == "phase":
        kwargs = {"phase_getter": fail, "on_started": lambda _wait: None}
    else:
        kwargs = {"on_gate_rise": fail}
    with pytest.raises(ValueError) as raised:
        instrument.run(**kwargs)
    assert raised.value is error
    assert instrument.poller_exited.is_set()
    assert not instrument.callbacks


def test_snapshot_error_is_counted_and_later_polls_complete(instrument):
    instrument.read_error = True
    result = instrument.run()
    assert result.metadata.gating_diagnostics["gpi_poll_failures"] == 1
    assert result.metadata.early_stopped


@pytest.mark.parametrize(
    "failure",
    [
        ("publish", "s/stats/scnt", 1000),
        ("publish", "s/stats/ctrl", 1),
        ("subscribe", "s/stats/value", None),
    ],
)
def test_mandatory_setup_failure_only_closes_device(instrument, failure):
    instrument.fail_at = failure
    with pytest.raises(PowerError) as raised:
        instrument.run()
    assert isinstance(raised.value.__cause__, RuntimeError)
    assert instrument.events[-1] == ("device", "close", None)
    assert ("publish", "s/stats/ctrl", 0) not in instrument.events
    assert not instrument.threads


@pytest.mark.parametrize(
    "failure",
    [
        ("subscribe", "s/gpi/0/!data", None),
        ("publish", "s/gpi/0/ctrl", 1),
        ("subscribe", "s/gpi/1/!data", None),
        ("publish", "s/gpi/1/ctrl", 1),
    ],
)
def test_partial_optional_stream_setup_cleans_successful_subscriptions(instrument, failure):
    instrument.fail_at = failure
    error = LookupError("stop after setup")

    def started(_wait):
        instrument.release_read.set()
        raise error

    with pytest.raises(LookupError) as raised:
        instrument.run(require_done_state=True, on_started=started)
    assert raised.value is error
    assert not instrument.callbacks
    assert instrument.events[-1] == ("device", "close", None)


def test_blocked_read_outlives_bounded_join_but_test_releases_it(instrument):
    error = LookupError("start failed while read blocked")

    def started(_wait):
        assert instrument.read_entered.wait(5)
        raise error

    try:
        with pytest.raises(LookupError) as raised:
            instrument.run(on_started=started)
        assert raised.value is error
        assert instrument.events[-1] == ("device", "close", None)
        assert not instrument.poller_exited.is_set()
    finally:
        instrument.release_read.set()
        for worker in instrument.threads:
            worker.join(5)
            assert not worker.is_alive()
