"""Memory-placement vocabulary for Ambiq silicon (#229 D4).

The logical placement/role enums and slack constants shared by the memory
planner, firmware templates, engine adapters, and preflight. They live in
the silicon-info package so the vocabulary travels with the per-SoC tables
at extraction time; :mod:`helia_profiler.placement` re-exports them and
keeps the hpx-side placement *policy*.
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
