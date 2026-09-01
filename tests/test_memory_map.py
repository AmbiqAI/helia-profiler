"""Contract tests for the verified linked-memory map (#133 Phase 1b).

The constants under test were characterized from the NSX linker scripts and
scatter files hpx actually links against (citations in memory_map.py) and
confirmed against real hpx_profiler.map files for apollo510 (gcc) and
apollo330P (gcc + ATfE).
"""

from __future__ import annotations

from pathlib import Path

from helia_profiler.placement import MemoryRegion, Placement
from helia_profiler.platform import get_soc
from helia_profiler.platform.memory_map import (
    LinkFamily,
    classify_address,
    link_family_for_toolchain,
    linked_memory_map,
)
from helia_profiler.platform.soc import MemoryRange, soc_placement_ranges

CHARACTERIZED_SOCS = (
    "apollo3p",
    "apollo4p",
    "apollo4l",
    "apollo510",
    "apollo510b",
    "apollo5b",
    "apollo330P",
)


def test_every_characterized_soc_has_nonoverlapping_windows():
    """Address classification is only sound if no two windows intersect."""
    for name in CHARACTERIZED_SOCS:
        windows = linked_memory_map(get_soc(name))
        assert windows, name
        spans = sorted((w.window.start, w.window.end) for w in windows)
        for (_, prev_end), (next_start, _) in zip(spans, spans[1:]):
            assert prev_end <= next_start, (name, spans)


def test_app_windows_nest_inside_the_classification_window():
    """The dataclass __post_init__ enforces this at construction; the test
    keeps it enforced against a future __post_init__ removal."""
    for name in CHARACTERIZED_SOCS:
        for w in linked_memory_map(get_soc(name)):
            for family in LinkFamily:
                extent = w.app_window[family]
                assert 0 < extent.length <= w.window.length, (name, w.region)
                assert w.window.start <= extent.start, (name, w.region)
                assert extent.end <= w.window.end, (name, w.region)


def test_partial_app_window_mapping_is_rejected():
    """LinkedRegionWindow is a public export; a partial mapping must fail
    at construction, not KeyError at a Phase-2 consumer."""
    import pytest

    from helia_profiler.platform import LinkedRegionWindow, MemoryRange

    with pytest.raises(ValueError):
        LinkedRegionWindow(
            region=MemoryRegion.DTCM,
            window=MemoryRange(0x20000000, 0x1000),
            app_window={LinkFamily.GNU: MemoryRange(0x20000000, 0x1000)},
        )
    with pytest.raises(ValueError):
        LinkedRegionWindow(
            region=MemoryRegion.DTCM,
            window=MemoryRange(0x20000000, 0x1000),
            app_window={
                LinkFamily.GNU: MemoryRange(0x20000000, 0x2000),  # exceeds
                LinkFamily.ARMLINK: MemoryRange(0x20000000, 0x1000),
            },
        )


def test_apollo510_windows_are_the_linker_script_values():
    """The load-bearing corrections vs the legacy tables, pinned exactly:
    MRAM at the app origin (NOT 0x0, which is ITCM on this family), DTCM's
    hardware aperture with per-link-family app extents (gcc: the full
    496 KB MCU_TCM script region; armlink: 492 KB MCU_TCM), ITCM
    present."""
    by_region = {w.region: w for w in linked_memory_map(get_soc("apollo510"))}
    assert by_region[MemoryRegion.MRAM].window.start == 0x00410000
    assert by_region[MemoryRegion.MRAM].window.length == 4_128_768
    assert by_region[MemoryRegion.ITCM].window.start == 0x00000000
    assert by_region[MemoryRegion.ITCM].window.length == 262_144
    dtcm = by_region[MemoryRegion.DTCM]
    assert dtcm.window.length == 524_288
    # gcc's extent is the FULL MCU_TCM script region — .stack floats
    # inside it and counts as occupancy; armlink's extent IS MCU_TCM
    # (its fixed heap+stack tile above it to the aperture).
    assert dtcm.app_window[LinkFamily.GNU] == MemoryRange(0x20000000, 507_904)
    assert dtcm.app_window[LinkFamily.ARMLINK] == MemoryRange(0x20000000, 503_808)
    psram = by_region[MemoryRegion.PSRAM]
    assert psram.window_provenance == "board-knowledge"
    assert psram.app_provenance == "board-knowledge"
    assert not psram.section_attributable  # no linker region ever maps here
    assert dtcm.section_attributable
    assert dtcm.window_provenance == "hardware-aperture"
    assert by_region[MemoryRegion.MRAM].window_provenance == "linker-app-origin"


# Every known divergence between capabilities._FAMILY_MEMORY_BASES (via
# soc_placement_ranges) and the verified windows, pinned EXACTLY per SoC
# (#176 review M-4, lengths added in the fresh-eyes round): real MRAM start
# plus (legacy length, verified length) for MRAM / TCM / SRAM. Every legacy
# MRAM base is 0x0 (asserted in the test body); a Phase-2 edit that turns
# any known divergence into agreement — or vice versa — must consciously
# edit this table.
_EXPECTED_LEGACY_VS_VERIFIED = {
    #  name         real MRAM    (legacy, verified) lengths for MRAM / TCM / SRAM
    "apollo3p": (0x0000C000, (2_048_000, 2_048_000), (65_536, 65_536), (716_800, 720_896)),
    "apollo4p": (0x00018000, (2_048_000, 1_998_848), (393_216, 393_216), (1_048_576, 1_048_576)),
    "apollo4l": (0x00018000, (2_048_000, 1_998_848), (393_216, 393_216), (1_048_576, 1_048_576)),
    "apollo510": (0x00410000, (4_194_304, 4_128_768), (524_288, 524_288), (3_145_728, 3_145_728)),
    "apollo510b": (0x00410000, (4_194_304, 4_128_768), (524_288, 524_288), (3_145_728, 3_145_728)),
    "apollo5b": (0x00410000, (4_194_304, 4_128_768), (524_288, 524_288), (3_145_728, 3_145_728)),
    "apollo330P": (0x00410000, (2_031_616, 2_031_616), (245_760, 262_144), (1_835_008, 1_835_008)),
}


def test_known_divergences_from_the_legacy_placement_table_are_pinned():
    """capabilities._FAMILY_MEMORY_BASES stays untouched in Phase 1 (verify-
    placement depends on it); this test pins EVERY known divergence — bases
    AND lengths — so the Phase-2 migration is a reviewed edit, not silent
    drift. Notable pinned facts: every legacy MRAM base is 0x0, entirely
    disjoint from the real windows on the AP5 family (0x0 is ITCM there);
    apollo330P's legacy 240 KB TCM is the gcc linker region while the
    verified window is the 256 KB hardware aperture; apollo3p's verified
    SRAM window includes the 4 KB STACKMEM slot the legacy table starts
    above."""
    for name in CHARACTERIZED_SOCS:
        soc = get_soc(name)
        legacy = soc_placement_ranges(soc)
        verified = {w.region: w for w in linked_memory_map(soc)}
        mram_start, mram_lens, tcm_lens, sram_lens = _EXPECTED_LEGACY_VS_VERIFIED[name]
        legacy_mram = legacy[Placement.MRAM]
        real_mram = verified[MemoryRegion.MRAM].window
        assert legacy_mram.start == 0x0, name
        assert real_mram.start == mram_start, name
        assert (legacy_mram.length, real_mram.length) == mram_lens, name
        legacy_tcm = legacy[Placement.TCM]
        real_dtcm = verified[MemoryRegion.DTCM].window
        assert legacy_tcm.start == real_dtcm.start, name
        assert (legacy_tcm.length, real_dtcm.length) == tcm_lens, name
        legacy_sram = legacy[Placement.SRAM]
        real_sram = verified[MemoryRegion.SRAM].window
        if name == "apollo3p":
            assert legacy_sram.start == 0x10011000 and real_sram.start == 0x10010000
        else:
            assert legacy_sram.start == real_sram.start, name
        assert (legacy_sram.length, real_sram.length) == sram_lens, name


def test_apollo330P_has_no_itcm_and_dtcm_is_the_256k_hardware_aperture():
    """No ITCM window on the default script. The DTCM window is the SDK's
    DTCM_MAX_SIZE = 256 KB — armlink tiles to exactly that (0x3B000 TCM +
    0x1000 heap + 0x4000 stack), so its 16 KB stack at 0x2003C000 IS DTCM;
    gcc's script merely declines the top 16 KB (#176 review M-1 corrected
    an earlier 240 KB window that pinned that stack as unclassifiable)."""
    windows = linked_memory_map(get_soc("apollo330P"))
    by_region = {w.region: w for w in windows}
    assert MemoryRegion.ITCM not in by_region
    assert by_region[MemoryRegion.DTCM].window.length == 262_144
    assert classify_address(0x2003C000, windows) is MemoryRegion.DTCM
    # gcc's extent is the full 240 KB MCU_TCM script region — on this
    # part .dtcm_text precedes the floating .stack, so no fixed carve-out
    # is possible. armlink's fixed stack sits inside the WINDOW but
    # outside the extent, which is what keeps the free math consistent:
    dtcm = by_region[MemoryRegion.DTCM]
    assert dtcm.app_window[LinkFamily.GNU] == MemoryRange(0x20000000, 245_760)
    assert dtcm.app_window[LinkFamily.ARMLINK] == MemoryRange(0x20000000, 241_664)
    assert not dtcm.app_window[LinkFamily.ARMLINK].contains(0x2003C000)


def test_apollo3p_dtcm_is_the_64k_hardware_aperture_and_stackmem_is_sram():
    """AM_HAL_FLASH_DTCM_END = 0x1000FFFF: the TCM is the FIRST 64 KB of
    the shared SRAM space, so the 4 KB STACKMEM slot at 0x10010000 (where
    gcc's .stack and armlink's ARM_LIB_STACK both live) is main SRAM, not
    TCM (#176 review M-2)."""
    windows = linked_memory_map(get_soc("apollo3p"))
    by_region = {w.region: w for w in windows}
    assert by_region[MemoryRegion.DTCM].window.length == 0x10000
    assert classify_address(0x10010000, windows) is MemoryRegion.SRAM
    assert classify_address(0x1000FFFF, windows) is MemoryRegion.DTCM
    # App SRAM is still only the 700 KB RWMEM either way — the 4 KB
    # STACKMEM slot is inside the window but below the app extent:
    sram = by_region[MemoryRegion.SRAM]
    assert sram.app_window[LinkFamily.GNU] == MemoryRange(0x10011000, 716_800)
    assert not sram.app_window[LinkFamily.GNU].contains(0x10010000)


def test_non_builtin_socs_degrade_to_empty_even_on_name_collision():
    """A custom SoC was never checked against any linker script — even one
    whose name collides with a registered part must NOT inherit its windows
    (#176 review M-3). Both forgery shapes: a renamed builtin (registered_name
    mismatch) and a custom-origin clone keeping the builtin name."""
    import dataclasses

    from helia_profiler.platform.soc import SocOrigin

    renamed = dataclasses.replace(get_soc("apollo510"), name="atomiq110")
    assert not renamed.is_builtin
    assert linked_memory_map(renamed) == ()

    custom_clone = dataclasses.replace(
        get_soc("apollo510"), origin=SocOrigin.CUSTOM, registered_name=None
    )
    assert custom_clone.name == "apollo510"  # the collision case
    assert not custom_clone.is_builtin
    assert linked_memory_map(custom_clone) == ()


def test_link_family_routing():
    assert link_family_for_toolchain("armclang") is LinkFamily.ARMLINK
    assert link_family_for_toolchain("arm-none-eabi-gcc") is LinkFamily.GNU
    assert link_family_for_toolchain("gcc") is LinkFamily.GNU
    assert link_family_for_toolchain("atfe") is LinkFamily.GNU


def test_link_family_rejects_unknown_toolchains():
    """An unrecognized toolchain raises rather than silently guessing GNU —
    a future armlink-based toolchain must be routed deliberately."""
    import pytest

    with pytest.raises(ValueError):
        link_family_for_toolchain("sdcc")


def test_real_gcc_fixture_inventory_classifies_correctly():
    """End-to-end with Phase 1a: every allocated section of the real
    readelf fixture (built with the NSX-shaped linker.ld) lands in the
    right apollo510 region."""
    from helia_profiler.hostenv.toolchain_probe import _inventory_via_readelf

    import helia_profiler.hostenv.toolchain_probe as tp

    text = (
        Path(__file__).parent / "fixtures" / "readelf" / "sections.txt"
    ).read_text()

    class _Result:
        returncode = 0
        stdout = text
        stderr = ""

    import unittest.mock as mock

    # NOTE: tp.subprocess IS the global subprocess module; the patch is
    # process-wide for the with-block. Kept because a module-local alias
    # would churn toolchain_probe for a test-only nicety.
    with mock.patch.object(tp.subprocess, "run", lambda *a, **k: _Result()):
        inventory = _inventory_via_readelf(
            Path("fw.elf"), readelf_cmd="readelf", timeout_s=5
        )
    assert inventory is not None
    sections, unparsed = inventory
    assert unparsed == 0
    windows = linked_memory_map(get_soc("apollo510"))
    # Keyed on (name, address), NOT name alone — section names are not
    # unique in general (armlink emits same-named sections per region;
    # NSX's gcc scripts declare .text twice). The fixture happens to have
    # unique names; the keying models the idiom Phase 2 must copy.
    classified = {
        (s.name, s.address): classify_address(s.address, windows)
        for s in sections
        if s.allocated
    }
    assert classified == {
        (".text", 0x00410000): MemoryRegion.MRAM,
        (".stack", 0x20000000): MemoryRegion.DTCM,
        (".data", 0x20004000): MemoryRegion.DTCM,
        (".bss", 0x20004020): MemoryRegion.DTCM,
        (".heap", 0x20004118): MemoryRegion.DTCM,
    }
    # And the free-math contract composes: gcc's floating .stack is
    # INSIDE the extent and counts as occupancy (live memory); the
    # fill-to-end .heap is inside but linker_reserved-excluded.
    # extent_len − Σ(non-reserved sections in extent) is the honest free.
    by_region = {w.region: w for w in windows}
    gcc_app = by_region[MemoryRegion.DTCM].app_window[LinkFamily.GNU]
    by_key = {(s.name, s.address): s for s in sections}
    stack = by_key[(".stack", 0x20000000)]
    assert gcc_app.contains(stack.address) and not stack.linker_reserved
    heap = by_key[(".heap", 0x20004118)]
    assert gcc_app.contains(heap.address) and heap.linker_reserved
    occupancy = sum(
        s.size
        for s in sections
        if s.allocated and not s.linker_reserved and gcc_app.contains(s.address)
    )
    assert occupancy == 0x4000 + 0x20 + 0xF8  # .stack + .data + .bss
    # .heap fills to the region top, so its size IS ground-truth free —
    # and the formula reproduces it exactly (robust to WHERE the stack
    # sits, the a50e63d fresh-eyes failure mode on apollo330P):
    assert gcc_app.length - occupancy == heap.size


# Every app extent pinned exactly, per (soc, region, family): (start, length).
# The app extents are the PR's headline deliverable — window starts/lengths
# are pinned by _EXPECTED_LEGACY_VS_VERIFIED, but a transposed digit in an
# extent would otherwise ship silently (#176 fresh-review M-4). GNU DTCM
# extents are the FULL script MCU_TCM regions (the floating .stack counts
# as occupancy — its position is script-order-dependent); armlink DTCM
# extents are MCU_TCM with the fixed heap/stack carved out by extent;
# apollo3p SRAM starts at RWMEM above the fixed STACKMEM slot.
_EXPECTED_APP_WINDOWS = {
    "apollo3p": {
        MemoryRegion.MRAM: {
            LinkFamily.GNU: (0x0000C000, 2_048_000),
            LinkFamily.ARMLINK: (0x0000C000, 2_048_000),
        },
        MemoryRegion.DTCM: {
            LinkFamily.GNU: (0x10000000, 65_536),
            LinkFamily.ARMLINK: (0x10000000, 65_536),
        },
        MemoryRegion.SRAM: {
            LinkFamily.GNU: (0x10011000, 716_800),
            LinkFamily.ARMLINK: (0x10011000, 716_800),
        },
    },
    "apollo4p": {
        MemoryRegion.MRAM: {
            LinkFamily.GNU: (0x00018000, 1_998_848),
            LinkFamily.ARMLINK: (0x00018000, 1_998_848),
        },
        MemoryRegion.DTCM: {
            LinkFamily.GNU: (0x10000000, 393_216),
            LinkFamily.ARMLINK: (0x10000000, 372_736),
        },
        MemoryRegion.SRAM: {
            LinkFamily.GNU: (0x10060000, 1_048_576),
            LinkFamily.ARMLINK: (0x10060000, 1_048_576),
        },
    },
    "apollo510": {
        MemoryRegion.ITCM: {
            LinkFamily.GNU: (0x00000000, 262_144),
            LinkFamily.ARMLINK: (0x00000000, 262_144),
        },
        MemoryRegion.MRAM: {
            LinkFamily.GNU: (0x00410000, 4_128_768),
            LinkFamily.ARMLINK: (0x00410000, 4_128_768),
        },
        MemoryRegion.DTCM: {
            LinkFamily.GNU: (0x20000000, 507_904),
            LinkFamily.ARMLINK: (0x20000000, 503_808),
        },
        MemoryRegion.SRAM: {
            LinkFamily.GNU: (0x20080000, 3_145_728),
            LinkFamily.ARMLINK: (0x20080000, 3_145_728),
        },
    },
    "apollo330P": {
        MemoryRegion.MRAM: {
            LinkFamily.GNU: (0x00410000, 2_031_616),
            LinkFamily.ARMLINK: (0x00410000, 2_031_616),
        },
        MemoryRegion.DTCM: {
            LinkFamily.GNU: (0x20000000, 245_760),
            LinkFamily.ARMLINK: (0x20000000, 241_664),
        },
        MemoryRegion.SRAM: {
            LinkFamily.GNU: (0x20080000, 1_835_008),
            LinkFamily.ARMLINK: (0x20080000, 1_835_008),
        },
    },
}
# The 4l/510b/5b variants share their sibling's linker layout exactly:
_EXPECTED_APP_WINDOWS["apollo4l"] = _EXPECTED_APP_WINDOWS["apollo4p"]
_EXPECTED_APP_WINDOWS["apollo510b"] = _EXPECTED_APP_WINDOWS["apollo510"]
_EXPECTED_APP_WINDOWS["apollo5b"] = _EXPECTED_APP_WINDOWS["apollo510"]


def test_every_app_window_extent_is_pinned_exactly():
    for name in CHARACTERIZED_SOCS:
        expected = _EXPECTED_APP_WINDOWS[name]
        windows = {
            w.region: w
            for w in linked_memory_map(get_soc(name))
            if w.region is not MemoryRegion.PSRAM  # board-knowledge, below
        }
        assert set(windows) == set(expected), name
        for region, per_family in expected.items():
            for family, (start, length) in per_family.items():
                extent = windows[region].app_window[family]
                assert (extent.start, extent.length) == (start, length), (
                    name,
                    region,
                    family,
                )


def test_psram_window_is_board_knowledge_and_not_section_attributable():
    """No linker region maps PSRAM on any SoC — Phase 2 must reconcile it
    from the plan, never report used=0/free=capacity off an inventory that
    structurally cannot see it (#176 fresh-review M-1's PSRAM corollary)."""
    for name in CHARACTERIZED_SOCS:
        for w in linked_memory_map(get_soc(name)):
            if w.region is MemoryRegion.PSRAM:
                assert not w.section_attributable, name
                assert w.window_provenance == "board-knowledge", name
            else:
                assert w.section_attributable, (name, w.region)


def test_non_default_linker_profile_returns_empty():
    """These tables characterize NSX's DEFAULT profile only. linker_profile
    is a documented engine knob whose itcm scripts declare DIFFERENT
    regions (AP510-sized ones on apollo330P — the upstream NSX bug), so a
    non-default profile must degrade to unavailable, not return a
    confidently wrong map (#176 fresh-review M-3)."""
    soc = get_soc("apollo330P")
    assert linked_memory_map(soc) == linked_memory_map(soc, linker_profile="default")
    assert linked_memory_map(soc, linker_profile="itcm") == ()
    assert linked_memory_map(soc, linker_profile="nbl") == ()


def test_characterized_socs_cover_the_entire_registry():
    """The docstring's guarantee — every registered builtin is
    characterized — enforced, so registering a new SoC without a memory map
    fails loudly here instead of silently returning () forever."""
    from helia_profiler.platform import list_socs

    assert set(CHARACTERIZED_SOCS) == {soc.name for soc in list_socs()}


def test_link_family_map_stays_in_lockstep_with_the_toolchain_enum():
    """#229 D2 inversion: platform owns the toolchain-name map and must
    classify exactly the names config's Toolchain enum admits — no silent
    drift in either direction."""
    from helia_profiler.platform.memory_map import _LINK_FAMILY_BY_TOOLCHAIN
    from helia_profiler.vocab import Toolchain

    assert set(_LINK_FAMILY_BY_TOOLCHAIN) == {t.value for t in Toolchain}


def test_unknown_toolchain_raises_with_the_known_set():
    import pytest

    from helia_profiler.platform.memory_map import link_family_for_toolchain

    with pytest.raises(ValueError, match="not a supported toolchain"):
        link_family_for_toolchain("mystery-cc")
