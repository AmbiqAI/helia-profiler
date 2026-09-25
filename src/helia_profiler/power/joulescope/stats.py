"""Stats-array processing, GPIO window segmentation, and energy integration.

Stats parsing works for both the JS110 ``s/sstats/value`` shape and the
JS220 ``s/stats/value`` shape, both of which expose
``packet['signals'][<sig>][<stat>] = {'value': <float>, 'units': <str>}``.
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any

from ...errors import PowerError
from ..base import GatedPowerWindow, PowerSample, PowerSummary
from .device import _extract_scalar

log = logging.getLogger("hpx")


def _process_stats(
    stats: list[dict[str, Any]],
    duration_s: float,
    io_voltage: float,
) -> tuple[list[PowerSample], PowerSummary]:
    import numpy as np

    n = max(len(stats), 1)
    dt = duration_s / n
    currents: list[float] = []
    voltages: list[float] = []
    peaks: list[float] = []

    for pkt in stats:
        sig = pkt.get("signals", {}) if isinstance(pkt, dict) else {}
        cur = sig.get("current", {})
        vol = sig.get("voltage", {})
        currents.append(_extract_scalar(cur.get("avg", 0.0)))
        voltages.append(_extract_scalar(vol.get("avg", io_voltage), default=io_voltage))
        peaks.append(
            np.fmax(
                abs(_extract_scalar(cur.get("max", 0.0))),
                abs(_extract_scalar(cur.get("min", 0.0))),
            )
        )

    if not currents:
        currents = [0.0]
        voltages = [io_voltage]
        peaks = [0.0]

    currents_np = np.asarray(currents, dtype=float)
    voltages_np = np.asarray(voltages, dtype=float)
    peaks_np = np.asarray(peaks, dtype=float)
    power_np = currents_np * voltages_np

    avg_current = float(np.nanmean(currents_np))
    avg_power = float(np.nanmean(power_np))
    peak_current = float(np.nanmax(peaks_np)) if peaks_np.size else avg_current
    energy = float(np.nansum(power_np)) * dt

    samples = [
        PowerSample(timestamp_s=i * dt, current_a=c, voltage_v=v)
        for i, (c, v) in enumerate(zip(currents, voltages))
    ]

    summary = PowerSummary(
        avg_current_a=avg_current,
        avg_power_w=avg_power,
        peak_current_a=peak_current,
        energy_j=energy,
        duration_s=duration_s,
        sample_count=len(stats),
    )
    return samples, summary


def _sv(field: Any, default: float = 0.0) -> float:
    """Extract a ``{'value': x, 'units': ...}`` scalar from a stats packet."""
    if isinstance(field, dict):
        try:
            return float(field.get("value", default))
        except (TypeError, ValueError):
            return default
    return default


def _counter_duration_ticks(t: dict[str, Any], second: float) -> float | None:
    """Return positive driver delta or sample-span duration, or None for UTC fallback."""
    delta = (t.get("delta", {}) or {}).get("value")
    try:
        if delta is not None:
            duration = float(delta)
            ticks = duration * second
            if math.isfinite(duration) and duration > 0 and math.isfinite(ticks) and ticks > 0:
                return ticks
    except (TypeError, ValueError, OverflowError):
        pass
    samples = (t.get("samples") or {}).get("value")
    freq = (t.get("sample_freq") or {}).get("value")
    if samples is None or freq is None:
        return None
    try:
        rate = float(freq)
        if math.isfinite(rate) and rate > 0 and len(samples) >= 2:
            span = float(samples[1]) - float(samples[0])
            if math.isfinite(span) and span > 0:
                ticks = (span / rate) * second
                if math.isfinite(ticks) and ticks > 0:
                    return ticks
    except (TypeError, ValueError, IndexError, OverflowError):
        pass
    return None


def _packet_duration_ticks(t: dict[str, Any], u0: float, u1: float, second: float) -> float:
    """Return packet duration in ticks: driver delta, sample span, then UTC (#249)."""
    ticks = _counter_duration_ticks(t, second)
    return u1 - u0 if ticks is None else ticks


def _packets_without_counter_span(packets: list[dict[str, Any]], second: float) -> int:
    """Count timestamped packets whose duration requires the UTC fallback."""
    missing = 0
    for packet in packets:
        t = packet.get("time", {}) if isinstance(packet, dict) else {}
        if not (t.get("utc", {}) or {}).get("value"):
            continue
        if _counter_duration_ticks(t, second) is None:
            missing += 1
    return missing


def _counter_rate_ratio(packets: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Report the fitted-rate ratio's range and endpoints across usable packets.

    Rate medians are labeled explicitly; see helia-profiler#249.
    """
    import numpy as np
    from pyjoulescope_driver import time64

    second = float(time64.SECOND)
    ratios, rates, freqs = [], [], []
    for packet in packets:
        t = packet.get("time", {}) if isinstance(packet, dict) else {}
        rate = (t.get("time_map", {}) or {}).get("counter_rate")
        freq = (t.get("sample_freq", {}) or {}).get("value")
        if rate is None or freq is None:
            continue
        try:
            rate, freq = float(rate), float(freq)
        except (TypeError, ValueError, OverflowError):
            continue
        if not (math.isfinite(rate) and math.isfinite(freq) and rate > 0 and freq > 0):
            continue
        ratio = freq / rate
        if not math.isfinite(ratio) or ratio <= 0:
            continue
        rates.append(rate)
        freqs.append(freq)
        ratios.append(ratio)
    if not ratios:
        return None
    return {
        "counter_rate_median_hz": float(np.median(rates)),
        "sample_freq_median_hz": float(np.median(freqs)),
        # >1 means utc runs fast, inflating any duration measured on it.
        "utc_over_counter_rate_min": float(np.min(ratios)),
        "utc_over_counter_rate_max": float(np.max(ratios)),
        "utc_over_counter_rate_first": ratios[0],
        "utc_over_counter_rate_last": ratios[-1],
        # Non-zero means the fit was still moving during the capture.
        "utc_over_counter_rate_sweep": float(np.max(ratios) - np.min(ratios)),
        "packets_with_time_map": len(ratios),
        "packets_total": len(packets),
        # Non-zero means some window mixed the counter and utc axes.
        "packets_without_counter_span": _packets_without_counter_span(packets, second),
    }


def _stats_arrays(packets: list[dict[str, Any]]) -> dict[str, Any]:
    import numpy as np
    from pyjoulescope_driver import time64

    second = float(time64.SECOND)

    mid, host_time, dur, cur_avg, cur_max, cur_min, cur_int, pwr_avg, pwr_int = (
        [] for _ in range(9)
    )
    for p in packets:
        t = p.get("time", {}) if isinstance(p, dict) else {}
        utc = (t.get("utc", {}) or {}).get("value")
        if not utc or len(utc) < 2:
            continue
        u0, u1 = float(utc[0]), float(utc[1])
        host_tick = p.get("_host_time64") if isinstance(p, dict) else None
        sig = p.get("signals", {})
        cur = sig.get("current", {})
        pwr = sig.get("power", {})
        mid.append(0.5 * (u0 + u1))
        host_time.append(float(host_tick) if host_tick is not None else np.nan)
        dur.append(_packet_duration_ticks(t, u0, u1, second))
        cur_avg.append(_sv(cur.get("avg")))
        cur_max.append(_sv(cur.get("max")))
        cur_min.append(_sv(cur.get("min")))
        cur_int.append(_sv(cur.get("integral")))
        pwr_avg.append(_sv(pwr.get("avg")))
        pwr_int.append(_sv(pwr.get("integral")))
    # Rectify each packet's net integral, not each full-rate sample.
    # Peak magnitude uses both signed extrema regardless of wiring polarity.
    abs_max = np.abs(np.asarray(cur_max, dtype=np.float64))
    abs_min = np.abs(np.asarray(cur_min, dtype=np.float64))
    return {
        "mid": np.asarray(mid, dtype=np.float64),
        "host_time": np.asarray(host_time, dtype=np.float64),
        "dur_ticks": np.asarray(dur, dtype=np.float64),
        "cur_avg": np.abs(np.asarray(cur_avg, dtype=np.float64)),
        "cur_max": abs_max,
        "cur_min": abs_min,
        "cur_peak": np.maximum(abs_max, abs_min),
        "cur_int": np.abs(np.asarray(cur_int, dtype=np.float64)),
        # Preserve signed charge for the gated backfeed/polarity check.
        "cur_int_signed": np.asarray(cur_int, dtype=np.float64),
        "pwr_avg": np.abs(np.asarray(pwr_avg, dtype=np.float64)),
        "pwr_int": np.abs(np.asarray(pwr_int, dtype=np.float64)),
    }


def _gated_mask_axis(a: dict[str, Any], *, prefer_device_time: bool = False) -> tuple[Any, str]:
    if prefer_device_time:
        return a["mid"], "device_packet_midpoint_time64"
    host_time = a.get("host_time")
    if host_time is not None and getattr(host_time, "size", 0) and not bool(host_time.size == 0):
        import numpy as np

        if not np.isnan(host_time).any():
            return host_time, "host_packet_arrival_time64"
    return a["mid"], "device_packet_midpoint_time64"


def _map_poll_samples_to_packet_time(
    *,
    packets: list[dict[str, Any]],
    poll_samples: list[tuple[int, int]],
    minimum_window_s: float = 0.0,
) -> list[tuple[int, int]]:
    """JS220/JS320 ``s/stats`` callbacks can arrive in USB bursts. Selecting
    packets by callback arrival time therefore truncates a correctly observed
    GPIO window. Each packet includes both its instrument midpoint and the
    host timestamp captured at callback arrival. Gate edges must be covered
    by those anchors, allowing at most one packet of endpoint uncertainty.
    """
    import numpy as np
    from pyjoulescope_driver import time64

    if len(poll_samples) < 1:
        return poll_samples

    windows = [
        (rise, fall)
        for rise, fall in _segment_gpi_windows(poll_samples)
        if (fall - rise) / time64.SECOND >= minimum_window_s
    ]
    if not windows:
        return poll_samples

    a = _stats_arrays(packets)
    host_time = a["host_time"]
    device_time = a["mid"]
    if host_time.size < 2 or np.isnan(host_time).any():
        raise PowerError("Insufficient stats timestamps to align the GPIO gate.")

    order = np.argsort(host_time, kind="stable")
    # Coverage is checked on host timestamps, so use packet duration, not fitted UTC span.
    duration = a["dur_ticks"][order]
    host_time = host_time[order]
    device_time = device_time[order]
    unique = np.concatenate(([True], np.diff(host_time) > 0))
    host_time = host_time[unique]
    device_time = device_time[unique]
    if host_time.size < 2:
        raise PowerError("Insufficient distinct stats timestamps to align the GPIO gate.")

    for rise, fall in windows:
        if rise < host_time[0] - duration[0] or fall > host_time[-1] + duration[-1]:
            raise PowerError(
                "Stats timestamps do not cover the GPIO gate; refusing to truncate the window.",
                hint="Check the Joulescope USB connection and retry the capture.",
            )

    polls = np.asarray([tick for tick, _level in poll_samples], dtype=np.float64)
    mapped = np.interp(polls, host_time, device_time)
    # Extrapolate endpoint uncertainty at clock rate, never clamp a gate edge.
    before = polls < host_time[0]
    after = polls > host_time[-1]
    mapped[before] = device_time[0] + polls[before] - host_time[0]
    mapped[after] = device_time[-1] + polls[after] - host_time[-1]
    return [(int(tick), level) for tick, (_host_tick, level) in zip(mapped, poll_samples)]


def _whole_summary_from_stats(packets: list[dict[str, Any]]) -> PowerSummary:
    from pyjoulescope_driver import time64

    a = _stats_arrays(packets)
    if a["mid"].size == 0:
        return PowerSummary(0.0, 0.0, 0.0, 0.0, 0.0, 0)
    duration_s = float(a["dur_ticks"].sum() / time64.SECOND)
    charge_c = float(a["cur_int"].sum())
    energy_j = float(a["pwr_int"].sum())
    peak = float(a["cur_peak"].max()) if a["cur_peak"].size else 0.0
    avg_current = charge_c / duration_s if duration_s > 0 else 0.0
    avg_power = energy_j / duration_s if duration_s > 0 else 0.0
    return PowerSummary(
        avg_current_a=avg_current,
        avg_power_w=avg_power,
        peak_current_a=peak,
        energy_j=energy_j,
        duration_s=duration_s,
        sample_count=int(a["mid"].size),
    )


def _segment_gpi_windows(poll_samples: list[tuple[int, int]]) -> list[tuple[float, float]]:
    import numpy as np

    if not poll_samples:
        return []
    poll_t = np.asarray([t for t, _ in poll_samples], dtype=np.float64)
    poll_v = np.asarray([v for _, v in poll_samples], dtype=np.int8)
    high = poll_v > 0
    edges = np.diff(high.astype(int))
    rises = poll_t[1:][edges == 1]
    falls = poll_t[1:][edges == -1]
    windows: list[tuple[float, float]] = []
    fall_index = 0
    for rise in rises:
        while fall_index < len(falls) and falls[fall_index] <= rise:
            fall_index += 1
        if fall_index < len(falls):
            windows.append((float(rise), float(falls[fall_index])))
            fall_index += 1
    return windows


def _unpack_gpi_levels(data: Any) -> Any:
    """Expand a GPI stream frame into one level per sample.

    The driver delivers GPI signals as uint1 packed 8 samples per byte,
    earliest sample in the least significant bit.
    """
    import numpy as np

    return np.unpackbits(np.asarray(data, dtype=np.uint8), bitorder="little")


def _frame_spacings(frames: list[dict[str, Any]]) -> list[float]:
    """Measure adjacent valid-frame spacing without bridging excluded input."""
    import numpy as np

    return [
        (float(nxt["utc"]) - float(cur["utc"])) / int(np.asarray(cur["data"]).size)
        for cur, nxt in zip(frames, frames[1:])
        if np.asarray(cur["data"]).size
        and np.asarray(nxt["data"]).size
        and math.isfinite(float(cur["rate"]))
        and math.isfinite(float(nxt["rate"]))
        and float(cur["rate"]) > 0
        and float(nxt["rate"]) > 0
        and float(nxt["utc"]) > float(cur["utc"])
    ]


def _segment_streamed_gpi(
    frames: list[dict[str, Any]],
) -> list[tuple[float, float]]:
    """Segment gate-high windows from device-timestamped GPI stream frames.

    Each frame is ``{"utc": <time64 of first sample>, "rate": <samples/s>,
    "data": <per-sample levels>}`` unpacked from ``s/gpi/N/!data``.  The
    returned ``(rise, fall)`` pairs are in instrument time64 — the same clock
    as the stat-packet midpoints — so no host-time mapping is involved and the
    edges carry sample-period resolution instead of host poll cadence.

    Only complete windows are returned (a trailing high with no observed fall
    is dropped), mirroring :func:`_segment_gpi_windows`.  A capture that
    starts high yields no leading window for the same reason that function
    rejects one: a rise that was never observed cannot be timed.
    """
    import numpy as np
    from pyjoulescope_driver import time64

    usable = [
        frame
        for frame in frames
        if np.asarray(frame["data"]).size
        and math.isfinite(float(frame["rate"]))
        and float(frame["rate"]) > 0
    ]
    if not usable:
        return []

    # Use adjacent frame timestamps for the fitted-UTC spacing (helia-profiler#249).
    spacings = _frame_spacings(frames)
    if spacings:
        tick_per_sample = float(np.median(spacings))
    else:
        tick_per_sample = time64.SECOND / float(usable[0]["rate"])

    edges: list[tuple[float, int]] = []  # (tick, new_level)
    prev_level: int | None = None
    for frame in frames:
        data = np.asarray(frame["data"])
        if not data.size or not math.isfinite(float(frame["rate"])) or float(frame["rate"]) <= 0:
            edges.append((float(frame["utc"]), -1))
            prev_level = None
            continue
        frame_t0 = float(frame["utc"])
        levels = (data > 0).astype(np.int8)
        if prev_level is None:
            prev_level = int(levels[0])
        # Boundary edge between frames, then intra-frame edges.
        if int(levels[0]) != prev_level:
            edges.append((frame_t0, int(levels[0])))
        idx = np.nonzero(np.diff(levels))[0]
        for i in idx:
            edges.append((frame_t0 + (int(i) + 1) * tick_per_sample, int(levels[i + 1])))
        prev_level = int(levels[-1])

    windows: list[tuple[float, float]] = []
    rise: float | None = None
    for tick, level in edges:
        if level < 0:
            rise = None
        elif level and rise is None:
            rise = tick
        elif not level and rise is not None:
            windows.append((rise, tick))
            rise = None
    return windows


def _streamed_gpi_timebase(frames: list[dict[str, Any]]) -> dict[str, Any]:
    import numpy as np
    from pyjoulescope_driver import time64

    usable = [
        frame
        for frame in frames
        if np.asarray(frame["data"]).size
        and math.isfinite(float(frame["rate"]))
        and float(frame["rate"]) > 0
    ]
    if not usable:
        return (
            {"frame_count": 0, "dropped_or_empty_frames": len(frames)}
            if frames
            else {"frame_count": 0}
        )

    spacings = _frame_spacings(frames)
    sizes = [int(np.asarray(frame["data"]).size) for frame in usable]
    reported_rate = float(usable[0]["rate"])
    out: dict[str, Any] = {
        "frame_count": len(usable),
        "dropped_or_empty_frames": len(frames) - len(usable),
        "reported_rate_hz": reported_rate,
        "sample_count_total": int(sum(sizes)),
        "frame_sample_count_min": min(sizes),
        "frame_sample_count_max": max(sizes),
        "spacing_sample_count": len(spacings),
    }
    if not spacings:
        out["tick_per_sample"] = time64.SECOND / reported_rate
        out["tick_per_sample_source"] = "reported_rate"
        return out

    median = float(np.median(spacings))
    out.update(
        {
            "tick_per_sample": median,
            "tick_per_sample_source": "median_frame_spacing",
            "implied_rate_hz": time64.SECOND / median if median else None,
            # Reported rate over the rate frame timestamps imply.
            "reported_over_implied_rate": (reported_rate * median / time64.SECOND)
            if median
            else None,
            # Spread across the capture. A stable time base gives a tight
            # band; a wide one means the median -- and therefore both gate
            # edges -- is an estimate over noisy input.
            "spacing_min_tick": float(np.min(spacings)),
            "spacing_max_tick": float(np.max(spacings)),
            "spacing_p05_tick": float(np.percentile(spacings, 5)),
            "spacing_p95_tick": float(np.percentile(spacings, 95)),
            "spacing_relative_spread": (float(np.max(spacings)) - float(np.min(spacings))) / median
            if median
            else None,
        }
    )
    return out


def _fullrate_sample_span(frame: dict[str, Any], count: int) -> tuple[int, int, float] | None:
    """Return a frame's delivered-sample interval and rate, or None if unknown."""
    try:
        step = int(frame.get("decimate_factor", 1))
        sample_id = int(frame["sample_id"])
        rate = float(frame["sample_rate"]) / step
        if step <= 0 or sample_id < 0 or sample_id % step or count <= 0:
            return None
        if not math.isfinite(rate) or rate <= 0:
            return None
        start = sample_id // step
        return start, start + count, rate
    except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError):
        return None


def _fullrate_streams_contiguous(
    current: list[tuple[int, int, float] | None],
    voltage: list[tuple[int, int, float] | None],
) -> bool:
    """Require complete, aligned current/voltage source intervals at one rate."""
    bounds = []
    for spans in (current, voltage):
        if not spans or spans[0] is None:
            return False
        start, end, rate = spans[0]
        for span in spans[1:]:
            if span is None or span[0] != end or span[2] != rate:
                return False
            end = span[1]
        bounds.append((start, end, rate))
    return bounds[0] == bounds[1]


def _fullrate_energy_over_windows(
    *,
    cur_chunks: list[Any],
    volt_chunks: list[Any],
    anchors: list[tuple[int, int, float]],
    poll_samples: list[tuple[int, int]],
    windows_override: list[tuple[float, float]] | None = None,
) -> dict[str, Any] | None:
    """Uses a signed rectangular sum, ``sum(I * V) / sample_rate``, rather than
    the packet-rectified integrals used for the primary gated measurement.

    Returns per-window and aggregate energy/charge, or ``None`` if there is
    insufficient data to build a timeline.
    """
    import numpy as np
    from pyjoulescope_driver import time64

    if not cur_chunks or not anchors:
        return None

    cur = np.concatenate(cur_chunks)
    volt = np.concatenate(volt_chunks) if volt_chunks else np.array([], np.float32)
    n = min(len(cur), len(volt)) if len(volt) else len(cur)
    if n == 0:
        return None
    cur = cur[:n]
    volt = volt[:n] if len(volt) else np.full(n, np.nan, np.float32)

    idx = np.asarray([a[0] for a in anchors], dtype=np.float64)
    utc = np.asarray([a[1] for a in anchors], dtype=np.float64)
    sr = float(anchors[-1][2])
    if sr <= 0:
        return None
    # Ticks per sample ON THE EDGES' OWN AXIS (#249). The window edges come from
    # GPI polls mapped to the driver's fitted utc, so placing samples at the
    # nameplate rate instead selects a span wrong by the fit's error. Consecutive
    # anchors carry both a sample index and a utc, so they give the conversion
    # directly; the nameplate rate is the fallback when there is only one.
    positions = np.arange(n, dtype=np.float64)
    if idx.size >= 2 and np.all(np.diff(idx) > 0) and np.all(np.diff(utc) > 0):
        # Piecewise between adjacent anchors, not a single endpoint slope: the
        # fit can move during a capture, and a capture-wide slope would then
        # misplace every sample in between -- the same error in a different
        # place. np.interp extrapolates flat past the ends, so carry the local
        # edge slopes out to the tails by hand.
        sample_utc = np.interp(positions, idx, utc)
        head, tail = positions < idx[0], positions > idx[-1]
        if head.any():
            first = (utc[1] - utc[0]) / (idx[1] - idx[0])
            sample_utc[head] = utc[0] + (positions[head] - idx[0]) * first
        if tail.any():
            last = (utc[-1] - utc[-2]) / (idx[-1] - idx[-2])
            sample_utc[tail] = utc[-1] + (positions[tail] - idx[-1]) * last
    else:
        sample_utc = utc[0] + (positions - idx[0]) * (time64.SECOND / sr)

    windows = (
        windows_override if windows_override is not None else _segment_gpi_windows(poll_samples)
    )
    if not windows:
        return None

    dt = 1.0 / sr
    win_out: list[dict[str, float]] = []
    tot_charge = 0.0
    tot_energy = 0.0
    tot_dur = 0.0
    for rise, fall in windows:
        mask = (sample_utc >= rise) & (sample_utc < fall)
        seg_i = cur[mask]
        seg_v = volt[mask]
        if seg_i.size == 0:
            continue
        charge_c = float(np.sum(seg_i) * dt)
        energy_j = float(np.sum(seg_i * seg_v) * dt)
        # Duration uses the integrated sample count and rate (helia-profiler#249).
        dur_s = float(seg_i.size) * dt
        tot_charge += charge_c
        tot_energy += energy_j
        tot_dur += dur_s
        win_out.append(
            {
                "duration_s": dur_s,
                "charge_c": charge_c,
                "energy_j": energy_j,
                "mean_current_a": float(np.mean(seg_i)),
                "peak_current_a": float(np.max(np.abs(seg_i))),
            }
        )

    if not win_out or tot_dur <= 0:
        return None

    return {
        "method": "fullrate_rectangular_integral",
        "sample_rate_hz": sr,
        "sample_count": int(n),
        "window_count": len(win_out),
        "duration_s": tot_dur,
        "charge_c": tot_charge,
        "energy_j": tot_energy,
        "mean_current_a": tot_charge / tot_dur,
        "mean_power_w": tot_energy / tot_dur,
        "energy_per_window_j": tot_energy / len(win_out),
        "windows": win_out,
    }


def _process_gated_stats(
    *,
    packets: list[dict[str, Any]],
    poll_samples: list[tuple[int, int]],
    io_voltage: float,
    prefer_device_time: bool = False,
    minimum_window_s: float = 0.0,
    windows_override: list[tuple[float, float]] | None = None,
) -> tuple[list[GatedPowerWindow], PowerSummary]:
    """Each packet carries the instrument's full-rate charge/energy integral over a
    ~1 ms sub-window. Select packets by midpoint, retaining packet-scale
    endpoint uncertainty in the window charge/energy. The per-packet
    avg/max samples within the window yield the spike-robust distribution
    (median / p95 / p99 / glitch-robust peak) so a lone transient sample cannot
    define the headline current.
    """
    import numpy as np
    from pyjoulescope_driver import time64

    del io_voltage  # voltage is folded into the on-device power integral

    a = _stats_arrays(packets)
    raw_windows = (
        windows_override if windows_override is not None else _segment_gpi_windows(poll_samples)
    )
    windows = [
        (rise, fall)
        for rise, fall in raw_windows
        if (fall - rise) / time64.SECOND >= minimum_window_s
    ]
    if a["mid"].size == 0 or not windows:
        return [], PowerSummary(0.0, 0.0, 0.0, 0.0, 0.0, 0)

    mask_axis, _axis_name = _gated_mask_axis(a, prefer_device_time=prefer_device_time)
    t0 = float(mask_axis.min())
    gated_windows: list[GatedPowerWindow] = []
    total_charge = 0.0
    total_signed_charge = 0.0
    total_energy = 0.0
    total_duration = 0.0
    total_samples = 0
    peak_current = 0.0

    for rise, fall in windows:
        # Admission uses the selected axis and configured floor; duration sums packet metadata.
        mask = (mask_axis >= rise) & (mask_axis <= fall)
        if not bool(mask.any()):
            continue
        duration_s = float(a["dur_ticks"][mask].sum() / time64.SECOND)
        if duration_s <= 0:
            continue
        charge_c = float(a["cur_int"][mask].sum())
        signed_charge_c = float(a["cur_int_signed"][mask].sum())
        energy_j = float(a["pwr_int"][mask].sum())
        seg_cur_avg = a["cur_avg"][mask]
        seg_cur_max = a["cur_peak"][mask]
        seg_pwr_avg = a["pwr_avg"][mask]
        avg_current_a = charge_c / duration_s
        avg_power_w = energy_j / duration_s
        peak_current_a = float(seg_cur_max.max())
        total_charge += charge_c
        total_signed_charge += signed_charge_c
        total_energy += energy_j
        total_duration += duration_s
        total_samples += int(seg_cur_avg.size)
        peak_current = max(peak_current, peak_current_a)
        gated_windows.append(
            GatedPowerWindow(
                start_s=float((rise - t0) / time64.SECOND),
                end_s=float((fall - t0) / time64.SECOND),
                duration_s=duration_s,
                charge_c=charge_c,
                energy_j=energy_j,
                avg_current_a=avg_current_a,
                avg_power_w=avg_power_w,
                peak_current_a=peak_current_a,
                sample_count=int(seg_cur_avg.size),
                median_current_a=float(np.median(seg_cur_avg)),
                p95_current_a=float(np.percentile(seg_cur_avg, 95)),
                p99_current_a=float(np.percentile(seg_cur_avg, 99)),
                peak_current_p99_a=float(np.percentile(seg_cur_max, 99)),
                median_power_w=float(np.median(seg_pwr_avg)),
                p95_power_w=float(np.percentile(seg_pwr_avg, 95)),
                p99_power_w=float(np.percentile(seg_pwr_avg, 99)),
            )
        )

    if total_duration <= 0 or not gated_windows:
        return [], PowerSummary(0.0, 0.0, 0.0, 0.0, 0.0, 0)

    # A net-negative gated charge is physically impossible for a powered
    # load: it means current flowed *backwards* through the sense path for
    # the majority of the window.  Known causes: reversed IN/OUT wiring, or
    # the target being backfed around the shunt (e.g. a host-driven GO GPIO
    # held high during the window, see power.sync.release_go).  Refuse to
    # launder it: fail loudly with the physical causes.  The 10% threshold
    # ignores noise-dominated near-zero windows on idle targets.
    if total_signed_charge < 0 and abs(total_signed_charge) > 0.1 * total_charge:
        if os.environ.get("HPX_POWER_ALLOW_NEGATIVE") != "1":
            raise PowerError(
                f"Gated window current is net NEGATIVE "
                f"({total_signed_charge / total_duration * 1e3:.3f} mA over "
                f"{total_duration:.3f} s) — the measurement is corrupt, not "
                "just polarity-flipped.",
                hint=(
                    "Check for (1) reversed Joulescope IN/OUT wiring, or "
                    "(2) current backfed into the target around the shunt — "
                    "e.g. a host-driven sync/GO GPIO held high during the "
                    "window, another power source attached (USB, debug "
                    "probe supply), or a shared rail. Set "
                    "HPX_POWER_ALLOW_NEGATIVE=1 to bypass this check for "
                    "diagnostic runs."
                ),
            )
        log.warning(
            "Gated window current is net negative (%.3f mA) but "
            "HPX_POWER_ALLOW_NEGATIVE=1 — reporting magnitudes anyway.",
            total_signed_charge / total_duration * 1e3,
        )

    summary = PowerSummary(
        avg_current_a=total_charge / total_duration,
        avg_power_w=total_energy / total_duration,
        peak_current_a=peak_current,
        energy_j=total_energy,
        duration_s=total_duration,
        sample_count=total_samples,
    )
    return gated_windows, summary


def _summary_to_dict(summary: PowerSummary) -> dict[str, float | int]:
    return {
        "avg_current_a": summary.avg_current_a,
        "avg_power_w": summary.avg_power_w,
        "peak_current_a": summary.peak_current_a,
        "energy_j": summary.energy_j,
        "duration_s": summary.duration_s,
        "sample_count": summary.sample_count,
    }
