"""Engine-agnostic memory plan serialisation and the detailed memory breakdown.

``_serialise_memory_plan`` is shared by ``summary.py`` (embeds a condensed
``memory_plan`` block in ``summary.json``) and ``_write_memory_breakdown``
below (the full ``detailed/memory.json`` report). Both also rely on
``_CACHE_COUNTERS`` to aggregate cache/memory PMU counters, so this module
owns that shared list rather than duplicating it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..pipeline import PipelineContext
    from ..results import MemoryPlan

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


def _serialise_memory_plan(plan: MemoryPlan) -> dict[str, Any]:
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
    out_path.write_text(
        json.dumps(data, indent=2, default=str),
        encoding="utf-8",
        newline="\n",
    )
    log.info("Wrote memory breakdown: %s", out_path)
    return out_path
