"""Typed fixed-fixture metrics from pinned linked images and raw observations."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re

from .fixture_runtime import FixtureFile
from .fixture_target import FixtureTarget
from .hostenv.toolchain_probe import section_inventory, symbol_inventory, SymbolEntry
from .platform import get_soc_for_board
from .platform.memory_map import linked_memory_map
from .placement import MemoryRegion


@dataclass(frozen=True)
class FixtureMetric:
    value: int | float | None
    unit: str
    basis: str
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.unit or not self.basis:
            raise ValueError("Metric unit and basis required")
        if self.value is None:
            if not self.reason:
                raise ValueError("Unavailable metric requires a reason")
        elif (
            type(self.value) not in (int, float)
            or not math.isfinite(self.value)
            or self.value < 0
            or self.reason is not None
        ):
            raise ValueError("Metric requires a finite nonnegative value or an unavailable reason")


@dataclass(frozen=True)
class LinkedSymbol:
    name: str
    address: int
    size_bytes: int
    kind: str


@dataclass(frozen=True)
class FixtureFootprint:
    """Linked allocation, not runtime peaks; component metrics are non-additive."""

    elf: FixtureFile
    image: FixtureFile
    link_map: FixtureFile
    elf_file_bytes: FixtureMetric
    flat_image_bytes: FixtureMetric
    load_image_bytes: FixtureMetric
    linked_ram_bytes: FixtureMetric
    static_ram_bytes: FixtureMetric
    stack_reserved_bytes: FixtureMetric
    heap_reserved_bytes: FixtureMetric
    code_bytes: FixtureMetric
    model_weights_bytes: FixtureMetric
    kernel_code_bytes: FixtureMetric
    runtime_code_bytes: FixtureMetric
    stack_peak_bytes: FixtureMetric
    heap_peak_bytes: FixtureMetric
    linked_symbols: tuple[LinkedSymbol, ...]
    symbol_inventory_reason: str | None
    linked_kernel_symbols: tuple[LinkedSymbol, ...]


def _bytes(value: int, basis: str) -> FixtureMetric:
    return FixtureMetric(value, "B", basis)


def _missing(basis: str, reason: str) -> FixtureMetric:
    return FixtureMetric(None, "B", basis, reason)


def _ranges_size(symbols: tuple[SymbolEntry, ...]) -> int:
    """Union ranges: aliases and overlapping symbol extents are not summed twice."""
    end = 0
    total = 0
    for symbol in sorted(symbols, key=lambda x: x.address):
        total += max(0, symbol.address + symbol.size - max(end, symbol.address))
        end = max(end, symbol.address + symbol.size)
    return total


def inspect_fixture_footprint(
    *,
    elf: FixtureFile,
    image: FixtureFile,
    link_map: FixtureFile,
    target: FixtureTarget,
    toolchain: str,
) -> FixtureFootprint:
    """Host tool probes only; never build, download or access a device.

    Kernel/weight totals remain unavailable without exhaustive attribution.
    The actual linked symbol inventory and pinned map support independent audit.
    """
    target.verify()
    if toolchain != "atfe":
        raise ValueError("Fixture footprint supports the qualified ATfE linker profile only")
    elf_bytes, image_bytes = elf.read(), image.read()
    map_text = link_map.read().decode()
    inventory = section_inventory(elf.path, toolchain)
    symbols = symbol_inventory(elf.path, toolchain)
    # Reject a changed file rather than attach probe output to a stale identity.
    elf.read()
    image.read()
    link_map.read()
    unavailable = _missing("ELF sections", "section_inventory_unavailable_or_partial")
    ram = static = stack = heap = load = unavailable
    if inventory is not None and inventory.unparsed_rows == 0:
        windows = linked_memory_map(get_soc_for_board(target.board))
        ram_windows = tuple(w.window for w in windows if w.region is not MemoryRegion.MRAM)
        allocated = [s for s in inventory.sections if s.allocated and s.size]
        unknown = [
            s
            for s in allocated
            if not any(
                w.window.start <= s.address and s.address + s.size <= w.window.end
                for w in windows
                if w.section_attributable
            )
        ]
        if not unknown:
            ram_sections = [
                s
                for s in allocated
                if any(w.start <= s.address and s.address + s.size <= w.end for w in ram_windows)
            ]
            stack_sections = [s for s in ram_sections if s.name in (".stack", "ARM_LIB_STACK")]
            stack_value = sum(s.size for s in stack_sections)
            heap_value = sum(s.size for s in ram_sections if s.linker_reserved)
            ram_value = sum(s.size for s in ram_sections if not s.linker_reserved)
            ram = _bytes(ram_value, "allocated RAM sections excluding linker heap reservation")
            stack = _bytes(stack_value, "linked stack reservation; not observed use")
            heap = _bytes(heap_value, "linker heap reservation; not observed use")
            static = _bytes(
                ram_value - stack_value,
                "non-stack linked RAM including arenas, terminals and RAM-resident code",
            )
        else:
            ram = static = stack = heap = _missing(
                "ELF sections", "unattributed_or_straddling_section"
            )
        if inventory.segments:
            load = _bytes(
                sum(s.file_size for s in inventory.segments),
                "PT_LOAD file bytes; excludes flat-image gaps",
            )
        else:
            load = _missing("PT_LOAD", "load_segments_unavailable")
    symbol_reason = None
    if symbols is None or symbols[1]:
        symbol_reason = "symbol_inventory_unavailable_or_partial"
    linked = tuple(
        LinkedSymbol(s.name, s.address, s.size, s.type) for s in (symbols[0] if symbols else ())
    )
    # nm text includes literal pools and constants on this linker, so it is
    # deliberately not reported as pure machine-code or model-weight bytes.
    code = _linked_text_size(map_text)
    return FixtureFootprint(
        elf,
        image,
        link_map,
        _bytes(len(elf_bytes), "ELF file including debug/metadata"),
        _bytes(len(image_bytes), "flat load image including layout gaps"),
        load,
        ram,
        static,
        stack,
        heap,
        code,
        _missing("model weights", "weights_not_separated_from_model_metadata"),
        _missing(
            "kernel contribution",
            "exhaustive_kernel_attribution_unavailable; inspect linked_symbols and map",
        ),
        _missing("runtime contribution", "runtime_and_kernel_members_not_exhaustively_partitioned"),
        _missing("stack high-water mark", "not_instrumented"),
        _missing("heap high-water mark", "not_instrumented"),
        linked,
        symbol_reason,
        tuple(s for s in linked if s.kind in ("t", "T") and s.name.startswith(("arm_", "helia_"))),
    )


_LLD_INPUT = re.compile(
    r"^\s*([0-9a-fA-F]+)\s+[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+\d+\s+(.+):\(([^)]+)\)\s*$"
)


def _linked_text_size(text: str) -> FixtureMetric:
    """Count retained LLD text input ranges, excluding nested symbol rows."""
    first = text.splitlines()[0].split() if text.splitlines() else []
    if first != ["VMA", "LMA", "Size", "Align", "Out", "In", "Symbol"]:
        return _missing("linked .text input sections", "unsupported_map_format")
    entries = []
    for line in text.splitlines()[1:]:
        if re.search(r"(?<!:):\(", line) is None:
            continue
        match = _LLD_INPUT.fullmatch(line)
        if match is None:
            return _missing("linked .text input sections", "partial_map_input_inventory")
        address, size, name, section = match.groups()
        if section == ".text" or section.startswith(".text."):
            entries.append(SymbolEntry(name, int(address, 16), int(size, 16), "T"))
    if not entries:
        return _missing("linked .text input sections", "no_text_input_sections")
    return _bytes(
        _ranges_size(tuple(entries)),
        "retained .text input bytes including literal pools; not instruction-only",
    )
