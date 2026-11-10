"""Placement policy plus re-exports of the silicon placement vocabulary.

The enums and slack constants moved to
:mod:`helia_profiler.platform.placement` so they travel with the silicon
tables at extraction time (#229 D4); this module remains the hpx-facing
spelling and keeps the fastest-fit *policy*, which is hpx's concern.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .errors import ImageTooLargeError, PlatformError
from .platform.linker.model import LinkerInput
from .platform.placement import (
    SRAM_PLACEMENT_SLACK_BYTES,
    TCM_PLACEMENT_SLACK_BYTES,
    ArenaRole,
    MemoryRegion,
    Placement,
)

if TYPE_CHECKING:
    from .platform import SocDef
    from .results import MemoryPlan


def region_capacity(region: MemoryRegion, declared: int, layout: LinkerInput | None) -> int:
    """Constrain a declared byte capacity when a corresponding interval budget is known."""
    if layout is None:
        return declared
    entry = next((value for value in layout.regions if value.region is region), None)
    if entry is None:
        return 0
    if entry.capacity is None:
        return declared
    return min(declared, entry.capacity)


@dataclass(frozen=True)
class PlacementCapacities:
    tcm: int
    sram: int
    psram: int


def placement_capacities(soc: SocDef | None, layout: LinkerInput | None) -> PlacementCapacities:
    """The same usable byte budgets for conversion and downstream placement."""
    return PlacementCapacities(
        region_capacity(MemoryRegion.DTCM, soc.memory.dtcm_kb * 1024 if soc else 1 << 31, layout),
        region_capacity(MemoryRegion.SRAM, soc.memory.sram_kb * 1024 if soc else 1 << 31, layout),
        region_capacity(MemoryRegion.PSRAM, soc.memory.psram_kb * 1024 if soc else 0, layout),
    )


def admit_memory_plan(plan: MemoryPlan, layout: LinkerInput) -> MemoryPlan:
    """Account fixed reservations once, and refuse known-empty or fragmented overflow."""
    from .results import ConsumerKind

    regions = []
    image_overflow: ImageTooLargeError | None = None
    for usage in plan.regions:
        entry = next((value for value in layout.regions if value.region is usage.region), None)
        if entry is not None and entry.capacity is None:
            if usage.overflow:
                raise PlatformError(f"{usage.region}: modelled usage exceeds its declared capacity")
            regions.append(usage)
            continue
        excluded = 0
        consumers = []
        for consumer in usage.consumers:
            if (
                entry is not None
                and entry.fixed_stack_bytes
                and consumer.kind is ConsumerKind.STACK
                and consumer.name == "boot_stack"
            ):
                if consumer.size != entry.fixed_stack_bytes:
                    raise PlatformError(
                        "Modelled boot stack differs from the SDK's fixed reservation"
                    )
                excluded += consumer.size
            else:
                consumers.append(consumer)
        used = usage.used - excluded
        if used < 0:
            raise PlatformError("Fixed stack reservation exceeds modelled region usage")
        capacity = region_capacity(usage.region, usage.capacity, layout)
        if used > capacity:
            error = ImageTooLargeError if usage.region is MemoryRegion.MRAM else PlatformError
            failure = error(
                f"{usage.region}: {used} B modelled usage exceeds the contiguous application "
                f"budget of {capacity} B ({layout.script.path})",
                hint="Reduce the requested arena/weights or select another explicit placement. "
                "Separated allowed intervals cannot form one contiguous allocation.",
            )
            if isinstance(failure, PlatformError):
                raise failure
            # A skippable MRAM overflow must not hide an invalid RAM plan or reservation.
            image_overflow = failure
        regions.append(replace(usage, capacity=capacity, used=used, consumers=tuple(consumers)))
    if image_overflow is not None:
        raise image_overflow
    return replace(plan, regions=tuple(regions), has_overflow=any(r.overflow for r in regions))


def resolve_fastest_fit_placement(
    *,
    arena_size: int,
    weights_size: int,
    tcm_cap: int,
    sram_cap: int,
) -> tuple[Placement, Placement]:
    """Resolve engine-neutral auto placement, prioritizing the mutable arena."""
    tcm_budget = max(0, tcm_cap - TCM_PLACEMENT_SLACK_BYTES)
    sram_budget = max(0, sram_cap - SRAM_PLACEMENT_SLACK_BYTES)
    arena_in_tcm = arena_size > 0 and arena_size <= tcm_budget
    arena_in_sram = not arena_in_tcm and arena_size > 0 and arena_size <= sram_budget

    if arena_in_tcm:
        remaining_tcm = tcm_budget - arena_size
        tcm_weight_budget = max(0, remaining_tcm - TCM_PLACEMENT_SLACK_BYTES)
        if weights_size > 0 and weights_size <= tcm_weight_budget:
            return Placement.TCM, Placement.TCM
        if weights_size > 0 and weights_size <= sram_budget:
            return Placement.TCM, Placement.SRAM
        return Placement.TCM, Placement.MRAM
    if arena_in_sram:
        return Placement.SRAM, Placement.MRAM
    return (
        Placement.SRAM if arena_size <= sram_cap else Placement.MRAM,
        Placement.MRAM,
    )


__all__ = [
    "SRAM_PLACEMENT_SLACK_BYTES",
    "TCM_PLACEMENT_SLACK_BYTES",
    "ArenaRole",
    "MemoryRegion",
    "Placement",
    "resolve_fastest_fit_placement",
]
