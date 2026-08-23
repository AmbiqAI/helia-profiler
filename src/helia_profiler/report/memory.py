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
    from ..results import MeasuredMemoryRegions, MemoryPlan

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
    """Serialise a ``MemoryPlan`` into a JSON-friendly dict.

    Schema v3 (#133): the plan is a DECISION RECORD — what hpx intended,
    computed before any compiler ran — so it no longer carries the
    measurement vocabulary (``free``/``overflow``/``has_overflow``) it wore
    in v2. The measured truth lives in ``memory_regions``, read from the
    linked ELF. The model's ``free``/``overflow`` PROPERTIES remain (the
    plan_memory stage still uses them as a plan-time capacity check).
    """
    return {
        "engine": plan.engine,
        "model_weight_bytes": plan.model_weight_bytes,
        "regions": [
            {
                "region": r.region,
                "capacity": r.capacity,
                "used": r.used,
                "consumers": [
                    {"name": c.name, "size": c.size, "kind": c.kind}
                    | ({"symbol": c.symbol} if c.symbol else {})
                    for c in r.consumers
                ],
            }
            for r in plan.regions
        ],
    }


def _serialise_memory_regions(measured: MeasuredMemoryRegions) -> dict[str, Any]:
    """Serialise the measured per-region occupancy (#133 Phase 2).

    ``free`` is emitted per region (``app.length − used``, unclamped —
    negative means the inventory and the characterized extent disagree,
    which the reader must SEE). ``unattributed`` lists allocated sections
    outside every verified window: the police flag.
    """
    return {
        "link_family": measured.link_family,
        "linker_profile": measured.linker_profile,
        "regions": [
            {
                "region": r.region,
                "window": {"start": r.window_start, "length": r.window_length},
                "app_window": {"start": r.app_start, "length": r.app_length},
                "used": r.used,
                "reserved": r.reserved,
                "free": r.free,
                "load_image": r.load_image,
                "window_provenance": r.window_provenance,
                "app_provenance": r.app_provenance,
            }
            for r in measured.regions
        ],
        "unattributed": [
            {"name": u.name, "address": u.address, "size": u.size}
            for u in measured.unattributed
        ],
        "unattributed_load_bytes": measured.unattributed_load_bytes,
    }


def _write_memory_breakdown(ctx: PipelineContext, detail_dir: Path) -> Path:
    """Write detailed memory breakdown: binary sections, arena, per-layer cache."""
    pmu = ctx.captured_pmu
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
        if bs.reserved:
            # The linker's .heap reservation: sized to whatever remained in
            # the region rather than to a requirement, so it states leftover
            # space, not need. Excluded from bss above and reported here so
            # the footprint stays reconcilable against `size`'s own Berkeley
            # totals, which fold it into bss. (.stack is deliberately NOT
            # counted here -- it is the live MSP/PSP stack.)
            data["binary_sections"]["reserved"] = bs.reserved

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

    # Memory plan — the engine-agnostic decision record
    if ctx.memory_plan is not None:
        data["memory_plan"] = _serialise_memory_plan(ctx.memory_plan)

    # Measured memory regions — the ELF classified into the verified map
    if ctx.memory_regions is not None:
        data["memory_regions"] = _serialise_memory_regions(ctx.memory_regions)

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
