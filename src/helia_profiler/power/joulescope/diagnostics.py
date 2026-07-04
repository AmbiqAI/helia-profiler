"""Joulescope-local gated-window diagnostics.

Summarises how GPIO high/low windows intersect the ingested stats packets —
used to sanity-check gating alignment and to surface selected-vs-rejected
packet stats when a gated capture looks suspicious.  This is distinct from
the cross-driver failure-classification helpers in :mod:`..diagnostics`
(``classify_gate_failure`` / ``GateTransitionTiming``), which are shared by
any power driver, not just Joulescope.
"""

from __future__ import annotations

from typing import Any

from .stats import _gated_mask_axis, _segment_gpi_windows, _stats_arrays


def _gated_stats_diagnostics(
    *,
    packets: list[dict[str, Any]],
    poll_samples: list[tuple[int, int]],
) -> dict[str, Any]:
    """Summarise how the GPIO windows intersect the stats packets."""
    import numpy as np

    a = _stats_arrays(packets)
    windows = _segment_gpi_windows(poll_samples)
    mask_axis, axis_name = _gated_mask_axis(a)

    diagnostics: dict[str, Any] = {
        "mask_time_axis": axis_name,
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
        "windows": [
            {"rise_tick": int(rise), "fall_tick": int(fall)} for rise, fall in windows
        ],
    }
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
        selected_mask |= (mask_axis >= rise) & (mask_axis <= fall)

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
