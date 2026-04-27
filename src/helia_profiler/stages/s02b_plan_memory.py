"""Stage 2b — Plan memory: choose placement and validate against capacity.

Two responsibilities:

1. **Resolve placement** — translate ``config.model.model_location`` (one of
   ``auto`` / ``tcm`` / ``sram`` / ``mram`` / ``psram``) plus the SoC
   memory layout into concrete ``arena_region`` and ``weights_region``
   values written to ``ctx``.  These drive the section attributes the
   firmware template applies to ``model_data[]`` and ``g_arena[]``.

2. **Build / validate the memory plan** — produce a ``MemoryPlan`` on
   ``ctx.memory_plan`` describing how much of each SoC memory region will
   be consumed.  Engines that know their layout (heliaAOT) supply the
   plan directly via ``EngineArtifacts.memory_plan``; otherwise we
   synthesise a single-arena plan from arena/model sizes and the
   resolved placement.  Each region is then sized against the SoC's
   ``MemoryLayout`` and any overflow raises ``PlatformError`` with an
   actionable hint *before* firmware is built.

Auto policy (greedy fastest-fit, arena prioritized over weights):

* both fit in TCM → both in TCM
* arena fits in TCM, weights fit in SRAM → arena=TCM, weights=SRAM
* arena fits in TCM, weights need MRAM → arena=TCM, weights=MRAM
* arena needs SRAM → arena=SRAM, weights=MRAM
* arena needs MRAM → arena=MRAM, weights=MRAM (rare; arena cannot be
  truly placed in non-volatile MRAM, so this case fails validation)

``auto`` never falls back to PSRAM; PSRAM requires explicit opt-in
because of the runtime upload handshake.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..errors import PlatformError
from ..pipeline import PipelineContext
from ..platform import MemoryLayout, SocDef
from ..results import MemoryConsumer, MemoryPlan, MemoryRegionUsage

if TYPE_CHECKING:
    from ..config import ProfileConfig

log = logging.getLogger("hpx")


# Mapping of MemoryPlan region names to MemoryLayout fields.
_REGION_FIELDS: dict[str, str] = {
    "MRAM": "mram_kb",
    "SRAM": "sram_kb",
    "DTCM": "dtcm_kb",
    "ITCM": "itcm_kb",
    "PSRAM": "psram_kb",
}

# Logical region (used by ctx.{arena,weights}_region) → physical region
# (used in MemoryPlan / NSX layout).  TCM means DTCM here — ITCM is a
# code-only region and not eligible for arena/weights.
_LOGICAL_TO_PHYSICAL = {
    "tcm": "DTCM",
    "sram": "SRAM",
    "mram": "MRAM",
    "psram": "PSRAM",
}


# Slack we leave unallocated in TCM and SRAM so stack/heap/BSS for the
# rest of the firmware still fits.  Conservative; can be tuned per-board
# later if needed.
_TCM_SLACK_BYTES = 8 * 1024
_SRAM_SLACK_BYTES = 32 * 1024


class PlanMemoryStage:
    @property
    def name(self) -> str:
        return "plan_memory"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        # 1. Resolve logical placement first (used by both AOT and
        #    interpreter paths for downstream firmware rendering).
        arena_region, weights_region = _resolve_placement(ctx)
        ctx.arena_region = arena_region
        ctx.weights_region = weights_region
        log.info(
            "Placement: arena=%s, weights=%s (model_location=%s)",
            arena_region,
            weights_region,
            ctx.config.model.model_location,
        )

        # 2. Build / select the memory plan.
        plan = self._select_plan(ctx)
        plan = self._apply_capacities(plan, ctx)
        self._validate(plan)

        ctx.memory_plan = plan
        ctx.run_metadata.memory_plan = plan

        log.info("Memory plan (%s):", plan.engine)
        for r in plan.regions:
            if r.capacity > 0 or r.used > 0:
                pct = (r.used * 100 / r.capacity) if r.capacity else 0
                log.info(
                    "  %-6s %7d / %7d B (%5.1f%%)",
                    r.region, r.used, r.capacity, pct,
                )

    # ------------------------------------------------------------------
    # Plan construction
    # ------------------------------------------------------------------

    def _select_plan(self, ctx: PipelineContext) -> MemoryPlan:
        """Prefer the engine-supplied plan; synthesise one otherwise."""
        artifacts = ctx.engine_artifacts
        if artifacts is not None and artifacts.memory_plan is not None:
            return artifacts.memory_plan

        return self._synthesise_plan(ctx)

    def _synthesise_plan(self, ctx: PipelineContext) -> MemoryPlan:
        """Build a single-arena plan for engines (tflm/heliaRT) that don't
        expose per-region allocations themselves.

        Uses the resolved ``ctx.arena_region`` and ``ctx.weights_region``
        for placement, so the plan reflects what the firmware template
        will actually emit.
        """
        engine = ctx.config.engine.type.value
        arena = int(ctx.config.model.arena_size or 0)

        try:
            model_bytes = int(ctx.config.model.path.stat().st_size)
        except OSError:
            model_bytes = 0

        weight_phys = _LOGICAL_TO_PHYSICAL.get(ctx.weights_region or "mram", "MRAM")
        arena_phys = _LOGICAL_TO_PHYSICAL.get(ctx.arena_region or "tcm", "DTCM")

        region_map: dict[str, list[MemoryConsumer]] = {}
        if model_bytes > 0:
            region_map.setdefault(weight_phys, []).append(
                MemoryConsumer(
                    name="model_flatbuffer", size=model_bytes, kind="weights",
                )
            )
        if arena > 0:
            region_map.setdefault(arena_phys, []).append(
                MemoryConsumer(
                    name="tensor_arena", size=arena, kind="arena",
                )
            )

        regions = tuple(
            MemoryRegionUsage(
                region=name,
                capacity=0,  # filled by _apply_capacities
                used=sum(c.size for c in consumers),
                consumers=tuple(consumers),
            )
            for name, consumers in region_map.items()
        )

        return MemoryPlan(
            engine=engine,
            regions=regions,
            model_weight_bytes=model_bytes,
        )

    # ------------------------------------------------------------------
    # Capacity + validation
    # ------------------------------------------------------------------

    def _apply_capacities(
        self, plan: MemoryPlan, ctx: PipelineContext,
    ) -> MemoryPlan:
        """Fill in per-region capacities from the resolved SoC layout."""
        if ctx.soc is None:
            return plan

        layout: MemoryLayout = ctx.soc.memory

        by_region = {r.region.upper(): r for r in plan.regions}

        rebuilt: list[MemoryRegionUsage] = []
        for region_name, field in _REGION_FIELDS.items():
            cap_kb = int(getattr(layout, field, 0))
            cap_bytes = cap_kb * 1024
            existing = by_region.pop(region_name, None)
            if existing is not None:
                rebuilt.append(MemoryRegionUsage(
                    region=region_name,
                    capacity=cap_bytes,
                    used=existing.used,
                    consumers=existing.consumers,
                ))
            elif cap_bytes > 0:
                rebuilt.append(MemoryRegionUsage(
                    region=region_name, capacity=cap_bytes, used=0,
                ))

        for leftover in by_region.values():
            rebuilt.append(leftover)

        return MemoryPlan(
            engine=plan.engine,
            regions=tuple(rebuilt),
            model_weight_bytes=plan.model_weight_bytes,
            has_overflow=any(r.overflow for r in rebuilt),
        )

    def _validate(self, plan: MemoryPlan) -> None:
        offenders = [r for r in plan.regions if r.overflow]
        if not offenders:
            return

        lines = [
            f"  {r.region}: {r.used} B used > {r.capacity} B capacity "
            f"(over by {r.used - r.capacity} B)"
            for r in offenders
        ]
        detail = "\n".join(lines)
        first = offenders[0]

        hint = (
            f"{first.region} is over capacity.  Try one of:\n"
            "  * shrink the tensor arena (--arena-size);\n"
            "  * pick a less-aggressive placement\n"
            "    (--model-location auto / mram);\n"
            "  * move weights to PSRAM (--model-location psram) if the\n"
            "    board has PSRAM;\n"
            "  * reduce model size (quantise / prune); or\n"
            "  * pick a larger-memory board."
        )
        raise PlatformError(
            f"Memory plan does not fit:\n{detail}",
            hint=hint,
        )


# ---------------------------------------------------------------------------
# Placement resolver
# ---------------------------------------------------------------------------


def _resolve_placement(ctx: PipelineContext) -> tuple[str, str]:
    """Resolve ``(arena_region, weights_region)`` from the requested
    ``model_location`` and the SoC memory layout.

    Returns a pair of strings drawn from ``{"tcm","sram","mram","psram"}``.

    Raises ``PlatformError`` if the user requested a region the SoC
    doesn't have (e.g. ``tcm`` on a board with no DTCM, or ``psram`` on a
    board without PSRAM).
    """
    cfg = ctx.config
    soc = ctx.soc
    location = cfg.model.model_location

    # AOT engines drive their own per-tensor placement via section
    # attributes generated by the AOT compiler.  We still resolve a
    # logical pair here so reports and hints stay consistent, but the
    # firmware template ignores them on the AOT path.
    is_aot = cfg.engine.type.value == "helia_aot"

    # Capacity probe (in bytes).  If soc is None (very early call), we
    # treat all regions as unbounded; the validate pass will catch real
    # overflow later.
    tcm_cap = (soc.memory.dtcm_kb * 1024) if soc else (1 << 31)
    sram_cap = (soc.memory.sram_kb * 1024) if soc else (1 << 31)
    psram_cap = (soc.memory.psram_kb * 1024) if soc else 0

    arena_size = int(cfg.model.arena_size or 0)
    try:
        model_size = int(cfg.model.path.stat().st_size)
    except OSError:
        model_size = 0

    # Explicit selections ----------------------------------------------------
    if location == "psram":
        if psram_cap == 0:
            raise PlatformError(
                f"model_location=psram requested, but board "
                f"{cfg.target.board} has no PSRAM.",
                hint="Use --model-location auto | mram | sram | tcm, "
                     "or pick a PSRAM-capable board.",
            )
        # weights uploaded to PSRAM at runtime; arena lives in SRAM.
        return ("sram", "psram")

    if location == "tcm":
        if tcm_cap == 0:
            raise PlatformError(
                f"model_location=tcm requested, but board "
                f"{cfg.target.board} has no DTCM.",
                hint="Use --model-location auto on AP3/AP4 boards, "
                     "or pick an AP5 board with DTCM.",
            )
        return ("tcm", "tcm")

    if location == "sram":
        return ("sram", "sram")

    if location == "mram":
        # Legacy default: arena in fastest available, weights in MRAM
        # (rodata).  Mirrors pre-auto behavior.
        arena = "tcm" if tcm_cap > 0 else "sram"
        return (arena, "mram")

    # auto -------------------------------------------------------------------
    if location != "auto":
        # Should be caught by preflight, but belt-and-braces.
        raise PlatformError(
            f"Unknown model_location: {location!r}",
            hint="Valid: auto, tcm, sram, mram, psram.",
        )

    # AOT: keep simple — auto means weights in MRAM, arena in TCM.  The
    # AOT compiler will further redistribute tensors via PUT_IN_* macros.
    if is_aot:
        arena = "tcm" if tcm_cap > 0 else "sram"
        return (arena, "mram")

    # Greedy fastest-fit, arena first.
    tcm_budget = max(0, tcm_cap - _TCM_SLACK_BYTES)
    sram_budget = max(0, sram_cap - _SRAM_SLACK_BYTES)

    arena_in_tcm = arena_size > 0 and arena_size <= tcm_budget
    arena_in_sram = (not arena_in_tcm) and arena_size > 0 and arena_size <= sram_budget

    if arena_in_tcm:
        arena_region = "tcm"
        # Subtract arena from the TCM budget when deciding weights.
        remaining_tcm = tcm_budget - arena_size
        if model_size > 0 and model_size <= remaining_tcm:
            weights_region = "tcm"
        elif model_size > 0 and model_size <= sram_budget:
            weights_region = "sram"
        else:
            weights_region = "mram"
    elif arena_in_sram:
        arena_region = "sram"
        remaining_sram = sram_budget - arena_size
        # weights in TCM if it fits there alone (TCM is faster than SRAM)
        if model_size > 0 and model_size <= tcm_budget:
            weights_region = "tcm"
        elif model_size > 0 and model_size <= remaining_sram:
            weights_region = "sram"
        else:
            weights_region = "mram"
    else:
        # arena doesn't fit in fast memory; weights stay in MRAM
        # (rodata).  Validation pass will fail if even MRAM is short.
        arena_region = "sram" if arena_size <= sram_cap else "mram"
        weights_region = "mram"

    return (arena_region, weights_region)
