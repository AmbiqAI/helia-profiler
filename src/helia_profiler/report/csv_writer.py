"""Core per-layer CSV writers.

``_layer_to_flat_dict`` is the shared row-flattening helper used by both
``_write_csv``/``_write_preset_csv`` here and ``_write_json`` in
``json_writer.py``.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import ReportError
from ..results import LayerResult

if TYPE_CHECKING:
    from ..evaluation import ModelAnalysis
    from ..results import PmuResult

log = logging.getLogger("hpx")


def _layer_to_flat_dict(
    layer: LayerResult,
    analysis: ModelAnalysis | None = None,
    total_cycles: float | None = None,
) -> dict[str, Any]:
    """Flatten a LayerResult into a CSV-friendly dict."""
    row: dict[str, Any] = {"id": layer.id, "op": layer.op}
    row.update(layer.counters)
    if layer.cycles is not None:
        row["cycles"] = layer.cycles
    if total_cycles is not None:
        if layer.cycles is None or total_cycles <= 0:
            row["cycles_pct"] = None
        else:
            row["cycles_pct"] = round(layer.cycles / total_cycles * 100, 1)
    row["overflow"] = layer.overflow

    # Enrich with model analysis data when available
    if analysis is not None:
        layer_idx = int(layer.id) if isinstance(layer.id, (int, float)) else None
        if layer_idx is not None and 0 <= layer_idx < len(analysis.layers):
            la = analysis.layers[layer_idx]
            row["macs"] = la.macs
            row["ops"] = la.ops
            if la.macs > 0 and layer.cycles:
                row["cycles_per_mac"] = round(layer.cycles / la.macs, 2)

    return row


def _write_csv(
    pmu: PmuResult,
    output_dir: Path,
    analysis: ModelAnalysis | None = None,
) -> Path:
    """Write merged per-layer profiling results as CSV."""
    layers = pmu.layers
    if not layers:
        raise ReportError("No layer data to write.")

    out_path = output_dir / "profile_results.csv"
    total_cycles = sum(layer.cycles or 0 for layer in layers)
    rows = [_layer_to_flat_dict(layer, analysis, total_cycles) for layer in layers]
    fieldnames = list(rows[0].keys())
    # Ensure enriched columns appear even if first row lacks them
    if analysis is not None:
        for col in ("macs", "ops", "cycles_per_mac"):
            if col not in fieldnames:
                fieldnames.append(col)

    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    log.info("Wrote CSV report: %s (%d layers)", out_path, len(layers))
    return out_path


def _write_preset_csv(
    preset_name: str,
    layers: list[LayerResult],
    output_dir: Path,
) -> Path:
    """Write per-layer results for a single PMU preset as CSV."""
    out_path = output_dir / f"profile_{preset_name}.csv"
    if not layers:
        return out_path

    total_cycles = sum(layer.cycles or 0 for layer in layers)
    rows = [_layer_to_flat_dict(layer, total_cycles=total_cycles) for layer in layers]
    fieldnames = list(rows[0].keys())

    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    log.info("Wrote preset CSV: %s (%d layers)", out_path, len(layers))
    return out_path
