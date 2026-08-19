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
    size_bin = str(tmp_path / "atfe" / "bin" / "llvm-size")
    assert calls == [
        [size_bin, str(tmp_path / "firmware")],
        [size_bin, "-A", str(tmp_path / "firmware")],
    ]


def _size_stub(monkeypatch, berkeley: str, sysv: str):
    """Stub `size`, returning Berkeley output plainly and SysV for ``-A``."""
    import subprocess as _sp

    def fake_run(command, **_kwargs):
        out = sysv if "-A" in command else berkeley
        return _sp.CompletedProcess(command, 0, stdout=out, stderr="")

    monkeypatch.setattr("helia_profiler.toolchain_probe.subprocess.run", fake_run)


_BERKELEY = "text data bss dec hex filename\n32 4 392188 392224 5fc20 firmware\n"
_SYSV = """firmware  :
section             size        addr
.text                 32       98304
.data                  4   268435456
.bss                 260   268435460
.heap             391928   268435720
.ARM.attributes       54           0
Total             392278
"""


def test_linker_reserved_heap_is_not_counted_as_bss(tmp_path: Path, monkeypatch) -> None:
    """#24: `size`'s Berkeley output lumps the .heap fill into bss.

    NSX's AP5 linker scripts reserve all remaining DTCM as `.heap` (NOLOAD) so
    _sbrk has a bounded region. It is never written, but Berkeley output has
    only four totals and no way to separate it -- so the reported footprint
    counted it as live zero-initialized state. These numbers are from a real
    ELF built to reproduce the shape: 260 bytes of genuine .bss reported as
    392,188.
    """
    _size_stub(monkeypatch, _BERKELEY, _SYSV)

    sections = binary_sections(tmp_path / "firmware", Toolchain.ARM_NONE_EABI_GCC, timeout_s=5)

    assert sections is not None
    assert sections.bss == 260, "the .heap reservation is still inside bss"
    assert sections.reserved == 391_928
    # total keeps the tool's own sum, so nothing silently vanishes
    assert sections.total == 392_224
    assert sections.bss + sections.reserved == 392_188


def test_unparseable_section_output_leaves_the_totals_alone(tmp_path: Path, monkeypatch) -> None:
    """Never invent an adjustment: if `-A` cannot be read, report what `size` said."""
    _size_stub(monkeypatch, _BERKELEY, "some unexpected tool output\n")

    sections = binary_sections(tmp_path / "firmware", Toolchain.ARM_NONE_EABI_GCC, timeout_s=5)

    assert sections is not None
    assert sections.bss == 392_188
    assert sections.reserved == 0


def test_a_binary_with_no_reservation_is_unchanged(tmp_path: Path, monkeypatch) -> None:
    """Boards whose linker script does not fill the region must not shift."""
    sysv = (
        "firmware  :\nsection   size   addr\n.text  32  98304\n"
        ".data  4  268435456\n.bss  260  268435460\nTotal  296\n"
    )
    _size_stub(monkeypatch, "text data bss dec hex filename\n32 4 260 296 128 firmware\n", sysv)

    sections = binary_sections(tmp_path / "firmware", Toolchain.ARM_NONE_EABI_GCC, timeout_s=5)

    assert sections is not None
    assert sections.bss == 260
    assert sections.reserved == 0
