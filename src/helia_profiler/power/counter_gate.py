"""Packed gate evidence and coverage on a shared device sample counter.

Clock fits are retained diagnostics. They do not define sample duration or
license a missing interval in either the gate or statistics stream.
"""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass, field
from numbers import Integral
from typing import Any


def _integer(value: Any, name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f"Invalid {name}")
    return int(value)


def _clock_map(value: dict[str, Any]) -> None:
    _integer(value["offset_counter"], "clock counter")
    if type(value["offset_time"]) is not int:
        raise ValueError("Invalid clock offset")
    rate = value["counter_rate"]
    if type(rate) not in (int, float) or not math.isfinite(rate) or rate <= 0:
        raise ValueError("Invalid fitted clock rate")


@dataclass
class PackedGateRecorder:
    """Copy bounded original frames; any dropped evidence makes the trace invalid."""

    channel: int
    max_bytes: int
    sample_mode: str | None = None
    frames: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    retained_bytes: int = 0

    def append(self, value: dict[str, Any], *, word_aligned: bool = False) -> None:
        if self.error is not None:
            return
        try:
            import numpy as np

            data = value["data"]
            if not isinstance(data, np.ndarray) or data.dtype != np.uint8 or data.ndim != 1:
                raise ValueError("GPI requires packed UINT1 bytes")
            size = int(data.size)
            # Include a fixed metadata allowance and cap frame count as well as payload.
            cost = size + 512
            if not size or self.retained_bytes + cost > self.max_bytes:
                raise ValueError("GPI evidence retention bound exceeded or empty frame")
            frame = {
                key: deepcopy(value[key])
                for key in (
                    "sample_id",
                    "utc",
                    "field_id",
                    "index",
                    "sample_rate",
                    "decimate_factor",
                    "time_map",
                )
            }
            count = value.get("element_count")
            if count is None:
                # JS320 GPI USB payloads are complete u32 words (32 UINT1 samples).
                # The Python binding omits element_count. Never infer tail padding
                # for a stream whose native packet contract does not establish it.
                if not word_aligned or size % 4:
                    raise ValueError("GPI valid sample count unavailable")
                count = size * 8
                source = "native_u32_payload"
            else:
                source = "element_count"
            frame.update(
                data_hex=data.tobytes().hex(),
                byte_length=size,
                element_count=count,
                count_source=source,
                bit_order="little",
                sample_mode=self.sample_mode,
            )
            _validate_frame(frame, self.channel)
            self.frames.append(frame)
            self.retained_bytes += cost
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            self.error = f"GPI evidence unavailable: {exc}"


def _validate_frame(frame: dict[str, Any], channel: int) -> bytes:
    raw = bytes.fromhex(frame["data_hex"])
    if frame["field_id"] != 5 or frame["index"] != channel or frame["bit_order"] != "little":
        raise ValueError("Incompatible GPI channel or format")
    size = _integer(frame["byte_length"], "GPI byte length", 1)
    count = _integer(frame["element_count"], "GPI sample count", 1)
    if len(raw) != size or (count + 7) // 8 != size:
        raise ValueError("GPI count/padding mismatch")
    if frame["count_source"] == "native_u32_payload":
        if size % 4 or count != size * 8:
            raise ValueError("Invalid native word sample count")
    elif frame["count_source"] != "element_count":
        raise ValueError("Unknown GPI count source")
    _integer(frame["sample_id"], "GPI sample counter")
    _integer(frame["sample_rate"], "GPI counter frequency", 1)
    step = _integer(frame["decimate_factor"], "GPI counter increment", 1)
    if frame["sample_mode"] not in ("first", "off") or (
        frame["sample_mode"] == "off" and step != 1
    ):
        raise ValueError("GPI mode does not preserve sampled levels")
    if type(frame["utc"]) is not int:
        raise ValueError("Missing GPI fitted timestamp")
    _clock_map(frame["time_map"])
    return raw


@dataclass(frozen=True)
class CounterGateTrace:
    rate: int
    origin: int
    windows: tuple[tuple[int, int], ...]
    edge_resolution: int
    intervals: tuple[tuple[int, int], ...]
    clock_diagnostics: dict[str, Any]

    def window_evidence(self, rise: int, fall: int) -> dict[str, Any]:
        if (rise, fall) not in self.windows:
            raise ValueError("Selected gate edges were not observed")
        if rise - self.edge_resolution < self.intervals[0][0] or fall > self.intervals[-1][1]:
            raise ValueError("Statistics do not cover gate edge brackets")
        selected = [i for i, (a, b) in enumerate(self.intervals) if 2 * rise <= a + b < 2 * fall]
        if not selected:
            raise ValueError("Gate selects no statistics")
        boundary = [(a, b) for a, b in self.intervals if a < rise < b or a < fall < b]
        # Whole packets are selected by midpoint. No partial packet integral is
        # invented; report a conservative boundary-duration bound explicitly.
        uncertainty = sum(b - a for a, b in set(boundary)) + 2 * self.edge_resolution
        return {
            "domain": "shared_device_sample_counter",
            "sample_rate": self.rate,
            "rise": rise,
            "fall": fall,
            "edge_resolution_samples": self.edge_resolution,
            "first_packet": selected[0],
            "last_packet": selected[-1],
            "packet_count": len(selected),
            "selection": "midpoint_half_open",
            "gate_duration_s": (fall - rise) / self.rate,
            "integrated_duration_s": sum(
                self.intervals[i][1] - self.intervals[i][0] for i in selected
            )
            / self.rate,
            "boundary_uncertainty_s": uncertainty / self.rate,
        }


def counter_gate_trace(
    frames: list[dict[str, Any]],
    packets: list[dict[str, Any]],
    *,
    channel: int,
) -> CounterGateTrace:
    """Decode retained UINT1 frames and validate one instrument's shared counter.

    The instrument adapter must establish the common counter origin. Matching
    UTC fits alone never establish it. Rates, continuity and coverage are checked
    again here, so this function also validates a JSON roundtrip offline.
    """
    import numpy as np

    if not frames or not packets:
        raise ValueError("Missing original gate/statistics evidence")
    previous_end = previous_level = rise = rate = increment = None
    windows: list[tuple[int, int]] = []
    for frame in frames:
        raw = _validate_frame(frame, channel)
        first, step, frequency = frame["sample_id"], frame["decimate_factor"], frame["sample_rate"]
        if previous_end is not None and (
            first != previous_end or frequency != rate or step != increment
        ):
            raise ValueError("Discontinuous or incompatible GPI counter stream")
        rate, increment = frequency, step
        levels = np.unpackbits(np.frombuffer(raw, dtype=np.uint8), bitorder="little")[
            : frame["element_count"]
        ]
        edges = np.flatnonzero(levels[1:] != levels[:-1]) + 1
        if previous_level is not None and int(levels[0]) != previous_level:
            edges = np.concatenate(([0], edges))
        for index in edges:
            counter = first + int(index) * step
            if levels[index]:
                rise = counter
            elif rise is not None:
                windows.append((rise, counter))
                rise = None
        previous_level = int(levels[-1])
        previous_end = first + len(levels) * step
    if rate is None or increment is None:
        raise ValueError("Missing GPI counter contract")
    intervals = []
    previous_end = previous_utc = previous_map = None
    jumps, map_changes = [], 0
    for packet in packets:
        t = packet["time"]
        a, b = t["samples"]["value"]
        a = _integer(a, "statistics counter")
        b = _integer(b, "statistics end counter", a + 1)
        if t["sample_freq"]["value"] != rate or (previous_end is not None and a != previous_end):
            raise ValueError("Discontinuous or incompatible statistics counter stream")
        _clock_map(t["time_map"])
        utc = t["utc"]["value"]
        if len(utc) != 2 or any(not isinstance(v, Integral) for v in utc) or utc[1] <= utc[0]:
            raise ValueError("Invalid fitted UTC interval")
        if previous_utc is not None and utc[0] != previous_utc:
            if previous_map == t["time_map"]:
                raise ValueError("Fitted UTC discontinuity without a map update")
            jumps.append(utc[0] - previous_utc)
        if previous_map is not None and t["time_map"] != previous_map:
            map_changes += 1
        delta = t.get("delta", {}).get("value")
        if delta is not None and (
            not math.isfinite(delta) or not math.isclose(delta, (b - a) / rate, rel_tol=1e-9)
        ):
            raise ValueError("Driver delta disagrees with sample duration")
        intervals.append((a, b))
        previous_end, previous_utc, previous_map = b, utc[1], t["time_map"]
    return CounterGateTrace(
        rate,
        intervals[0][0],
        tuple(windows),
        increment,
        tuple(intervals),
        {
            "fitted_map_changes": map_changes,
            "utc_boundary_discontinuities_ticks": jumps,
            "duration_authority": "sample_counter/sample_rate",
            "clock_calibration": "not_assessed",
        },
    )
