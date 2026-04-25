"""Stage 2b — Plan memory: build or validate the run's memory plan.

Produces a ``MemoryPlan`` on ``ctx.memory_plan`` describing how much of each
SoC memory region will be consumed at runtime.  Engines that know their
layout (heliaAOT via the planner) supply it directly on
``EngineArtifacts.memory_plan``; otherwise we synthesise a conservative
single-arena plan from ``config.model.arena_size``, model size, and
``config.model.model_location``.

After the plan is chosen, we validate each region against the resolved
``SocDef.MemoryLayout`` and raise ``PlatformError`` with a clear, actionable
hint if anything is over-subscribed — before firmware is even built.  This
prevents the classic "AllocateTensors failed / link error / boot hang"
chain that used to mask simple capacity problems.
"""

from __future__ import annotations

import logging

from ..errors import PlatformError
from ..pipeline import PipelineContext
from ..platform import MemoryLayout
from ..results import MemoryConsumer, MemoryPlan, MemoryRegionUsage

log = logging.getLogger("hpx")


# Mapping of MemoryPlan region names to MemoryLayout fields.
_REGION_FIELDS: dict[str, str] = {
    "MRAM": "mram_kb",
    "SRAM": "sram_kb",
    "DTCM": "dtcm_kb",
    "ITCM": "itcm_kb",
    "PSRAM": "psram_kb",
}


class PlanMemoryStage:
    @property
    def name(self) -> str:
        return "plan_memory"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
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
        """Build a simple single-arena plan for engines (tflm/heliaRT)
        that don't expose per-region allocations.

        * model weights go to ``model_location`` (mram by default, psram
          if requested).
        * the TFLM tensor arena lives in SRAM.
        """
        engine = ctx.config.engine.type.value
        arena = int(ctx.config.model.arena_size or 0)

        # Model size — best-effort: use the file size.
        try:
            model_bytes = int(ctx.config.model.path.stat().st_size)
        except OSError:
            model_bytes = 0

        weight_region = (
            "PSRAM" if ctx.config.model.model_location == "psram" else "MRAM"
        )

        region_map: dict[str, list[MemoryConsumer]] = {}
        if model_bytes > 0:
            region_map.setdefault(weight_region, []).append(
                MemoryConsumer(
                    name="model_flatbuffer", size=model_bytes, kind="weights",
                )
            )
        if arena > 0:
            region_map.setdefault("SRAM", []).append(
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
        """Fill in per-region capacities from the resolved SoC layout.

        Also adds empty ``MemoryRegionUsage`` entries for every region the
        SoC physically has, so reports can show "0 / 3 MB" for unused
        regions too.
        """
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

        # Keep any engine regions we did not recognise (future-proof).
        for leftover in by_region.values():
            rebuilt.append(leftover)

        return MemoryPlan(
            engine=plan.engine,
            regions=tuple(rebuilt),
            model_weight_bytes=plan.model_weight_bytes,
            has_overflow=any(r.overflow for r in rebuilt),
        )

    def _validate(self, plan: MemoryPlan) -> None:
        """Raise ``PlatformError`` if any region is over-subscribed."""
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
            "  * shrink the tensor arena (--arena-size) if SRAM overflowed;\n"
            "  * move weights to PSRAM (--model-location psram) if MRAM\n"
            "    overflowed and the board has PSRAM;\n"
            "  * pick a larger-memory board; or\n"
            "  * reduce model size (quantise / prune)."
        )
        raise PlatformError(
            f"Memory plan does not fit:\n{detail}",
            hint=hint,
        )
