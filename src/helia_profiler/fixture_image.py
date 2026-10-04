"""Bounded ARM ELF image and writable terminal extent validation."""

import re
import struct
from dataclasses import dataclass
from hashlib import sha256

from .fixture_target import fixture_app_region, supported_fixture_target
from .platform.memory_map import MemoryRegion

#: Largest flat image a fixture capture flashes and reads back in full, well inside the
#: MRAM application region below; each capture pays for flashing and verifying every byte.
MAX_IMAGE = 2 * 1024 * 1024
MAX_ELF = 32 * MAX_IMAGE
MRAM = fixture_app_region(MemoryRegion.MRAM)
DTCM = fixture_app_region(MemoryRegion.DTCM)


class ContractError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ContractError(message)


def digest(data):
    return sha256(data).hexdigest()


def integer(value):
    return type(value) is int


def bounded(start, size, limits):
    return (
        integer(start)
        and integer(size)
        and size > 0
        and limits[0] <= start < start + size <= limits[1]
    )


def span(data, offset, size):
    require(
        integer(offset)
        and integer(size)
        and 0 <= offset <= len(data)
        and 0 <= size <= len(data) - offset,
        "truncated ELF range",
    )
    return data[offset : offset + size]


def cstring(data, offset):
    require(0 <= offset < len(data), "invalid string offset")
    end = data.find(b"\0", offset, min(len(data), offset + 4097))
    require(end >= 0, "unterminated or oversized ELF name")
    try:
        return data[offset:end].decode("ascii")
    except UnicodeError as e:
        raise ContractError("non-ASCII ELF name") from e


@dataclass(frozen=True)
class Sink:
    name: str
    address: int
    size: int


@dataclass(frozen=True)
class Image:
    load_address: int
    binary: bytes
    sinks: tuple[Sink, ...]
    elf_sha256: str

    def sink(self, name):
        return next(s for s in self.sinks if s.name == name)


_BYTE_SINK = re.compile(r"deployment_output(_[1-9][0-9]*)?")
#: Sinks only some builds emit; an image carrying one the caller did not declare is refused.
_TYPED_SINK = re.compile(
    r"deployment_output_[1-9][0-9]*|deployment_arena_scan|deployment_gate_ticks"
)


def inspect_elf(data: bytes, binary: bytes, load_address: int, sizes: dict[str, int]) -> Image:
    require(
        load_address == supported_fixture_target().load_address,
        "unsupported application boot origin",
    )
    require(52 <= len(data) <= MAX_ELF, "ELF size")
    require(data[:7] == b"\x7fELF\x01\x01\x01", "requires ELF32 little endian version 1")
    fields = struct.unpack_from("<HHIIIIIHHHHHH", data, 16)
    (
        kind,
        machine,
        version,
        entry,
        phoff,
        shoff,
        flags,
        ehsize,
        phsize,
        phnum,
        shsize,
        shnum,
        shstr,
    ) = fields
    require(
        kind == 2 and machine == 40 and version == 1 and ehsize == 52, "requires ARM executable"
    )
    require(
        phsize == 32
        and shsize == 40
        and 0 < phnum <= 256
        and 0 < shnum <= 4096
        and 0 < shstr < shnum,
        "unsupported ELF tables",
    )
    span(data, phoff, phnum * phsize)
    span(data, shoff, shnum * shsize)
    require(
        0 < len(binary) <= MAX_IMAGE and bounded(load_address, len(binary), MRAM),
        "image outside bounded application MRAM",
    )
    sections = [struct.unpack_from("<10I", data, shoff + i * 40) for i in range(shnum)]
    require(sections[shstr][1] == 3, "invalid section names")
    names = span(data, sections[shstr][4], sections[shstr][5])
    segments = []
    coverage = bytearray(len(binary))
    for i in range(phnum):
        typ, off, va, pa, fs, ms, fl, align = struct.unpack_from("<8I", data, phoff + i * 32)
        if typ != 1:
            continue
        require(fs <= ms and va + ms <= 2**32 and pa + fs <= 2**32, "invalid load segment sizes")
        require(
            align in (0, 1) or (align & (align - 1) == 0 and va % align == off % align),
            "invalid load alignment",
        )
        payload = span(data, off, fs)
        segments.append((va, ms, fl, off, fs, pa))
        if fs:
            require(
                bounded(pa, fs, (load_address, load_address + len(binary))),
                "load segment outside bin",
            )
            start = pa - load_address
            require(not any(coverage[start : start + fs]), "overlapping physical load segments")
            require(binary[start : start + fs] == payload, "ELF/bin load bytes differ")
            coverage[start : start + fs] = b"\1" * fs
    require(segments and coverage[0] and coverage[-1], "bin bounds do not match load segments")
    require(
        all(b == 0 for b, covered in zip(binary, coverage) if not covered), "nonzero binary gap"
    )
    require(
        any(fl & 1 and fs and va <= (entry & ~1) < va + fs for va, ms, fl, off, fs, pa in segments),
        "entry outside executable load bytes",
    )
    file_sections = sorted((s[4], s[4] + s[5]) for s in sections if s[1] != 8 and s[5])
    require(
        all(a[1] <= b[0] for a, b in zip(file_sections, file_sections[1:])),
        "overlapping section file ranges",
    )
    require(sum(s[5] // 16 for s in sections if s[1] in (2, 11)) <= 100000, "symbol table budget")
    found = {name: [] for name in sizes}
    for i, s in enumerate(sections):
        n, typ, fl, va, off, size, link, info, align, entsize = s
        cstring(names, n)
        if typ != 8:
            span(data, off, size)
        if typ not in (2, 11):
            continue
        require(entsize == 16 and size % 16 == 0 and 0 < link < shnum, "invalid symbol table")
        strings = sections[link]
        require(strings[1] == 3, "invalid symbol string table")
        table = span(data, strings[4], strings[5])
        for pos in range(off, off + size, 16):
            n, value, length, sym_info, other, section = struct.unpack_from("<IIIBBH", data, pos)
            name = cstring(table, n)
            if name not in found:
                require(not _TYPED_SINK.fullmatch(name), f"undeclared fixture sink {name}")
                continue
            require(
                0 < section < shnum and sym_info >> 4 == 1 and sym_info & 15 == 1,
                "sink must be defined global object",
            )
            require(length == sizes[name] and bounded(value, length, DTCM), "sink size/DTCM range")
            require(_BYTE_SINK.fullmatch(name) or value % 4 == 0, "unaligned word sink")
            owner = sections[section]
            require(
                owner[2] & 3 == 3 and bounded(value, length, (owner[3], owner[3] + owner[5])),
                "sink outside writable allocated section",
            )
            require(
                any(
                    fl & 2 and bounded(value, length, (va, va + ms))
                    for va, ms, fl, off, fs, pa in segments
                ),
                "sink outside writable load memory",
            )
            found[name].append(Sink(name, value, length))
    require(all(len(v) == 1 for v in found.values()), "missing or duplicate exact sink symbol")
    sinks = tuple(found[n][0] for n in sorted(found))
    ordered = sorted(sinks, key=lambda s: s.address)
    require(
        all(a.address + a.size <= b.address for a, b in zip(ordered, ordered[1:])),
        "overlapping sinks",
    )
    return Image(load_address, bytes(binary), sinks, digest(data))
