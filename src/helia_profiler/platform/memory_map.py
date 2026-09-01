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

Aperture rule (applied uniformly): every RAM classification ``window``
(ITCM/DTCM/SRAM) is the HARDWARE aperture from the SDK's
``am_reg_base_addresses.h`` for that part — never a single linker script's
opinion of it — because the two link families carve the same silicon
differently (gcc may decline to use the top of a TCM that armlink tiles
exactly full). What differs per link family is only ``app_window``,
which IS a linker-script fact. (#176 review M-1/M-2 corrected two windows
that had drifted from this rule.) MRAM windows are deliberately NOT the
hardware flash aperture: they start at the SBL-excluded app origin both
scripts link at (e.g. 0x00410000 on AP5, where hardware MRAM begins at
0x00400000) — an app section can only ever land in the app window, and
"below the app origin" should classify as outside, not as MRAM.

Scoping: only the RAM banks the NSX scripts actually link into are
windowed. apollo4p/4l also have EXTRAM (0x10160000) and SSRAM1 (0x101C0000)
apertures in the SDK, but no script places anything there and
``soc.memory.sram_kb`` agrees with SSRAM0 alone — sections there classify
``None``, correctly flagging an uncharacterized placement rather than
absorbing it into "SRAM".

PSRAM has no linker region on any SoC (grep-verified across every ``.ld`` and
``.sct``); its window comes from the existing board-knowledge tables, is
marked with that provenance, and carries ``section_attributable=False`` —
no ELF section can ever land there, so occupancy must come from the plan.
``nvm_kb`` likewise has no linker counterpart and is deliberately absent
here.

Phase-2 free math (the contract these shapes exist for):
``free = app_window[family].length − Σ(size of allocated sections whose
address falls inside app_window[family], excluding linker_reserved ones)``.
Sections inside ``window`` but outside ``app_window`` are the linker's
FIXED reservations (armlink's scatter heap/stack, apollo3p's STACKMEM) —
reserved, not app usage. gcc's floating ``.stack`` sits INSIDE the extent
and counts as occupancy (live memory, the #131 stance); only the
fill-to-end ``.heap`` is ``linker_reserved``-excluded. See the
``LinkedRegionWindow`` docstring for why the two mechanisms exist.
Load-image (MRAM) accounting sums ``LoadSegment.file_size`` grouped by
``classify_address(physical_address)`` — see the ``LoadSegment`` docstring
for why walking sections into segments is wrong on armlink.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from ..placement import MemoryRegion, Placement
from .soc import MemoryRange, SocDef


class LinkFamily(StrEnum):
    """Which linker laid the image out — the axis app_window keys on."""

    #: GNU ld / LLD linking the gcc ``*.ld`` scripts (gcc AND ATfE builds).
    GNU = "gnu"
    #: armlink with the ``*.sct`` scatter files (armclang builds).
    ARMLINK = "armlink"


#: Every supported toolchain name, classified deliberately: an unknown name
#: raises instead of silently guessing GNU, so a future armlink-based
#: toolchain must be added here on purpose. Kept in lockstep with
#: :class:`helia_profiler.vocab.Toolchain` by a contract test — platform
#: deliberately does not import the config layer (#229 D2).
_LINK_FAMILY_BY_TOOLCHAIN: dict[str, LinkFamily] = {
    "arm-none-eabi-gcc": LinkFamily.GNU,
    "gcc": LinkFamily.GNU,
    "armclang": LinkFamily.ARMLINK,
    "atfe": LinkFamily.GNU,
}


def link_family_for_toolchain(toolchain: str) -> LinkFamily:
    """The link family a toolchain builds with (``ValueError`` on unknown)."""
    try:
        return _LINK_FAMILY_BY_TOOLCHAIN[toolchain]
    except KeyError:
        known = ", ".join(sorted(_LINK_FAMILY_BY_TOOLCHAIN))
        raise ValueError(
            f"{toolchain!r} is not a supported toolchain (known: {known})."
        ) from None


@dataclass(frozen=True)
class LinkedRegionWindow:
    """One region's verified window plus its per-link-family app sub-window.

    ``window`` is the CLASSIFICATION aperture — an allocated section whose
    address falls inside it belongs to this region. ``app_window`` is the
    address EXTENT of the linked region the app's image occupies under
    each link family. Honest Phase-2 free math is
    ``app_window.length − Σ(allocated sections inside app_window,
    excluding linker_reserved ones)``.

    Reservations are handled by TWO mechanisms, matched to how each link
    family expresses them (#176 fresh-review rounds — both single-
    mechanism designs were wrong):

    * FIXED script regions are carved out by EXTENT: armlink's scatter
      pins ``ARM_LIB_HEAP``/``ARM_LIB_STACK`` above ``MCU_TCM`` (so the
      armlink extent is ``MCU_TCM`` itself), and apollo3p's gcc script
      pins ``STACKMEM`` below ``RWMEM``. Sections inside ``window`` but
      outside ``app_window`` are these fixed reservations — report them
      as reserved, never as app usage or free space.
    * FLOATING sections are handled by the INVENTORY: gcc's ``.stack`` is
      an ordinary output section whose position depends on what else the
      script places first (apollo330P's ``.dtcm_text`` — which hpx's own
      AOT engine emits via ``HELIAAOT_PUT_IN_ITCM`` — precedes it; a
      fixed carve-out was wrong there). The gcc DTCM extent is therefore
      the FULL script region, and ``.stack`` counts as occupancy — it is
      live memory the firmware needs, the same #131 stance
      ``BinarySections`` takes. gcc's fill-to-end ``.heap`` is the one
      ``linker_reserved`` exclusion (its size states what was LEFT, so
      counting it would make free identically zero).

    A region absent from a SoC's map simply has no window entry.
    """

    region: MemoryRegion
    window: MemoryRange
    #: ``hash=False``: a MappingProxyType is unhashable, and frozen dataclass
    #: hashing would otherwise make ``hash(window)`` raise. Equality still
    #: compares it.
    app_window: Mapping[LinkFamily, MemoryRange] = field(hash=False)
    #: Where the WINDOW bounds came from: "hardware-aperture" (SDK regs
    #: headers — the RAM regions), "linker-app-origin" (MRAM: app link
    #: origin to hardware flash top), or "board-knowledge" (PSRAM). The
    #: window and the app extents genuinely have different provenances;
    #: one string covering both published a false claim (#176 fresh-review).
    window_provenance: str = "hardware-aperture"
    #: Where the app extents came from — linker-script characterization,
    #: or board knowledge (PSRAM).
    app_provenance: str = "linker-script"
    #: False when no ELF section can ever land here (PSRAM: no linker
    #: region exists on any SoC). Phase 2 must reconcile such regions from
    #: the PLAN, never report "used 0, free capacity" from an inventory
    #: that structurally cannot see them — that would recreate the exact
    #: #133 pathology this module exists to close.
    section_attributable: bool = True

    def __post_init__(self) -> None:
        # Freeze the mapping so the construction-time validation below is
        # an invariant, not a snapshot (a caller-supplied plain dict could
        # otherwise be mutated behind the frozen dataclass).
        object.__setattr__(self, "app_window", MappingProxyType(dict(self.app_window)))
        for family in LinkFamily:
            extent = self.app_window.get(family)
            if extent is None:
                raise ValueError(
                    f"{self.region}: app_window missing {family!r}"
                )
            if extent.start < self.window.start or extent.end > self.window.end:
                raise ValueError(
                    f"{self.region}: app_window {family!r} lies outside the window"
                )


def _window(
    region: MemoryRegion,
    start: int,
    length: int,
    *,
    gnu: int,
    armlink: int,
    gnu_start: int | None = None,
    armlink_start: int | None = None,
    window_provenance: str = "hardware-aperture",
    app_provenance: str = "linker-script",
    section_attributable: bool = True,
) -> LinkedRegionWindow:
    """``gnu``/``armlink`` are app-extent LENGTHS; ``gnu_start``/
    ``armlink_start`` default to the window start (they differ only where
    the script pins a FIXED region below the app one — apollo3p's
    STACKMEM slot under RWMEM)."""
    return LinkedRegionWindow(
        region=region,
        window=MemoryRange(start, length),
        app_window=MappingProxyType(
            {
                LinkFamily.GNU: MemoryRange(
                    start if gnu_start is None else gnu_start, gnu
                ),
                LinkFamily.ARMLINK: MemoryRange(
                    start if armlink_start is None else armlink_start, armlink
                ),
            }
        ),
        window_provenance=window_provenance,
        app_provenance=app_provenance,
        section_attributable=section_attributable,
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
    _window(
        MemoryRegion.MRAM,
        0x0000C000,
        2_048_000,
        gnu=2_048_000,
        armlink=2_048_000,
        window_provenance="linker-app-origin",
    ),
    _window(MemoryRegion.DTCM, 0x10000000, 0x10000, gnu=65_536, armlink=65_536),
    # App SRAM is RWMEM @0x10011000 under BOTH families; the 4 KB STACKMEM
    # slot below it (gcc .stack / armlink ARM_LIB_STACK) is stack-reserved.
    _window(
        MemoryRegion.SRAM,
        0x10010000,
        0xB0000,
        gnu=716_800,
        armlink=716_800,
        gnu_start=0x10011000,
        armlink_start=0x10011000,
    ),
)

# apollo4p / apollo4l — apollo4p/gcc/linker_script.ld:10-12 (4l's
# ORIGIN/LENGTH values identical; only region attrs (rw)/(rwx) differ),
# apollo4p/armclang/linker_script.sct:8-34. MRAM is 1952 KB from 0x18000
# (NOT the 2000 KB datasheet figure in MemoryLayout). gcc: the floating
# 16 KB .stack and the fill-to-end .heap live inside the full 384 KB
# MCU_TCM region. armlink: MCU_TCM is 364 KB with its fixed 4 KB heap +
# 16 KB stack above it.
_APOLLO4 = (
    _window(
        MemoryRegion.MRAM,
        0x00018000,
        1_998_848,
        gnu=1_998_848,
        armlink=1_998_848,
        window_provenance="linker-app-origin",
    ),
    # gcc extent = the FULL 384 KB MCU_TCM script region (.stack floats
    # inside it and counts as occupancy); armlink extent = MCU_TCM with
    # its fixed heap/stack tiled above.
    _window(
        MemoryRegion.DTCM,
        0x10000000,
        393_216,
        gnu=393_216,
        armlink=372_736,
    ),
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
    _window(
        MemoryRegion.MRAM,
        0x00410000,
        4_128_768,
        gnu=4_128_768,
        armlink=4_128_768,
        window_provenance="linker-app-origin",
    ),
    # gcc extent = the FULL 496 KB MCU_TCM script region [0x20000000,
    # 0x2007C000) — .stack is a floating section inside it (first on
    # AP510, but position is script-order-dependent in general) and
    # counts as occupancy. armlink extent = MCU_TCM; its fixed 4 KB heap
    # + 16 KB stack tile ABOVE it to the hardware aperture.
    _window(
        MemoryRegion.DTCM,
        0x20000000,
        524_288,
        gnu=507_904,
        armlink=503_808,
    ),
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
    _window(
        MemoryRegion.MRAM,
        0x00410000,
        2_031_616,
        gnu=2_031_616,
        armlink=2_031_616,
        window_provenance="linker-app-origin",
    ),
    # gcc extent = the FULL 240 KB MCU_TCM script region. On THIS part
    # .dtcm_text precedes .stack into MCU_TCM (and hpx's AOT engine emits
    # .dtcm_text via HELIAAOT_PUT_IN_ITCM on ITCM-less parts), so the
    # stack's position is link-dependent — a fixed carve-out was wrong
    # (#176 fresh-eyes on a50e63d). armlink extent = MCU_TCM.
    _window(
        MemoryRegion.DTCM,
        0x20000000,
        262_144,
        gnu=245_760,
        armlink=241_664,
    ),
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


def linked_memory_map(
    soc: SocDef,
    *,
    linker_profile: str = "default",
) -> tuple[LinkedRegionWindow, ...]:
    """The verified region windows for *soc*, PSRAM appended when present.

    Returns an empty tuple for any non-builtin SoC — a ``target.custom_socs``
    part was never checked against these scripts, EVEN IF its name collides
    with a registered part's (same rule and rationale as
    ``capabilities._FAMILY_APP_FLASH_LOAD_ADDR``; see ``SocDef.is_builtin``).
    Every registered SoC is currently characterized, so builtins never hit
    the empty branch today. Callers treat empty as unavailable, never
    guessed, per #131's discipline.

    ``linker_profile`` is the third axis of the real layout: these tables
    characterize NSX's DEFAULT (sbl-based) profile ONLY. ``itcm`` is a
    documented engine knob (``docs/guide/engines.md``) forwarded straight
    to CMake, and its scripts declare DIFFERENT regions — on apollo330P,
    AP510-sized ones (the upstream NSX bug in PR #176's report) — so any
    profile other than ``default`` returns empty: the honest "unavailable"
    instead of a confidently wrong map (#176 fresh-review M-3).
    """
    if linker_profile != "default":
        return ()
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
                window_provenance="board-knowledge",
                app_provenance="board-knowledge",
                section_attributable=False,
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
