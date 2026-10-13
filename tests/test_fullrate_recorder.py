"""Optional raw-stream ownership tested without opening an instrument driver."""

import numpy as np
import pytest

from helia_profiler.power.joulescope.fullrate import FullRateRecorder


class MemoryDriver:
    def __init__(self, failure=None):
        self.failure = failure
        self.subscriptions = {}
        self.controls = []

    def subscribe(self, topic, flags, callback):
        if self.failure == "before_subscribe" and topic.endswith("/v/!data"):
            raise RuntimeError("before subscription")
        self.subscriptions[topic] = callback
        if self.failure == "after_subscribe" and topic.endswith("/v/!data"):
            raise RuntimeError("after subscription")

    def publish(self, topic, value, **kwargs):
        self.controls.append((topic, value))
        if self.failure == "enable" and topic.endswith("/v/ctrl") and value == 1:
            raise RuntimeError("during enable")

    def unsubscribe(self, topic, callback):
        if self.failure == "unsubscribe_once":
            self.failure = None
            raise RuntimeError("temporary teardown error")
        self.subscriptions.pop(topic, None)


@pytest.mark.parametrize("failure", ["before_subscribe", "after_subscribe", "enable"])
def test_partial_setup_stops_both_channels_and_removes_callbacks(failure):
    driver = MemoryDriver(failure)
    recorder = FullRateRecorder(driver, "memory")
    assert recorder.start() is False
    assert driver.subscriptions == {}
    assert driver.controls[-2:] == [("memory/s/i/ctrl", 0), ("memory/s/v/ctrl", 0)]


def test_successful_streams_copy_owned_buffers_and_stop():
    driver = MemoryDriver()
    recorder = FullRateRecorder(driver, "memory")
    assert recorder.start() is True
    data = np.array([1, 2], dtype=np.float32)
    for topic, callback in driver.subscriptions.items():
        callback(
            topic,
            {
                "data": data,
                "sample_id": 16000,
                "sample_rate": 16000000,
                "decimate_factor": 16000,
                "utc": 7,
            },
        )
    data[:] = 99
    assert recorder.current[0].tolist() == [1, 2]
    assert recorder.voltage[0].tolist() == [1, 2]
    assert recorder.current_spans == recorder.voltage_spans == [(1, 3, 1000)]
    assert recorder.anchors == [(0, 7, 1000)]
    recorder.stop()
    assert driver.subscriptions == {}


def test_cleanup_retries_only_subscription_that_could_not_be_removed():
    driver = MemoryDriver("unsubscribe_once")
    recorder = FullRateRecorder(driver, "memory")
    assert recorder.start() is True
    recorder.stop()
    assert list(driver.subscriptions) == ["memory/s/i/!data"]
    recorder.stop()
    assert not driver.subscriptions


@pytest.mark.parametrize("anchors", [[], [(0, 7, 1000), (1, 7, 1000)]])
def test_utc_fallback_rejects_missing_or_nonmonotonic_anchors(anchors):
    recorder = FullRateRecorder(MemoryDriver(), "memory")
    recorder.current[:] = [np.ones(1), np.ones(1)]
    recorder.voltage[:] = [np.ones(1), np.ones(1)]
    recorder.current_spans[:] = recorder.voltage_spans[:] = [(0, 1, 1000), (1, 2, 1000)]
    recorder.anchors[:] = anchors
    result, reason = recorder.integrate(poll_samples=[], windows=None, counter_origin=None)
    assert result is None
    assert reason == "incomplete_or_nonmonotonic_utc_anchors"


def test_unknown_source_span_is_not_integrated():
    recorder = FullRateRecorder(MemoryDriver(), "memory")
    recorder.current_spans[:] = [None]
    recorder.voltage_spans[:] = [(0, 1, 1000)]
    result, reason = recorder.integrate(poll_samples=[], windows=None, counter_origin=(0, 16000000))
    assert result is None
    assert reason == "noncontiguous_or_unaligned_source_samples"
