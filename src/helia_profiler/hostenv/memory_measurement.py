"""Measured memory regions — the join of the ELF inventory and the map.

The #133 Phase 2 measurement: classify the linked binary's section
inventory (``toolchain_probe.section_inventory``) into the verified per-SoC
windows (``platform.memory_map.linked_memory_map``) and compute per-region
occupancy under the contract those modules define:

* ``used``  = Σ allocated, non-``linker_reserved`` sections inside the link
  family's app extent (gcc's floating ``.stack`` included — live memory);
* ``reserved`` = the linker's own reservations: ``linker_reserved``
  sections inside the extent (fill-to-end/fixed heaps) plus allocated
  sections inside the window but outside the extent (armlink's fixed
  heap/stack regions, apollo3p's STACKMEM slot);
* ``free`` = ``app_length − used`` (a :class:`MeasuredRegion` property);
* ``load_image`` = Σ ``LoadSegment.file_size`` grouped by the PHYSICAL
  address's region — per segment, never per section, because armlink emits
  one aggregate PT_LOAD (see ``LoadSegment``'s docstring).

Degrades to ``None`` — never guesses — when the map is uncharacterized
(custom SoC, non-default linker profile), the tool probe fails, or the
inventory is PARTIAL (``unparsed_rows`` nonzero): publishing occupancy from
a partial inventory would understate it silently, the exact #131 failure
class. Allocated sections outside every window are returned as
``unattributed`` — the police flag that either the binary or the
characterized table is wrong.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..platform.memory_map import (
    classify_address,
    link_family_for_toolchain,
    linked_memory_map,
)
from ..platform.soc import SocDef
from ..results import (
    ConsumerReconciliation,
    MeasuredMemoryRegions,
    MeasuredRegion,
    MemoryPlan,
    MemoryReconciliation,
    RegionReconciliation,
    UnattributedSection,
)
from .toolchain_probe import SymbolEntry, section_inventory

log = logging.getLogger("hpx")


def measure_memory_regions(
    binary_path: Path,
    toolchain: str,
    soc: SocDef,
    *,
    linker_profile: str = "default",
    timeout_s: int = 30,
) -> MeasuredMemoryRegions | None:
    """The measured region occupancy of *binary_path*, or None.

    ``linker_profile`` must be the profile the binary was actually linked
    with (the ``NSX_LINKER_PROFILE`` engine knob); anything but the
    characterized ``default`` yields None via the map.
    """
    windows = linked_memory_map(soc, linker_profile=linker_profile)
    if not windows:
        log.debug(
            "No characterized memory map for %s (profile %r); measured regions unavailable.",
            soc.name,
            linker_profile,
        )
        return None
    inventory = section_inventory(binary_path, toolchain, timeout_s=timeout_s)
    if inventory is None:
        log.debug("Section inventory unavailable; measured regions unavailable.")
        return None
    if inventory.unparsed_rows:
        log.debug(
            "Section inventory is partial (%d unparsed rows); refusing to "
            "publish understated occupancy.",
            inventory.unparsed_rows,
        )
        return None

    family = link_family_for_toolchain(toolchain)
    allocated = [s for s in inventory.sections if s.allocated]
    # Attribution runs against the ATTRIBUTABLE windows only: a
    # section_attributable=False window (PSRAM) is a region no linker
    # script maps, so anything landing there is just as anomalous as an
    # address outside every window — classifying it silently would disable
    # the police flag on exactly the region this block cannot report
    # (#177 review m2).
    attributable = tuple(w for w in windows if w.section_attributable)

    regions: list[MeasuredRegion] = []
    for w in attributable:
        extent = w.app_window[family]
        used = 0
        reserved = 0
        for s in allocated:
            if extent.contains(s.address):
                if s.linker_reserved:
                    reserved += s.size
                else:
                    used += s.size
            elif w.window.contains(s.address):
                # Inside the window, outside the extent: the linker's FIXED
                # reservations (armlink heap/stack, apollo3p STACKMEM).
                reserved += s.size
        load_image = sum(
            seg.file_size
            for seg in inventory.segments
            if classify_address(seg.physical_address, attributable) is w.region
        )
        regions.append(
            MeasuredRegion(
                region=w.region,
                window_start=w.window.start,
                window_length=w.window.length,
                app_start=extent.start,
                app_length=extent.length,
                used=used,
                reserved=reserved,
                load_image=load_image,
                window_provenance=w.window_provenance,
                app_provenance=w.app_provenance,
            )
        )

    unattributed = tuple(
        UnattributedSection(name=s.name, address=s.address, size=s.size)
        for s in allocated
        # Zero-length markers are noise, not lost bytes.
        if s.size and classify_address(s.address, attributable) is None
    )
    if unattributed:
        log.warning(
            "%d allocated section(s) fall outside every verified %s window: %s",
            len(unattributed),
            soc.name,
            ", ".join(f"{u.name}@0x{u.address:08X}" for u in unattributed[:5]),
        )

    # Segments have the same police problem as sections (#177 review m3):
    # PT_LOAD file bytes whose physical address classifies nowhere would
    # otherwise vanish from load_image with no trace.
    unattributed_load = sum(
        seg.file_size
        for seg in inventory.segments
        if seg.file_size and classify_address(seg.physical_address, attributable) is None
    )
    if unattributed_load:
        log.warning(
            "%d load-image byte(s) load outside every verified %s window.",
            unattributed_load,
            soc.name,
        )

    return MeasuredMemoryRegions(
        link_family=str(family),
        linker_profile=linker_profile,
        regions=tuple(regions),
        unattributed=unattributed,
        unattributed_load_bytes=unattributed_load,
    )


# ---------------------------------------------------------------------------
# Plan-vs-measured reconciliation (#133 Phase 3)
# ---------------------------------------------------------------------------

#: Plan-consumer name -> candidate symbol suffixes in the linked image.
#: Matching is by SUFFIX (the symbol_address idiom), so C++ mangling
#: (_ZL15g_arena_storage) resolves. Multi-piece consumers list every
#: piece and the pieces' sizes are SUMMED (rtt_buffers = up buffer +
#: down buffer + control block). heliaAOT consumers carry their own
#: symbol hints (MemoryConsumer.symbol) instead — their names do not
#: resemble their symbols.
_CONSUMER_SYMBOLS: dict[str, tuple[str, ...]] = {
    "tensor_arena": ("g_arena_storage",),
    "model_flatbuffer": ("model_data",),
    "pte_program": ("model_data",),
    "planned_arena": ("g_planned_arena",),
    "method_arena": ("g_method_arena",),
    "temporary_arena": ("g_temporary_arena",),
    "input_buffer": ("g_input",),
    "output_buffer": ("g_output",),
    #: TFLM/heliaRT records live inside g_profiler (the whole profiler
    #: object, records dominating); AOT/ET declare g_layers directly.
    "pmu_layer_records": ("g_profiler", "g_layers"),
    "rtt_buffers": ("_acUpBuffer", "_acDownBuffer", "_SEGGER_RTT"),
    "usb_buffers": ("usb_tx_buf", "usb_rx_buf"),
    #: GNU-family links (gcc AND ATfE) carry a sized g_pui32Stack;
    #: armlink's stack is a scatter REGION, not a symbol -> missing
    #: there, by design.
    "boot_stack": ("g_pui32Stack",),
}


def _name_matches(symbol_name: str, candidate: str) -> bool:
    """Exact name, or GCC's file-static mangling ``_ZL<len><name>``.

    NOT a bare suffix test (#179 review M-1): real HAL globals like
    ``am_hal_gpio_pincfg_input`` END WITH ``g_input`` and would flip
    verdicts with 4-byte MRAM constants."""
    return symbol_name == candidate or symbol_name == f"_ZL{len(candidate)}{candidate}"


def _match_symbols(
    candidates: tuple[str, ...], symbols: tuple[SymbolEntry, ...]
) -> tuple[SymbolEntry, ...]:
    """Every SIZED symbol matching any candidate, deduped by
    (address, size) keep-first — aliases (two names over one object,
    e.g. an extern alias plus the mangled static) must not double the
    sum. Zero-size symbols are ignored: llvm-nm reports st_size verbatim
    and armlink's linker-defined markers carry none, so a zero-size
    "match" would manufacture measured_size=0 (#179 review M-5)."""
    matched: list[SymbolEntry] = []
    seen: set[tuple[int, int]] = set()
    for sym in symbols:
        if sym.size and any(_name_matches(sym.name, c) for c in candidates):
            key = (sym.address, sym.size)
            if key not in seen:
                seen.add(key)
                matched.append(sym)
    return tuple(matched)


def reconcile_memory(
    plan: MemoryPlan,
    measured: MeasuredMemoryRegions,
    symbols: tuple[SymbolEntry, ...],
) -> MemoryReconciliation:
    """Hold the plan against the linked binary, by name and by region.

    Consumer statuses per :class:`ConsumerReconciliation`; region rows
    compare plan ``used`` to measured ``used`` for every region both
    sides know. Purely additive — never mutates either input.
    """
    measured_windows = tuple(
        (str(r.region), r.window_start, r.window_start + r.window_length) for r in measured.regions
    )

    def _classify(address: int) -> str | None:
        for region_name, start, end in measured_windows:
            if start <= address < end:
                return region_name
        return None

    consumers: list[ConsumerReconciliation] = []
    for region_usage in plan.regions:
        region_name = str(region_usage.region)
        for consumer in region_usage.consumers:
            candidates = (
                (consumer.symbol,) if consumer.symbol else _CONSUMER_SYMBOLS.get(consumer.name, ())
            )
            if region_name == "PSRAM":
                # PSRAM objects bind through runtime POINTERS — and the
                # pointer itself IS a 4-byte sized symbol carrying the
                # same name (verified: _ZL10model_data size 4 on the
                # PSRAM-weights render). Matching it would report the
                # planned megabytes as delta shortfall (#179 review M-2).
                status, matched = "unmatchable", ()
            elif not candidates:
                # Structural: armlink's stack is a scatter region and AOT
                # source-staging entries may carry no hint — nothing to
                # look for is not a failure to find.
                status, matched = "unmatchable", ()
            else:
                matched = _match_symbols(tuple(candidates), symbols)
                status = "matched" if matched else "missing"
            measured_size = sum(m.size for m in matched) if status == "matched" else None
            measured_region = (
                _classify(max(matched, key=lambda m: m.size).address) if matched else None
            )
            consumers.append(
                ConsumerReconciliation(
                    name=consumer.name,
                    kind=str(consumer.kind),
                    region=region_name,
                    planned_size=consumer.size,
                    status=status,
                    matched_symbols=tuple(m.name for m in matched),
                    measured_size=measured_size,
                    measured_region=measured_region,
                    delta=(measured_size - consumer.size if measured_size is not None else None),
                )
            )

    measured_by_region = {str(r.region): r for r in measured.regions}
    regions = tuple(
        RegionReconciliation(
            region=str(r.region),
            planned_used=r.used,
            measured_used=measured_by_region[str(r.region)].used,
        )
        for r in plan.regions
        if str(r.region) in measured_by_region
    )
    return MemoryReconciliation(consumers=tuple(consumers), regions=regions)
