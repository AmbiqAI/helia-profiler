"""Tests for read-only toolchain probes."""

from __future__ import annotations

import subprocess
from pathlib import Path

from helia_profiler.config import Toolchain
from helia_profiler.results import BinarySections
from helia_profiler.toolchain_probe import binary_sections


def test_atfe_binary_sections_uses_llvm_size_from_atfe_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ATFE_ROOT", str(tmp_path / "atfe"))
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="text data bss dec hex filename\n10 20 30 60 3c firmware\n",
            stderr="",
        )

    monkeypatch.setattr("helia_profiler.toolchain_probe.subprocess.run", fake_run)

    sections = binary_sections(
        tmp_path / "firmware",
        Toolchain.ATFE,
        timeout_s=5,
    )

    assert sections == BinarySections(text=10, data=20, bss=30, total=60)
    # Both probes must resolve llvm-size from ATFE_ROOT: the Berkeley call for
    # the totals, and the `-A` call that separates linker-reserved NOBITS from
    # real bss (#24). The stub returns Berkeley output for both, so `-A` finds
    # no section lines and the reserved adjustment is correctly skipped.
    # Both probes must resolve from ATFE_ROOT: `size` for the Berkeley totals
    # and `readelf` for the section types that separate the linker's reserved
    # NOBITS regions from real bss (#24). The stub returns Berkeley output for
    # both, so no section headers parse and the adjustment is correctly
    # skipped -- which also covers the unreadable-headers fallback.
    assert calls == [
        [str(tmp_path / "atfe" / "bin" / "llvm-size"), str(tmp_path / "firmware")],
        [
            str(tmp_path / "atfe" / "bin" / "llvm-readelf"),
            "-S",
            "-W",
            str(tmp_path / "firmware"),
        ],
    ]


_BERKELEY = "text data bss dec hex filename\n32 4 392188 392224 5fc20 firmware\n"

# readelf -S -W, sizes in HEX. The NOLOAD .heap is NOBITS+A, exactly what
# `size` folds into its bss column.
_READELF_NOLOAD_HEAP = """There are 9 section headers, starting at offset 0x21e4:

Section Headers:
  [Nr] Name              Type            Addr     Off    Size   ES Flg Lk Inf Al
  [ 0]                   NULL            00000000 000000 000000 00      0   0  0
  [ 1] .text             PROGBITS        00018000 001000 000020 00  AX  0   0  4
  [ 2] .data             PROGBITS        10000000 002000 000004 00  WA  0   0  4
  [ 3] .bss              NOBITS          10000004 002004 000104 00  WA  0   0  4
  [ 4] .heap             NOBITS          10000108 002108 05faf8 00  WA  0   0  1
  [ 5] .ARM.attributes   ARM_ATTRIBUTES  00000000 002004 000036 00      0   0  1
"""


def _probe_stub(monkeypatch, berkeley: str, readelf: str):
    """Stub both probes: `size` for the totals, `readelf -S` for section types."""
    import subprocess as _sp

    def fake_run(command, **_kwargs):
        out = readelf if "-S" in command else berkeley
        return _sp.CompletedProcess(command, 0, stdout=out, stderr="")

    monkeypatch.setattr("helia_profiler.toolchain_probe.subprocess.run", fake_run)


def test_linker_reserved_heap_is_not_counted_as_bss(tmp_path: Path, monkeypatch) -> None:
    """#24: `size`'s Berkeley output lumps the NOLOAD .heap fill into bss.

    NSX's AP5 linker scripts reserve all remaining DTCM as `.heap` (NOLOAD) so
    _sbrk has a bounded region. It is never written, but Berkeley output is
    four totals with no way to separate it. Numbers are from a real ELF built
    to reproduce the shape: 260 bytes of genuine .bss reported as 392,188.
    """
    _probe_stub(monkeypatch, _BERKELEY, _READELF_NOLOAD_HEAP)

    sections = binary_sections(tmp_path / "firmware", Toolchain.ARM_NONE_EABI_GCC, timeout_s=5)

    assert sections is not None
    assert sections.bss == 260, "the .heap reservation is still inside bss"
    assert sections.reserved == 391_928
    assert sections.total == 392_224, "total keeps the tool's own sum"
    assert sections.bss + sections.reserved == 392_188


def test_a_heap_with_contents_is_not_treated_as_reserved(tmp_path: Path, monkeypatch) -> None:
    """A PROGBITS `.heap` must NOT be subtracted from bss.

    Adversarial review caught this on a purpose-built ELF: an initialized pool
    named `.heap` is counted by `size` in DATA, not bss. An earlier version of
    this probe used `size -A`, which reports no section TYPE, so it matched on
    name alone and reported bss 65,536 -> 57,344 -- understating real
    zero-initialized state while double-counting those bytes in both `data`
    and `reserved`. That was WORSE than not fixing #24 at all, and the
    `reserved <= bss` guard did not catch it because the pool is smaller than
    bss. The type check is what makes the name check safe.
    """
    berkeley = "text data bss dec hex filename\n24 8192 65536 73752 12018 firmware\n"
    readelf = """Section Headers:
  [Nr] Name              Type            Addr     Off    Size   ES Flg Lk Inf Al
  [ 1] .text             PROGBITS        00018000 001000 000018 00  AX  0   0  4
  [ 2] .heap             PROGBITS        20000000 002000 002000 00  WA  0   0  4
  [ 3] .bss              NOBITS          20002000 004000 010000 00  WA  0   0  4
"""
    _probe_stub(monkeypatch, berkeley, readelf)

    sections = binary_sections(tmp_path / "firmware", Toolchain.ARM_NONE_EABI_GCC, timeout_s=5)

    assert sections is not None
    assert sections.bss == 65_536, "a PROGBITS .heap was wrongly taken out of bss"
    assert sections.reserved == 0


def test_non_allocated_nobits_is_not_treated_as_reserved(tmp_path: Path, monkeypatch) -> None:
    """Only ALLOC sections are in `size`'s bss column, so only they can leave it."""
    readelf = """Section Headers:
  [Nr] Name              Type            Addr     Off    Size   ES Flg Lk Inf Al
  [ 3] .bss              NOBITS          10000004 002004 000104 00  WA  0   0  4
  [ 4] .heap             NOBITS          00000000 002108 05faf8 00      0   0  1
"""
    _probe_stub(monkeypatch, _BERKELEY, readelf)

    sections = binary_sections(tmp_path / "firmware", Toolchain.ARM_NONE_EABI_GCC, timeout_s=5)

    assert sections is not None
    assert sections.bss == 392_188
    assert sections.reserved == 0


def test_unparseable_section_output_leaves_the_totals_alone(tmp_path: Path, monkeypatch) -> None:
    """Never invent an adjustment: if the headers cannot be read, report what `size` said."""
    _probe_stub(monkeypatch, _BERKELEY, "some unexpected tool output\n")

    sections = binary_sections(tmp_path / "firmware", Toolchain.ARM_NONE_EABI_GCC, timeout_s=5)

    assert sections is not None
    assert sections.bss == 392_188
    assert sections.reserved == 0


def test_a_binary_with_no_reservation_is_unchanged(tmp_path: Path, monkeypatch) -> None:
    """Boards whose linker script does not fill the region must not shift."""
    readelf = """Section Headers:
  [Nr] Name              Type            Addr     Off    Size   ES Flg Lk Inf Al
  [ 1] .text             PROGBITS        00018000 001000 000020 00  AX  0   0  4
  [ 3] .bss              NOBITS          10000004 002004 000104 00  WA  0   0  4
"""
    _probe_stub(monkeypatch, "text data bss dec hex filename\n32 4 260 296 128 firmware\n", readelf)

    sections = binary_sections(tmp_path / "firmware", Toolchain.ARM_NONE_EABI_GCC, timeout_s=5)

    assert sections is not None
    assert sections.bss == 260
    assert sections.reserved == 0


def test_reserved_exceeding_bss_is_not_subtracted(tmp_path: Path, monkeypatch) -> None:
    """Belt-and-braces backstop behind the type check: trust it only if it adds up."""
    berkeley = "text data bss dec hex filename\n32 4 100 136 88 firmware\n"
    readelf = """Section Headers:
  [Nr] Name              Type            Addr     Off    Size   ES Flg Lk Inf Al
  [ 3] .bss              NOBITS          10000004 002004 000064 00  WA  0   0  4
  [ 4] .heap             NOBITS          10000068 002068 001388 00  WA  0   0  1
"""
    _probe_stub(monkeypatch, berkeley, readelf)

    sections = binary_sections(tmp_path / "firmware", Toolchain.ARM_NONE_EABI_GCC, timeout_s=5)

    assert sections is not None
    assert sections.bss == 100, "bss was adjusted despite inconsistent section data"
    assert sections.reserved == 0


def test_the_live_stack_is_not_treated_as_a_reservation(tmp_path: Path, monkeypatch) -> None:
    """`.stack` is NOBITS and allocated, but it is NOT a reservation.

    An earlier version of this probe matched `.stack` alongside `.heap` and
    justified it as "never written at runtime". Review showed that is simply
    false: on every NSX SoC `startup_gcc.c` loads the initial MSP from the top
    of `.stack` and sets MSPLIM/PSPLIM from its base. It is live memory the
    firmware needs, so it belongs in the reported footprint.

    `.heap` is excluded for a different reason -- NSX scripts run it to the
    end of the region rather than sizing it to a requirement, so its size
    states leftover space, not need.
    """
    berkeley = "text data bss dec hex filename\n32 4 12548 12584 3128 firmware\n"
    readelf = """Section Headers:
  [Nr] Name              Type            Addr     Off    Size   ES Flg Lk Inf Al
  [ 3] .bss              NOBITS          10000004 002004 000104 00  WA  0   0  4
  [ 4] .stack            NOBITS          10000108 002108 003000 00  WA  0   0  8
"""
    _probe_stub(monkeypatch, berkeley, readelf)

    sections = binary_sections(tmp_path / "firmware", Toolchain.ARM_NONE_EABI_GCC, timeout_s=5)

    assert sections is not None
    assert sections.bss == 12_548, "the live stack was wrongly moved out of bss"
    assert sections.reserved == 0


def test_a_non_allocated_section_named_like_a_reservation_is_ignored(
    tmp_path: Path, monkeypatch
) -> None:
    """clang's -fstack-size-section emits a non-ALLOC `.stack_sizes` metadata
    section, and ATfE is clang. Name-only matching over `size -A` counted it
    and understated bss; requiring NOBITS **and** the alloc flag excludes it
    twice over."""
    readelf = """Section Headers:
  [Nr] Name              Type            Addr     Off    Size   ES Flg Lk Inf Al
  [ 3] .bss              NOBITS          10000004 002004 000104 00  WA  0   0  4
  [ 4] .heap_sizes       PROGBITS        00000000 002108 000064 00      0   0  1
"""
    _probe_stub(monkeypatch, _BERKELEY, readelf)

    sections = binary_sections(tmp_path / "firmware", Toolchain.ARM_NONE_EABI_GCC, timeout_s=5)

    assert sections is not None
    assert sections.bss == 392_188
    assert sections.reserved == 0


def test_a_region_qualified_heap_name_is_matched(tmp_path: Path, monkeypatch) -> None:
    """`.ram_heap` / `.tcm_heap` are real reservations under a qualified name.

    First-token-only stem matching kept "ram"/"tcm" and silently missed them;
    review proved it on a real ELF. Matching any dot- or underscore-separated
    token fixes it, and is safe because the NOBITS+alloc filter has already
    excluded everything outside `size`'s bss column.
    """
    berkeley = "text data bss dec hex filename\n32 4 8452 8488 2128 firmware\n"
    readelf = """Section Headers:
  [Nr] Name              Type            Addr     Off    Size   ES Flg Lk Inf Al
  [ 3] .bss              NOBITS          10000004 002004 000104 00  WA  0   0  4
  [ 4] .ram_heap         NOBITS          10000108 002108 000fa0 00  WA  0   0  8
  [ 5] .tcm_heap         NOBITS          100010a8 0030a8 001004 00  WA  0   0  8
"""
    _probe_stub(monkeypatch, berkeley, readelf)

    sections = binary_sections(tmp_path / "firmware", Toolchain.ARM_NONE_EABI_GCC, timeout_s=5)

    assert sections is not None
    assert sections.reserved == 0x0FA0 + 0x1004
    assert sections.bss == 8452 - (0x0FA0 + 0x1004)


# ---------------------------------------------------------------------------
# armclang / fromelf (#132: the gap #131 left open)
# ---------------------------------------------------------------------------
#
# Real output captured from Arm Compiler for Embedded 6.23 (fromelf
# [5f102800]) on an ELF built to reproduce the #24 shape for armlink:  (fromelf's own --vsn serial; the capture's
    # ELF headers show armlink [5f102400]/armclang [5f103000] — different tools,
    # different serials, same AC6 6.23 install)
# 248 bytes of genuine zero-init state, an ARM_LIB_HEAP execution region of
# 391,928 bytes (the reservation), and a 4,096-byte ARM_LIB_STACK (the live
# stack). ARM_LIB_HEAP / ARM_LIB_STACK are the exact region names NSX's own
# armclang scatter files use (e.g. apollo510/armclang/linker_script_nbl.sct).
# The source, scatter file, and both captures are checked in under
# tests/fixtures/fromelf/ so the ELF can be regenerated.

_FROMELF_FIXTURES = Path(__file__).parent / "fixtures" / "fromelf"
_FROMELF_Z = (_FROMELF_FIXTURES / "fw_text_z.txt").read_text()
_FROMELF_V = (_FROMELF_FIXTURES / "fw_text_v.txt").read_text()

# The captured image: Code 288 + RO 32 = text 320, RW data 4, and
# ZI 396,272 = 248 (ER_ZI) + 391,928 (ARM_LIB_HEAP) + 4,096 (ARM_LIB_STACK).
_REAL_TEXT = 320
_REAL_DATA = 4
_REAL_ZI = 396_272
_REAL_HEAP = 391_928
_REAL_STACK = 4_096
_REAL_TRUE_BSS = 248


def _fromelf_stub(monkeypatch, z_out: str, v_out: str, calls: list | None = None):
    """Stub both fromelf invocations: `-z` for totals, `-v` for section detail."""
    import subprocess as _sp

    def fake_run(command, **_kwargs):
        if calls is not None:
            calls.append(command)
        out = v_out if "-v" in command else z_out
        return _sp.CompletedProcess(command, 0, stdout=out, stderr="")

    monkeypatch.setattr("helia_profiler.toolchain_probe.subprocess.run", fake_run)


def test_armclang_linker_reservation_is_not_counted_as_bss(tmp_path: Path, monkeypatch) -> None:
    """#132: fromelf's ZI figure folds ARM_LIB_HEAP in, exactly as Berkeley
    `size` folded `.heap` into bss (#24). The per-section probe must pull the
    reservation out so armclang reports the same meaning of bss as gcc."""
    calls: list = []
    _fromelf_stub(monkeypatch, _FROMELF_Z, _FROMELF_V, calls)

    sections = binary_sections(tmp_path / "fw.axf", Toolchain.ARMCLANG, timeout_s=5)

    assert sections is not None
    assert sections.reserved == _REAL_HEAP
    # The live stack stays in bss -- same rule as the gcc path (#131): armlink
    # points the initial SP at ARM_LIB_STACK's top, it is memory the firmware
    # genuinely needs.
    assert sections.bss == _REAL_TRUE_BSS + _REAL_STACK
    assert sections.text == _REAL_TEXT
    assert sections.data == _REAL_DATA
    assert sections.total == _REAL_TEXT + _REAL_DATA + _REAL_ZI, "total keeps the tool's own sum"
    assert sections.bss + sections.reserved == _REAL_ZI
    assert calls == [
        ["fromelf", "--text", "-z", str(tmp_path / "fw.axf")],
        ["fromelf", "--text", "-v", str(tmp_path / "fw.axf")],
    ]


def test_armclang_degrades_to_unadjusted_totals_without_section_detail(
    tmp_path: Path, monkeypatch
) -> None:
    """Old fromelf / unexpected -v shape: keep the totals, never invent an
    adjustment. Same degradation contract as the unreadable-readelf case."""
    _fromelf_stub(monkeypatch, _FROMELF_Z, "some unexpected tool output\n")

    sections = binary_sections(tmp_path / "fw.axf", Toolchain.ARMCLANG, timeout_s=5)

    assert sections is not None
    assert sections.bss == _REAL_ZI, "degraded path reports exactly what -z said"
    assert sections.reserved == 0
    assert sections.text == _REAL_TEXT
    assert sections.data == _REAL_DATA


def test_armclang_legacy_grand_totals_line_still_parses(tmp_path: Path, monkeypatch) -> None:
    """The label-first `Grand Totals:` shape the old parser expected (no real
    fromelf we have seen emits it) stays as the last-resort fallback, and on
    its own reproduces the pre-#132 numbers -- the #132 reviewer's exact
    BinarySections(text=608, data=4, bss=392188, total=392800, reserved=0)."""
    legacy = "  Grand Totals: 600 8 4 392188\n"
    _fromelf_stub(monkeypatch, legacy, "some unexpected tool output\n")

    sections = binary_sections(tmp_path / "fw.axf", Toolchain.ARMCLANG, timeout_s=5)

    assert sections == BinarySections(text=608, data=4, bss=392_188, total=392_800)


def test_armclang_a_heap_with_contents_is_not_treated_as_reserved(
    tmp_path: Path, monkeypatch
) -> None:
    """SHT_PROGBITS means the region carries an image -- it is data, not a
    reservation, whatever its name says. Mirrors the gcc-path type check."""
    progbits_heap = """** Section #1

    Name        : ARM_LIB_HEAP
    Type        : SHT_PROGBITS (0x00000001)
    Flags       : SHF_ALLOC + SHF_WRITE (0x00000003)
    Size        : 8192 bytes (0x2000)
"""
    _fromelf_stub(monkeypatch, _FROMELF_Z, progbits_heap)

    sections = binary_sections(tmp_path / "fw.axf", Toolchain.ARMCLANG, timeout_s=5)

    assert sections is not None
    assert sections.bss == _REAL_ZI, "a PROGBITS heap was wrongly taken out of bss"
    assert sections.reserved == 0


def test_armclang_reserved_exceeding_bss_is_not_subtracted(tmp_path: Path, monkeypatch) -> None:
    """Belt-and-braces backstop behind the type check, same as the gcc path:
    trust the section detail only if it adds up against the totals."""
    small_zi = "  Grand Totals: 600 8 4 100\n"
    _fromelf_stub(monkeypatch, small_zi, _FROMELF_V)

    sections = binary_sections(tmp_path / "fw.axf", Toolchain.ARMCLANG, timeout_s=5)

    assert sections is not None
    assert sections.bss == 100, "bss was adjusted despite inconsistent section data"
    assert sections.reserved == 0


def test_size_and_fromelf_parsers_agree_on_the_same_binary_shape(
    tmp_path: Path, monkeypatch
) -> None:
    """#132 acceptance: the cross-toolchain divergence table collapses.

    Feed both parsers semantically equivalent descriptions of the SAME image
    -- the real armclang capture on one side, and the gcc tool output a
    binary with identical sections would produce on the other -- and require
    the identical (text, data, bss, reserved) split, so a cross-toolchain
    compare no longer shows a ~1500x bss artifact of the measuring tool.
    """
    berkeley = "text data bss dec hex filename\n320 4 396272 396596 60d34 firmware\n"
    readelf = """Section Headers:
  [Nr] Name              Type            Addr     Off    Size   ES Flg Lk Inf Al
  [ 1] .text             PROGBITS        00000000 000034 000140 00  AX  0   0  4
  [ 2] .data             PROGBITS        20000000 000174 000004 00  WA  0   0  4
  [ 3] .bss              NOBITS          20000004 000178 0000f8 00  WA  0   0  1
  [ 4] .heap             NOBITS          200000fc 000178 05faf8 00  WA  0   0  1
  [ 5] .stack            NOBITS          2005fbf4 000178 001000 00  WA  0   0  1
"""
    _probe_stub(monkeypatch, berkeley, readelf)
    via_size = binary_sections(tmp_path / "firmware", Toolchain.ARM_NONE_EABI_GCC, timeout_s=5)

    _fromelf_stub(monkeypatch, _FROMELF_Z, _FROMELF_V)
    via_fromelf = binary_sections(tmp_path / "fw.axf", Toolchain.ARMCLANG, timeout_s=5)

    assert via_size is not None and via_fromelf is not None
    assert (via_size.text, via_size.data, via_size.bss, via_size.reserved) == (
        via_fromelf.text,
        via_fromelf.data,
        via_fromelf.bss,
        via_fromelf.reserved,
    )
    assert via_fromelf.bss == _REAL_TRUE_BSS + _REAL_STACK
    assert via_fromelf.reserved == _REAL_HEAP


def test_combined_stackheap_region_stays_bss():
    """#175 review NIT: ARM_LIB_STACKHEAP (combined region) contains the
    live stack and cannot be split — per #131's never-invent rule it stays
    in bss with reserved=0. Verified against a real armlink build by the
    #175 review (bss=65784, reserved=0); pinned here with a synthetic -v
    listing in the real output shape."""
    listing = (
        "** Section #4 'ARM_LIB_STACKHEAP' (SHT_NOBITS) [SHF_ALLOC + SHF_WRITE]\n"
        "    Size   : 65536 bytes (alignment 8)\n"
    )
    from helia_profiler.toolchain_probe import _reserved_from_section_listing

    assert _reserved_from_section_listing(listing) == 0
