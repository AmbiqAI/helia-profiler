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
from ..evaluation.layer_attribution import LayerAttribution, LayerAttributor
from ..results import LayerResult

if TYPE_CHECKING:
    from ..evaluation import ModelAnalysis
    from ..results import PmuResult

log = logging.getLogger("hpx")


def _layer_to_flat_dict(
    layer: LayerResult,
    attribution: LayerAttribution | None = None,
    total_cycles: float | None = None,
) -> dict[str, Any]:
    """Flatten a LayerResult into a CSV-friendly dict.

    ``attribution`` carries the analysis facts already resolved on the
    ORIGINAL operator index (#218) — this function never indexes the
    analysis positionally.
    """
    row: dict[str, Any] = {"id": layer.id, "op": layer.op}
    if attribution is not None and attribution.explicit and attribution.source_index is not None:
        row["source_index"] = attribution.source_index
    row.update(layer.counters)
    if layer.cycles is not None:
        row["cycles"] = layer.cycles
    if total_cycles is not None:
        if layer.cycles is None or total_cycles <= 0:
            row["cycles_pct"] = None
        else:
            row["cycles_pct"] = round(layer.cycles / total_cycles * 100, 1)
    row["overflow"] = layer.overflow

    if attribution is not None and attribution.macs is not None:
        row["macs"] = attribution.macs
        row["ops"] = attribution.ops
        if attribution.macs > 0 and layer.cycles:
            row["cycles_per_mac"] = round(layer.cycles / attribution.macs, 2)

    return row


def _write_csv(
    pmu: PmuResult,
    output_dir: Path,
    analysis: ModelAnalysis | None = None,
    aot_op_manifest: list[dict[str, Any]] | None = None,
) -> Path:
    """Write merged per-layer profiling results as CSV."""
    layers = pmu.layers
    if not layers:
        raise ReportError("No layer data to write.")

    out_path = output_dir / "profile_results.csv"
    total_cycles = sum(layer.cycles or 0 for layer in layers)
    attributor = LayerAttributor(analysis, aot_op_manifest)
    rows = [
        _layer_to_flat_dict(layer, attributor.attribute(layer.id, layer.op), total_cycles)
        for layer in layers
    ]
    fieldnames = list(rows[0].keys())
    # Ensure enriched columns appear even if first row lacks them
    if any("source_index" in row for row in rows) and "source_index" not in fieldnames:
        fieldnames.insert(2, "source_index")
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
