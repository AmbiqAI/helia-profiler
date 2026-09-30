"""Power capture serialisation and the detailed power summary CSV."""

from __future__ import annotations

import csv
import logging
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..power.base import PowerResult, PowerSummary
    from ..results.artifacts import PowerRun

log = logging.getLogger("hpx")


def _power_summary_to_dict(summary: PowerSummary) -> dict[str, Any]:
    """``summary.json``'s published power keys.

    ``profile_results.json`` and ``power_summary.csv`` publish the
    ``PowerSummary`` field names instead.
    """
    return {
        "avg_current_a": summary.avg_current_a,
        "avg_power_w": summary.avg_power_w,
        "peak_current_a": summary.peak_current_a,
        "energy_j": summary.energy_j,
        "capture_duration_s": summary.duration_s,
    }


def _power_run_records(power_run: PowerRun | None) -> dict[str, dict[str, Any]]:
    """The power run's ``terminal`` and ``on_device_summary``, whichever exist."""
    if power_run is None:
        return {}
    records: dict[str, dict[str, Any]] = {}
    if power_run.terminal is not None:
        records["terminal"] = asdict(power_run.terminal)
    if power_run.on_device_summary is not None:
        records["on_device_summary"] = asdict(power_run.on_device_summary)
    return records


def _write_power_csv(power: PowerResult, output_dir: Path) -> Path:
    """Write power summary as a separate CSV.

    The high-level summary.json reports only the gated (inference) portion. This
    detailed CSV is the place where the non-inference whole-capture window is
    annotated, so it carries both the gated metrics and, when available, the
    ``whole_capture_window`` rows for reference.
    """
    out_path = output_dir / "power_summary.csv"
    summary = power.summary
    # Serialization boundary: the CSV is built from the flat view.
    meta = power.metadata.to_metadata_dict()
    scope = meta.get("measurement_scope", "whole_capture_window")
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["scope", "metric", "value"])
        for key, value in asdict(summary).items():
            writer.writerow([scope, key, value])

        for i, w in enumerate(power.gated_windows):
            writer.writerow([f"gated_window_{i}", "start_s", w.start_s])
            writer.writerow([f"gated_window_{i}", "duration_s", w.duration_s])
            writer.writerow([f"gated_window_{i}", "energy_j", w.energy_j])
            writer.writerow([f"gated_window_{i}", "charge_c", w.charge_c])
            writer.writerow([f"gated_window_{i}", "avg_current_a", w.avg_current_a])
            writer.writerow([f"gated_window_{i}", "avg_power_w", w.avg_power_w])
            writer.writerow([f"gated_window_{i}", "peak_current_a", w.peak_current_a])
            writer.writerow([f"gated_window_{i}", "sample_count", w.sample_count])
            # Spike-robust distribution from per-packet stats.
            writer.writerow([f"gated_window_{i}", "median_current_a", w.median_current_a])
            writer.writerow([f"gated_window_{i}", "p95_current_a", w.p95_current_a])
            writer.writerow([f"gated_window_{i}", "p99_current_a", w.p99_current_a])
            writer.writerow([f"gated_window_{i}", "peak_current_p99_a", w.peak_current_p99_a])
            writer.writerow([f"gated_window_{i}", "median_power_w", w.median_power_w])
            writer.writerow([f"gated_window_{i}", "p95_power_w", w.p95_power_w])
            writer.writerow([f"gated_window_{i}", "p99_power_w", w.p99_power_w])

        # Non-inference reference: whole captured window (annotation only).
        whole = meta.get("whole_capture_summary")
        if isinstance(whole, dict):
            for key in (
                "avg_current_a",
                "avg_power_w",
                "peak_current_a",
                "energy_j",
                "duration_s",
                "sample_count",
            ):
                if key in whole:
                    writer.writerow(["whole_capture_window", key, whole[key]])
    log.info("Wrote power summary: %s", out_path)
    return out_path
