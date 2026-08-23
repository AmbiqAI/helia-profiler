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
from helia_profiler.platform.soc import soc_placement_ranges

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


def test_app_available_never_exceeds_the_window():
    for name in CHARACTERIZED_SOCS:
        for w in linked_memory_map(get_soc(name)):
            for family in LinkFamily:
                assert 0 < w.app_available[family] <= w.window.length, (
                    name,
                    w.region,
                    family,
                )


def test_apollo510_windows_are_the_linker_script_values():
    """The load-bearing corrections vs the legacy tables, pinned exactly:
    MRAM at the app origin (NOT 0x0, which is ITCM on this family), DTCM's
    hardware aperture with per-link-family app-available (gcc 480 KB /
    armlink 492 KB), ITCM present."""
    by_region = {w.region: w for w in linked_memory_map(get_soc("apollo510"))}
    assert by_region[MemoryRegion.MRAM].window.start == 0x00410000
    assert by_region[MemoryRegion.MRAM].window.length == 4_128_768
    assert by_region[MemoryRegion.ITCM].window.start == 0x00000000
    assert by_region[MemoryRegion.ITCM].window.length == 262_144
    dtcm = by_region[MemoryRegion.DTCM]
    assert dtcm.window.length == 524_288
    assert dtcm.app_available[LinkFamily.GNU] == 491_520
    assert dtcm.app_available[LinkFamily.ARMLINK] == 503_808
    assert by_region[MemoryRegion.PSRAM].provenance == "board-knowledge"


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
    # gcc/armlink app-available stay the linker-script facts:
    assert by_region[MemoryRegion.DTCM].app_available[LinkFamily.GNU] == 229_376
    assert by_region[MemoryRegion.DTCM].app_available[LinkFamily.ARMLINK] == 241_664


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
    # App-available SRAM is still only the 700 KB RWMEM either way:
    assert by_region[MemoryRegion.SRAM].app_available[LinkFamily.GNU] == 716_800


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
    from helia_profiler.toolchain_probe import _inventory_via_readelf

    import helia_profiler.toolchain_probe as tp

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
        sections = _inventory_via_readelf(
            Path("fw.elf"), readelf_cmd="readelf", timeout_s=5
        )
    windows = linked_memory_map(get_soc("apollo510"))
    classified = {
        s.name: classify_address(s.address, windows)
        for s in sections
        if s.allocated
    }
    assert classified == {
        ".text": MemoryRegion.MRAM,
        ".stack": MemoryRegion.DTCM,
        ".data": MemoryRegion.DTCM,
        ".bss": MemoryRegion.DTCM,
        ".heap": MemoryRegion.DTCM,
    }
