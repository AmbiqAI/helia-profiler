"""Intersect hardware application intervals with HPX's selected script extents."""

from __future__ import annotations

from dataclasses import replace

from ..soc import MemoryRange
from .model import ApplicationRegion, LinkerInput


def intersect_intervals(
    intervals: tuple[MemoryRange, ...] | None, extent: MemoryRange
) -> tuple[MemoryRange, ...] | None:
    """Preserve unknown/empty and merge only intervals that overlap or touch."""
    if intervals is None:
        return None
    clipped = []
    for value in intervals:
        if value.start < 0 or value.length < 0:
            raise ValueError("Application ranges cannot be negative")
        start, end = max(value.start, extent.start), min(value.end, extent.end)
        if start < end:
            clipped.append(MemoryRange(start, end - start))
    merged: list[MemoryRange] = []
    for value in sorted(clipped, key=lambda item: item.start):
        if merged and value.start <= merged[-1].end:
            previous = merged.pop()
            merged.append(
                MemoryRange(previous.start, max(previous.end, value.end) - previous.start)
            )
        else:
            merged.append(value)
    return tuple(merged)


def bind_application_ranges(
    layout: LinkerInput,
    applications: tuple[ApplicationRegion, ...] | None,
    *,
    sdk_descriptor: bool = False,
) -> LinkerInput:
    """Apply sourced hardware facts without treating missing facts as unrestricted space."""
    if applications is None:
        return replace(
            layout,
            admission_source="sdk-descriptor" if sdk_descriptor else "unavailable",
            regions=tuple(
                replace(entry, allowed=(entry.app_window,) if sdk_descriptor else None)
                for entry in layout.regions
            ),
        )
    regions = []
    for entry in layout.regions:
        matching = [r for r in applications if r.region is entry.region]
        intervals = (
            None
            if any(r.intervals is None for r in matching)
            else tuple(span for r in matching for span in (r.intervals or ()))
        )
        regions.append(replace(entry, allowed=intersect_intervals(intervals, entry.app_window)))
    return replace(layout, regions=tuple(regions), admission_source="catalog-and-linker")
