"""Gated Joulescope stream setup, processing drain and poller teardown."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import Condition
from typing import Any

from ..counter_gate import PackedGateRecorder
from .device import _HOST_STATS, _POWER_CYCLE
from .fullrate import FullRateRecorder

log = logging.getLogger("hpx")


class _GatedCaptureResources:
    """Own stream attempts, poller lifetime and the shutdown processing fence."""

    def __init__(self, driver: Any, device_path: str, family: str) -> None:
        self.driver = driver
        self.device_path = device_path
        self.family = family
        self._processing_condition = Condition()
        self._processing_count = 0
        self._stopped = False
        self.fullrate = FullRateRecorder(driver, device_path)
        self.stats_ctrl_attempted = False
        self.stats_subscribe_attempted = False
        self.fullrate_start_attempted = False
        self.thread: threading.Thread | None = None
        self.gpi_subscribe_attempted = False
        self.gpi_ctrl_attempted = False
        self.state_subscribe_attempted = False
        self.state_ctrl_attempted = False
        self.gpi_stream_enabled = False
        self.fr_xcheck = False

    @contextmanager
    def processing(self) -> Iterator[bool]:
        # Application callbacks can publish to the driver, which may deliver
        # stream callbacks on another thread. Never hold this lock across work.
        with self._processing_condition:
            active = not self._stopped
            if active:
                self._processing_count += 1
        try:
            yield active
        finally:
            if active:
                with self._processing_condition:
                    self._processing_count -= 1
                    self._processing_condition.notify_all()

    def start_streams(
        self,
        *,
        scnt: int,
        sync_input_index: int,
        state_input_index: int,
        require_done_state: bool,
        on_stats: Callable[[str, Any], None],
        on_gpi_data: Callable[[str, Any], None],
        on_state_data: Callable[[str, Any], None],
        state_recorder: PackedGateRecorder,
        fullrate_requested: bool,
    ) -> None:
        driver, device_path, family = self.driver, self.device_path, self.family
        cycle_topic, _off_value, on_value = _POWER_CYCLE[family]
        scnt_topic, sctrl_topic, sval_topic = _HOST_STATS[family]
        self.on_stats = on_stats
        self.on_gpi_data = on_gpi_data
        self.on_state_data = on_state_data
        self.stats_ctrl_topic = f"{device_path}/{sctrl_topic}"
        self.stats_data_topic = f"{device_path}/{sval_topic}"
        self.gpi_data_topic = f"{device_path}/s/gpi/{sync_input_index}/!data"
        self.gpi_ctrl_topic = f"{device_path}/s/gpi/{sync_input_index}/ctrl"
        self.state_data_topic = f"{device_path}/s/gpi/{state_input_index}/!data"
        self.state_ctrl_topic = f"{device_path}/s/gpi/{state_input_index}/ctrl"
        try:
            driver.publish(f"{device_path}/{cycle_topic}", on_value)
        except Exception:
            pass

        driver.publish(f"{device_path}/{scnt_topic}", scnt)
        self.stats_ctrl_attempted = True
        driver.publish(self.stats_ctrl_topic, 1)
        self.stats_subscribe_attempted = True
        driver.subscribe(self.stats_data_topic, "pub", self.on_stats)
        if family in ("js220", "js320"):
            try:
                self.gpi_subscribe_attempted = True
                driver.subscribe(self.gpi_data_topic, "pub", self.on_gpi_data)
                self.gpi_ctrl_attempted = True
                driver.publish(self.gpi_ctrl_topic, 1)
                self.gpi_stream_enabled = True
                if require_done_state:
                    self.state_subscribe_attempted = True
                    driver.subscribe(self.state_data_topic, "pub", self.on_state_data)
                    self.state_ctrl_attempted = True
                    driver.publish(self.state_ctrl_topic, 1)
            except Exception:
                if require_done_state:
                    state_recorder.error = "Required state stream could not be enabled"
                log.warning(
                    "GPI edge streaming unavailable on %s; gate edges fall "
                    "back to snapshot polling",
                    device_path,
                    exc_info=True,
                )
        if fullrate_requested:
            self.fullrate_start_attempted = True
            self.fr_xcheck = self.fullrate.start()

    def start_poller(self, poller: Callable[[], None]) -> None:
        self.thread = threading.Thread(target=poller, daemon=True)
        self.thread.start()
        log.debug("gate-race timeline: GPI poller thread started t=%.3f", time.time())

    def stop(self, stop: threading.Event) -> None:
        # Drain processing already in flight, then reject delayed reads and
        # deliveries before vendor teardown, which may itself invoke callbacks.
        stop.set()
        with self._processing_condition:
            self._stopped = True
            self._processing_condition.wait_for(lambda: self._processing_count == 0)
        try:
            if self.thread is not None:
                self.thread.join(timeout=1.0)
        except Exception:
            pass
        if self.stats_ctrl_attempted:
            try:
                self.driver.publish(self.stats_ctrl_topic, 0)
            except Exception:
                pass
        if self.stats_subscribe_attempted:
            try:
                self.driver.unsubscribe(self.stats_data_topic, self.on_stats)
            except Exception:
                pass
        if self.gpi_ctrl_attempted:
            try:
                self.driver.publish(self.gpi_ctrl_topic, 0)
            except Exception:
                pass
        if self.gpi_subscribe_attempted:
            try:
                self.driver.unsubscribe(self.gpi_data_topic, self.on_gpi_data)
            except Exception:
                pass
        if self.state_ctrl_attempted:
            try:
                self.driver.publish(self.state_ctrl_topic, 0)
            except Exception:
                pass
        if self.state_subscribe_attempted:
            try:
                self.driver.unsubscribe(self.state_data_topic, self.on_state_data)
            except Exception:
                pass
        if self.fullrate_start_attempted:
            self.fullrate.stop()
