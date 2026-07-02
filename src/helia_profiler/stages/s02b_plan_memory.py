"""Stage 2b — Plan memory: choose placement and validate against capacity.

Two responsibilities:

1. **Resolve placement** — translate split ``model.arena_location`` /
    ``model.weights_location`` controls, or compatibility ``model_location``
    presets, plus the SoC memory layout into concrete ``arena_region`` and
    ``weights_region``
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

from ..config import DEFAULT_ARENA_SIZE_BYTES
from ..errors import PlatformError
from ..engines import get_adapter
from ..pipeline import PipelineContext
from ..placement import MemoryRegion, ModelLocation, Placement
from ..platform import MemoryLayout, SocDef
from ..results import MemoryConsumer, MemoryPlan, MemoryRegionUsage

if TYPE_CHECKING:
    from ..config import ProfileConfig

log = logging.getLogger("hpx")


# Mapping of MemoryPlan region names to MemoryLayout fields.
_REGION_FIELDS: dict[MemoryRegion, str] = {
    MemoryRegion.MRAM: "mram_kb",
    MemoryRegion.SRAM: "sram_kb",
    MemoryRegion.DTCM: "dtcm_kb",
    MemoryRegion.ITCM: "itcm_kb",
    MemoryRegion.PSRAM: "psram_kb",
}

# Logical region (used by ctx.{arena,weights}_region) → physical region
# (used in MemoryPlan / NSX layout).  ``Placement.TCM`` means DTCM here —
# ITCM is a code-only region and not eligible for arena/weights.
_LOGICAL_TO_PHYSICAL: dict[Placement, MemoryRegion] = {
    Placement.TCM: MemoryRegion.DTCM,
    Placement.SRAM: MemoryRegion.SRAM,
    Placement.MRAM: MemoryRegion.MRAM,
    Placement.PSRAM: MemoryRegion.PSRAM,
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
                    r.region,
                    r.used,
                    r.capacity,
                    pct,
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
        arena = int(ctx.config.model.arena_size or DEFAULT_ARENA_SIZE_BYTES)

        try:
            model_bytes = int(ctx.config.model.path.stat().st_size)
        except OSError:
            model_bytes = 0

        weight_phys = _LOGICAL_TO_PHYSICAL.get(
            Placement(ctx.weights_region) if ctx.weights_region else Placement.MRAM,
            MemoryRegion.MRAM,
        )
        arena_phys = _LOGICAL_TO_PHYSICAL.get(
            Placement(ctx.arena_region) if ctx.arena_region else Placement.TCM,
            MemoryRegion.DTCM,
        )

        region_map: dict[str, list[MemoryConsumer]] = {}
        if model_bytes > 0:
            region_map.setdefault(weight_phys, []).append(
                MemoryConsumer(
                    name="model_flatbuffer",
                    size=model_bytes,
                    kind="weights",
                )
            )
        if arena > 0:
            region_map.setdefault(arena_phys, []).append(
                MemoryConsumer(
                    name="tensor_arena",
                    size=arena,
                    kind="arena",
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
        self,
        plan: MemoryPlan,
        ctx: PipelineContext,
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
                rebuilt.append(
                    MemoryRegionUsage(
                        region=region_name,
                        capacity=cap_bytes,
                        used=existing.used,
                        consumers=existing.consumers,
                    )
                )
            elif cap_bytes > 0:
                rebuilt.append(
                    MemoryRegionUsage(
                        region=region_name,
                        capacity=cap_bytes,
                        used=0,
                    )
                )

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


def _resolve_placement(ctx: PipelineContext) -> tuple[Placement, Placement]:
    """Resolve ``(arena_region, weights_region)`` from the requested
    placement policy and any explicit per-object overrides.

    Returns a pair of :class:`Placement` members.

    Raises ``PlatformError`` if the user requested a region the SoC
    doesn't have (e.g. ``tcm`` on a board with no DTCM, or ``psram`` on a
    board without PSRAM).
    """
    cfg = ctx.config
    soc = ctx.soc
    location = cfg.model.model_location

    # The engine adapter owns engine-specific placement policy.  Stage 2
    # populates ctx.engine_adapter; for the rare early-call path where
    # soc/adapter aren't yet available we fall back to a fresh adapter
    # via the registry.
    adapter = ctx.engine_adapter or get_adapter(cfg.engine.type)

    # Capacity probe (in bytes).  If soc is None (very early call), we
    # treat all regions as unbounded; the validate pass will catch real
    # overflow later.
    tcm_cap = (soc.memory.dtcm_kb * 1024) if soc else (1 << 31)
    sram_cap = (soc.memory.sram_kb * 1024) if soc else (1 << 31)
    psram_cap = (soc.memory.psram_kb * 1024) if soc else 0

    arena_size = int(cfg.model.arena_size or DEFAULT_ARENA_SIZE_BYTES)
    try:
        model_size = int(cfg.model.path.stat().st_size)
    except OSError:
        model_size = 0

    arena_region: Placement
    weights_region: Placement

    # Policy selections ------------------------------------------------------
    if location == ModelLocation.PSRAM:
        if psram_cap == 0:
            raise PlatformError(
                f"model_location=psram requested, but board {cfg.target.board} has no PSRAM.",
                hint="Use --model-location auto | mram | sram | tcm, "
                "or pick a PSRAM-capable board.",
            )
        # weights uploaded to PSRAM at runtime; arena lives in SRAM.
        arena_region = Placement.SRAM
        weights_region = Placement.PSRAM
    elif location == ModelLocation.TCM:
        if tcm_cap == 0:
            raise PlatformError(
                f"model_location=tcm requested, but board {cfg.target.board} has no DTCM.",
                hint="Use --model-location auto, or pick a board with DTCM.",
            )
        arena_region = Placement.TCM
        weights_region = Placement.TCM
    elif location == ModelLocation.SRAM:
        arena_region = Placement.SRAM
        weights_region = Placement.SRAM
    elif location == ModelLocation.MRAM:
        # Legacy default: arena in fastest available, weights in MRAM
        # (rodata).  Mirrors pre-auto behavior.
        arena_region = Placement.TCM if tcm_cap > 0 else Placement.SRAM
        weights_region = Placement.MRAM
    # auto -------------------------------------------------------------------
    elif location != ModelLocation.AUTO:
        # Should be caught by preflight, but belt-and-braces.
        raise PlatformError(
            f"Unknown model_location: {location!r}",
            hint="Valid: auto, tcm, sram, mram, psram.",
        )

    # Engine-specific auto policy (e.g. AOT pins arena=TCM, weights=MRAM).
    elif (
        engine_default := adapter.default_auto_placement(tcm_cap=tcm_cap, sram_cap=sram_cap)
    ) is not None:
        arena_region, weights_region = engine_default
    else:
        # Greedy fastest-fit, arena first.
        tcm_budget = max(0, tcm_cap - _TCM_SLACK_BYTES)
        sram_budget = max(0, sram_cap - _SRAM_SLACK_BYTES)

        arena_in_tcm = arena_size > 0 and arena_size <= tcm_budget
        arena_in_sram = (not arena_in_tcm) and arena_size > 0 and arena_size <= sram_budget

        if arena_in_tcm:
            arena_region = Placement.TCM
            # Subtract arena from the TCM budget when deciding weights.
            remaining_tcm = tcm_budget - arena_size
            if model_size > 0 and model_size <= remaining_tcm:
                weights_region = Placement.TCM
            elif model_size > 0 and model_size <= sram_budget:
                weights_region = Placement.SRAM
            else:
                weights_region = Placement.MRAM
        elif arena_in_sram:
            arena_region = Placement.SRAM
            remaining_sram = sram_budget - arena_size
            # weights in TCM if it fits there alone (TCM is faster than SRAM)
            if model_size > 0 and model_size <= tcm_budget:
                weights_region = Placement.TCM
            elif model_size > 0 and model_size <= remaining_sram:
                weights_region = Placement.SRAM
            else:
                weights_region = Placement.MRAM
        else:
            # arena doesn't fit in fast memory; weights stay in MRAM
            # (rodata).  Validation pass will fail if even MRAM is short.
            arena_region = Placement.SRAM if arena_size <= sram_cap else Placement.MRAM
            weights_region = Placement.MRAM

    arena_region, weights_region = _apply_explicit_overrides(
        cfg,
        arena_region,
        weights_region,
        tcm_cap=tcm_cap,
        psram_cap=psram_cap,
    )

    return (arena_region, weights_region)


def _apply_explicit_overrides(
    cfg,
    arena_region: Placement,
    weights_region: Placement,
    *,
    tcm_cap: int,
    psram_cap: int,
) -> tuple[Placement, Placement]:
    requested_arena = cfg.model.arena_location
    requested_weights = cfg.model.weights_location

    # Backwards compatibility for configs written before model.arena_location
    # and model.weights_location existed.
    if requested_arena is None:
        requested_arena = cfg.engine.config.get("runtime_arena_location")
    if requested_weights is None:
        requested_weights = cfg.engine.config.get("runtime_weights_location")

    if requested_arena == Placement.TCM and tcm_cap == 0:
        raise PlatformError(
            f"model.arena_location=tcm requested, but board {cfg.target.board} has no DTCM.",
            hint="Use --runtime-arena-location sram, or pick a board with DTCM.",
        )

    if requested_arena == Placement.PSRAM and psram_cap == 0:
        raise PlatformError(
            f"model.arena_location=psram requested, but board {cfg.target.board} has no PSRAM.",
            hint="Use --runtime-arena-location tcm | sram, or pick a PSRAM-capable board.",
        )

    if requested_weights == Placement.TCM and tcm_cap == 0:
        raise PlatformError(
            f"model.weights_location=tcm requested, but board {cfg.target.board} has no DTCM.",
            hint="Use --runtime-weights-location sram | mram, or pick a board with DTCM.",
        )

    if requested_weights == Placement.PSRAM and psram_cap == 0:
        raise PlatformError(
            f"model.weights_location=psram requested, but board {cfg.target.board} has no PSRAM.",
            hint="Use --runtime-weights-location tcm | sram | mram, or pick a PSRAM-capable board.",
        )

    if requested_arena is not None:
        arena_region = Placement(requested_arena)
    if requested_weights is not None:
        weights_region = Placement(requested_weights)

    return arena_region, weights_region
