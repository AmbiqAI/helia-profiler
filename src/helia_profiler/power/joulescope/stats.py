"""Stats-array processing, GPIO window segmentation, and energy integration.

Stats parsing works for both the JS110 ``s/sstats/value`` shape and the
JS220 ``s/stats/value`` shape, both of which expose
``packet['signals'][<sig>][<stat>] = {'value': <float>, 'units': <str>}``.
"""

from __future__ import annotations

from typing import Any

from ..base import GatedPowerWindow, PowerSample, PowerSummary
from .device import _extract_scalar


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
        peaks.append(_extract_scalar(cur.get("max", 0.0)))

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


def _stats_arrays(packets: list[dict[str, Any]]) -> dict[str, Any]:
    """Vectorise the per-packet fields we use from ``s/stats/value`` packets."""
    import numpy as np

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
        dur.append((u1 - u0))
        cur_avg.append(_sv(cur.get("avg")))
        cur_max.append(_sv(cur.get("max")))
        cur_min.append(_sv(cur.get("min")))
        cur_int.append(_sv(cur.get("integral")))
        pwr_avg.append(_sv(pwr.get("avg")))
        pwr_int.append(_sv(pwr.get("integral")))
    # Report current/power as magnitude.  The Joulescope's sign reflects which
    # terminal sources vs sinks; on a SoC-only rail wired with reversed IN/OUT
    # the draw reads negative even though the magnitude is correct.  We measure
    # consumption, so normalize to |I|/|P|; timestamps are left untouched.
    #
    # Polarity-robust peak: the JS110 reports per-window avg/min/max as *signed*
    # values.  With reversed IN/OUT the SoC draw is negative, so the true current
    # PEAK is the most-negative sample (the ``min`` field) and ``max`` holds the
    # trough (closest to zero).  Taking ``|max|`` alone therefore reports the
    # trough as the peak — the tell is a "peak" that comes out *below* the p99 of
    # the per-window averages, which is physically impossible (max >= mean
    # always).  We saw exactly that on AP510 (peak 1.34 mA < p99-avg 1.55 mA).
    # ``max(|max|, |min|)`` recovers the real peak regardless of wiring polarity;
    # it also leaves the correctly-wired (positive) case unchanged.  The window
    # average is unaffected — it comes from the abs'd charge integral, which is
    # direction-independent — so this only fixes the peak/percentile stats.
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
        "pwr_avg": np.abs(np.asarray(pwr_avg, dtype=np.float64)),
        "pwr_int": np.abs(np.asarray(pwr_int, dtype=np.float64)),
    }


def _gated_mask_axis(a: dict[str, Any]) -> tuple[Any, str]:
    """Return the timestamp axis used to align packets with GPI polls."""
    host_time = a.get("host_time")
    if host_time is not None and getattr(host_time, "size", 0) and not bool(host_time.size == 0):
        import numpy as np

        if not np.isnan(host_time).any():
            return host_time, "host_packet_arrival_time64"
    return a["mid"], "device_packet_midpoint_time64"


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


def _fullrate_energy_over_windows(
    *,
    cur_chunks: list[Any],
    volt_chunks: list[Any],
    anchors: list[tuple[int, int, float]],
    poll_samples: list[tuple[int, int]],
) -> dict[str, Any] | None:
    """Integrate raw full-rate current/voltage over the GPI-high windows.

    This is the *reference* energy method used by AutoDeploy: rather than
    summing the device's 1 kHz statistics ``integral`` fields, it integrates
    the full-rate (``s/i/!data`` + ``s/v/!data``) sample stream directly.  Any
    high-frequency current content (e.g. SIMO buck switching spikes) that the
    decimated statistics stream smooths away is captured here.

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
    slope = time64.SECOND / sr
    i0, u0 = idx[0], utc[0]
    sample_utc = u0 + (np.arange(n, dtype=np.float64) - i0) * slope

    windows = _segment_gpi_windows(poll_samples)
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
        dur_s = (fall - rise) / time64.SECOND
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
        "method": "fullrate_trapezoid_integral",
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
) -> tuple[list[GatedPowerWindow], PowerSummary]:
    """Integrate the gated window(s) from on-device stat-packet integrals.

    Each packet carries the instrument's full-rate charge/energy integral over a
    ~1 ms sub-window, so summing the packets whose midpoint falls inside a
    GPIO-high window gives exact window charge/energy.  The per-packet
    avg/max samples within the window yield the spike-robust distribution
    (median / p95 / p99 / glitch-robust peak) so a lone transient sample cannot
    define the headline current.
    """
    import numpy as np
    from pyjoulescope_driver import time64

    del io_voltage  # voltage is folded into the on-device power integral

    a = _stats_arrays(packets)
    windows = _segment_gpi_windows(poll_samples)
    if a["mid"].size == 0 or not windows:
        return [], PowerSummary(0.0, 0.0, 0.0, 0.0, 0.0, 0)

    mask_axis, _axis_name = _gated_mask_axis(a)
    t0 = float(mask_axis.min())
    gated_windows: list[GatedPowerWindow] = []
    total_charge = 0.0
    total_energy = 0.0
    total_duration = 0.0
    total_samples = 0
    peak_current = 0.0

    for rise, fall in windows:
        mask = (mask_axis >= rise) & (mask_axis <= fall)
        if not bool(mask.any()):
            continue
        duration_s = float(a["dur_ticks"][mask].sum() / time64.SECOND)
        if duration_s <= 0:
            continue
        charge_c = float(a["cur_int"][mask].sum())
        energy_j = float(a["pwr_int"][mask].sum())
        seg_cur_avg = a["cur_avg"][mask]
        seg_cur_max = a["cur_peak"][mask]
        seg_pwr_avg = a["pwr_avg"][mask]
        avg_current_a = charge_c / duration_s
        avg_power_w = energy_j / duration_s
        peak_current_a = float(seg_cur_max.max())
        total_charge += charge_c
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
