"""Verified linked-memory map per SoC (#133 Phase 1b).

The characterized truth of where the LINKER puts things, per SoC and — where
the two link families disagree — per link family. Every constant below was
read from the NSX linker scripts and scatter files that hpx's builds actually
link against (the DEFAULT/``sbl`` profile; ``nsx_select_linker_script`` in
nsx's ``nsx_toolchain_flags.cmake`` picks it unless an engine opts into the
``itcm`` profile), byte-verified identical across every cached nsx-ambiq-sdk
revision, the dev checkout, and a materialized hpx workspace, and — for
apollo510 (gcc) and apollo330P (gcc + ATfE) — confirmed against real
``hpx_profiler.map`` files. Citations are ``<soc>/<toolchain>/<script>:<line>``
inside ``nsx-core/src``.

Why this table exists SEPARATELY from ``capabilities._FAMILY_MEMORY_BASES``:
that older table records the datasheet-flavored values ``soc_placement_ranges``
and ``VerifyPlacementStage`` grew up on, and its MRAM bases are wrong for
accounting — apollo5's ``MRAM: 0x0`` is entirely disjoint from the real app
flash window ``0x00410000+`` (0x0 is ITCM there). Correcting it in place would
silently change verify-placement behavior mid-epic; Phase 2 migrates the old
callers here, and a contract test pins the known divergences until then.

The toolchain axis is TWO-valued: there is no ATfE-specific script anywhere —
ATfE links the gcc ``*.ld`` scripts (confirmed from nsx's cmake and an
apollo330mP ATfE LLD map), so app-available lengths key on the LINK family
(GNU ld vs armlink), not the compiler.

Aperture rule (applied uniformly): every classification ``window`` is the
HARDWARE aperture from the SDK's ``am_reg_base_addresses.h`` for that part —
never a single linker script's opinion of it — because the two link families
carve the same silicon differently (gcc may decline to use the top of a TCM
that armlink tiles exactly full). What differs per link family is only
``app_available``, which IS a linker-script fact. Where a hardware aperture
is wider than both scripts' regions the window still ends at the hardware
top (#176 review M-1/M-2 corrected two windows that had drifted from this
rule).

PSRAM has no linker region on any SoC (grep-verified across every ``.ld`` and
``.sct``); its window comes from the existing board-knowledge tables and is
marked with that provenance. ``nvm_kb`` likewise has no linker counterpart
and is deliberately absent here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from ..placement import MemoryRegion, Placement
from .soc import MemoryRange, SocDef


class LinkFamily(StrEnum):
    """Which linker laid the image out — the axis app-available keys on."""

    #: GNU ld / LLD linking the gcc ``*.ld`` scripts (gcc AND ATfE builds).
    GNU = "gnu"
    #: armlink with the ``*.sct`` scatter files (armclang builds).
    ARMLINK = "armlink"


def link_family_for_toolchain(toolchain: str) -> LinkFamily:
    """The link family a toolchain builds with.

    Routed through the :class:`~helia_profiler.config.Toolchain` enum so an
    unknown name raises (``ValueError``) instead of silently guessing GNU —
    a future armlink-based toolchain must be classified here deliberately.
    (Imported inside the function: config imports this package, so a
    module-level import would be circular.)
    """
    from ..config import Toolchain

    resolved = Toolchain(toolchain)
    return LinkFamily.ARMLINK if resolved is Toolchain.ARMCLANG else LinkFamily.GNU


@dataclass(frozen=True)
class LinkedRegionWindow:
    """One region's verified window plus its per-link-family usable size.

    ``window`` is the CLASSIFICATION aperture — an allocated section whose
    address falls inside it belongs to this region. ``app_available`` is the
    number of bytes the app's own static data can actually occupy under each
    link family, i.e. the window minus what the script/startup reserves
    (stack arrays, armlink's fixed heap/stack regions); it is what honest
    ``free`` accounting subtracts from. A region absent from a SoC's map
    simply has no window entry.
    """

    region: MemoryRegion
    window: MemoryRange
    #: ``hash=False``: a MappingProxyType is unhashable, and frozen dataclass
    #: hashing would otherwise make ``hash(window)`` raise. Equality still
    #: compares it.
    app_available: Mapping[LinkFamily, int] = field(hash=False)
    #: Where the numbers came from — linker-script characterization or
    #: board knowledge (PSRAM). Kept on the record so Phase 2's artifact can
    #: state it.
    provenance: str = "linker-script"


def _window(
    region: MemoryRegion,
    start: int,
    length: int,
    *,
    gnu: int,
    armlink: int,
    provenance: str = "linker-script",
) -> LinkedRegionWindow:
    return LinkedRegionWindow(
        region=region,
        window=MemoryRange(start, length),
        app_available=MappingProxyType(
            {LinkFamily.GNU: gnu, LinkFamily.ARMLINK: armlink}
        ),
        provenance=provenance,
    )


# apollo3p — apollo3p/gcc/linker_script.ld:10-13, apollo3p/armclang/
# linker_script.sct:6-33, hardware apertures from the SDK's
# am_reg_base_addresses.h / am_hal_flash.h (TCM = the FIRST 64 KB of the
# shared 0x10000000 SRAM space; AM_HAL_FLASH_DTCM_END = 0x1000FFFF).
# Windows follow the HARDWARE aperture rule (see the module docstring):
# DTCM is exactly the 64 KB TCM; the SRAM window starts at 0x10010000 —
# main SRAM proper — covering the 4 KB STACKMEM (gcc) / ARM_LIB_STACK
# (armlink) slot, which is stack-reserved SRAM, not TCM. App-available SRAM
# stays the 700 KB RWMEM either way; armlink's ARM_LIB_HEAP is zero-length
# (sct:27).
_APOLLO3P = (
    _window(MemoryRegion.MRAM, 0x0000C000, 2_048_000, gnu=2_048_000, armlink=2_048_000),
    _window(MemoryRegion.DTCM, 0x10000000, 0x10000, gnu=65_536, armlink=65_536),
    _window(MemoryRegion.SRAM, 0x10010000, 0xB0000, gnu=716_800, armlink=716_800),
)

# apollo4p / apollo4l — apollo4p/gcc/linker_script.ld:10-12 (4l's
# ORIGIN/LENGTH values identical; only region attrs (rw)/(rwx) differ),
# apollo4p/armclang/linker_script.sct:8-34. MRAM is 1952 KB from 0x18000
# (NOT the 2000 KB datasheet figure in MemoryLayout). gcc: 16 KB .stack
# inside the 384 KB window and a fill-to-end .heap → 368 KB for app data.
# armlink: MCU_TCM is 364 KB with fixed 4 KB heap + 16 KB stack above it.
_APOLLO4 = (
    _window(MemoryRegion.MRAM, 0x00018000, 1_998_848, gnu=1_998_848, armlink=1_998_848),
    _window(MemoryRegion.DTCM, 0x10000000, 393_216, gnu=376_832, armlink=372_736),
    _window(MemoryRegion.SRAM, 0x10060000, 1_048_576, gnu=1_048_576, armlink=1_048_576),
)

# apollo510 / apollo510b / apollo5b — apollo510/gcc/linker_script_sbl.ld:10-13
# (510b/5b MEMORY blocks byte-identical), apollo510/armclang/
# linker_script_sbl.sct:7-39. The DTCM classification aperture is the full
# hardware 512 KB (gcc's MCU_TCM stops at 496 KB; armlink's fixed 16 KB
# stack occupies 0x2007C000-0x20080000, inside the hardware aperture but
# beyond gcc's window). gcc app data: 496 KB - 16 KB stack = 480 KB, with
# the fill-to-end .heap absorbing the remainder. armlink: 492 KB MCU_TCM
# for RW+ZI (fixed 4 KB heap + 16 KB stack live above it).
# MRAM: 4032 KB from the app origin 0x00410000 — hpx's older family table
# says base 0x0, which on this family is ITCM, not MRAM.
_APOLLO5_FULL = (
    _window(MemoryRegion.ITCM, 0x00000000, 262_144, gnu=262_144, armlink=262_144),
    _window(MemoryRegion.MRAM, 0x00410000, 4_128_768, gnu=4_128_768, armlink=4_128_768),
    _window(MemoryRegion.DTCM, 0x20000000, 524_288, gnu=491_520, armlink=503_808),
    _window(MemoryRegion.SRAM, 0x20080000, 3_145_728, gnu=3_145_728, armlink=3_145_728),
)

# apollo330P — apollo330P/gcc/linker_script_sbl.ld:10-12,
# apollo330P/armclang/linker_script_sbl.sct:6-33 (byte-identical to
# apollo510L's). No ITCM region in the DEFAULT script (.dtcm_text goes to
# MCU_TCM instead). The DTCM window is the HARDWARE aperture, 256 KB — the
# SDK's am_reg_base_addresses.h says DTCM_MAX_SIZE = 256 KB, and armlink's
# scatter tiles to exactly that (MCU_TCM 0x3B000 + heap 0x1000 + stack
# 0x4000 = 0x40000), the same pattern it uses on AP510. gcc's script
# simply declines to use the top 16 KB (MCU_TCM stops at 240 KB), which is
# why soc.py's earlier "hardware-confirmed 240" was circular — it was
# confirmed against the gcc script, not the part (#176 review M-1). The
# armlink 16 KB stack at 0x2003C000 therefore classifies as DTCM, as it
# should.
_APOLLO330P = (
    _window(MemoryRegion.MRAM, 0x00410000, 2_031_616, gnu=2_031_616, armlink=2_031_616),
    _window(MemoryRegion.DTCM, 0x20000000, 262_144, gnu=229_376, armlink=241_664),
    _window(MemoryRegion.SRAM, 0x20080000, 1_835_008, gnu=1_835_008, armlink=1_835_008),
)

_MAPS: Mapping[str, tuple[LinkedRegionWindow, ...]] = MappingProxyType(
    {
        "apollo3p": _APOLLO3P,
        "apollo4p": _APOLLO4,
        "apollo4l": _APOLLO4,
        "apollo510": _APOLLO5_FULL,
        "apollo510b": _APOLLO5_FULL,
        "apollo5b": _APOLLO5_FULL,
        "apollo330P": _APOLLO330P,
    }
)


def linked_memory_map(soc: SocDef) -> tuple[LinkedRegionWindow, ...]:
    """The verified region windows for *soc*, PSRAM appended when present.

    Returns an empty tuple for any non-builtin SoC — a ``target.custom_socs``
    part was never checked against these scripts, EVEN IF its name collides
    with a registered part's (same rule and rationale as
    ``capabilities._FAMILY_APP_FLASH_LOAD_ADDR``; see ``SocDef.is_builtin``).
    Every registered SoC is currently characterized, so builtins never hit
    the empty branch today. Callers treat empty as unavailable, never
    guessed, per #131's discipline.
    """
    if not soc.is_builtin:
        return ()
    windows = _MAPS.get(soc.name, ())
    if not windows:
        return ()
    psram_kb = soc.memory.psram_kb
    psram_base = soc.capabilities.memory.placement_bases.get(Placement.PSRAM)
    if psram_kb > 0 and psram_base is not None:
        windows = windows + (
            _window(
                MemoryRegion.PSRAM,
                psram_base,
                psram_kb * 1024,
                gnu=psram_kb * 1024,
                armlink=psram_kb * 1024,
                provenance="board-knowledge",
            ),
        )
    return windows


def classify_address(
    address: int, windows: tuple[LinkedRegionWindow, ...]
) -> MemoryRegion | None:
    """The region whose window contains *address*, or None (the honest flag
    for an occupant outside every verified window — including the table
    itself being wrong, which is exactly what Phase 2's police check
    surfaces)."""
    for entry in windows:
        if entry.window.contains(address):
            return entry.region
    return None
