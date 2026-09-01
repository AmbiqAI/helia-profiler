"""Placement policy plus re-exports of the silicon placement vocabulary.

The enums and slack constants moved to
:mod:`helia_profiler.platform.placement` so they travel with the silicon
tables at extraction time (#229 D4); this module remains the hpx-facing
spelling and keeps the fastest-fit *policy*, which is hpx's concern.
"""

from __future__ import annotations

from .platform.placement import (
    SRAM_PLACEMENT_SLACK_BYTES,
    TCM_PLACEMENT_SLACK_BYTES,
    ArenaRole,
    MemoryRegion,
    Placement,
)


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
