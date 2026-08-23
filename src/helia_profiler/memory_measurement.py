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

from .platform.memory_map import (
    classify_address,
    link_family_for_toolchain,
    linked_memory_map,
)
from .platform.soc import SocDef
from .results import MeasuredMemoryRegions, MeasuredRegion, UnattributedSection
from .toolchain_probe import section_inventory

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
            "No characterized memory map for %s (profile %r); "
            "measured regions unavailable.",
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
        if seg.file_size
        and classify_address(seg.physical_address, attributable) is None
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
