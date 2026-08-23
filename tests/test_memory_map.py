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
# soc_placement_ranges) and the verified windows, enumerated exhaustively
# (#176 review M-4). "disjoint": the legacy range shares NO address with the
# real window; "overlap": shifted/resized but intersecting; "equal": same
# span. Phase 2's migration must consciously edit this table.
_EXPECTED_LEGACY_MRAM_RELATION = {
    "apollo3p": "overlap",  # legacy base 0x0 vs real ROMEM 0xC000
    "apollo4p": "overlap",  # legacy base 0x0/2000 KB vs real 0x18000/1952 KB
    "apollo4l": "overlap",
    "apollo510": "disjoint",  # legacy [0x0,0x400000) is ITCM territory
    "apollo510b": "disjoint",
    "apollo5b": "disjoint",
    "apollo330P": "disjoint",  # same 0x0 base; real MRAM starts 0x410000
}


def _relation(a, b) -> str:
    if a.start == b.start and a.length == b.length:
        return "equal"
    if a.end <= b.start or b.end <= a.start:
        return "disjoint"
    return "overlap"


def test_known_divergences_from_the_legacy_placement_table_are_pinned():
    """capabilities._FAMILY_MEMORY_BASES stays untouched in Phase 1 (verify-
    placement depends on it); this test pins EVERY known divergence so the
    Phase-2 migration is a reviewed edit, not silent drift."""
    for name in CHARACTERIZED_SOCS:
        soc = get_soc(name)
        legacy = soc_placement_ranges(soc)
        verified = {w.region: w for w in linked_memory_map(soc)}
        # MRAM: every legacy base is 0x0; the relation to the real window
        # varies per family and is pinned exactly.
        legacy_mram = legacy[Placement.MRAM]
        assert legacy_mram.start == 0x0, name
        assert (
            _relation(legacy_mram, verified[MemoryRegion.MRAM].window)
            == _EXPECTED_LEGACY_MRAM_RELATION[name]
        ), name
        # TCM bases agree everywhere; lengths agree except where the legacy
        # table recorded a single linker script's slice of the aperture.
        legacy_tcm = legacy[Placement.TCM]
        real_dtcm = verified[MemoryRegion.DTCM].window
        assert legacy_tcm.start == real_dtcm.start, name
        # SRAM: bases agree except apollo3p, where the verified window now
        # starts at the hardware SRAM boundary 0x10010000 (the 4 KB STACKMEM
        # slot) while the legacy table starts at RWMEM 0x10011000.
        legacy_sram = legacy[Placement.SRAM]
        real_sram = verified[MemoryRegion.SRAM].window
        if name == "apollo3p":
            assert legacy_sram.start == 0x10011000 and real_sram.start == 0x10010000
        else:
            assert legacy_sram.start == real_sram.start, name


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
