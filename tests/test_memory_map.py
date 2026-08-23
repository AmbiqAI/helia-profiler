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


def test_known_divergences_from_the_legacy_placement_table_are_pinned():
    """capabilities._FAMILY_MEMORY_BASES stays untouched in Phase 1 (verify-
    placement depends on it); this test pins the KNOWN divergences so the
    Phase-2 migration is a reviewed edit, not silent drift. The legacy AP5
    'MRAM' range [0x0, 0x400000) is entirely disjoint from the real MRAM
    window — it actually spans ITCM."""
    soc = get_soc("apollo510")
    legacy = soc_placement_ranges(soc)
    verified = {w.region: w for w in linked_memory_map(soc)}
    legacy_mram = legacy[Placement.MRAM]
    real_mram = verified[MemoryRegion.MRAM].window
    assert legacy_mram.start == 0x0
    assert legacy_mram.end <= real_mram.start  # disjoint, not merely shifted
    # TCM/SRAM bases agree between the tables:
    assert legacy[Placement.TCM].start == verified[MemoryRegion.DTCM].window.start
    assert legacy[Placement.SRAM].start == verified[MemoryRegion.SRAM].window.start


def test_apollo330P_has_no_itcm_and_the_armlink_stack_question_is_honest():
    """No ITCM window on the default script; the DTCM window is the
    hardware-confirmed 240 KB, so armlink's 16 KB stack at 0x2003C000 —
    which the scatter places beyond it — classifies as outside-every-window,
    the honest flag for the unresolved gcc-vs-armclang aperture question."""
    windows = linked_memory_map(get_soc("apollo330P"))
    regions = {w.region for w in windows}
    assert MemoryRegion.ITCM not in regions
    assert classify_address(0x2003C000, windows) is None


def test_uncharacterized_soc_degrades_to_empty():
    class _FakeSoc:
        name = "not-a-soc"

    assert linked_memory_map(_FakeSoc()) == ()


def test_link_family_routing():
    assert link_family_for_toolchain("armclang") is LinkFamily.ARMLINK
    assert link_family_for_toolchain("arm-none-eabi-gcc") is LinkFamily.GNU
    assert link_family_for_toolchain("atfe") is LinkFamily.GNU


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
