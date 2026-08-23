"""Tests for the measured memory-regions join (#133 Phase 2).

Driven by the real readelf fixture (tests/fixtures/readelf/) against the
verified apollo510 map, with every number derived by hand from the capture:
the fill-to-end .heap size IS ground-truth free, and the formula must
reproduce it exactly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.memory_measurement import measure_memory_regions
from helia_profiler.placement import MemoryRegion
from helia_profiler.platform import get_soc
from helia_profiler.results import MeasuredMemoryRegions
from helia_profiler.toolchain_probe import SectionInventory

FIXTURES = Path(__file__).parent / "fixtures" / "readelf"


@pytest.fixture
def gcc_inventory(monkeypatch):
    """Route the tool probes at the committed real captures."""
    import helia_profiler.toolchain_probe as tp

    sections_text = (FIXTURES / "sections.txt").read_text()
    segments_text = (FIXTURES / "segments.txt").read_text()

    class _Result:
        returncode = 0
        stderr = ""

        def __init__(self, stdout):
            self.stdout = stdout

    def _run(argv, **kwargs):
        return _Result(sections_text if "-S" in argv else segments_text)

    monkeypatch.setattr(tp.subprocess, "run", _run)


def _measure(**kwargs) -> MeasuredMemoryRegions | None:
    return measure_memory_regions(
        Path("fw.elf"), "arm-none-eabi-gcc", get_soc("apollo510"), **kwargs
    )


class TestFixtureMeasurement:
    def test_dtcm_free_reproduces_the_fill_to_end_heap_exactly(self, gcc_inventory):
        measured = _measure()
        assert measured is not None
        dtcm = measured.region(MemoryRegion.DTCM)
        # used = .stack (live) + .data + .bss; reserved = the fill-to-end
        # .heap, whose size IS what was left in the region:
        assert dtcm.used == 0x4000 + 0x20 + 0xF8
        assert dtcm.reserved == 0x77EE8
        assert dtcm.free == 0x77EE8
        assert dtcm.app_length == 507_904
        assert dtcm.window_length == 524_288

    def test_mram_load_image_sums_segments_by_physical_address(self, gcc_inventory):
        """#133 D3: .text loads in place (0x3c bytes) and .data's init
        image loads at paddr 0x0041003C (0x20 bytes) — both land in MRAM
        by PHYSICAL address; the NOBITS segments contribute file_size 0."""
        measured = _measure()
        mram = measured.region(MemoryRegion.MRAM)
        assert mram.used == 0x3C  # .text runs in place
        assert mram.load_image == 0x3C + 0x20
        assert measured.region(MemoryRegion.DTCM).load_image == 0

    def test_idle_regions_report_zero_and_psram_is_absent(self, gcc_inventory):
        measured = _measure()
        regions = {r.region for r in measured.regions}
        assert MemoryRegion.PSRAM not in regions  # plan-owned, not measured
        itcm = measured.region(MemoryRegion.ITCM)
        assert itcm.used == 0 and itcm.reserved == 0 and itcm.load_image == 0

    def test_link_family_and_profile_are_recorded(self, gcc_inventory):
        measured = _measure()
        assert measured.link_family == "gnu"
        assert measured.linker_profile == "default"
        assert measured.unattributed == ()


class TestDegradation:
    def test_non_default_linker_profile_degrades_to_none(self, gcc_inventory):
        assert _measure(linker_profile="itcm") is None

    def test_uncharacterized_soc_degrades_to_none(self, gcc_inventory):
        import dataclasses

        custom = dataclasses.replace(get_soc("apollo510"), name="not-a-soc")
        assert (
            measure_memory_regions(Path("fw.elf"), "arm-none-eabi-gcc", custom)
            is None
        )

    def test_tool_failure_degrades_to_none(self, monkeypatch):
        import helia_profiler.toolchain_probe as tp

        def _boom(*a, **k):
            raise FileNotFoundError("readelf")

        monkeypatch.setattr(tp.subprocess, "run", _boom)
        assert _measure() is None

    def test_partial_inventory_is_refused(self, monkeypatch):
        """unparsed_rows nonzero -> the occupancy would be understated;
        the whole measured view must be absent, not silently low."""
        import helia_profiler.memory_measurement as mm

        partial = SectionInventory(sections=(), segments=(), unparsed_rows=2)
        monkeypatch.setattr(mm, "section_inventory", lambda *a, **k: partial)
        assert _measure() is None


def test_unattributed_sections_are_flagged(monkeypatch):
    """An allocated section outside every verified window is the police
    flag — reported, never silently dropped or misfiled."""
    import helia_profiler.memory_measurement as mm
    from helia_profiler.toolchain_probe import ElfSection

    inventory = SectionInventory(
        sections=(
            ElfSection(
                name=".mystery",
                address=0x30000000,
                size=64,
                nobits=False,
                allocated=True,
                index=1,
            ),
        ),
        segments=(),
    )
    monkeypatch.setattr(mm, "section_inventory", lambda *a, **k: inventory)
    measured = measure_memory_regions(
        Path("fw.elf"), "arm-none-eabi-gcc", get_soc("apollo510")
    )
    assert len(measured.unattributed) == 1
    flag = measured.unattributed[0]
    assert (flag.name, flag.address, flag.size) == (".mystery", 0x30000000, 64)


def test_armlink_join_uses_the_extent_not_the_window(monkeypatch):
    """#177 review M1: the armlink half of the two-mechanism rule, shaped
    like a real NSX AP510 scatter link (numbers from the #176 fresh-review
    real-armlink reproduction): two same-named MCU_TCM sections (PROGBITS
    + NOBITS), the fixed 4 KB ARM_LIB_HEAP at the extent end 0x2007B000,
    and the fixed 16 KB ARM_LIB_STACK at 0x2007C000 — inside the WINDOW,
    outside the EXTENT. Kills both previously-surviving mutations:
    extent.contains -> window.contains flips the stack into `used`;
    deleting the outside-extent branch zeroes heap+stack out of
    `reserved`."""
    import helia_profiler.memory_measurement as mm
    from helia_profiler.toolchain_probe import ElfSection, LoadSegment

    inventory = SectionInventory(
        sections=(
            ElfSection(".text", 0x00410000, 40, False, True, index=1),
            ElfSection("MCU_TCM", 0x20000000, 16384, False, True, index=2),
            ElfSection("MCU_TCM", 0x20004000, 8192, True, True, index=3),
            ElfSection(
                "ARM_LIB_HEAP",
                0x2007B000,
                4096,
                True,
                True,
                index=4,
                linker_reserved=True,
            ),
            ElfSection("ARM_LIB_STACK", 0x2007C000, 16384, True, True, index=5),
        ),
        segments=(LoadSegment(0x00410000, 0x00410000, 16424, 45096),),
    )
    monkeypatch.setattr(mm, "section_inventory", lambda *a, **k: inventory)
    measured = measure_memory_regions(
        Path("fw.axf"), "armclang", get_soc("apollo510")
    )
    assert measured.link_family == "armlink"
    dtcm = measured.region(MemoryRegion.DTCM)
    # used: BOTH same-named MCU_TCM sections, nothing else.
    assert dtcm.used == 16384 + 8192
    # reserved: in-extent linker_reserved (none here — the heap sits at
    # exactly the extent END, outside) + in-window/out-of-extent allocated
    # (heap 4096 + stack 16384).
    assert dtcm.reserved == 4096 + 16384
    assert dtcm.app_length == 503_808
    assert dtcm.free == 503_808 - (16384 + 8192)  # 479232, the real figure
    # armlink's single aggregate PT_LOAD: all file bytes to MRAM by paddr.
    mram = measured.region(MemoryRegion.MRAM)
    assert mram.load_image == 16424
    assert measured.unattributed == ()


def test_psram_landing_bytes_are_flagged_not_swallowed(monkeypatch):
    """#177 review m2: PSRAM is excluded from the measured regions (the
    plan owns it), so a section at a PSRAM address must surface as
    unattributed — classifying it silently would disable the police flag
    on exactly the region this block cannot report."""
    import helia_profiler.memory_measurement as mm
    from helia_profiler.toolchain_probe import ElfSection

    inventory = SectionInventory(
        sections=(
            ElfSection(".psram_data", 0x60000000, 4096, False, True, index=1),
        ),
        segments=(),
    )
    monkeypatch.setattr(mm, "section_inventory", lambda *a, **k: inventory)
    measured = measure_memory_regions(
        Path("fw.elf"), "arm-none-eabi-gcc", get_soc("apollo510")
    )
    assert [u.name for u in measured.unattributed] == [".psram_data"]


def test_unattributed_load_bytes_are_counted(monkeypatch):
    """#177 review m3: PT_LOAD file bytes whose paddr classifies nowhere
    (below the app MRAM origin, a PSRAM address, anywhere uncharacterized)
    must be counted, not vanish from load_image."""
    import helia_profiler.memory_measurement as mm
    from helia_profiler.toolchain_probe import ElfSection, LoadSegment

    inventory = SectionInventory(
        sections=(ElfSection(".text", 0x00410000, 60, False, True, index=1),),
        segments=(
            LoadSegment(0x00410000, 0x00410000, 60, 60),
            LoadSegment(0x20000000, 0x00400000, 0x2000, 0x2000),  # below origin
        ),
    )
    monkeypatch.setattr(mm, "section_inventory", lambda *a, **k: inventory)
    measured = measure_memory_regions(
        Path("fw.elf"), "arm-none-eabi-gcc", get_soc("apollo510")
    )
    assert measured.region(MemoryRegion.MRAM).load_image == 60
    assert measured.unattributed_load_bytes == 0x2000


def test_zero_length_orphan_sections_are_not_flagged(monkeypatch):
    """A zero-byte end marker outside every window is noise, not lost
    bytes (#177 review n3)."""
    import helia_profiler.memory_measurement as mm
    from helia_profiler.toolchain_probe import ElfSection

    inventory = SectionInventory(
        sections=(ElfSection(".marker", 0x30000000, 0, False, True, index=1),),
        segments=(),
    )
    monkeypatch.setattr(mm, "section_inventory", lambda *a, **k: inventory)
    measured = measure_memory_regions(
        Path("fw.elf"), "arm-none-eabi-gcc", get_soc("apollo510")
    )
    assert measured.unattributed == ()


def test_serialised_shape_is_the_contract():
    """The summary.json / memory.json key set for the measured block —
    schema v3's new surface, pinned independently of the golden digests."""
    from helia_profiler.report.memory import _serialise_memory_regions
    from helia_profiler.results import (
        MeasuredRegion,
        UnattributedSection,
    )

    measured = MeasuredMemoryRegions(
        link_family="armlink",
        linker_profile="default",
        regions=(
            MeasuredRegion(
                region=MemoryRegion.DTCM,
                window_start=0x20000000,
                window_length=262_144,
                app_start=0x20000000,
                app_length=241_664,
                used=1000,
                reserved=20_480,
                load_image=0,
            ),
        ),
        unattributed=(UnattributedSection(name=".x", address=0x0, size=1),),
    )
    payload = _serialise_memory_regions(measured)
    assert set(payload) == {
        "link_family",
        "linker_profile",
        "regions",
        "unattributed",
        "unattributed_load_bytes",
    }
    (region,) = payload["regions"]
    assert set(region) == {
        "region",
        "window",
        "app_window",
        "used",
        "reserved",
        "free",
        "load_image",
        "window_provenance",
        "app_provenance",
    }
    assert region["free"] == 241_664 - 1000
    assert region["window"] == {"start": 0x20000000, "length": 262_144}
    assert payload["unattributed"] == [{"name": ".x", "address": 0, "size": 1}]
