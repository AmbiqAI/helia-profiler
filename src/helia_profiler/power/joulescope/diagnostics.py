"""Joulescope-local gated-window diagnostics.

Summarises how GPIO high/low windows intersect the ingested stats packets —
used to sanity-check gating alignment and to surface selected-vs-rejected
packet stats when a gated capture looks suspicious.  This is distinct from
the cross-driver failure-classification helpers in :mod:`..diagnostics`
(``classify_gate_failure`` / ``GateTransitionTiming``), which are shared by
any power driver, not just Joulescope.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..base import PowerResult, PowerSummary
from ..diagnostics import GateFailure
from ..metadata import MeasurementScope, ObservationMode, PowerIntegrity, PowerMetadata
from .stats import (
    _gated_mask_axis,
    _segment_gpi_windows,
    _stats_arrays,
    _summary_to_dict,
    _whole_summary_from_stats,
)

log = logging.getLogger("hpx")


def _poll_edge_uncertainty_s(
    reads: list[tuple[int, int, int]],
    *,
    minimum_window_s: float = 0.0,
    plan_floor_s: float | None = None,
) -> float:
    """Edge read brackets of the polled gate window(s).

    With a plan floor above the minimum, only the window the capture selects
    counts (the last high reaching the floor, else the last past the minimum);
    a high it passed over must not widen the tolerance of the window it kept.
    """
    from pyjoulescope_driver import time64

    rise: int | None = None
    rise_bound = 0
    highs: list[tuple[float, int]] = []
    for previous, current in zip(reads, reads[1:]):
        _, end, level = current
        if level and not previous[2]:
            rise = end
            rise_bound = end - previous[0]
        elif not level and previous[2] and rise is not None:
            duration_s = (end - rise) / time64.SECOND
            if duration_s >= minimum_window_s:
                highs.append((duration_s, rise_bound + end - previous[0]))
            rise = None
    if plan_floor_s is None or plan_floor_s <= minimum_window_s:
        return sum(bound for _, bound in highs) / time64.SECOND
    planned = [bound for duration_s, bound in highs if duration_s >= plan_floor_s]
    chosen = planned or [bound for _, bound in highs]
    return chosen[-1] / time64.SECOND if chosen else 0.0


def _with_gpi_poll_failures(failure: GateFailure, poll_failures: int) -> GateFailure:
    """Name failed GPI snapshot polls in *failure*'s hint, if there were any.

    The classification only sees which edges the poller observed, so a dead
    read path would otherwise read as a wiring or timing fault.
    """
    if not poll_failures:
        return failure
    return replace(
        failure,
        hint=(
            f"{failure.hint} {poll_failures} GPI snapshot poll(s) failed during the "
            "capture, so the host may have missed a gate edge the target did drive; "
            "check the Joulescope USB connection, and re-run with -vv to see the "
            "first error."
        ),
    )


def _gated_stats_diagnostics(
    *,
    packets: list[dict[str, Any]],
    poll_samples: list[tuple[int, int]],
    prefer_device_time: bool = False,
    windows_override: list[tuple[float, float]] | None = None,
    gate_edge_source: str = "gpi_snapshot_poll",
    stream_segment_count: int | None = None,
    counter_origin: tuple[int, int] | None = None,
) -> dict[str, Any]:
    import numpy as np

    a = _stats_arrays(packets, counter_origin=counter_origin)
    windows = (
        windows_override if windows_override is not None else _segment_gpi_windows(poll_samples)
    )
    mask_axis, axis_name = _gated_mask_axis(a, prefer_device_time=prefer_device_time)

    if counter_origin is not None:
        axis_name = "device_sample_counter_relative_time64"
    diagnostics: dict[str, Any] = {
        "mask_time_axis": axis_name,
        "gate_edge_source": gate_edge_source,
        "window_count": len(windows),
        "gpi_poll_count": len(poll_samples),
        "stat_packet_count": int(mask_axis.size),
        "selected_packets": 0,
        "rejected_packets": int(mask_axis.size),
        "selected_median_current_a": 0.0,
        "rejected_median_current_a": 0.0,
        "selected_min_current_a": 0.0,
        "selected_max_current_a": 0.0,
        "rejected_min_current_a": 0.0,
        "rejected_max_current_a": 0.0,
        "mask_axis_first_tick": None,
        "mask_axis_last_tick": None,
        "packet_midpoint_first_tick": None,
        "packet_midpoint_last_tick": None,
        "gpi_first_tick": None,
        "gpi_last_tick": None,
        "windows": [{"rise_tick": int(rise), "fall_tick": int(fall)} for rise, fall in windows],
    }
    if stream_segment_count is not None:
        # How many raw high segments the GPI stream resolved — an undriven
        # gate line coupling to device activity shows up here as hundreds of
        # micro-pulses, which is the tell that separates a noisy line from a
        # clean strobe when reading artifacts after the fact.
        diagnostics["stream_segment_count"] = stream_segment_count
    if poll_samples:
        diagnostics["gpi_first_tick"] = int(poll_samples[0][0])
        diagnostics["gpi_last_tick"] = int(poll_samples[-1][0])
    if mask_axis.size == 0:
        return diagnostics

    diagnostics["mask_axis_first_tick"] = int(mask_axis.min())
    diagnostics["mask_axis_last_tick"] = int(mask_axis.max())
    diagnostics["packet_midpoint_first_tick"] = int(a["mid"].min())
    diagnostics["packet_midpoint_last_tick"] = int(a["mid"].max())

    selected_mask = np.zeros(mask_axis.shape, dtype=bool)
    for rise, fall in windows:
        selected_mask |= (mask_axis >= rise) & (mask_axis < fall)

    selected = a["cur_avg"][selected_mask]
    rejected = a["cur_avg"][~selected_mask]
    diagnostics["selected_packets"] = int(selected.size)
    diagnostics["rejected_packets"] = int(rejected.size)
    if selected.size:
        diagnostics["selected_median_current_a"] = float(np.median(selected))
        diagnostics["selected_min_current_a"] = float(selected.min())
        diagnostics["selected_max_current_a"] = float(selected.max())
    if rejected.size:
        diagnostics["rejected_median_current_a"] = float(np.median(rejected))
        diagnostics["rejected_min_current_a"] = float(rejected.min())
        diagnostics["rejected_max_current_a"] = float(rejected.max())
    return diagnostics


def _degraded_observation_result(
    *,
    packets: list[dict[str, Any]],
    family: str,
    device_path: str,
    io_voltage: float,
    sync_input_index: int,
    stats_rate_hz: int,
    scnt: int,
    poll_count: int,
    duration_s: float,
    captured_s: float,
    saw_gate_rise: bool,
    saw_gate_fall: bool,
    short_pulses_ignored: int,
    failure: GateFailure,
    gating_diagnostics: dict[str, Any] | None = None,
) -> PowerResult:
    whole_summary = _whole_summary_from_stats(packets)
    return PowerResult(
        summary=whole_summary,
        statistics=packets,
        metadata=PowerMetadata(
            driver=f"joulescope-{family}",
            device=device_path,
            io_voltage=io_voltage,
            measurement_scope=MeasurementScope.FREE_FORM_CAPTURE,
            gating_method="gpi_snapshot_poll+host_stats_integral",
            observation_mode=ObservationMode.FREE_FORM,
            integrity=PowerIntegrity.DEGRADED,
            gate_rise_observed=saw_gate_rise,
            gate_fall_observed=saw_gate_fall,
            gate_failure=failure,
            sync_input_index=sync_input_index,
            stats_rate_hz=stats_rate_hz,
            stats_scnt=scnt,
            window_count=0,
            gpi_poll_count=poll_count,
            stat_packets=len(packets),
            capture_window_s=round(captured_s, 4),
            capture_safety_bound_s=duration_s,
            short_gate_pulses_ignored=short_pulses_ignored,
            whole_capture_summary=_summary_to_dict(whole_summary),
            gating_diagnostics=gating_diagnostics if gating_diagnostics else None,
        ),
    )


def _attach_capture_diagnostics(
    metadata: PowerMetadata,
    packets: list[dict[str, Any]],
    gated_summary: PowerSummary,
    gating_diagnostics: dict[str, Any],
    stats_rate_hz: int,
) -> None:
    if packets:
        whole_summary = _whole_summary_from_stats(packets)
        metadata.whole_capture_summary = _summary_to_dict(whole_summary)
        diagnostics = gating_diagnostics
        metadata.gating_diagnostics = diagnostics
        sane_window = gated_summary.avg_current_a > whole_summary.avg_current_a
        metadata.gated_vs_whole_current_ok = sane_window
        if not sane_window:
            log.warning(
                "Joulescope gated avg current %.3f mA <= whole-capture avg %.3f mA; "
                "gate/stats timing may be misaligned or the firmware may be sleeping "
                "inside the asserted window",
                gated_summary.avg_current_a * 1000.0,
                whole_summary.avg_current_a * 1000.0,
            )
        if diagnostics["selected_packets"] == 0:
            log.warning(
                "Joulescope gated window selected zero stat packets; check GPI/stat "
                "timestamp alignment and stats_rate_hz=%d",
                stats_rate_hz,
            )
        else:
            log.info(
                "Joulescope gated packet mask: selected=%d rejected=%d, "
                "selected median=%.3f mA rejected median=%.3f mA",
                diagnostics["selected_packets"],
                diagnostics["rejected_packets"],
                diagnostics["selected_median_current_a"] * 1000.0,
                diagnostics["rejected_median_current_a"] * 1000.0,
            )


def _write_gate_dump(
    dump_dir: str,
    *,
    packets: list[dict[str, Any]],
    gpi_stream_frames: list[dict[str, Any]],
    capture_start: float,
    go_release_at: float | None,
    first_high_at: float | None,
    first_low_after_high_at: float | None,
    poll_samples: list[tuple[int, int]],
    aligned_poll_samples: list[tuple[int, int]],
    gate_edge_source: str,
    raw_streamed: list[tuple[float, float]],
    streamed_gate_windows: list[tuple[float, float]] | None,
) -> None:
    try:
        rows = []
        for p in packets:
            t = p.get("time", {}) if isinstance(p, dict) else {}
            utc = (t.get("utc", {}) or {}).get("value")
            if not utc or len(utc) < 2:
                continue
            sig = p.get("signals", {})
            rows.append(
                [
                    p.get("_host_time64"),
                    float(utc[0]),
                    float(utc[1]),
                    float((sig.get("current", {}).get("avg", {}) or {}).get("value", 0.0)),
                ]
            )
        out = Path(dump_dir)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"gate_dump_{int(time.time())}.json"
        path.write_text(
            json.dumps(
                {
                    "capture_start_monotonic": capture_start,
                    "go_release_monotonic": go_release_at,
                    "first_high_monotonic": first_high_at,
                    "first_low_after_high_monotonic": first_low_after_high_at,
                    "poll_samples_host_time64": poll_samples,
                    "aligned_poll_samples": aligned_poll_samples,
                    "packets_host_u0_u1_curavg": rows,
                    "gate_edge_source": gate_edge_source,
                    "gpi_stream_frame_count": len(gpi_stream_frames),
                    "gpi_stream_frames_meta": [
                        {
                            "utc": f["utc"],
                            "rate": f["sample_rate"],
                            "sample_id": f.get("sample_id"),
                            "n": f["element_count"],
                        }
                        for f in gpi_stream_frames
                    ],
                    "gpi_stream_windows_raw": raw_streamed,
                    "gpi_stream_windows": streamed_gate_windows,
                }
            ),
            encoding="utf-8",
        )
        log.info("Gate debug dump written: %s", path)
    except Exception:
        log.warning("Gate debug dump failed", exc_info=True)
