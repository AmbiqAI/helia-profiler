"""Toolchain probes — ``--version`` and binary-section size queries.

Centralises every shell-out for read-only toolchain info so that:

* ``build_firmware`` does not need ``subprocess`` at all;
* timeout handling, error capture, and output parsing live in one place;
* tests can monkeypatch a single module to simulate missing toolchains.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .results import BinarySections
from .toolchains import get_toolchain_spec, resolve_toolchain_executable

log = logging.getLogger("hpx")


# ---------------------------------------------------------------------------
# Compiler / cmake --version probes
# ---------------------------------------------------------------------------


def _compiler_command(toolchain: str) -> str:
    """Return the executable name to query for ``--version`` info.

    Maps profile toolchain names ("armclang", "atfe", "arm-none-eabi-gcc")
    onto the actual binary that will respond to ``--version``.
    """
    spec = get_toolchain_spec(toolchain)
    return resolve_toolchain_executable(toolchain, spec.compiler)


def _run_version(cmd: str, *, timeout_s: int) -> str:
    """Return the first line of ``<cmd> --version`` stdout, or ``""``."""
    try:
        result = subprocess.run(
            [cmd, "--version"], capture_output=True, text=True, timeout=timeout_s
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        log.debug("%s --version probe failed: %s", cmd, exc)
        return ""
    if result.returncode != 0:
        log.debug("%s --version returned rc=%d", cmd, result.returncode)
        return ""
    out = (result.stdout or "").strip().splitlines()
    return out[0] if out else ""


def compiler_version(toolchain: str, *, timeout_s: int) -> str:
    """Return the first line of the compiler's ``--version`` banner."""
    return _run_version(_compiler_command(toolchain), timeout_s=timeout_s)


def cmake_version(*, timeout_s: int) -> str:
    """Return the first line of ``cmake --version``."""
    return _run_version("cmake", timeout_s=timeout_s)


# ---------------------------------------------------------------------------
# Binary section size probes
# ---------------------------------------------------------------------------


def _sections_via_size(
    binary_path: Path,
    *,
    size_cmd: str,
    readelf_cmd: str | None,
    timeout_s: int,
) -> BinarySections | None:
    """Parse Berkeley-format ``size`` output for GCC ELF binaries.

    Output shape::

           text    data     bss     dec     hex filename
         123420   27032   92412  242864   3b4b0 hpx_profiler
    """
    try:
        result = subprocess.run(
            [size_cmd, str(binary_path)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        log.debug("%s probe failed: %s", size_cmd, exc)
        return None
    if result.returncode != 0:
        log.debug("%s failed: %s", size_cmd, (result.stderr or "").strip())
        return None
    lines = (result.stdout or "").strip().splitlines()
    if len(lines) < 2:
        return None
    parts = lines[1].split()
    if len(parts) < 4:
        return None
    try:
        text, data, bss, total = (int(parts[i]) for i in range(4))
    except ValueError:
        return None
    log.info("Binary sections: text=%d data=%d bss=%d total=%d", text, data, bss, total)
    reserved = (
        _reserved_via_readelf(
            binary_path, readelf_cmd=readelf_cmd, timeout_s=timeout_s
        )
        if readelf_cmd is not None
        else None
    )
    if reserved is None or reserved > bss:
        # Either the section list could not be read, or the reserved regions
        # are not all inside what Berkeley called bss -- `size -A` reports no
        # section TYPE, so a `.heap` carrying contents (PROGBITS) would land
        # in Berkeley's data instead, and subtracting it from bss would be
        # wrong in a way a clamp would hide. Report exactly what the tool
        # said rather than guess; the numbers stay explainable either way.
        if reserved is not None and reserved > bss:
            log.debug(
                "reserved sections (%d B) exceed bss (%d B); not adjusting",
                reserved,
                bss,
            )
        return BinarySections(text=text, data=data, bss=bss, total=total)
    return BinarySections(
        text=text,
        data=data,
        bss=bss - reserved,
        total=total,
        reserved=reserved,
    )


#: Legacy ``Grand Totals: <code> <ro> <rw> <zi>`` line, label first. No
#: shipping fromelf has been observed to emit this shape -- Arm Compiler
#: 6.23's table puts the numbers BEFORE the row label -- but it is kept as
#: the last-resort fallback so an unrecognised variant degrades to the old
#: behaviour instead of reporting nothing (#132).
#: Totals-row labels in the component-sizes table ("ROM Totals for x.axf",
#: "Object Totals", "Library Totals", "Grand Totals", and armlink's own
#: "ELF Image Totals" for defense). FULL-match, not prefix: fromelf echoes
#: the input path in the Object Name column, so a relative path whose
#: LEADING component is a totals label ("ROM Totals/fw.axf") must still
#: read as an image row (#175 round-2 review m-1 — the prefix version
#: traded the substring hazard for this narrower one).
_FROMELF_TOTALS_LABEL_RE = re.compile(
    r"(?:ROM|Object|Library|Grand|ELF Image)\s+Totals(?:\s+for\s+.*)?"
)
_FROMELF_TOTALS_RE = re.compile(r"\s*Grand Totals?\s*[:\s]+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)")

#: Column header of ``fromelf --text -z``'s "Object/Image Component Sizes"
#: table. Rows are only trusted after this header has been seen.
_FROMELF_SIZES_HEADER_RE = re.compile(
    r"^\s*Code \(inc\. data\)\s+RO Data\s+RW Data\s+ZI Data\s+Debug\s+Object Name"
)

#: A data row of that table: six integers then the object name. Captured
#: from a real Arm Compiler for Embedded 6.23 ``fromelf``::
#:
#:       Code (inc. data)   RO Data    RW Data    ZI Data      Debug   Object Name
#:        288         16         32          4     396272        652   fw.axf
#:        288         16         32          4          0          0   ROM Totals for fw.axf
#:
#: ``(inc. data)`` is an informational subset of Code, not an addend.
_FROMELF_SIZES_ROW_RE = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\S.*?)\s*$"
)


#: Section-name tokens treated as linker reservations rather than program
#: state. Matched only among sections already known to be **NOBITS and
#: allocated** -- the name alone is not evidence.
#:
#: Only ``.heap``. Review corrected an earlier version that also claimed
#: ``.stack``: on every NSX SoC the ``.stack`` region IS the live stack --
#: ``startup_gcc.c`` loads the initial MSP from its top and sets MSPLIM/PSPLIM
#: from its base -- so it is memory the firmware genuinely needs, and it
#: belongs in the reported footprint.
#:
#: ``.heap`` is different in kind on these parts, and that difference is the
#: whole point of issue #24: NSX linker scripts do not size it to a
#: requirement, they run it to the end of the region
#: (``. = ORIGIN(MCU_TCM) + LENGTH(MCU_TCM);``) purely so ``_sbrk`` has a
#: bounded area. Its size states what was left over, not what is needed, so
#: counting it as footprint tells the reader nothing useful about the build.
_RESERVED_NOBITS_NAMES = frozenset({"heap"})


def _is_reserved_section_name(name: str) -> bool:
    """True when a section NAME marks a linker reservation.

    Matches any dot- or underscore-separated token, not just the first: a
    region-qualified name like ``.ram_heap`` or ``.tcm_heap`` stems to
    "ram"/"tcm" under first-token-only matching and was silently missed --
    proven by review on a real ELF (#24). Case-insensitive because armlink's
    execution-region sections are conventionally upper-case (``ARM_LIB_HEAP``
    in NSX's own scatter files) where GNU linker scripts use ``.heap``.

    Only meaningful for sections already known to be NOBITS and allocated --
    the name alone is not evidence. Note ``ARM_LIB_STACK`` does NOT match,
    for the same reason ``.stack`` does not: it is the live stack (armlink
    points the initial SP at its top), so it belongs in the footprint.

    Reachability note (#175 review m3): the case-insensitivity only matters
    on the fromelf path — armlink's conventional upper-case region names
    (ARM_LIB_HEAP) never reach the readelf caller, whose section regex
    anchors on a leading dot. Known accepted false positive: a user section
    whose name tokenizes to a heap token (e.g. MY_HEAP_STATS) counts as
    reserved; no shipped NSX linker script or scatter produces one.
    """
    tokens = set(name.lower().lstrip(".").replace("_", ".").split("."))
    return bool(tokens & _RESERVED_NOBITS_NAMES)

#: ``readelf -S -W`` row: ``[Nr] Name Type Addr Off Size ES Flg ...`` with the
#: numeric columns in hex. The name is anchored to a leading dot so the empty
#: name of section 0 cannot shift the field positions.
_READELF_SECTION_RE = re.compile(
    r"^\s*\[\s*\d+\]\s+(\.\S+)\s+(\S+)\s+"
    r"[0-9a-fA-F]+\s+[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+[0-9a-fA-F]+\s+(\S*)"
)


def _reserved_via_readelf(
    binary_path: Path,
    *,
    readelf_cmd: str,
    timeout_s: int,
) -> int | None:
    """Bytes of linker-reserved NOBITS regions, from the section headers.

    An earlier version of this used ``size -A``, which reports name, size and
    address but **no TYPE** -- so a ``.heap`` carrying contents (PROGBITS,
    which ``size`` correctly counts in *data*) was indistinguishable from the
    NOLOAD reservation, and subtracting it from bss understated real
    zero-initialized state while double-counting those bytes. Adversarial
    review caught that on a purpose-built ELF; issue #24 had named readelf
    for this reason from the start.

    So the type is checked: only ``NOBITS`` sections carrying the ``A``
    (alloc) flag are candidates, which is exactly the set ``size`` folds into
    its bss column. The name then selects the reserved ones among them.

    Returns ``None`` when the tool is unavailable or the output cannot be
    parsed, so the caller keeps the unadjusted numbers rather than inventing
    an adjustment.
    """
    try:
        result = subprocess.run(
            [readelf_cmd, "-S", "-W", str(binary_path)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        log.debug("%s section probe failed: %s", readelf_cmd, exc)
        return None
    if result.returncode != 0:
        log.debug("%s failed: %s", readelf_cmd, (result.stderr or "").strip())
        return None

    reserved = 0
    seen_section = False
    for line in (result.stdout or "").splitlines():
        match = _READELF_SECTION_RE.match(line)
        if match is None:
            continue
        name, sec_type, size_hex, flags = match.groups()
        seen_section = True
        if sec_type != "NOBITS" or "A" not in flags:
            continue
        # Over-matching on the name is cheap here because the type and alloc
        # filters above already excluded everything that is not in `size`'s
        # bss column.
        if _is_reserved_section_name(name):
            reserved += int(size_hex, 16)
    if not seen_section:
        return None
    return reserved


#: ``fromelf --text -v`` prints one block per section::
#:
#:     ** Section #4
#:
#:         Name        : ARM_LIB_HEAP
#:         Type        : SHT_NOBITS (0x00000008)
#:         Flags       : SHF_ALLOC + SHF_WRITE (0x00000003)
#:         ...
#:         Size        : 391928 bytes (0x5faf8)
_FROMELF_SECTION_START_RE = re.compile(r"^\*\* Section #\d+")
_FROMELF_SECTION_FIELD_RE = re.compile(r"^\s*(Name|Type|Flags|Size)\s*:\s*(.+?)\s*$")
_FROMELF_SIZE_BYTES_RE = re.compile(r"^(\d+) bytes")


def _reserved_via_fromelf(
    binary_path: Path,
    *,
    timeout_s: int,
) -> int | None:
    """Bytes of linker-reserved NOBITS regions, from ``fromelf --text -v``.

    The armclang counterpart of :func:`_reserved_via_readelf` (#132, closing
    the gap #131 left): the verbose listing reports each section's Name, Type
    and Flags, so armlink's ``ARM_LIB_HEAP`` reservation -- ``SHT_NOBITS`` +
    ``SHF_ALLOC``, exactly what ``fromelf -z`` folds into ``ZI Data`` -- can
    be separated from real zero-initialized state the same way readelf
    separates ``.heap``. ``ARM_LIB_STACK`` is deliberately NOT a reservation;
    see :func:`_is_reserved_section_name`.

    Returns ``None`` when the tool is unavailable or no section block parses,
    so the caller keeps the unadjusted totals rather than inventing an
    adjustment -- the same degradation contract as the readelf probe.
    """
    try:
        result = subprocess.run(
            ["fromelf", "--text", "-v", str(binary_path)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        log.debug("fromelf section probe failed: %s", exc)
        return None
    if result.returncode != 0:
        log.debug("fromelf -v failed: %s", (result.stderr or "").strip())
        return None
    return _reserved_from_section_listing(result.stdout or "")


def _reserved_from_section_listing(stdout: str) -> int | None:
    """Parse a ``fromelf --text -v`` section listing for reserved bytes.

    Split from :func:`_reserved_via_fromelf` so the classification rules are
    testable without spawning the tool (#175 review)."""
    reserved = 0
    seen_section = False
    block: dict[str, str] | None = None

    def _consume(fields: dict[str, str] | None) -> int:
        if fields is None:
            return 0
        name = fields.get("Name")
        if name is None or not _is_reserved_section_name(name):
            return 0
        if not fields.get("Type", "").startswith("SHT_NOBITS"):
            return 0
        if "SHF_ALLOC" not in fields.get("Flags", ""):
            return 0
        size_match = _FROMELF_SIZE_BYTES_RE.match(fields.get("Size", ""))
        return int(size_match.group(1)) if size_match else 0

    for line in stdout.splitlines():
        if _FROMELF_SECTION_START_RE.match(line):
            reserved += _consume(block)
            block = {}
            seen_section = True
            continue
        if block is None:
            continue
        match = _FROMELF_SECTION_FIELD_RE.match(line)
        if match is not None:
            # First occurrence wins: embedded text (the `.comment` section
            # echoes the armlink command line) must not overwrite the real
            # header fields.
            block.setdefault(match.group(1), match.group(2))
    reserved += _consume(block)
    if not seen_section:
        return None
    return reserved


def _fromelf_totals(stdout: str) -> tuple[int, int, int, int] | None:
    """``(code, ro_data, rw_data, zi_data)`` from ``fromelf --text -z``.

    Real Arm Compiler 6.23 output is the "Object/Image Component Sizes"
    table whose first data row is the image itself (a ``ROM Totals`` row
    follows it with ZI zeroed -- never that one). A ``Grand Totals`` row is
    preferred when present. The legacy label-first ``Grand Totals:`` line is
    kept as a final fallback for unrecognised variants.
    """
    in_table = False
    image_rows: list[tuple[int, int, int, int]] = []
    for line in stdout.splitlines():
        if _FROMELF_SIZES_HEADER_RE.match(line):
            in_table = True
            continue
        if not in_table:
            continue
        m = _FROMELF_SIZES_ROW_RE.match(line)
        if m is None:
            continue
        row = (int(m.group(1)), int(m.group(3)), int(m.group(4)), int(m.group(5)))
        name = m.group(7)
        # Totals rows are recognised by their LABEL PREFIX, never by
        # substring: fromelf prints the full input path in the Object Name
        # column, so a build directory containing "Totals" made the
        # substring test skip the image row too and the probe silently
        # returned no sections (#175 review m1, reproduced on the real
        # tool).
        if _FROMELF_TOTALS_LABEL_RE.fullmatch(name):
            continue
        image_rows.append(row)
    # A LINKED image emits exactly one image row (verified across single-
    # and multi-load-region, C++, and production .axf inputs on the real
    # tool). More than one data row means we were handed something else —
    # a library or object listing, or multiple images at once — where
    # "first row" would be silently wrong (or arbitrarily chosen) numbers;
    # degrade instead (#175 review m2/round-2 m-2).
    if len(image_rows) == 1:
        return image_rows[0]
    for line in stdout.splitlines():
        m = _FROMELF_TOTALS_RE.match(line)
        if m:
            return (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))
    return None


def _sections_via_fromelf(
    binary_path: Path,
    *,
    timeout_s: int,
) -> BinarySections | None:
    """Parse ``fromelf`` output for armclang binaries.

    ``fromelf --text -z`` reports ``Code RO_Data RW_Data ZI_Data`` which we
    collapse to ``(text=Code+RO, data=RW, bss=ZI)`` to match the GCC ``size``
    shape. ``ZI Data`` folds armlink's ``ARM_LIB_HEAP`` reservation into the
    same figure -- the armclang face of issue #24 -- so a second probe reads
    the per-section detail (``fromelf --text -v``) and moves linker
    reservations to ``reserved``, mirroring #131's size/readelf split. If
    the per-section output is unavailable or unparseable, the totals are
    reported unadjusted (#132's documented degradation) rather than failing.
    """
    try:
        result = subprocess.run(
            ["fromelf", "--text", "-z", str(binary_path)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        log.debug("fromelf probe failed: %s", exc)
        return None
    if result.returncode != 0:
        log.debug("fromelf failed: %s", (result.stderr or "").strip())
        return None
    totals = _fromelf_totals(result.stdout or "")
    if totals is None:
        log.debug("Could not find component sizes or Grand Totals in fromelf output")
        return None
    code, ro_data, rw_data, zi_data = totals
    text = code + ro_data
    data = rw_data
    bss = zi_data
    total = text + data + bss
    log.info(
        "Binary sections (fromelf): text=%d data=%d bss=%d total=%d",
        text,
        data,
        bss,
        total,
    )
    reserved = _reserved_via_fromelf(binary_path, timeout_s=timeout_s)
    if reserved is None or reserved > bss:
        # Same discipline as the size/readelf path: report exactly what the
        # tool said rather than guess when the section detail is missing or
        # does not add up.
        if reserved is not None and reserved > bss:
            log.debug(
                "reserved sections (%d B) exceed bss (%d B); not adjusting",
                reserved,
                bss,
            )
        return BinarySections(text=text, data=data, bss=bss, total=total)
    return BinarySections(
        text=text,
        data=data,
        bss=bss - reserved,
        total=total,
        reserved=reserved,
    )


def binary_sections(
    binary_path: Path,
    toolchain: str,
    *,
    timeout_s: int,
) -> BinarySections | None:
    """Return section sizes for *binary_path*, dispatching by toolchain.

    Uses ``fromelf`` for armclang binaries and the configured ``size`` tool
    for GCC and ATFE binaries. Returns ``None`` if the size tool is
    unavailable or its output cannot be parsed.
    """
    spec = get_toolchain_spec(toolchain)
    if spec.section_probe == "fromelf":
        return _sections_via_fromelf(binary_path, timeout_s=timeout_s)
    assert spec.size is not None
    return _sections_via_size(
        binary_path,
        size_cmd=resolve_toolchain_executable(toolchain, spec.size),
        readelf_cmd=(
            resolve_toolchain_executable(toolchain, spec.readelf)
            if spec.readelf is not None
            else None
        ),
        timeout_s=timeout_s,
    )


# ---------------------------------------------------------------------------
# Symbol address probe (for build-time placement verification)
# ---------------------------------------------------------------------------


def _nm_command(toolchain: str) -> str:
    """Return the ``nm`` executable matching *toolchain*.

    armclang / ATfE ship the LLVM binutils (``llvm-nm``); GCC uses the
    cross-prefixed ``<prefix>-nm`` (e.g. ``arm-none-eabi-nm``).
    """
    spec = get_toolchain_spec(toolchain)
    return resolve_toolchain_executable(toolchain, spec.nm)


def symbol_address(
    binary_path: Path,
    toolchain: str,
    symbol: str,
    *,
    timeout_s: int,
) -> tuple[int, str] | None:
    """Return ``(address, nm_type_letter)`` for *symbol* in *binary_path*.

    Reads the linked address via ``nm``.  The symbol is matched as a suffix so
    a C++-mangled local (``_ZL15g_arena_storage``) and a plain C symbol
    (``g_arena_storage``) both resolve.  Returns ``None`` on any failure
    (missing tool, symbol absent, parse error) so callers stay best-effort.
    """
    nm = _nm_command(toolchain)
    try:
        result = subprocess.run(
            [nm, str(binary_path)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        log.debug("%s probe failed: %s", nm, exc)
        return None
    if result.returncode != 0:
        log.debug("%s failed: %s", nm, (result.stderr or "").strip())
        return None
    pattern = re.compile(
        rf"^([0-9a-fA-F]+)\s+(\S)\s+\S*{re.escape(symbol)}\s*$",
        re.MULTILINE,
    )
    match = pattern.search(result.stdout or "")
    if match is None:
        log.debug("symbol %s not found via %s in %s", symbol, nm, binary_path)
        return None
    return int(match.group(1), 16), match.group(2)


__all__ = [
    "binary_sections",
    "cmake_version",
    "compiler_version",
    "symbol_address",
]


# ---------------------------------------------------------------------------
# Section inventory (#133 Phase 1)
# ---------------------------------------------------------------------------
#
# The measured half of the memory model starts here: the full per-section
# (name, address, size) inventory plus the PT_LOAD segments, captured from
# the SAME tools the reserved/bss split already runs — readelf kept only
# name/type/size/flags and deliberately discarded the Addr column; fromelf's
# -v blocks carry Addr and full program headers. Everything below is
# ADDITIVE: the BinarySections paths above are untouched, and every probe
# degrades to None per #131's never-guess discipline. Nothing here reaches
# an artifact yet (Phase 2 owns serialization and the region attribution).


@dataclass(frozen=True)
class ElfSection:
    """One section of the linked image, address included."""

    name: str
    address: int
    size: int
    nobits: bool
    allocated: bool


@dataclass(frozen=True)
class LoadSegment:
    """One PT_LOAD program header: where bytes load (physical) vs run
    (virtual) — the fact that makes initialized data's MRAM load image
    accountable (#133 D3)."""

    virtual_address: int
    physical_address: int
    file_size: int
    memory_size: int


@dataclass(frozen=True)
class SectionInventory:
    """The measured memory inventory of one linked binary."""

    sections: tuple[ElfSection, ...]
    segments: tuple[LoadSegment, ...] = ()


#: readelf -S -W row, GENERAL section names (the reserved-path regex above
#: anchors on a leading dot and cannot see armlink-style ARM_LIB_* names).
#: The type group is constrained to an uppercase identifier so the NULL
#: row — whose blank name column would otherwise let (\S+) swallow "NULL"
#: and misalign every following group — fails to match and is skipped.
_READELF_INVENTORY_RE = re.compile(
    r"^\s*\[\s*\d+\]\s+(\S+)\s+([A-Z][A-Z0-9_]*)\s+([0-9a-fA-F]+)\s+"
    r"[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+[0-9a-fA-F]+\s+(\S*)"
)

#: readelf -l -W LOAD row: offset, vaddr, paddr, filesz, memsz.
_READELF_LOAD_RE = re.compile(
    r"^\s*LOAD\s+0x[0-9a-fA-F]+\s+0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)\s+"
    r"0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)"
)

#: fromelf --text -v field lines consumed by the INVENTORY walk (a superset
#: of the reserved-path's four; that regex stays untouched above).
_FROMELF_INVENTORY_FIELD_RE = re.compile(
    r"^\s*(Name|Type|Flags|Size|Addr|Virtual Addr|Physical Addr"
    r"|Size in file|Size in memory)\s*:\s*(.+?)\s*$"
)
_FROMELF_PROGRAM_START_RE = re.compile(r"^\*\* Program header #\d+")
_FROMELF_HEX_RE = re.compile(r"0x([0-9a-fA-F]+)")


def _inventory_via_readelf(
    binary_path: Path,
    *,
    readelf_cmd: str,
    timeout_s: int,
) -> tuple[ElfSection, ...] | None:
    try:
        result = subprocess.run(
            [readelf_cmd, "-S", "-W", str(binary_path)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        log.debug("readelf inventory probe failed: %s", exc)
        return None
    if result.returncode != 0:
        log.debug("readelf -S failed: %s", (result.stderr or "").strip())
        return None
    sections: list[ElfSection] = []
    for line in result.stdout.splitlines():
        match = _READELF_INVENTORY_RE.match(line)
        if match is None:
            continue
        name, sh_type, addr_hex, size_hex, flags = match.groups()
        sections.append(
            ElfSection(
                name=name,
                address=int(addr_hex, 16),
                size=int(size_hex, 16),
                nobits=sh_type == "NOBITS",
                allocated="A" in flags,
            )
        )
    return tuple(sections) if sections else None


def _segments_via_readelf(
    binary_path: Path,
    *,
    readelf_cmd: str,
    timeout_s: int,
) -> tuple[LoadSegment, ...]:
    """PT_LOAD segments; empty on any failure — segments refine the
    inventory (load-image accounting) but their absence must not discard
    it."""
    try:
        result = subprocess.run(
            [readelf_cmd, "-l", "-W", str(binary_path)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        log.debug("readelf segment probe failed: %s", exc)
        return ()
    if result.returncode != 0:
        log.debug("readelf -l failed: %s", (result.stderr or "").strip())
        return ()
    segments: list[LoadSegment] = []
    for line in result.stdout.splitlines():
        match = _READELF_LOAD_RE.match(line)
        if match is None:
            continue
        vaddr, paddr, filesz, memsz = (int(g, 16) for g in match.groups())
        segments.append(
            LoadSegment(
                virtual_address=vaddr,
                physical_address=paddr,
                file_size=filesz,
                memory_size=memsz,
            )
        )
    return tuple(segments)


def _hex_field(value: str) -> int | None:
    match = _FROMELF_HEX_RE.search(value)
    return int(match.group(1), 16) if match else None


def _inventory_from_fromelf_listing(
    stdout: str,
) -> tuple[tuple[ElfSection, ...], tuple[LoadSegment, ...]] | None:
    """Sections + PT_LOAD segments from a ``fromelf --text -v`` listing."""
    sections: list[ElfSection] = []
    segments: list[LoadSegment] = []
    block: dict[str, str] | None = None
    block_kind: str | None = None

    def _consume() -> None:
        if block is None:
            return
        if block_kind == "section":
            name = block.get("Name")
            addr = _hex_field(block.get("Addr", ""))
            size_match = _FROMELF_SIZE_BYTES_RE.match(block.get("Size", ""))
            if name is None or addr is None or size_match is None:
                return
            sections.append(
                ElfSection(
                    name=name,
                    address=addr,
                    size=int(size_match.group(1)),
                    nobits=block.get("Type", "").startswith("SHT_NOBITS"),
                    allocated="SHF_ALLOC" in block.get("Flags", ""),
                )
            )
        elif block_kind == "segment" and block.get("Type", "").startswith("PT_LOAD"):
            vaddr = _hex_field(block.get("Virtual Addr", ""))
            paddr = _hex_field(block.get("Physical Addr", ""))
            filesz = _FROMELF_SIZE_BYTES_RE.match(block.get("Size in file", ""))
            memsz = _FROMELF_SIZE_BYTES_RE.match(block.get("Size in memory", ""))
            if None in (vaddr, paddr, filesz, memsz):
                return
            segments.append(
                LoadSegment(
                    virtual_address=vaddr,
                    physical_address=paddr,
                    file_size=int(filesz.group(1)),
                    memory_size=int(memsz.group(1)),
                )
            )

    for line in stdout.splitlines():
        if _FROMELF_SECTION_START_RE.match(line):
            _consume()
            block, block_kind = {}, "section"
            continue
        if _FROMELF_PROGRAM_START_RE.match(line):
            _consume()
            block, block_kind = {}, "segment"
            continue
        if block is None:
            continue
        match = _FROMELF_INVENTORY_FIELD_RE.match(line)
        if match is not None:
            # First occurrence wins, same rationale as the reserved path.
            block.setdefault(match.group(1), match.group(2))
    _consume()
    return (tuple(sections), tuple(segments)) if sections else None


def section_inventory(
    binary_path: Path,
    toolchain: str,
    *,
    timeout_s: int = 30,
) -> SectionInventory | None:
    """The measured section/segment inventory of *binary_path*, or None.

    Dispatches like :func:`binary_sections`: readelf for the size-probed
    toolchains, ``fromelf --text -v`` for armclang. Degrades to ``None`` on
    any tool or parse failure — the measured memory view simply does not
    exist for that run, exactly like an absent ``reserved`` split.
    """
    spec = get_toolchain_spec(toolchain)
    if spec.section_probe == "fromelf":
        try:
            result = subprocess.run(
                ["fromelf", "--text", "-v", str(binary_path)],
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            log.debug("fromelf inventory probe failed: %s", exc)
            return None
        if result.returncode != 0:
            log.debug("fromelf -v failed: %s", (result.stderr or "").strip())
            return None
        parsed = _inventory_from_fromelf_listing(result.stdout or "")
        if parsed is None:
            return None
        sections, segments = parsed
        return SectionInventory(sections=sections, segments=segments)
    if spec.readelf is None:
        return None
    readelf_cmd = resolve_toolchain_executable(toolchain, spec.readelf)
    sections = _inventory_via_readelf(
        binary_path, readelf_cmd=readelf_cmd, timeout_s=timeout_s
    )
    if sections is None:
        return None
    segments = _segments_via_readelf(
        binary_path, readelf_cmd=readelf_cmd, timeout_s=timeout_s
    )
    return SectionInventory(sections=sections, segments=segments)
