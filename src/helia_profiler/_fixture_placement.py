"""Validate fixture backing without replacing the engine's placement planner."""

import json
from dataclasses import asdict
from typing import Any

from .engines.base import ArenaRegion, HeliaAotArtifacts
from .errors import ConfigError
from .fixture_target import fixture_app_region
from .pipeline import PipelineContext
from .placement import ArenaRole, Placement

_BANKS = {
    Placement.SRAM: ("sram", "dram"),
    Placement.MRAM: ("mram",),
    Placement.TCM: ("dtcm",),
    Placement.PSRAM: ("psram",),
}


def validate_fixture_regions(
    ctx: PipelineContext, regions: list[ArenaRegion]
) -> dict[str, Any] | None:
    artifacts = ctx.engine_artifacts
    intent = {}
    plan = ctx.memory_plan
    explicit = False
    if (
        isinstance(artifacts, HeliaAotArtifacts)
        and artifacts.aot_user_memory_config
        and ctx.config.model.weights_location is not Placement.PSRAM
    ):
        intent = json.loads(artifacts.aot_memory_intent_json)
        explicit = bool(intent.get("tensors"))
    if explicit:
        if (
            not isinstance(artifacts, HeliaAotArtifacts)
            or not artifacts.aot_allocate_arenas
            or artifacts.memory_plan is None
            or plan is None
            or regions != artifacts.aot_arena_regions
        ):
            raise ConfigError("Mixed fixture placement requires the emitted internal AOT plan")
        if not regions or {r.region_id for r in regions} != set(range(len(regions))):
            raise ConfigError("Mixed fixture placement requires complete AOT region identities")

    used: dict[Placement, int] = {}
    for region in regions:
        requested = (
            ctx.config.model.weights_location
            if region.role is ArenaRole.CONSTANT
            else ctx.config.model.arena_location
        )
        if not explicit and region.placement != requested:
            raise ConfigError("AOT region placement differs from fixture request")
        if region.memory not in _BANKS.get(region.placement, ()):
            raise ConfigError("AOT physical memory differs from fixture request")
        if not explicit:
            continue
        if region.placement is Placement.PSRAM:
            raise ConfigError("Mixed fixture placement requires internal SRAM/MRAM/DTCM backing")
        if (
            type(region.size) is not int
            or region.size <= 0
            or type(region.alignment) is not int
            or region.alignment <= 0
            or region.alignment & (region.alignment - 1)
        ):
            raise ConfigError("Invalid AOT region size or alignment")
        used[region.placement] = used.get(region.placement, 0) + region.size

    if explicit and plan is not None:
        for placement, size in used.items():
            start, end = fixture_app_region(placement.region, ctx.config.target.board)
            planned = next((r for r in plan.regions if r.region == placement.region), None)
            if planned is None or not size <= planned.used <= end - start:
                raise ConfigError("AOT regions exceed the recorded plan or canonical fixture bank")
        return {
            "schema_version": 1,
            "memory_intent": intent,
            "regions": [asdict(region) for region in regions],
            "planned_memory": asdict(plan),
        }
    return None
