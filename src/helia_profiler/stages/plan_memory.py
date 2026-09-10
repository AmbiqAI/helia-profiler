"""Resolve placement and publish the validated firmware memory plan."""

from __future__ import annotations

import logging

from ..engines import get_adapter
from ..firmware.memory_plan import (
    add_hpx_owned_consumers,
    apply_capacities,
    resolve_placement,
    select_memory_plan,
    validate_memory_plan,
)
from ..pipeline import PipelineContext

log = logging.getLogger("hpx")


class PlanMemoryStage:
    @property
    def name(self) -> str:
        return "plan_memory"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        cfg = ctx.config
        arena_region, weights_region = resolve_placement(
            model=cfg.model,
            board=cfg.target.board,
            soc=ctx.soc,
            adapter=ctx.engine_adapter or get_adapter(cfg.engine.type),
        )
        ctx.arena_region = arena_region
        ctx.weights_region = weights_region
        log.info("Placement: arena=%s, weights=%s", arena_region, weights_region)

        plan = select_memory_plan(
            engine_type=cfg.engine.type,
            model=cfg.model,
            artifacts=ctx.engine_artifacts,
            arena_region=arena_region,
            weights_region=weights_region,
        )
        plan = add_hpx_owned_consumers(
            plan, soc=ctx.soc, engine_type=cfg.engine.type, target=cfg.target
        )
        plan = apply_capacities(plan, ctx.soc.memory if ctx.soc else None)
        validate_memory_plan(plan)
        ctx.memory_plan = plan
        ctx.run_metadata.memory_plan = plan

        log.info("Memory plan (%s):", plan.engine)
        for r in plan.regions:
            if r.capacity > 0 or r.used > 0:
                pct = (r.used * 100 / r.capacity) if r.capacity else 0
                log.info("  %-6s %7d / %7d B (%5.1f%%)", r.region, r.used, r.capacity, pct)
