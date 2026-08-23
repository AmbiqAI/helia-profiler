"""Tests for toolchain_probe's section inventory (#133 Phase 1).

Fixtures are UNEDITED real captures: tests/fixtures/readelf/* from
Arm GNU Toolchain 15.2.Rel1 (GNU readelf 2.45.1) on an ELF built by the
committed linker.ld +
main.c, and tests/fixtures/fromelf/fw_text_v.txt from Arm Compiler 6.23
(the #132 capture). Regeneration commands live in the fixture comments.
"""

from __future__ import annotations

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
    def _sections(self, monkeypatch, text=None):
        import helia_profiler.toolchain_probe as tp

        class _Result:
            returncode = 0
            stdout = text if text is not None else _readelf_sections_text()
            stderr = ""

        monkeypatch.setattr(tp.subprocess, "run", lambda *a, **k: _Result())
        return _inventory_via_readelf(
            Path("fw.elf"), readelf_cmd="readelf", timeout_s=5
        )

    def test_real_capture_yields_the_full_shape(self, monkeypatch):
        sections = self._sections(monkeypatch)
        by_name = {s.name: s for s in sections}
        # The NSX shape, from the real capture:
        assert by_name[".stack"] == ElfSection(
            name=".stack", address=0x20000000, size=0x4000, nobits=True, allocated=True
        )
        assert by_name[".heap"].nobits and by_name[".heap"].size == 0x77EE8
        assert by_name[".text"].address == 0x00410000
        assert by_name[".data"].address == 0x20004000
        # Unallocated metadata sections are captured but flagged:
        assert not by_name[".comment"].allocated
        assert not by_name[".symtab"].allocated

    def test_null_row_is_skipped_not_misparsed(self, monkeypatch):
        """The NULL row has a BLANK name column — a general (\\S+) name
        group would swallow "NULL" as the name and misalign every column.
        The type-constrained regex skips it (#133 Phase 1 design note)."""
        sections = self._sections(monkeypatch)
        assert all(s.name != "NULL" for s in sections)
        assert len(sections) == 10  # 11 headers minus the NULL row

    def test_armlink_style_names_are_captured(self, monkeypatch):
        """The reserved-path regex anchors on a leading dot; the inventory
        must also see armlink-style names (readelf reads armclang ELFs —
        the #173 parity proof relied on it)."""
        row = (
            "  [ 4] ARM_LIB_HEAP      NOBITS          200000fc 000174 05faf8 00  WA  0   0  1\n"
        )
        sections = self._sections(monkeypatch, text=row)
        assert sections == (
            ElfSection(
                name="ARM_LIB_HEAP",
                address=0x200000FC,
                size=0x5FAF8,
                nobits=True,
                allocated=True,
            ),
        )

    def test_tool_failure_degrades_to_none(self, monkeypatch):
        import helia_profiler.toolchain_probe as tp

        def _boom(*a, **k):
            raise FileNotFoundError("readelf")

        monkeypatch.setattr(tp.subprocess, "run", _boom)
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
        ARM_LIB_HEAP as allocated NOBITS, and the PT_LOAD block."""
        listing = (FIXTURES / "fromelf" / "fw_text_v.txt").read_text()
        parsed = _inventory_from_fromelf_listing(listing)
        assert parsed is not None
        sections, segments = parsed
        by_name = {s.name: s for s in sections}
        assert by_name["ER_RW"].address == 0x20000000
        assert by_name["ER_RW"].size == 4
        heap = by_name["ARM_LIB_HEAP"]
        assert heap.nobits and heap.allocated and heap.size == 391928
        assert segments == (
            LoadSegment(
                virtual_address=0x0,
                physical_address=0x0,
                file_size=324,
                memory_size=396596,
            ),
        )

    def test_unparseable_listing_degrades_to_none(self):
        assert _inventory_from_fromelf_listing("no sections here") is None


def test_unknown_readelf_toolchain_degrades(monkeypatch):
    # armclang spec routes to fromelf; a spec with no readelf degrades.
    import helia_profiler.toolchain_probe as tp

    class _Spec:
        section_probe = "size"
        readelf = None

    monkeypatch.setattr(tp, "get_toolchain_spec", lambda name: _Spec())
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
