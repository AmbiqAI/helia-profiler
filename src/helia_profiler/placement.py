"""Logical placement / role enums for the memory & firmware layer.

These enums are the single vocabulary used by:

* the placement resolver in :mod:`helia_profiler.stages.plan_memory`
* the firmware Jinja templates (via the ``StrEnum`` ``__str__`` /
  ``__eq__`` semantics — no ``.value`` unwrapping needed)
* engine adapters that emit arena regions
* preflight validation

Using ``StrEnum`` keeps backwards compatibility with the raw string
constants previously sprayed across templates and dicts (``"tcm"``,
``"sram"``, …) while letting Python code use ``is``-comparisons against
the enum members.
"""

from __future__ import annotations

from enum import StrEnum


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


class ModelLocation(StrEnum):
    """User-facing ``model.model_location`` values.

    Includes :attr:`AUTO` for automatic placement plus the four
    :class:`Placement` regions.
    """

    AUTO = "auto"
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
