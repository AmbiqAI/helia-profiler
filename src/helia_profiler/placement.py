"""Logical placement / role enums for the memory & firmware layer.

These enums are the single vocabulary used by:

* the placement resolver in :mod:`helia_profiler.stages.plan_memory`
* the firmware Jinja templates (via the ``StrEnum`` ``__str__`` /
  ``__eq__`` semantics — no ``.value`` unwrapping needed)
* engine adapters that emit arena regions
* preflight validation

Using ``StrEnum`` preserves interoperability with the raw string
constants previously sprayed across templates and dicts (``"tcm"``,
``"sram"``, …) while letting Python code use ``is``-comparisons against
the enum members.
"""

from __future__ import annotations

from enum import StrEnum


TCM_PLACEMENT_SLACK_BYTES = 128 * 1024
SRAM_PLACEMENT_SLACK_BYTES = 32 * 1024


class Placement(StrEnum):
    """Logical placement region for arenas / weights / model data.

    The four logical regions abstract over the SoC physical layout —
    e.g. ``Placement.TCM`` covers DTCM on AP5 and is unavailable on AP3.
    Engine adapters that emit physical names (heliaAOT's ``DTCM``,
    ``ITCM``, …) normalise to this enum at the adapter boundary.
    """

    TCM = "tcm"
    SRAM = "sram"
    MRAM = "mram"
    PSRAM = "psram"


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


class ArenaRole(StrEnum):
    """Role classification for an AOT arena region.

    Drives firmware-level placement overrides — e.g. moving *scratch*
    arenas to PSRAM while leaving *constant* arenas in MRAM.
    """

    SCRATCH = "scratch"
    PERSISTENT = "persistent"
    CONSTANT = "constant"


class MemoryRegion(StrEnum):
    """Physical SoC memory region names used in :class:`MemoryPlan`.

    These map onto the Apollo SoC layout:

    * ``DTCM`` — data TCM (zero-wait, smallest)
    * ``ITCM`` — instruction TCM (Apollo5 only)
    * ``SRAM`` — shared SRAM
    * ``MRAM`` — non-volatile flash (XIP)
    * ``PSRAM`` — external PSRAM (board-dependent)

    The :class:`Placement` enum is the *logical* user-facing vocabulary
    (``tcm`` → DTCM); :class:`MemoryRegion` is the *physical* region
    name surfaced in reports and consumed by linker scripts.
    """

    DTCM = "DTCM"
    ITCM = "ITCM"
    SRAM = "SRAM"
    MRAM = "MRAM"
    PSRAM = "PSRAM"
