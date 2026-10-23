"""Real-thread lifecycle characterization; numerical timelines live in test_power."""

from __future__ import annotations

import sys
import threading
from types import SimpleNamespace

import pytest

from helia_profiler.errors import PowerError
from helia_profiler.power.joulescope import _gated_resources as resources
from helia_profiler.power.joulescope import capture_gated as capture
from helia_profiler.power.joulescope.driver import JoulescopeDriver
from tests.test_power import TestGatedStatsProcessing as _StatsPackets
from tests.test_power import _packed_gpi_frame


class _Instrument:
    path = "u/js320/lifecycle"

    def __init__(self):
        self.events = []
        self.callbacks = {}
        self.deliveries = {}
        self.fail_after_subscribe = None
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
        self.deliveries[topic] = callback
        if topic.endswith(str(self.fail_after_subscribe)):
            raise RuntimeError("subscribe registered then failed")

    def unsubscribe(self, topic, callback):
        if topic in self.callbacks:
            assert self.callbacks[topic] is callback
        self._record("unsubscribe", topic)
        self.callbacks.pop(topic, None)

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
            duration_s=kwargs.pop("duration_s", 2),
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
        resources,
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
def test_mandatory_setup_failure_cleans_attempted_resources(instrument, failure):
    instrument.fail_at = failure
    with pytest.raises(PowerError) as raised:
        instrument.run()
    assert isinstance(raised.value.__cause__, RuntimeError)
    assert instrument.events[-1] == ("device", "close", None)
    if failure[1] != "s/stats/scnt":
        assert ("publish", "s/stats/ctrl", 0) in instrument.events
    if failure[0] == "subscribe":
        assert ("unsubscribe", "s/stats/value", None) in instrument.events
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
    for channel in (0, 1):
        topic = f"s/gpi/{channel}/ctrl"
        assert (("publish", topic, 0) in instrument.events) == (
            ("publish", topic, 1) in instrument.events
        )
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


@pytest.mark.parametrize("topic", ["s/stats/value", "s/gpi/0/!data", "s/gpi/1/!data"])
def test_subscribe_that_registers_then_fails_is_unsubscribed(instrument, topic):
    instrument.fail_after_subscribe = topic
    error = LookupError("stop after setup")

    def started(_wait):
        instrument.release_read.set()
        raise error

    expected = PowerError if topic == "s/stats/value" else LookupError
    with pytest.raises(expected):
        instrument.run(require_done_state=True, on_started=started)
    assert not instrument.callbacks
    assert ("unsubscribe", topic, None) in instrument.events
    if topic.startswith("s/gpi/"):
        assert ("publish", topic.replace("!data", "ctrl"), 0) not in instrument.events


@pytest.mark.parametrize("construction", ["recorder", "resources"])
def test_failure_after_device_open_releases_reference(instrument, monkeypatch, construction):
    error = RuntimeError("allocation failed")

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(
        capture,
        "PackedGateRecorder" if construction == "recorder" else "_GatedCaptureResources",
        fail,
    )
    with pytest.raises(RuntimeError) as raised:
        instrument.run()
    assert raised.value is error
    assert instrument.events == [("device", "close", None)]


def test_delayed_high_read_cannot_call_back_after_release(instrument, monkeypatch):
    calls = []
    error = LookupError("start failed")

    def read(*_args):
        instrument.read_entered.set()
        assert instrument.release_read.wait(5)
        return 1

    def started(_wait):
        assert instrument.read_entered.wait(5)
        raise error

    monkeypatch.setattr(capture, "_read_gpi_snapshot", read)
    try:
        with pytest.raises(LookupError) as raised:
            instrument.run(
                on_started=started,
                on_gate_rise=lambda: calls.append("rise"),
                phase_getter=lambda: calls.append("phase") or "go_signaled",
            )
        assert raised.value is error
        before = list(calls)
        assert instrument.events[-1] == ("device", "close", None)
    finally:
        instrument.release_read.set()
        for thread in instrument.threads:
            thread.join(5)
            assert not thread.is_alive()
    assert calls == before


def test_late_stream_callbacks_cannot_mutate_returned_evidence(instrument):
    result = instrument.run()
    packet_count = len(result.statistics)
    frame_count = len(result.gate_frames)
    for topic, callback in instrument.deliveries.items():
        value = (
            _StatsPackets._millisecond_packet(7)
            if topic.endswith("stats/value")
            else _packed_gpi_frame([0, 1, 0], 7)
        )
        callback(topic, value)
    assert len(result.statistics) == packet_count
    assert len(result.gate_frames) == frame_count


@pytest.mark.parametrize("callback_kind", ["phase", "rise", "stream"])
def test_shutdown_drains_inflight_processing_before_device_release(
    instrument, monkeypatch, callback_kind
):
    entered = threading.Event()
    release = threading.Event()
    waiting = threading.Event()
    conditions = []
    returned = threading.Event()
    error = LookupError("start failed during callback")
    outcome = []
    callbacks = []
    original_init = resources._GatedCaptureResources.__init__

    def initialize(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        owner = self

        class ObservedCondition(threading.Condition):
            def wait(self, timeout=None):
                assert owner._processing_count > 0
                waiting.set()
                return super().wait(timeout)

        self._processing_condition = ObservedCondition()
        conditions.append(self._processing_condition)

    def blocked_callback():
        entered.set()
        assert release.wait(10)
        callbacks.append("finished")
        return "go_signaled"

    monkeypatch.setattr(resources._GatedCaptureResources, "__init__", initialize)
    if callback_kind == "stream":
        original_append = capture.PackedGateRecorder.append

        def append(self, *args, **kwargs):
            blocked_callback()
            return original_append(self, *args, **kwargs)

        monkeypatch.setattr(capture.PackedGateRecorder, "append", append)

    stream_worker = None

    def started(_wait):
        nonlocal stream_worker
        instrument.release_read.set()
        if callback_kind == "stream":
            topic = instrument.path + "/s/gpi/0/!data"
            stream_worker = threading.Thread(
                target=instrument.callbacks[topic], args=(topic, _packed_gpi_frame([0, 1, 0], 0))
            )
            stream_worker.start()
        assert entered.wait(5)
        raise error

    def run():
        try:
            kwargs = (
                {"phase_getter": blocked_callback}
                if callback_kind == "phase"
                else {"on_gate_rise": blocked_callback}
                if callback_kind == "rise"
                else {}
            )
            instrument.run(on_started=started, **kwargs)
        except Exception as exc:
            outcome.append(exc)
        finally:
            returned.set()

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert waiting.wait(5), "shutdown never waited for active processing"
        # Acquiring the condition proves wait() actually released it, rather
        # than merely observing the event immediately before wait().
        condition = conditions[0]
        assert condition.acquire(timeout=5), "drain wait did not release the condition"
        try:
            assert not returned.is_set()
            assert ("device", "close", None) not in instrument.events
            release.set()
        finally:
            condition.release()
    finally:
        release.set()
        instrument.release_read.set()
        worker.join(5)
        if stream_worker is not None:
            stream_worker.join(5)
            assert not stream_worker.is_alive()
        assert not worker.is_alive()
    assert outcome == [error]
    assert callbacks == ["finished"]
    assert instrument.events[-1] == ("device", "close", None)


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_synchronous_teardown_deliveries_are_ignored(instrument, monkeypatch, cleanup_fails):
    original_unsubscribe = instrument.unsubscribe
    instrument.cleanup_fails = cleanup_fails

    def unsubscribe(topic, callback):
        value = (
            _StatsPackets._millisecond_packet(7)
            if topic.endswith("stats/value")
            else _packed_gpi_frame([0, 1, 0], 7)
        )
        callback(topic, value)
        original_unsubscribe(topic, callback)

    monkeypatch.setattr(instrument, "unsubscribe", unsubscribe)
    result = instrument.run()
    assert len(result.statistics) == 6
    assert len(result.gate_frames) == 1


def test_late_required_state_delivery_is_ignored(instrument):
    result = instrument.run(duration_s=0, require_done_state=True)
    assert result.state_frames == []
    topic = instrument.path + "/s/gpi/1/!data"
    frame = _packed_gpi_frame([0, 1, 0], 0)
    frame["index"] = 1
    instrument.deliveries[topic](topic, frame)
    assert result.state_frames == []


def test_gate_callback_can_wait_for_delivery_on_another_driver_thread(instrument):
    delivered = threading.Event()

    def release_go():
        topic = instrument.path + "/s/stats/value"

        def deliver():
            instrument.callbacks[topic](topic, _StatsPackets._millisecond_packet(7))
            delivered.set()

        worker = threading.Thread(target=deliver)
        instrument.threads.append(worker)
        worker.start()
        assert delivered.wait(5), "GO publish was blocked by a stream callback"
        worker.join(5)
        assert not worker.is_alive()

    result = instrument.run(on_gate_rise=release_go)
    assert delivered.is_set()
    assert len(result.statistics) == 7


@pytest.mark.parametrize(
    "failure",
    [
        ("publish", "s/stats/scnt", 1000),
        ("publish", "s/stats/ctrl", 1),
        ("subscribe", "s/stats/value", None),
    ],
)
def test_fullrate_request_does_not_own_raw_controls_before_start(instrument, monkeypatch, failure):
    monkeypatch.setenv("HPX_POWER_FULLRATE_XCHECK", "1")
    instrument.fail_at = failure
    with pytest.raises(PowerError):
        instrument.run()
    assert not any(event[1] in {"s/i/ctrl", "s/v/ctrl"} for event in instrument.events)
