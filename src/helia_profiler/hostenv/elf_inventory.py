"""ELF inventory probes — sections, segments, and sized symbols (#133).

Extracted from ``toolchain_probe`` when it outgrew the module-size ceiling;
``toolchain_probe`` re-exports everything here, so importers keep their
single probe entry point. Same disciplines throughout: shell out via the
toolchain's own tools, degrade to None (never guess) per #131, and count
what could not be parsed instead of silently understating.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .toolchains import get_toolchain_spec, resolve_toolchain_executable

log = logging.getLogger("hpx")


def _nm_command(toolchain: str) -> str:
    """The ``nm`` executable matching *toolchain* (mirrors
    ``toolchain_probe._nm_command``; duplicated here because the import
    would be circular — toolchain_probe re-exports this module)."""
    spec = get_toolchain_spec(toolchain)
    return resolve_toolchain_executable(toolchain, spec.nm)


_FROMELF_SECTION_START_RE = re.compile(r"^\*\* Section #(\d+)")
_FROMELF_SIZE_BYTES_RE = re.compile(r"^(\d+) bytes")


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

    Reachability (updated for #133 Phase 1): originally only the fromelf
    path saw armlink-style names (the reserved-path readelf regex anchors
    on a leading dot), but the INVENTORY readelf path takes general names,
    so ARM_LIB_HEAP now reaches this predicate from both tools — the
    case-insensitivity is load-bearing on both. Known accepted false
    positive: a user section whose name tokenizes to a heap token (e.g.
    MY_HEAP_STATS, .heap_manager_state) counts as reserved when it is
    NOBITS+allocated; no shipped NSX linker script or scatter produces
    one, and ``ElfSection.linker_reserved`` inherits this predicate.
    """
    tokens = set(name.lower().lstrip(".").replace("_", ".").split("."))
    return bool(tokens & _RESERVED_NOBITS_NAMES)


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
    """One section of the linked image, address included.

    ``index`` is the ELF section-header index — the section's IDENTITY.
    Section NAMES are not unique: armlink emits one section per execution
    region per content class all named after the region (a real NSX AP510
    scatter link yields two ``MCU_TCM`` sections, PROGBITS + NOBITS), and
    NSX's own gcc scripts declare ``.text`` twice. Never key a collection
    of these on ``name`` alone — bytes vanish (#176 fresh-review M-2).

    ``linker_reserved`` marks the NOBITS+allocated regions the linker
    manufactures rather than the program needing them (today: fill-to-end
    heaps, armlink's ``ARM_LIB_HEAP`` — the #24/#131 rule, same predicate
    as the ``BinarySections.reserved`` split). ``.stack``/``ARM_LIB_STACK``
    are deliberately NOT reserved: the stack is live memory the firmware
    needs — but note it usually sits OUTSIDE the per-family app window
    (``LinkedRegionWindow.app_window``), which is how the free-space math
    stays consistent."""

    name: str
    address: int
    size: int
    nobits: bool
    allocated: bool
    index: int = -1
    linker_reserved: bool = False


@dataclass(frozen=True)
class LoadSegment:
    """One PT_LOAD program header: where bytes load (physical) vs run
    (virtual) — the fact that makes initialized data's MRAM load image
    accountable (#133 D3).

    Toolchain shape caveat (#176, measured on real links against the NSX
    AP510 scripts): GNU ld emits one PT_LOAD per load
    region (``.data`` gets vaddr in DTCM, paddr in MRAM — per-section
    recovery works). armlink emits a SINGLE PT_LOAD whose vaddr==paddr is
    the image base and whose ``memory_size`` SUMS discontiguous execution
    regions — ``paddr + memory_size`` is a phantom span, and mapping
    sections into segments by vaddr containment understates armlink MRAM
    ~400x. Region-level load-image accounting that works on BOTH families:
    sum ``file_size`` grouped by ``classify_address(physical_address)``,
    never walk sections into segments. Per-symbol load-image attribution
    on armlink is NOT recoverable from these primitives (Phase-3 scope
    note)."""

    virtual_address: int
    physical_address: int
    file_size: int
    memory_size: int


@dataclass(frozen=True)
class SectionInventory:
    """The measured memory inventory of one linked binary.

    ``unparsed_rows`` counts SECTION rows/blocks that LOOKED like
    inventory entries but failed to parse. When nonzero the section
    inventory is PARTIAL — occupancy computed from it is understated, and
    a Phase-2 consumer must treat the measured view as unavailable rather
    than publish a silently-low number (#131's discipline, structural
    instead of a debug log). Segment parse failures are NOT counted:
    segments refine the inventory and their absence degrades
    independently (empty tuple), on both tool paths."""

    sections: tuple[ElfSection, ...]
    segments: tuple[LoadSegment, ...] = ()
    unparsed_rows: int = 0


#: readelf -S -W row, GENERAL section names (the reserved-path regex above
#: anchors on a leading dot and cannot see armlink-style ARM_LIB_* names).
#: The type group is constrained to an uppercase identifier so the NULL
#: row — whose blank name column would otherwise let (\S+) swallow "NULL"
#: and misalign every following group — fails to match and is skipped.
#: NB the trailing flags group: on a blank ``Flg`` column ``(\S*)`` slides
#: to the decimal ``Lk`` value ("0", "27") — safe because ``Lk`` is always
#: numeric and can never contain "A", so ``allocated`` stays False, but do
#: NOT read writable/executable out of this group without fixing that.
_READELF_INVENTORY_RE = re.compile(
    r"^\s*\[\s*(\d+)\]\s+(\S+)\s+([A-Z][A-Z0-9_]*)\s+([0-9a-fA-F]+)\s+"
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
) -> tuple[tuple[ElfSection, ...], int] | None:
    """(sections, unparsed_row_count), or None when the tool failed."""
    try:
        result = subprocess.run(
            [readelf_cmd, "-S", "-W", str(binary_path)],
            capture_output=True,
            text=True,
            # Section names are arbitrary bytes; the platform default codec
            # (cp1252 on Windows) can RAISE mid-decode, escaping the
            # degrade-to-None contract. Decode deterministically, replace
            # the undecodable (#176 fresh-review).
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        log.debug("readelf inventory probe failed: %s", exc)
        return None
    if result.returncode != 0:
        log.debug("readelf -S failed: %s", (result.stderr or "").strip())
        return None
    sections: list[ElfSection] = []
    unparsed = 0
    for line in result.stdout.splitlines():
        match = _READELF_INVENTORY_RE.match(line)
        if match is None:
            # A bracket row that fails the type-constrained pattern (e.g. a
            # numeric/LOOS+ sh_type readelf cannot name) would silently
            # understate occupancy — count it AND surface it. Index 0 is
            # the expected NULL row (matched by position, not name, so a
            # section whose name merely contains "NULL" still gets logged).
            if re.match(r"^\s*\[\s*\d+\]", line) and not re.match(r"^\s*\[\s*0\]", line):
                unparsed += 1
                log.debug("readelf -S row not parsed by inventory: %r", line)
            continue
        idx, name, sh_type, addr_hex, size_hex, flags = match.groups()
        nobits = sh_type == "NOBITS"
        allocated = "A" in flags
        sections.append(
            ElfSection(
                name=name,
                address=int(addr_hex, 16),
                size=int(size_hex, 16),
                nobits=nobits,
                allocated=allocated,
                index=int(idx),
                linker_reserved=(nobits and allocated and _is_reserved_section_name(name)),
            )
        )
    return (tuple(sections), unparsed) if sections else None


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
            encoding="utf-8",
            errors="replace",
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
) -> tuple[tuple[ElfSection, ...], tuple[LoadSegment, ...], int] | None:
    """(sections, PT_LOAD segments, unparsed-block count) from a
    ``fromelf --text -v`` listing."""
    sections: list[ElfSection] = []
    segments: list[LoadSegment] = []
    unparsed = 0
    block: dict[str, str] | None = None
    block_kind: str | None = None
    block_index: int = -1

    def _consume() -> None:
        nonlocal unparsed
        if block is None:
            return
        if block_kind == "section":
            name = block.get("Name")
            addr = _hex_field(block.get("Addr", ""))
            size_match = _FROMELF_SIZE_BYTES_RE.match(block.get("Size", ""))
            if name is None or addr is None or size_match is None:
                # A "** Section #N" block whose fields did not parse is a
                # section MISSING from the inventory — count it, mirroring
                # the readelf path's unparsed-row accounting (a silently
                # partial inventory understates occupancy).
                unparsed += 1
                log.debug(
                    "fromelf section block #%d not parsed: %r",
                    block_index,
                    sorted(block),
                )
                return
            nobits = block.get("Type", "").startswith("SHT_NOBITS")
            allocated = "SHF_ALLOC" in block.get("Flags", "")
            sections.append(
                ElfSection(
                    name=name,
                    address=addr,
                    size=int(size_match.group(1)),
                    nobits=nobits,
                    allocated=allocated,
                    index=block_index,
                    linker_reserved=(nobits and allocated and _is_reserved_section_name(name)),
                )
            )
        elif block_kind == "segment" and block.get("Type", "").startswith("PT_LOAD"):
            vaddr = _hex_field(block.get("Virtual Addr", ""))
            paddr = _hex_field(block.get("Physical Addr", ""))
            filesz = _FROMELF_SIZE_BYTES_RE.match(block.get("Size in file", ""))
            memsz = _FROMELF_SIZE_BYTES_RE.match(block.get("Size in memory", ""))
            if vaddr is None or paddr is None or filesz is None or memsz is None:
                # NOT counted in unparsed_rows: that counter states SECTION
                # completeness, and segments only refine (their absence
                # must not discard the inventory — same rule as
                # _segments_via_readelf, which drops a mangled LOAD row
                # without poisoning the section signal).
                log.debug("fromelf PT_LOAD block not parsed: %r", sorted(block))
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
        section_start = _FROMELF_SECTION_START_RE.match(line)
        if section_start:
            _consume()
            block, block_kind = {}, "section"
            block_index = int(section_start.group(1))
            continue
        if _FROMELF_PROGRAM_START_RE.match(line):
            _consume()
            block, block_kind = {}, "segment"
            block_index = -1
            continue
        if block is None:
            continue
        match = _FROMELF_INVENTORY_FIELD_RE.match(line)
        if match is not None:
            # First occurrence wins, same rationale as the reserved path:
            # the .comment section body echoes the armlink command line and
            # could otherwise overwrite already-read fields.
            block.setdefault(match.group(1), match.group(2))
    _consume()
    return (tuple(sections), tuple(segments), unparsed) if sections else None


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
                encoding="utf-8",
                errors="replace",
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
        sections, segments, unparsed = parsed
        return SectionInventory(sections=sections, segments=segments, unparsed_rows=unparsed)
    if spec.readelf is None:
        return None
    readelf_cmd = resolve_toolchain_executable(toolchain, spec.readelf)
    inventory = _inventory_via_readelf(binary_path, readelf_cmd=readelf_cmd, timeout_s=timeout_s)
    if inventory is None:
        return None
    sections, unparsed = inventory
    segments = _segments_via_readelf(binary_path, readelf_cmd=readelf_cmd, timeout_s=timeout_s)
    return SectionInventory(sections=sections, segments=segments, unparsed_rows=unparsed)


# ---------------------------------------------------------------------------
# Symbol inventory (#133 Phase 3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SymbolEntry:
    """One sized symbol from the linked image (``nm -S``).

    ``type`` is nm's one-letter class (b/B bss, d/D data, t/T text, ...).
    Names are RAW (mangled where C++ mangles them) — consumers match by
    suffix, the same idiom :func:`symbol_address` uses, so
    ``_ZL15g_arena_storage`` matches ``g_arena_storage``. Aliases are
    real (two names at one address+size, e.g. a section-start alias over
    a static); collection consumers dedup by (address, size) when
    summing, keep-first in nm order.
    """

    name: str
    address: int
    size: int
    type: str


#: nm -S --size-sort row: addr, size, one-letter type, name. GNU nm
#: emits only sized rows under --size-sort; llvm-nm ALSO emits size-0
#: rows and undefined/absolute rows (verified on real output — the two
#: tools are NOT row-identical, #179 review M-5). Sized rows parse;
#: recognisable unsized/undefined shapes are SKIPPED silently; anything
#: else counts as unparsed.
_NM_SIZED_ROW_RE = re.compile(r"^([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+(\S)\s+(\S+)\s*$")
#: Undefined/weak rows ("U name", "w name") and unsized address rows
#: ("00000000 T name") — legitimate nm output, not parse failures.
_NM_UNSIZED_ROW_RE = re.compile(r"^\s*(?:[0-9a-fA-F]+\s+)?[A-Za-z?]\s+\S+\s*$")


def symbol_inventory(
    binary_path: Path,
    toolchain: str,
    *,
    timeout_s: int = 30,
) -> tuple[tuple[SymbolEntry, ...], int] | None:
    """Every sized symbol of *binary_path* via the toolchain's ``nm``,
    plus an unparsed-row count, or None on tool failure.

    One probe SHAPE serves all four toolchains, with a documented
    asymmetry (#179 review M-5): GNU nm SYNTHESIZES sizes for some
    linker-defined symbols from the gap to the next symbol, where
    llvm-nm reports st_size verbatim (often 0) and also prints
    undefined/absolute rows. Real objects (arrays, buffers — everything
    the reconciler matches) carry true st_size on both; linker markers
    (__HeapBase, Region$$Table$$*) are NOT comparable across tools and
    zero-size symbols are excluded from matching for the same reason.
    Degrades to None per #131 — and callers must treat a nonzero
    unparsed count like a partial section inventory: refuse, never
    understate.

    Scope limit carried from Phase 1: symbols attribute by VIRTUAL
    address only — per-symbol load-image attribution is not recoverable
    on armlink (single aggregate PT_LOAD; see ``LoadSegment``).
    """
    nm = _nm_command(toolchain)
    try:
        result = subprocess.run(
            [nm, "-S", "--size-sort", str(binary_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        log.debug("nm symbol inventory probe failed: %s", exc)
        return None
    if result.returncode != 0:
        log.debug("nm -S failed: %s", (result.stderr or "").strip())
        return None
    symbols: list[SymbolEntry] = []
    unparsed = 0
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        match = _NM_SIZED_ROW_RE.match(line)
        if match is None:
            if _NM_UNSIZED_ROW_RE.match(line):
                # Undefined/unsized rows carry no occupancy — skipping
                # them silently is correct; only a row that matches NO
                # known nm shape marks the listing partial.
                continue
            unparsed += 1
            log.debug("nm row not parsed by symbol inventory: %r", line)
            continue
        addr_hex, size_hex, sym_type, name = match.groups()
        symbols.append(
            SymbolEntry(
                name=name,
                address=int(addr_hex, 16),
                size=int(size_hex, 16),
                type=sym_type,
            )
        )
    return (tuple(symbols), unparsed)
