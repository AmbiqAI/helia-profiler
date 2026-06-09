"""Report generation — CSV, JSON, terminal summary, and Model Explorer overlays.

The ``write_report`` function is called by the report stage and dispatches to
the appropriate formatters based on ``OutputConfig``.

Output structure
----------------

Always generated (root of output_dir):
    summary.json          High-level machine-readable summary (cycles, memory,
                          cache highlights, binary sections, top layers).
    profile_results.csv   Merged per-layer profiling results.
    run_metadata.json     Full run metadata (config, toolchain, platform, …).

Model Explorer (``model_explorer/`` subfolder, unless ``--no-model-explorer``):
    me_overlay_<COUNTER>.json   Per-counter overlay files.

Detailed (``detailed/`` subfolder, only with ``--detailed``):
    profile_<preset>.csv        Per-preset CSV breakdowns.
    profile_<group>.csv         Per-group (compute-unit) merged CSVs.
    memory.json                 Memory breakdown (binary sections, arena,
                                per-layer cache counters).
    power_summary.csv           Power capture summary (when available).
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import ReportError
from ..model_analysis import ModelAnalysis
from ..results import BinarySections, LayerResult, PmuResult, RunMetadata

if TYPE_CHECKING:
    from ..pipeline import PipelineContext
    from ..power.base import PowerResult

log = logging.getLogger("hpx")

# Memory-related PMU counter names used for cache/memory summaries.
_CACHE_COUNTERS = (
    "ARM_PMU_L1D_CACHE",
    "ARM_PMU_L1D_CACHE_RD",
    "ARM_PMU_L1D_CACHE_REFILL",
    "ARM_PMU_L1D_CACHE_MISS_RD",
    "ARM_PMU_L1D_CACHE_WB",
    "ARM_PMU_L1D_CACHE_ALLOCATE",
    "ARM_PMU_L1I_CACHE",
    "ARM_PMU_L1I_CACHE_REFILL",
    "ARM_PMU_DTCM_ACCESS",
    "ARM_PMU_ITCM_ACCESS",
    "ARM_PMU_MEM_ACCESS",
    "ARM_PMU_BUS_ACCESS",
    "ARM_PMU_BUS_CYCLES",
)


def write_report(ctx: PipelineContext) -> list[Path]:
    """Generate all configured report outputs.

    Returns a list of paths to the files written.
    """
    assert ctx.pmu_result is not None

    output_dir = ctx.config.output.dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    fmt = ctx.config.output.format
    pmu = ctx.pmu_result
    detailed = ctx.config.output.detailed
    analysis = ctx.model_analysis

    # --- Always: primary profile results ---
    if fmt == "csv":
        p = _write_csv(pmu, output_dir, analysis)
        paths.append(p)
    elif fmt == "json":
        p = _write_json(pmu, ctx.power_result, ctx.run_metadata, output_dir)
        paths.append(p)
    else:
        raise ReportError(f"Unknown output format: '{fmt}'")

    # --- Always: summary.json ---
    p = _write_summary(ctx, output_dir)
    paths.append(p)

    # --- Always: run metadata ---
    p = _write_run_metadata(ctx, output_dir)
    paths.append(p)

    # --- heliaAOT operator manifest (engine-specific) ---
    p = _write_aot_manifest(ctx, output_dir)
    if p is not None:
        paths.append(p)

    # --- Model Explorer overlays → model_explorer/ subfolder ---
    if ctx.config.output.model_explorer:
        try:
            me_dir = output_dir / "model_explorer"
            me_dir.mkdir(parents=True, exist_ok=True)
            _write_model_explorer_overlays(ctx, me_dir, paths)
        except Exception as exc:
            raise ReportError(
                f"Model Explorer overlay generation failed: {exc}",
            ) from exc

    # --- Detailed outputs → detailed/ subfolder ---
    if detailed:
        detail_dir = output_dir / "detailed"
        detail_dir.mkdir(parents=True, exist_ok=True)

        # Per-preset CSV breakdowns
        if len(pmu.presets) > 1:
            for preset_name, pr in pmu.presets.items():
                if preset_name.startswith("_"):
                    continue
                p = _write_preset_csv(preset_name, pr.layers, detail_dir)
                paths.append(p)

        # Per-group (compute-unit) unified CSVs
        if pmu.groups:
            for group_name, group_layers in pmu.groups.items():
                p = _write_preset_csv(group_name, group_layers, detail_dir)
                paths.append(p)

        # Memory breakdown JSON
        p = _write_memory_breakdown(ctx, detail_dir)
        paths.append(p)

        # Power summary CSV
        if ctx.power_result is not None:
            p = _write_power_csv(ctx.power_result, detail_dir)
            paths.append(p)

    return paths


# ---------------------------------------------------------------------------
# summary.json — high-level machine-readable summary (always generated)
# ---------------------------------------------------------------------------


def _write_summary(ctx: PipelineContext, output_dir: Path) -> Path:
    """Write a high-level summary JSON with cycles, memory, cache, and binary info."""
    assert ctx.pmu_result is not None
    pmu = ctx.pmu_result
    meta = pmu.meta
    layers = pmu.layers

    total_cycles = sum(layer.cycles or 0 for layer in layers)
    sorted_layers = sorted(layers, key=lambda l: l.cycles or 0, reverse=True)

    summary: dict[str, Any] = {
        "engine": ctx.config.engine.type.value,
        "layers": len(layers),
        "total_cycles": total_cycles,
        "overflow_detected": pmu.overflow_detected,
    }

    # Top layers by cycles
    summary["top_layers"] = [
        {
            "op": l.op,
            "cycles": l.cycles or 0,
            "pct": round((l.cycles or 0) / total_cycles * 100, 1) if total_cycles else 0,
        }
        for l in sorted_layers[:5]
    ]

    # Memory from firmware meta
    mem: dict[str, Any] = {}
    if meta.arena_size is not None:
        mem["arena_size"] = meta.arena_size
    if meta.allocated_arena is not None:
        mem["allocated_arena"] = meta.allocated_arena
    if meta.model_size is not None:
        mem["model_size"] = meta.model_size
    if meta.num_tensors is not None:
        mem["num_tensors"] = meta.num_tensors
    if meta.input_size is not None:
        mem["input_size"] = meta.input_size
    if meta.output_size is not None:
        mem["output_size"] = meta.output_size
    if mem:
        summary["memory"] = mem

    # Memory plan — engine-agnostic per-region usage
    if ctx.memory_plan is not None:
        summary["memory_plan"] = _serialise_memory_plan(ctx.memory_plan)

    # Binary sections
    if ctx.binary_sections is not None:
        bs = ctx.binary_sections
        summary["binary"] = {
            "text": bs.text,
            "data": bs.data,
            "bss": bs.bss,
            "total": bs.total,
        }

    # Cache / memory counter totals (summed across all layers)
    cache: dict[str, float] = {}
    for layer in layers:
        for cname in _CACHE_COUNTERS:
            if cname in layer.counters:
                cache[cname] = cache.get(cname, 0) + layer.counters[cname]
    if cache:
        # Compute derived metrics
        l1d_accesses = cache.get("ARM_PMU_L1D_CACHE_RD", cache.get("ARM_PMU_L1D_CACHE", 0))
        l1d_misses = cache.get(
            "ARM_PMU_L1D_CACHE_MISS_RD", cache.get("ARM_PMU_L1D_CACHE_REFILL", 0)
        )
        if l1d_accesses > 0:
            cache["l1d_hit_rate_pct"] = round((1 - l1d_misses / l1d_accesses) * 100, 2)
        summary["cache"] = cache

    # Model analysis — MACs, OPS, TOPS
    if ctx.model_analysis is not None:
        ma = ctx.model_analysis
        analysis_dict: dict[str, Any] = {
            "total_macs": ma.total_macs,
            "total_ops": ma.total_ops,
            "num_parameters": ma.num_parameters,
        }
        if total_cycles > 0 and ma.total_ops > 0:
            analysis_dict["cycles_per_mac"] = (
                round(total_cycles / ma.total_macs, 2) if ma.total_macs else None
            )
            analysis_dict["cycles_per_op"] = round(total_cycles / ma.total_ops, 2)
        summary["model_analysis"] = analysis_dict

    # Power summary
    if ctx.power_result is not None:
        ps = ctx.power_result.summary
        summary["power"] = {
            "avg_current_a": ps.avg_current_a,
            "avg_power_w": ps.avg_power_w,
            "peak_current_a": ps.peak_current_a,
            "energy_j": ps.energy_j,
        }

    # Compute TOPS/W if both model analysis and power data are available
    if ctx.model_analysis is not None and ctx.power_result is not None:
        ma = ctx.model_analysis
        ps = ctx.power_result.summary
        if ps.avg_power_w and ps.avg_power_w > 0 and ps.duration_s and ps.duration_s > 0:
            tops = ma.total_ops / 1e12 / ps.duration_s
            tops_per_watt = tops / ps.avg_power_w
            summary.setdefault("model_analysis", {})["tops"] = round(tops, 6)
            summary.setdefault("model_analysis", {})["tops_per_watt"] = round(tops_per_watt, 6)

    out_path = output_dir / "summary.json"
    out_path.write_text(json.dumps(summary, indent=2, default=str))
    log.info("Wrote summary: %s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# memory.json — detailed memory breakdown (detailed/ only)
# ---------------------------------------------------------------------------


def _serialise_memory_plan(plan: Any) -> dict[str, Any]:
    """Serialise a ``MemoryPlan`` into a JSON-friendly dict."""
    return {
        "engine": plan.engine,
        "model_weight_bytes": plan.model_weight_bytes,
        "has_overflow": plan.has_overflow,
        "regions": [
            {
                "region": r.region,
                "capacity": r.capacity,
                "used": r.used,
                "free": r.free,
                "overflow": r.overflow,
                "consumers": [
                    {"name": c.name, "size": c.size, "kind": c.kind} for c in r.consumers
                ],
            }
            for r in plan.regions
        ],
    }


def _write_memory_breakdown(ctx: PipelineContext, detail_dir: Path) -> Path:
    """Write detailed memory breakdown: binary sections, arena, per-layer cache."""
    assert ctx.pmu_result is not None
    pmu = ctx.pmu_result
    meta = pmu.meta
    layers = pmu.layers

    data: dict[str, Any] = {}

    # Binary sections
    if ctx.binary_sections is not None:
        bs = ctx.binary_sections
        data["binary_sections"] = {
            "text": bs.text,
            "data": bs.data,
            "bss": bs.bss,
            "total": bs.total,
        }

    # Arena / tensor info from firmware meta
    arena: dict[str, Any] = {}
    if meta.arena_size is not None:
        arena["arena_size"] = meta.arena_size
    if meta.allocated_arena is not None:
        arena["allocated_arena"] = meta.allocated_arena
    if meta.num_tensors is not None:
        arena["num_tensors"] = meta.num_tensors
    if meta.num_inputs is not None:
        arena["num_inputs"] = meta.num_inputs
    if meta.num_outputs is not None:
        arena["num_outputs"] = meta.num_outputs
    if meta.model_size is not None:
        arena["model_size"] = meta.model_size
    if arena:
        data["arena"] = arena

    # Memory plan — engine-agnostic per-region usage
    if ctx.memory_plan is not None:
        data["memory_plan"] = _serialise_memory_plan(ctx.memory_plan)

    # Per-layer cache/memory counters
    per_layer: list[dict[str, Any]] = []
    for layer in layers:
        row: dict[str, Any] = {"op": layer.op}
        layer_cache = {k: v for k, v in layer.counters.items() if k in _CACHE_COUNTERS}
        if layer_cache:
            row["counters"] = layer_cache
            per_layer.append(row)
    if per_layer:
        data["per_layer_memory"] = per_layer

    # Aggregate cache totals
    totals: dict[str, float] = {}
    for layer in layers:
        for cname in _CACHE_COUNTERS:
            if cname in layer.counters:
                totals[cname] = totals.get(cname, 0) + layer.counters[cname]
    if totals:
        l1d_accesses = totals.get("ARM_PMU_L1D_CACHE_RD", totals.get("ARM_PMU_L1D_CACHE", 0))
        l1d_misses = totals.get(
            "ARM_PMU_L1D_CACHE_MISS_RD", totals.get("ARM_PMU_L1D_CACHE_REFILL", 0)
        )
        if l1d_accesses > 0:
            totals["l1d_hit_rate_pct"] = round((1 - l1d_misses / l1d_accesses) * 100, 2)
        data["cache_totals"] = totals

    out_path = detail_dir / "memory.json"
    out_path.write_text(json.dumps(data, indent=2, default=str))
    log.info("Wrote memory breakdown: %s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# heliaAOT operator manifest persistence
# ---------------------------------------------------------------------------


def _write_aot_manifest(ctx: PipelineContext, output_dir: Path) -> Path | None:
    """Persist the heliaAOT operator manifest into the report directory.

    The manifest captures exactly which operators the AOT compiler emitted
    (after fusion / DCE / etc.) together with input/output tensor shapes
    and dtypes — so a post-run consumer can line the CSV layer rows up
    with the actual compiled graph.  Silently no-ops for non-AOT engines
    or when the manifest is empty.
    """
    artifacts = getattr(ctx, "engine_artifacts", None)
    if artifacts is None:
        return None
    manifest = artifacts.aot_op_manifest
    if not manifest:
        return None
    out_path = output_dir / "aot_operator_manifest.json"
    out_path.write_text(json.dumps(manifest, indent=2, default=str))
    log.info("Wrote AOT operator manifest: %s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# Core CSV / JSON writers
# ---------------------------------------------------------------------------


def _layer_to_flat_dict(
    layer: LayerResult,
    analysis: ModelAnalysis | None = None,
) -> dict[str, Any]:
    """Flatten a LayerResult into a CSV-friendly dict."""
    row: dict[str, Any] = {"id": layer.id, "op": layer.op}
    row.update(layer.counters)
    if layer.cycles is not None:
        row["cycles"] = layer.cycles
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
    rows = [_layer_to_flat_dict(layer, analysis) for layer in layers]
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

    rows = [_layer_to_flat_dict(layer) for layer in layers]
    fieldnames = list(rows[0].keys())

    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    log.info("Wrote preset CSV: %s (%d layers)", out_path, len(layers))
    return out_path


def _write_json(
    pmu: PmuResult,
    power: PowerResult | None,
    run_metadata: RunMetadata,
    output_dir: Path,
) -> Path:
    """Write full profiling results as JSON."""
    out_path = output_dir / "profile_results.json"

    data: dict[str, Any] = {
        "metadata": _metadata_to_dict(run_metadata),
        "summary": _firmware_meta_to_dict(pmu.meta),
        "layers": [_layer_to_flat_dict(l) for l in pmu.layers],
        "presets": {
            name: {
                "layers": [_layer_to_flat_dict(l) for l in pr.layers],
                "iteration_count": len(pr.iterations),
            }
            for name, pr in pmu.presets.items()
        },
        "overflow_detected": pmu.overflow_detected,
    }

    if power is not None:
        data["power"] = {
            "avg_current_a": power.summary.avg_current_a,
            "avg_power_w": power.summary.avg_power_w,
            "peak_current_a": power.summary.peak_current_a,
            "energy_j": power.summary.energy_j,
            "duration_s": power.summary.duration_s,
            "sample_count": power.summary.sample_count,
        }

    out_path.write_text(json.dumps(data, indent=2, default=str))
    log.info("Wrote JSON report: %s", out_path)
    return out_path


def _write_power_csv(power: PowerResult, output_dir: Path) -> Path:
    """Write power summary as a separate CSV."""
    out_path = output_dir / "power_summary.csv"
    summary = power.summary
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "value"])
        writer.writerow(["avg_current_a", summary.avg_current_a])
        writer.writerow(["avg_power_w", summary.avg_power_w])
        writer.writerow(["peak_current_a", summary.peak_current_a])
        writer.writerow(["energy_j", summary.energy_j])
        writer.writerow(["duration_s", summary.duration_s])
        writer.writerow(["sample_count", summary.sample_count])
    log.info("Wrote power summary: %s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------


def _firmware_meta_to_dict(meta: Any) -> dict[str, Any]:
    """Convert FirmwareMeta to a JSON-safe dict, dropping None values."""
    from ..results import FirmwareMeta

    if isinstance(meta, FirmwareMeta):
        return {k: v for k, v in asdict(meta).items() if v is not None}
    return {}


def _metadata_to_dict(meta: RunMetadata) -> dict[str, Any]:
    """Convert RunMetadata to a JSON-safe dict."""
    d: dict[str, Any] = {
        "hpx_version": meta.hpx_version,
        "run_id": meta.run_id,
        "timestamp": meta.timestamp,
    }
    if meta.config_snapshot:
        d["config"] = meta.config_snapshot
    if meta.platform is not None:
        d["platform"] = asdict(meta.platform)
    if meta.model is not None:
        d["model"] = asdict(meta.model)
    if meta.toolchain is not None:
        d["toolchain"] = asdict(meta.toolchain)
    return d


def _write_run_metadata(ctx: PipelineContext, output_dir: Path) -> Path:
    """Write run metadata (config, toolchain, model info, etc.) as JSON."""
    out_path = output_dir / "run_metadata.json"

    meta_dict = _metadata_to_dict(ctx.run_metadata)

    # Enrich with firmware-reported values from the capture
    if ctx.pmu_result is not None:
        meta_dict["firmware"] = _firmware_meta_to_dict(ctx.pmu_result.meta)

    out_path.write_text(json.dumps(meta_dict, indent=2, default=str))
    log.info("Wrote run metadata: %s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# Model Explorer overlays → model_explorer/ subfolder
# ---------------------------------------------------------------------------


def _write_model_explorer_overlays(
    ctx: PipelineContext,
    me_dir: Path,
    paths: list[Path],
) -> None:
    """Build and save Model Explorer overlay files from PMU data."""
    from .model_explorer import build_multi_metric_overlays

    assert ctx.pmu_result is not None
    layers = ctx.pmu_result.layers
    if not layers:
        return

    # Extract per-metric node_key→value dicts from layer data.
    #
    # Model Explorer matches nodes by ID (integer string).  For TFLite
    # models the node ID is the sequential operator index in the graph.
    #
    # AOT firmware emits "TYPE:id" in the Op column (e.g. "CONV_2D:3")
    # where `id` is the original TFLite operator index preserved through
    # AOT transforms.  We extract that suffix as the node key.
    #
    # TFLM firmware emits just the type string (e.g. "CONV_2D").  Since
    # multiple layers can share the same type, we fall back to the
    # sequential layer index, which matches TFLite graph operator order.
    metrics: dict[str, dict[str, float]] = {}
    for layer in layers:
        op_str = str(layer.op) if layer.op else ""
        if ":" in op_str:
            # AOT format — "CONV_2D:3" → use "3" as node key
            node_key = op_str.rsplit(":", 1)[1]
        else:
            # TFLM / generic — use sequential layer index
            node_key = str(layer.id)
        for key, val in layer.counters.items():
            metrics.setdefault(key, {})[node_key] = val

    if not metrics:
        return

    overlays = build_multi_metric_overlays(metrics)
    for metric_name, overlay in overlays.items():
        out_path = me_dir / f"me_overlay_{metric_name}.json"
        overlay.save(out_path)
        paths.append(out_path)
