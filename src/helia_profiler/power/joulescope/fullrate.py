"""Own optional current/voltage stream copies and their subscription lifecycle."""

from __future__ import annotations

import logging
from threading import Lock
from typing import Any

from .stats import (
    _fullrate_energy_over_windows,
    _fullrate_sample_span,
    _fullrate_streams_contiguous,
)

log = logging.getLogger("hpx")


class FullRateRecorder:
    """Retain callback buffers before the driver reuses them; stop partial setup."""

    def __init__(self, driver: Any, device_path: str) -> None:
        self.driver = driver
        self.device_path = device_path
        self.current: list[Any] = []
        self.voltage: list[Any] = []
        self.anchors: list[tuple[int, int, float]] = []
        self.current_spans: list[tuple[int, int, float] | None] = []
        self.voltage_spans: list[tuple[int, int, float] | None] = []
        self._callback_lock = Lock()
        self._stopped = False
        self._current_count = 0
        self._subscriptions: list[tuple[str, Any]] = []

    def _on_current(self, _topic: str, value: Any) -> None:
        with self._callback_lock:
            if self._stopped:
                return
            import numpy as np

            data = np.asarray(value["data"], dtype=np.float32)
            sr = value["sample_rate"] / max(1, value.get("decimate_factor", 1))
            utc = value.get("utc")
            if utc is not None:
                self.anchors.append((self._current_count, int(utc), float(sr)))
            self.current.append(data.copy())
            self.current_spans.append(_fullrate_sample_span(value, len(data)))
            self._current_count += len(data)

    def _on_voltage(self, _topic: str, value: Any) -> None:
        with self._callback_lock:
            if self._stopped:
                return
            import numpy as np

            data = np.asarray(value["data"], dtype=np.float32)
            self.voltage.append(data.copy())
            self.voltage_spans.append(_fullrate_sample_span(value, len(data)))

    def start(self) -> bool:
        """Enable both channels, rolling back even a partially failed subscribe."""
        with self._callback_lock:
            self._stopped = False
        try:
            for channel, callback in (("i", self._on_current), ("v", self._on_voltage)):
                topic = f"{self.device_path}/s/{channel}/!data"
                self._subscriptions.append((topic, callback))
                self.driver.subscribe(topic, ["pub"], callback)
            self.driver.publish(f"{self.device_path}/s/i/ctrl", 1, timeout=0)
            self.driver.publish(f"{self.device_path}/s/v/ctrl", 1, timeout=0)
            log.info("Joulescope full-rate energy cross-check enabled (s/i + s/v streaming)")
            return True
        except Exception:
            log.warning("Failed to enable full-rate cross-check streaming", exc_info=True)
            self.stop()
            return False

    def stop(self) -> None:
        """Best-effort stop both channels and every attempted subscription."""
        with self._callback_lock:
            self._stopped = True
        for channel in ("i", "v"):
            try:
                self.driver.publish(f"{self.device_path}/s/{channel}/ctrl", 0, timeout=0)
            except Exception:
                log.debug("Failed to stop full-rate %s stream", channel, exc_info=True)
        for topic, callback in list(self._subscriptions):
            try:
                self.driver.unsubscribe(topic, callback)
                self._subscriptions.remove((topic, callback))
            except Exception:
                log.debug("Failed to unsubscribe full-rate stream", exc_info=True)

    def integrate(
        self,
        *,
        poll_samples: list[tuple[int, int]],
        windows: list[tuple[float, float]] | None,
        counter_origin: tuple[int, int] | None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Use complete source counters, or complete UTC anchors on the fallback path."""
        if not _fullrate_streams_contiguous(self.current_spans, self.voltage_spans):
            return None, "noncontiguous_or_unaligned_source_samples"
        if counter_origin is None and not (
            len(self.anchors) == len(self.current)
            and all(nxt[1] > cur[1] for cur, nxt in zip(self.anchors, self.anchors[1:]))
        ):
            return None, "incomplete_or_nonmonotonic_utc_anchors"
        first = self.current_spans[0]
        result = _fullrate_energy_over_windows(
            cur_chunks=self.current,
            volt_chunks=self.voltage,
            anchors=self.anchors,
            poll_samples=poll_samples,
            windows_override=windows,
            counter_axis=(*counter_origin, first[0], first[2])
            if counter_origin is not None and first is not None
            else None,
        )
        return result, None if result else "no_integrable_gate_samples"
