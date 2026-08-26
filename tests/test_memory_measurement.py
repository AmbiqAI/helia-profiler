"""Tests for the measured memory-regions join (#133 Phase 2).

Driven by the real readelf fixture (tests/fixtures/readelf/) against the
verified apollo510 map, with every number derived by hand from the capture:
the fill-to-end .heap size IS ground-truth free, and the formula must
reproduce it exactly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.engines import EngineType
from helia_profiler.memory_measurement import measure_memory_regions
from helia_profiler.placement import MemoryRegion
from helia_profiler.platform import get_soc
from helia_profiler.results import ConsumerKind, MeasuredMemoryRegions
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
        assert dtcm is not None
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
        assert measured is not None
        mram = measured.region(MemoryRegion.MRAM)
        assert mram is not None
        assert mram.used == 0x3C  # .text runs in place
        assert mram.load_image == 0x3C + 0x20
        dtcm = measured.region(MemoryRegion.DTCM)
        assert dtcm is not None
        assert dtcm.load_image == 0

    def test_idle_regions_report_zero_and_psram_is_absent(self, gcc_inventory):
        measured = _measure()
        assert measured is not None
        regions = {r.region for r in measured.regions}
        assert MemoryRegion.PSRAM not in regions  # plan-owned, not measured
        itcm = measured.region(MemoryRegion.ITCM)
        assert itcm is not None
        assert itcm.used == 0 and itcm.reserved == 0 and itcm.load_image == 0

    def test_link_family_and_profile_are_recorded(self, gcc_inventory):
        measured = _measure()
        assert measured is not None
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
    assert measured is not None
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
    assert measured is not None
    assert measured.link_family == "armlink"
    dtcm = measured.region(MemoryRegion.DTCM)
    assert dtcm is not None
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
    assert mram is not None
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
    assert measured is not None
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
    assert measured is not None
    mram = measured.region(MemoryRegion.MRAM)
    assert mram is not None
    assert mram.load_image == 60
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
    assert measured is not None
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


# ---------------------------------------------------------------------------
# Phase 3: symbol inventory + reconciliation
# ---------------------------------------------------------------------------


class TestSymbolInventory:
    def _symbols(self, monkeypatch, text=None):
        import helia_profiler.toolchain_probe as tp
        from helia_profiler.toolchain_probe import symbol_inventory

        class _Result:
            returncode = 0
            stderr = ""
            stdout = (
                text
                if text is not None
                else (FIXTURES / "symbols.txt").read_text()
            )

        monkeypatch.setattr(tp.subprocess, "run", lambda *a, **k: _Result())
        return symbol_inventory(Path("fw.elf"), "arm-none-eabi-gcc")

    def test_real_capture_parses_every_row(self, monkeypatch):
        symbols, unparsed = self._symbols(monkeypatch)
        assert unparsed == 0
        by_name = {s.name: s for s in symbols}
        assert by_name["g_stack"].size == 0x4000
        assert by_name["g_stack"].address == 0x20000000
        assert by_name["g_initialized"].size == 0x20
        assert by_name["g_zero_init"].size == 0xF8
        assert by_name["__HeapBase"].size == 0x77EE8  # == the .heap section
        assert by_name["main"].type == "T"

    def test_garbage_rows_count_as_unparsed(self, monkeypatch):
        text = "20000000 00004000 b g_stack\nnot a symbol row at all\n"
        symbols, unparsed = self._symbols(monkeypatch, text=text)
        assert len(symbols) == 1 and unparsed == 1

    def test_tool_failure_degrades_to_none(self, monkeypatch):
        import helia_profiler.toolchain_probe as tp
        from helia_profiler.toolchain_probe import symbol_inventory

        def _boom(*a, **k):
            raise FileNotFoundError("nm")

        monkeypatch.setattr(tp.subprocess, "run", _boom)
        assert symbol_inventory(Path("fw.elf"), "arm-none-eabi-gcc") is None


class TestReconciliation:
    def _symbols(self):
        from helia_profiler.toolchain_probe import SymbolEntry

        return (
            SymbolEntry("_ZL15g_arena_storage", 0x200128E0, 0x8000, "b"),
            SymbolEntry("_ZL10model_data", 0x20004050, 0xD1F0, "d"),
            SymbolEntry("g_pui32Stack", 0x20000000, 0x4000, "b"),
            SymbolEntry("_acUpBuffer", 0x2001AAF8, 0x8000, "b"),
            SymbolEntry("_acDownBuffer", 0x2001AAE8, 0x10, "b"),
            SymbolEntry("_SEGGER_RTT", 0x20012838, 0xA8, "d"),
            # alias pair over one object — must not double the sum:
            SymbolEntry("_ZL10g_profiler", 0x20080000, 0x180FC, "d"),
            SymbolEntry("_ssdata", 0x20080000, 0x180FC, "D"),
        )

    def _plan(self, consumers_by_region):
        from helia_profiler.results import (
            MemoryConsumer,
            MemoryPlan,
            MemoryRegionUsage,
        )

        regions = tuple(
            MemoryRegionUsage(
                region=region,
                capacity=0,
                used=sum(c.size for c in consumers),
                consumers=tuple(consumers),
            )
            for region, consumers in consumers_by_region.items()
        )
        return MemoryPlan(engine=EngineType.HELIA_RT, regions=regions)

    def _measured(self):
        from helia_profiler.results import MeasuredRegion

        return MeasuredMemoryRegions(
            link_family="gnu",
            linker_profile="default",
            regions=(
                MeasuredRegion(
                    region=MemoryRegion.DTCM,
                    window_start=0x20000000,
                    window_length=524_288,
                    app_start=0x20000000,
                    app_length=507_904,
                    used=142_528,
                    reserved=365_372,
                ),
                MeasuredRegion(
                    region=MemoryRegion.SRAM,
                    window_start=0x20080000,
                    window_length=3_145_728,
                    app_start=0x20080000,
                    app_length=3_145_728,
                    used=98_556,
                    reserved=0,
                ),
            ),
        )

    def test_matched_missing_unmatchable_and_deltas(self):
        from helia_profiler.memory_measurement import reconcile_memory
        from helia_profiler.results import MemoryConsumer

        plan = self._plan(
            {
                MemoryRegion.DTCM: [
                    MemoryConsumer(
                        name="tensor_arena", size=0x8000, kind=ConsumerKind.ARENA
                    ),
                    MemoryConsumer(
                        name="rtt_buffers",
                        size=0x8000 + 16 + 168,
                        kind=ConsumerKind.OTHER,
                    ),
                    MemoryConsumer(
                        name="usb_buffers", size=5120, kind=ConsumerKind.OTHER
                    ),
                ],
                MemoryRegion.PSRAM: [
                    MemoryConsumer(
                        name="model_psram_blob", size=1024, kind=ConsumerKind.WEIGHTS
                    ),
                ],
            }
        )
        rec = reconcile_memory(plan, self._measured(), self._symbols())
        by_name = {c.name: c for c in rec.consumers}
        arena = by_name["tensor_arena"]
        assert arena.status == "matched" and arena.delta == 0
        assert arena.matched_symbols == ("_ZL15g_arena_storage",)
        # rtt: three pieces summed exactly -> delta 0
        rtt = by_name["rtt_buffers"]
        assert rtt.status == "matched"
        assert rtt.measured_size == 0x8000 + 0x10 + 0xA8
        assert rtt.delta == 0
        # usb_buffers has candidates but no symbol in this image:
        assert by_name["usb_buffers"].status == "missing"
        # a consumer with no mapping at all is structural:
        assert by_name["model_psram_blob"].status == "unmatchable"

    def test_alias_pair_is_not_double_counted(self):
        """Two MATCHING names over one object (the extern alias plus the
        mangled static) must sum once. The #179 review proved the earlier
        version of this test vacuous — its alias (_ssdata) never matched
        a candidate, so the dedup branch never ran."""
        from helia_profiler.memory_measurement import reconcile_memory
        from helia_profiler.results import MemoryConsumer
        from helia_profiler.toolchain_probe import SymbolEntry

        plan = self._plan(
            {
                MemoryRegion.SRAM: [
                    MemoryConsumer(
                        name="pmu_layer_records", size=4096 * 24, kind=ConsumerKind.OTHER
                    ),
                ],
            }
        )
        symbols = (
            SymbolEntry("_ZL10g_profiler", 0x20080000, 0x180FC, "d"),
            SymbolEntry("g_profiler", 0x20080000, 0x180FC, "D"),  # alias
        )
        rec = reconcile_memory(plan, self._measured(), symbols)
        (records,) = rec.consumers
        assert records.status == "matched"
        assert records.measured_size == 0x180FC  # once, not twice
        assert records.delta == 0x180FC - 4096 * 24
        assert records.measured_region == "SRAM"

    def test_object_booked_plan_reconciles_at_zero(self):
        """The shipped TFLM/heliaRT plan books the whole g_profiler object
        (records + 252 header on ARMV8M_PMU parts), so against the real
        symbol the delta is exactly zero."""
        from helia_profiler.memory_measurement import reconcile_memory
        from helia_profiler.results import MemoryConsumer
        from helia_profiler.toolchain_probe import SymbolEntry

        plan = self._plan(
            {
                MemoryRegion.SRAM: [
                    MemoryConsumer(
                        name="pmu_layer_records",
                        size=4096 * 24 + 252,
                        kind=ConsumerKind.OTHER,
                    ),
                ],
            }
        )
        symbols = (SymbolEntry("_ZL10g_profiler", 0x20080000, 0x180FC, "d"),)
        rec = reconcile_memory(plan, self._measured(), symbols)
        (records,) = rec.consumers
        assert records.delta == 0  # 0x180FC == 4096*24+252

    def test_aot_symbol_hint_wins_over_the_name_table(self):
        from helia_profiler.memory_measurement import reconcile_memory
        from helia_profiler.results import MemoryConsumer
        from helia_profiler.toolchain_probe import SymbolEntry

        plan = self._plan(
            {
                MemoryRegion.DTCM: [
                    MemoryConsumer(
                        name="dtcm_scratch_arena_0",
                        size=0x1000,
                        kind=ConsumerKind.ARENA,
                        symbol="hpx_arena_dtcm_buffer",
                    ),
                ],
            }
        )
        symbols = (SymbolEntry("hpx_arena_dtcm_buffer", 0x20001000, 0x1200, "b"),)
        rec = reconcile_memory(plan, self._measured(), symbols)
        (consumer,) = rec.consumers
        assert consumer.status == "matched"
        assert consumer.delta == 0x200

    def test_region_deltas_compare_plan_to_measured_used(self):
        from helia_profiler.memory_measurement import reconcile_memory
        from helia_profiler.results import MemoryConsumer

        plan = self._plan(
            {
                MemoryRegion.SRAM: [
                    MemoryConsumer(
                        name="pmu_layer_records", size=4096 * 24, kind=ConsumerKind.OTHER
                    ),
                ],
            }
        )
        rec = reconcile_memory(plan, self._measured(), self._symbols())
        (sram,) = [r for r in rec.regions if r.region == "SRAM"]
        assert sram.planned_used == 4096 * 24
        assert sram.measured_used == 98_556
        assert sram.delta == 98_556 - 4096 * 24


class TestReviewRegressionPins:
    """#179 review round: each finding pinned so it cannot recur."""

    def test_hal_symbols_do_not_false_positive_the_matcher(self):
        """M-1: am_hal_gpio_pincfg_input ENDS WITH g_input — a bare
        suffix test matched a 4-byte MRAM constant and flipped verdicts."""
        from helia_profiler.memory_measurement import _match_symbols
        from helia_profiler.toolchain_probe import SymbolEntry

        symbols = (
            SymbolEntry("am_hal_gpio_pincfg_input", 0x00420000, 4, "R"),
            SymbolEntry("_ZL7g_input", 0x20005000, 1960, "b"),
            SymbolEntry("g_input", 0x20006000, 1960, "b"),
        )
        matched = _match_symbols(("g_input",), symbols)
        assert [m.name for m in matched] == ["_ZL7g_input", "g_input"]

    def test_zero_size_symbols_never_match(self):
        """M-5: llvm-nm reports st_size verbatim — armlink's linker
        markers are 0 and a zero-size 'match' manufactures
        measured_size=0, delta=-planned."""
        from helia_profiler.memory_measurement import _match_symbols
        from helia_profiler.toolchain_probe import SymbolEntry

        symbols = (SymbolEntry("g_pui32Stack", 0x20000000, 0, "b"),)
        assert _match_symbols(("g_pui32Stack",), symbols) == ()

    def test_psram_consumers_are_unmatchable_even_when_the_pointer_matches(
        self,
    ):
        """M-2: the PSRAM-weights render declares a 4-byte POINTER that
        mangles to _ZL10model_data — matching it would report the planned
        megabytes as shortfall."""
        from helia_profiler.memory_measurement import reconcile_memory
        from helia_profiler.results import MemoryConsumer
        from helia_profiler.toolchain_probe import SymbolEntry

        plan = TestReconciliation()._plan(
            {
                MemoryRegion.PSRAM: [
                    MemoryConsumer(
                        name="model_flatbuffer", size=53744, kind=ConsumerKind.WEIGHTS
                    ),
                ],
            }
        )
        symbols = (SymbolEntry("_ZL10model_data", 0x20004000, 4, "b"),)
        rec = reconcile_memory(
            plan, TestReconciliation()._measured(), symbols
        )
        (weights,) = rec.consumers
        assert weights.status == "unmatchable"
        assert weights.measured_size is None

    def test_measured_region_flags_a_wrong_region_match(self):
        """M-6: a matched symbol whose address is in a DIFFERENT region
        than the plan intended must say so — the check that catches
        wrong-region 'clean' matches."""
        from helia_profiler.memory_measurement import reconcile_memory
        from helia_profiler.results import MemoryConsumer
        from helia_profiler.toolchain_probe import SymbolEntry

        plan = TestReconciliation()._plan(
            {
                MemoryRegion.DTCM: [
                    MemoryConsumer(
                        name="tensor_arena", size=0x8000, kind=ConsumerKind.ARENA
                    ),
                ],
            }
        )
        # arena symbol at an SRAM address:
        symbols = (SymbolEntry("_ZL15g_arena_storage", 0x20090000, 0x8000, "b"),)
        rec = reconcile_memory(
            plan, TestReconciliation()._measured(), symbols
        )
        (arena,) = rec.consumers
        assert arena.status == "matched" and arena.delta == 0
        assert arena.region == "DTCM"
        assert arena.measured_region == "SRAM"

    def test_unsized_and_undefined_nm_rows_skip_silently(self, monkeypatch):
        """M-5: llvm-nm emits U rows and size-0-omitted shapes under
        --size-sort; they are legitimate output, not parse failures — one
        of them must not mark the listing partial and drop attribution."""
        import helia_profiler.elf_inventory as ei
        from helia_profiler.toolchain_probe import symbol_inventory

        class _Result:
            returncode = 0
            stderr = ""
            stdout = (
                "         U memcpy\n"
                "00000128 T Region$$Table$$Base\n"
                "20000000 00004000 b g_pui32Stack\n"
                "utter garbage row\n"
            )

        monkeypatch.setattr(ei.subprocess, "run", lambda *a, **k: _Result())
        result = symbol_inventory(Path("fw.elf"), "arm-none-eabi-gcc")
        assert result is not None
        symbols, unparsed = result
        assert [s.name for s in symbols] == ["g_pui32Stack"]
        assert unparsed == 1  # only the garbage row

    def test_nm_command_duplicate_stays_in_sync(self):
        """m7: elf_inventory duplicates toolchain_probe._nm_command to
        avoid an import cycle — pin that they agree for every toolchain."""
        import helia_profiler.elf_inventory as ei
        import helia_profiler.toolchain_probe as tp

        for toolchain in ("arm-none-eabi-gcc", "gcc", "armclang", "atfe"):
            assert ei._nm_command(toolchain) == tp._nm_command(toolchain)


def test_llvm_nm_capture_parses_with_the_same_regexes():
    """#179 Sonnet 'untested claim': real llvm-nm output
    (symbols_atfe.txt, same fixture ELF) through the same parser. Real
    objects carry identical sizes to the GNU capture; the linker markers
    (__HeapBase/__HeapLimit) report st_size 0 — the documented
    asymmetry, and exactly why zero-size symbols never match."""
    from helia_profiler.elf_inventory import _NM_SIZED_ROW_RE

    text = (FIXTURES / "symbols_atfe.txt").read_text()
    rows = {
        m.group(4): int(m.group(2), 16)
        for m in map(_NM_SIZED_ROW_RE.match, text.splitlines())
        if m
    }
    assert rows["g_stack"] == 0x4000
    assert rows["g_initialized"] == 0x20
    assert rows["g_zero_init"] == 0xF8
    assert rows["__HeapBase"] == 0  # llvm reports st_size verbatim
    assert rows["__HeapLimit"] == 0  # GNU omits this row entirely
