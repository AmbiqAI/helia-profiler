"""Tests for toolchain_probe's section inventory (#133 Phase 1).

Fixtures are UNEDITED real captures: tests/fixtures/readelf/* from
Arm GNU Toolchain 15.2.Rel1 (GNU readelf 2.45.1) on an ELF built by the
committed linker.ld + main.c, and tests/fixtures/fromelf/fw_text_v.txt
from Arm Compiler 6.23 (the #132 capture). Regeneration commands live in
the fixture comments.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from helia_profiler.toolchain_probe import (
    ElfSection,
    LoadSegment,
    _inventory_from_fromelf_listing,
    _inventory_via_readelf,
    _segments_via_readelf,
    section_inventory,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _readelf_sections_text() -> str:
    return (FIXTURES / "readelf" / "sections.txt").read_text()


def _readelf_segments_text() -> str:
    return (FIXTURES / "readelf" / "segments.txt").read_text()


class TestReadelfInventory:
    def _inventory(self, monkeypatch, text=None):
        import helia_profiler.toolchain_probe as tp

        class _Result:
            returncode = 0
            stdout = text if text is not None else _readelf_sections_text()
            stderr = ""

        monkeypatch.setattr(tp.subprocess, "run", lambda *a, **k: _Result())
        return _inventory_via_readelf(
            Path("fw.elf"), readelf_cmd="readelf", timeout_s=5
        )

    def _sections(self, monkeypatch, text=None):
        inventory = self._inventory(monkeypatch, text=text)
        assert inventory is not None
        sections, unparsed = inventory
        assert unparsed == 0
        return sections

    def test_real_capture_yields_the_full_shape(self, monkeypatch):
        sections = self._sections(monkeypatch)
        by_name = {s.name: s for s in sections}
        # The NSX shape, from the real capture (index = the [Nr] column):
        assert by_name[".stack"] == ElfSection(
            name=".stack",
            address=0x20000000,
            size=0x4000,
            nobits=True,
            allocated=True,
            index=2,
            linker_reserved=False,  # the stack is LIVE memory, never reserved
        )
        heap = by_name[".heap"]
        assert heap.nobits and heap.size == 0x77EE8
        assert heap.linker_reserved  # fill-to-end heap IS the #24 reservation
        assert by_name[".text"].address == 0x00410000
        assert by_name[".data"].address == 0x20004000
        # Unallocated metadata sections are captured but flagged:
        assert not by_name[".comment"].allocated
        assert not by_name[".symtab"].allocated

    def test_null_row_is_skipped_not_misparsed(self, monkeypatch):
        """The NULL row has a BLANK name column — a general (\\S+) name
        group would swallow "NULL" as the name and misalign every column.
        The type-constrained regex skips it (#133 Phase 1 design note),
        and it does NOT count as an unparsed row."""
        inventory = self._inventory(monkeypatch)
        sections, unparsed = inventory
        assert all(s.name != "NULL" for s in sections)
        assert len(sections) == 10  # 11 headers minus the NULL row
        assert unparsed == 0

    def test_unnameable_sh_type_counts_as_unparsed(self, monkeypatch):
        """A section whose sh_type readelf renders numerically (LOOS+…)
        fails the type-constrained pattern. Silently dropping it would
        understate occupancy, so the inventory carries a structural count
        a Phase-2 consumer can refuse to publish on."""
        rows = (
            "  [ 1] .text             PROGBITS        00410000 001000 00003c 00  AX  0   0  4\n"
            "  [ 2] .weird            LOOS+0xd        20000000 003000 004000 00  WA  0   0  4\n"
        )
        inventory = self._inventory(monkeypatch, text=rows)
        sections, unparsed = inventory
        assert [s.name for s in sections] == [".text"]
        assert unparsed == 1

    def test_duplicate_section_names_are_all_preserved(self, monkeypatch):
        """Section names are NOT unique: armlink emits one section per
        execution region per content class all named after the region, and
        NSX's own gcc scripts declare .text twice. The inventory must keep
        every one, disambiguated by index — a name-keyed dict silently
        loses bytes (#176 fresh-review M-2)."""
        rows = (
            "  [ 1] MCU_TCM           PROGBITS        20000000 001000 004000 00  WA  0   0  8\n"
            "  [ 2] MCU_TCM           NOBITS          20004000 005000 002000 00  WA  0   0  8\n"
        )
        sections = self._sections(monkeypatch, text=rows)
        assert len(sections) == 2
        assert [s.name for s in sections] == ["MCU_TCM", "MCU_TCM"]
        assert {s.index for s in sections} == {1, 2}
        assert sum(s.size for s in sections) == 0x6000  # nothing collapsed

    def test_armlink_style_names_are_captured(self, monkeypatch):
        """The reserved-path regex anchors on a leading dot; the inventory
        must also see armlink-style names (readelf reads armclang ELFs —
        the #173 parity proof relied on it). ARM_LIB_HEAP is a linker
        reservation; ARM_LIB_STACK is live memory."""
        rows = (
            "  [ 4] ARM_LIB_HEAP      NOBITS          200000fc 000174 05faf8 00  WA  0   0  1\n"
            "  [ 5] ARM_LIB_STACK     NOBITS          2005fbf4 000174 001000 00  WA  0   0  1\n"
        )
        sections = self._sections(monkeypatch, text=rows)
        by_name = {s.name: s for s in sections}
        assert by_name["ARM_LIB_HEAP"] == ElfSection(
            name="ARM_LIB_HEAP",
            address=0x200000FC,
            size=0x5FAF8,
            nobits=True,
            allocated=True,
            index=4,
            linker_reserved=True,
        )
        assert not by_name["ARM_LIB_STACK"].linker_reserved

    def test_tool_failure_degrades_to_none(self, monkeypatch):
        import helia_profiler.toolchain_probe as tp

        def _boom(*a, **k):
            raise FileNotFoundError("readelf")

        monkeypatch.setattr(tp.subprocess, "run", _boom)
        assert (
            _inventory_via_readelf(Path("fw.elf"), readelf_cmd="readelf", timeout_s=5)
            is None
        )

    def test_timeout_degrades_to_none(self, monkeypatch):
        import helia_profiler.toolchain_probe as tp

        def _slow(*a, **k):
            raise subprocess.TimeoutExpired(cmd="readelf", timeout=5)

        monkeypatch.setattr(tp.subprocess, "run", _slow)
        assert (
            _inventory_via_readelf(Path("fw.elf"), readelf_cmd="readelf", timeout_s=5)
            is None
        )


class TestReadelfSegments:
    def test_real_capture_carries_the_load_image_fact(self, monkeypatch):
        """#133 D3: .data runs at 0x20004000 but LOADS at 0x0041003c —
        the paddr != vaddr segment is why MRAM accounting needs program
        headers, and the real capture proves the shape."""
        import helia_profiler.toolchain_probe as tp

        class _Result:
            returncode = 0
            stdout = _readelf_segments_text()
            stderr = ""

        monkeypatch.setattr(tp.subprocess, "run", lambda *a, **k: _Result())
        segments = _segments_via_readelf(
            Path("fw.elf"), readelf_cmd="readelf", timeout_s=5
        )
        assert (
            LoadSegment(
                virtual_address=0x20004000,
                physical_address=0x0041003C,
                file_size=0x20,
                memory_size=0x118,
            )
            in segments
        )
        assert len(segments) == 4

    def test_failure_degrades_to_empty_not_none(self, monkeypatch):
        """Segments refine the inventory; their absence must not discard
        the section list."""
        import helia_profiler.toolchain_probe as tp

        def _boom(*a, **k):
            raise FileNotFoundError("readelf")

        monkeypatch.setattr(tp.subprocess, "run", _boom)
        assert (
            _segments_via_readelf(Path("fw.elf"), readelf_cmd="readelf", timeout_s=5)
            == ()
        )


class TestFromelfInventory:
    def test_real_capture_sections_and_segments(self):
        """Against the unedited #132 AC6 capture: ER_RW at its address,
        ARM_LIB_HEAP as allocated+reserved NOBITS, non-alloc metadata
        correctly NOT allocated, and the PT_LOAD block."""
        listing = (FIXTURES / "fromelf" / "fw_text_v.txt").read_text()
        parsed = _inventory_from_fromelf_listing(listing)
        assert parsed is not None
        sections, segments, unparsed = parsed
        assert unparsed == 0
        by_name = {s.name: s for s in sections}
        assert by_name["ER_RW"].address == 0x20000000
        assert by_name["ER_RW"].size == 4
        assert by_name["ER_RW"].index == 2  # the "** Section #2" header
        heap = by_name["ARM_LIB_HEAP"]
        assert heap.nobits and heap.allocated and heap.size == 391928
        assert heap.linker_reserved
        assert not by_name["ARM_LIB_STACK"].linker_reserved
        # The allocated flag must be DERIVED, not defaulted (#176
        # fresh-review: a mutation to allocated=True survived before):
        assert not by_name[".debug_frame"].allocated
        assert not by_name[".symtab"].allocated
        assert segments == (
            LoadSegment(
                virtual_address=0x0,
                physical_address=0x0,
                file_size=324,
                memory_size=396596,
            ),
        )

    def test_duplicated_field_first_occurrence_wins(self):
        """The .comment section body echoes the armlink command line, which
        can contain field-shaped text — the first occurrence of each field
        within a block must win (#176 fresh-review: the last-wins mutant
        survived before this test)."""
        listing = (
            "** Section #1\n"
            "\n"
            "    Name        : ER_RW\n"
            "    Type        : SHT_PROGBITS (0x00000001)\n"
            "    Flags       : SHF_ALLOC + SHF_WRITE (0x00000003)\n"
            "    Addr        : 0x20000000\n"
            "    Size        : 4 bytes (0x4)\n"
            "    Size        : 9999 bytes (0x270f)\n"
        )
        parsed = _inventory_from_fromelf_listing(listing)
        assert parsed is not None
        sections, _, unparsed = parsed
        assert sections[0].size == 4  # not 9999
        assert unparsed == 0

    def test_malformed_section_block_counts_as_unparsed(self):
        """A section block missing its Size line is a section MISSING from
        the inventory — the count must say so."""
        listing = (
            "** Section #1\n"
            "    Name        : ER_RO\n"
            "    Type        : SHT_PROGBITS (0x00000001)\n"
            "    Flags       : SHF_ALLOC (0x00000002)\n"
            "    Addr        : 0x00000000\n"
            "    Size        : 320 bytes (0x140)\n"
            "** Section #2\n"
            "    Name        : ER_RW\n"
            "    Type        : SHT_PROGBITS (0x00000001)\n"
            "    Addr        : 0x20000000\n"
        )
        parsed = _inventory_from_fromelf_listing(listing)
        assert parsed is not None
        sections, _, unparsed = parsed
        assert [s.name for s in sections] == ["ER_RO"]
        assert unparsed == 1

    def test_unparseable_listing_degrades_to_none(self):
        assert _inventory_from_fromelf_listing("no sections here") is None


class TestSectionInventoryDispatch:
    """End-to-end through section_inventory() itself — both branches were
    previously only tested below the dispatch (#176 fresh-review)."""

    def test_gcc_dispatch_runs_readelf_twice_and_threads_results(
        self, monkeypatch
    ):
        import helia_profiler.toolchain_probe as tp

        calls = []

        class _Result:
            returncode = 0
            stderr = ""

            def __init__(self, stdout):
                self.stdout = stdout

        def _run(argv, **kwargs):
            calls.append(argv)
            if "-S" in argv:
                return _Result(_readelf_sections_text())
            return _Result(_readelf_segments_text())

        monkeypatch.setattr(tp.subprocess, "run", _run)
        inventory = section_inventory(Path("fw.elf"), "arm-none-eabi-gcc")
        assert inventory is not None
        assert len(inventory.sections) == 10
        assert len(inventory.segments) == 4
        assert inventory.unparsed_rows == 0
        assert len(calls) == 2
        assert all("readelf" in argv[0] for argv in calls)

    def test_armclang_dispatch_parses_the_fromelf_listing(self, monkeypatch):
        import helia_profiler.toolchain_probe as tp

        class _Result:
            returncode = 0
            stderr = ""
            stdout = (FIXTURES / "fromelf" / "fw_text_v.txt").read_text()

        monkeypatch.setattr(tp.subprocess, "run", lambda *a, **k: _Result())
        inventory = section_inventory(Path("fw.axf"), "armclang")
        assert inventory is not None
        names = [s.name for s in inventory.sections if s.allocated]
        assert names == [
            "ER_RO",
            "ER_RW",
            "ER_ZI",
            "ARM_LIB_HEAP",
            "ARM_LIB_STACK",
        ]
        assert len(inventory.segments) == 1
        assert inventory.unparsed_rows == 0


def test_unknown_readelf_toolchain_degrades(monkeypatch):
    # armclang spec routes to fromelf; a spec with no readelf degrades.
    # (section_inventory lives in elf_inventory since the module split —
    # patch the name where the implementation resolves it.)
    import helia_profiler.elf_inventory as ei

    class _Spec:
        section_probe = "size"
        readelf = None

    monkeypatch.setattr(ei, "get_toolchain_spec", lambda name: _Spec())
    assert section_inventory(Path("fw.elf"), "whatever") is None


def test_llvm_readelf_atfe_captures_parse_identically_to_gnu():
    """D5's ATfE leg: llvm-readelf (what ATfE's spec resolves ``readelf``
    to) on the same fixture ELF must yield the same inventory and segments
    as GNU readelf — the captures differ only in header wording the parser
    never reads."""
    from helia_profiler.toolchain_probe import (
        _READELF_INVENTORY_RE,
        _READELF_LOAD_RE,
    )

    fixtures = Path(__file__).parent / "fixtures" / "readelf"

    def rows(name: str, pattern) -> list:
        text = (fixtures / name).read_text()
        return [m.groups() for m in map(pattern.match, text.splitlines()) if m]

    gnu = rows("sections.txt", _READELF_INVENTORY_RE)
    atfe = rows("sections_atfe.txt", _READELF_INVENTORY_RE)
    assert atfe == gnu
    assert len(atfe) == 10  # every non-NULL section

    assert rows("segments_atfe.txt", _READELF_LOAD_RE) == rows(
        "segments.txt", _READELF_LOAD_RE
    )
    assert len(rows("segments_atfe.txt", _READELF_LOAD_RE)) == 4
