"""Assemble the expected linker input from immutable platform views."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from ..memory_map import LinkedRegionWindow, LinkFamily, linked_memory_map
from ..placement import MemoryRegion
from ..soc import MemoryRange, SocDef
from .budget import bind_application_ranges, intersect_intervals
from .data import characterized_input
from .model import ApplicationRegion, LinkerInput, LinkerRegion

if TYPE_CHECKING:
    from ..provenance import TargetSelection


def expected_linker_input(
    soc: SocDef,
    selection: TargetSelection,
    link_family: LinkFamily,
    linker_profile: str,
) -> LinkerInput | None:
    """Record an input assumption; the backend separately verifies actual applicability."""
    from ..catalog import application_regions

    if not soc.is_builtin:
        return None
    characterized = characterized_input(soc.name, link_family, linker_profile)
    if characterized is None:
        return None
    applications = application_regions(selection)
    result = bind_application_ranges(
        characterized, applications, sdk_descriptor=selection.origin == "sdk-descriptor"
    )
    psram = _psram_window(soc, applications, linker_profile)
    if psram is not None:
        extent = psram.app_window[link_family]
        matching = (
            None
            if applications is None
            else tuple(r for r in applications if r.region is MemoryRegion.PSRAM)
        )
        allowed = None
        if matching is not None and all(r.intervals is not None for r in matching):
            allowed = intersect_intervals(
                tuple(v for r in matching for v in (r.intervals or ())), extent
            )
        if selection.origin == "sdk-descriptor":
            allowed = (extent,)
        result = replace(
            result,
            regions=(
                *result.regions,
                LinkerRegion(
                    MemoryRegion.PSRAM,
                    psram.window,
                    extent,
                    allowed,
                    psram.window_provenance,
                    section_attributable=False,
                ),
            ),
        )
    return result


def _psram_window(
    soc: SocDef,
    applications: tuple[ApplicationRegion, ...] | None,
    linker_profile: str,
) -> LinkedRegionWindow | None:
    previous = next(
        (
            r
            for r in linked_memory_map(soc, linker_profile=linker_profile)
            if r.region is MemoryRegion.PSRAM
        ),
        None,
    )
    matches = tuple(r for r in applications or () if r.region is MemoryRegion.PSRAM)
    if len(matches) != 1 or matches[0].aperture is None:
        return previous
    aperture = matches[0].aperture
    length = min(aperture.length, soc.memory.psram_kb * 1024)
    if length <= 0:
        return None
    extent = MemoryRange(aperture.start, length)
    if previous is not None and previous.window == extent:
        return previous
    return LinkedRegionWindow(
        MemoryRegion.PSRAM,
        extent,
        {family: extent for family in LinkFamily},
        window_provenance="catalog",
        app_provenance="catalog",
        section_attributable=False,
    )
