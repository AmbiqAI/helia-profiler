"""Stats-array processing, GPIO window segmentation, and energy integration.

Stats parsing works for both the JS110 ``s/sstats/value`` shape and the
JS220 ``s/stats/value`` shape, both of which expose
``packet['signals'][<sig>][<stat>] = {'value': <float>, 'units': <str>}``.
"""

from __future__ import annotations

import logging
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


def _packet_duration_ticks(t: dict[str, Any], u0: float, u1: float, second: float) -> float:
    """How long one stats packet covered, in time64 ticks (#249).

    ``utc`` is not a device timestamp. jsdrv maps the instrument's sample
    counter to UTC through a filter it fits while streaming, and publishes that
    fit in every packet as ``time.time_map.counter_rate``. Measured on a JS320
    that rate read 15,849,906 Hz against a nameplate ``sample_freq`` of
    16,000,000 -- a 9470 ppm deficit -- and ``u1 - u0`` overstated the packet by
    9469 ppm to match. Early in a session the same fit was 2.9 % out.

    The window's duration is the sum of these, so that error lands directly on
    the reported gate width and on everything divided by it: average current,
    average power and TOPS. Not TOPS-per-watt -- it is ops over energy once the
    duration cancels -- and not ``energy_j``, which the device integrated.

    Measured range on one bench session: 130 ppm once the fit had settled,
    1.5 % on the first captures after the stream started, and 2.9 % at the
    coldest reading. The single-packet figure above is one sample of that, not
    a typical value.

    ``utc`` stays the axis packets are *selected* on -- a shared scale error
    cancels out of a selection -- and is the fallback when a packet carries no
    counter span.
    ``second`` is ``time64.SECOND``, passed in rather than imported here: this
    runs once per stat packet, and a per-element import cost ~15 % of
    ``_stats_arrays`` on a minute-long capture.
    """
    # `delta` first: the driver divides by exactly this to build the charge and
    # energy integrals in the same packet, so taking it makes
    # ``energy_j / duration_s`` consistent by construction rather than by
    # reimplementing the same arithmetic and hoping it matches.
    delta = (t.get("delta", {}) or {}).get("value")
    try:
        if delta is not None and float(delta) > 0:
            return float(delta) * second
    except (TypeError, ValueError):
        pass
    samples = (t.get("samples", {}) or {}).get("value")
    freq = (t.get("sample_freq", {}) or {}).get("value")
    try:
        if samples is not None and len(samples) >= 2 and freq:
            span = float(samples[1]) - float(samples[0])
            # Strictly positive: a decreasing pair is a corrupt packet, and a
            # negative duration would make _process_gated_stats drop the whole
            # window silently rather than fall back to utc.
            if span > 0:
                return span * second / float(freq)
    except (TypeError, ValueError, IndexError):
        pass
    return u1 - u0


def _packets_without_counter_span(packets: list[dict[str, Any]]) -> int:
    """Packets whose duration had to come from ``utc`` after all.

    Should always be zero: every ``s/stats/value`` packet carries ``delta``,
    ``samples`` and ``sample_freq`` from one dict literal, on all three
    instrument families. It is counted because if it ever is not zero, that
    window mixed two axes — the exact defect this module was changed to
    remove — and would otherwise do so without saying a word.

    Deliberately a separate predicate from ``_counter_rate_ratio``'s: that one
    keys on ``time_map``, this one on what the duration actually used.
    """
    missing = 0
    for packet in packets:
        t = packet.get("time", {}) if isinstance(packet, dict) else {}
        if not (t.get("utc", {}) or {}).get("value"):
            continue
        delta = (t.get("delta", {}) or {}).get("value")
        samples = (t.get("samples", {}) or {}).get("value")
        freq = (t.get("sample_freq", {}) or {}).get("value")
        has_delta = isinstance(delta, (int, float)) and not isinstance(delta, bool) and delta > 0
        has_span = bool(samples) and len(samples) >= 2 and bool(freq)
        if not has_delta and not has_span:
            missing += 1
    return missing


def _counter_rate_ratio(packets: list[dict[str, Any]]) -> dict[str, Any] | None:
    """How far jsdrv's fitted counter rate sits from the nameplate rate.

    Published as a diagnostic because it is the direct read on the error above:
    it is what ``utc`` is scaled by, and one run of it identifies a bad time
    base that previously took a nineteen-capture series to attribute.

    Reported as a range, not a median. The quantity *moves* -- that is the whole
    point of it -- and a median over a capture that converged halfway through
    returns the settled value, hiding the very sweep worth seeing. A capture
    2.9 % out for its first half reduces to a median of exactly 1.000000. The
    first/last pair says which direction it moved and whether it had settled by
    the end.
    """
    import numpy as np

    ratios, rates, freqs = [], [], []
    for packet in packets:
        t = packet.get("time", {}) if isinstance(packet, dict) else {}
        rate = (t.get("time_map", {}) or {}).get("counter_rate")
        freq = (t.get("sample_freq", {}) or {}).get("value")
        if rate and freq:
            rates.append(float(rate))
            freqs.append(float(freq))
            ratios.append(float(freq) / float(rate))
    if not ratios:
        return None
    return {
        "counter_rate_hz": float(np.median(rates)),
        "sample_freq_hz": float(np.median(freqs)),
        # >1 means utc runs fast, inflating any duration measured on it.
        "utc_over_counter_rate": float(np.median(ratios)),
        "utc_over_counter_rate_min": float(np.min(ratios)),
        "utc_over_counter_rate_max": float(np.max(ratios)),
        "utc_over_counter_rate_first": ratios[0],
        "utc_over_counter_rate_last": ratios[-1],
        # Non-zero means the fit was still moving during the capture.
        "counter_rate_swept_by": float(np.max(ratios) - np.min(ratios)),
        "packets_with_time_map": len(ratios),
        "packets_total": len(packets),
        # Non-zero means some window mixed the counter and utc axes.
        "packets_without_counter_span": _packets_without_counter_span(packets),
    }


def _stats_arrays(packets: list[dict[str, Any]]) -> dict[str, Any]:
    """Vectorise the per-packet fields we use from ``s/stats/value`` packets."""
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
    """Return the timestamp axis used to align packets with GPI polls."""
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
    """Map host-timestamped GPI polls onto the instrument stats timeline.

    JS220/JS320 ``s/stats`` callbacks can arrive in USB bursts. Selecting
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
    """Summarise the entire captured window from on-device stat integrals."""
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


def _frame_spacings(usable: list[dict[str, Any]]) -> list[float]:
    """Ticks per delivered sample, from each consecutive pair of GPI frames.

    Divides by ``cur``'s sample count, not ``nxt``'s: frame ``cur`` starts at
    its own ``utc`` and holds ``N_cur`` samples, so the next frame's first
    sample sits at ``utc_cur + N_cur * spacing``. Using ``nxt``'s count is
    wrong whenever the two differ -- which ``_streamed_gpi_timebase`` expects
    often enough to publish ``frame_sample_count_min``/``_max``.

    One helper for both callers so the diagnostic cannot drift from the
    expression that actually places the gate edges.
    """
    import numpy as np

    return [
        (float(nxt["utc"]) - float(cur["utc"])) / int(np.asarray(cur["data"]).size)
        for cur, nxt in zip(usable, usable[1:])
        if float(nxt["utc"]) > float(cur["utc"])
    ]


def _segment_streamed_gpi(
    frames: list[dict[str, Any]],
) -> list[tuple[float, float]]:
    """Segment gate-high windows from device-timestamped GPI stream frames.

    Each frame is ``{"utc": <time64 of first sample>, "rate": <samples/s>,
    "data": <per-sample levels>}`` as captured from ``s/gpi/N/!data``.  The
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
        frame for frame in frames if np.asarray(frame["data"]).size and float(frame["rate"]) > 0
    ]
    if not usable:
        return []

    # Per-sample spacing measured from the frames themselves, NOT from the
    # reported rate: JS320 GPI ``!data`` frames report ``sample_rate`` at the
    # raw instrument rate with ``decimate_factor`` 1 while actually carrying
    # 8:1-decimated samples (observed live: ``sample_id`` counts raw samples
    # and both sid and utc advance exactly 8 per delivered sample).  Trusting
    # the reported rate compressed every frame's intra-frame time 8x and put
    # streamed edges tens of ms off — flagged by the firmware window clock,
    # whose STIMER bracket a correctly measured gate can never exceed.  The
    # per-frame ``utc`` values are device-exact, so consecutive frames give
    # the true spacing directly and a median over the capture rejects any
    # frame-drop outliers.
    spacings = _frame_spacings(usable)
    if spacings:
        tick_per_sample = float(np.median(spacings))
    else:
        tick_per_sample = time64.SECOND / float(usable[0]["rate"])

    edges: list[tuple[float, int]] = []  # (tick, new_level)
    prev_level: int | None = None
    for frame in usable:
        data = np.asarray(frame["data"])
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
        if level and rise is None:
            rise = tick
        elif not level and rise is not None:
            windows.append((rise, tick))
            rise = None
    return windows


def _streamed_gpi_timebase(frames: list[dict[str, Any]]) -> dict[str, Any]:
    """Report how the streamed-GPI time base was reconstructed (#249).

    :func:`_segment_streamed_gpi` cannot trust the JS320's reported sample rate
    -- the instrument advertises the raw rate with ``decimate_factor`` 1 while
    delivering 8:1-decimated samples -- so it derives the per-sample spacing
    from consecutive frame ``utc`` values instead. Every intra-frame edge is
    then placed at ``frame_t0 + index * tick_per_sample``, which makes that one
    derived number decide both gate edges.

    This records what the derivation actually saw, so a window that disagrees
    with the firmware clock can be attributed to the time base rather than
    guessed at. Diagnostic only: nothing here feeds the gate or the energy.
    """
    import numpy as np
    from pyjoulescope_driver import time64

    usable = [
        frame for frame in frames if np.asarray(frame["data"]).size and float(frame["rate"]) > 0
    ]
    if not usable:
        return {"frame_count": 0}

    spacings = _frame_spacings(usable)
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
            # Ratio of the rate the instrument claims to the rate its own frame
            # timestamps imply. The 8:1 decimation shows up here as ~8.
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


def _fullrate_energy_over_windows(
    *,
    cur_chunks: list[Any],
    volt_chunks: list[Any],
    anchors: list[tuple[int, int, float]],
    poll_samples: list[tuple[int, int]],
    windows_override: list[tuple[float, float]] | None = None,
) -> dict[str, Any] | None:
    """Integrate raw full-rate current/voltage over the GPI-high windows.

    Uses a signed rectangular sum, ``sum(I * V) / sample_rate``, rather than
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
    # nameplate rate instead selects a span wrong by the fit's error -- 2.9 %
    # at its worst, which is 286 extra samples on a 10 ms window. Consecutive
    # anchors carry both a sample index and a utc, so they give the conversion
    # directly; the nameplate rate is the fallback when there is only one.
    if len(idx) >= 2 and idx[-1] > idx[0] and utc[-1] > utc[0]:
        slope = (utc[-1] - utc[0]) / (idx[-1] - idx[0])
    else:
        slope = time64.SECOND / sr
    i0, u0 = idx[0], utc[0]
    sample_utc = u0 + (np.arange(n, dtype=np.float64) - i0) * slope

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
        # Count the samples, do not measure the edges (#249). The samples are
        # spaced at the nameplate rate ``dt``, while ``rise``/``fall`` are on
        # the driver's fitted utc axis -- so ``(fall - rise)`` carries that
        # fit's error while the charge and energy above do not. This is the
        # measurement someone reaches for to corroborate the gated path, and
        # it would have disagreed with it by up to 1.5 %.
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
    """Integrate the gated window(s) from on-device stat-packet integrals.

    Each packet carries the instrument's full-rate charge/energy integral over a
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
        # Admission below is on the utc span; the duration reported for the
        # window is the counter-based sum (#249). Different axes, deliberately:
        # the floor is a coarse "is this long enough to trust" gate at 1 s
        # against a multi-second window, so the fit's error cannot move a
        # window across it. Worth knowing they differ if that margin ever
        # narrows.
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
