"""Geometry and timing evidence for the Apollo510 transfer probe."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean
from typing import Any

SIZES = tuple(1024 << i for i in range(7))
REPETITIONS = 64


@dataclass(frozen=True)
class Cell:
    size: int
    pieces: int
    regime: str
    offset: int = 0
    source_offset: int = 0

    ticket: int = 0
    members: tuple[Cell, ...] = ()

    @property
    def descriptors(self) -> tuple[tuple[int, int, int, int], ...]:
        """Provider ticket, source offset, destination offset and bytes per issue."""
        descriptors = []
        for unit in self.members or (self,):
            offset = 0
            for length in unit.lengths:
                descriptors.append(
                    (unit.ticket, unit.source_offset + offset, unit.offset + offset, length)
                )
                offset += length
        return tuple(descriptors)

    @property
    def lengths(self) -> tuple[int, ...]:
        """Aligned consecutive pieces, each within a 4 KiB DTCM window."""
        if self.members:
            return tuple(n for unit in self.members for n in unit.lengths)
        if self.pieces > 8:
            return ()
        if not self.pieces:
            return (self.size,)
        spans = []
        remaining = self.size
        at = self.offset
        while remaining:
            chunk = min(remaining, 4096 - at % 4096)
            spans.append(chunk)
            at += chunk
            remaining -= chunk
        if len(spans) > self.pieces:
            return ()
        while len(spans) < self.pieces:
            i = spans.index(max(spans))
            first = spans[i] // 8 * 4
            if not first:
                return ()
            spans[i : i + 1] = [first, spans[i] - first]
        return tuple(spans)


def matrix() -> tuple[Cell, ...]:
    return tuple(
        cell
        for size in SIZES
        for cell in (
            *(Cell(size, pieces, "none") for pieces in range(1, 9)),
            Cell(size, 0, "cold"),
            Cell(size, 0, "warm"),
        )
    )


def schedule_cells(plan: dict[str, Any]) -> tuple[Cell, ...]:
    """Reproduce the converted DMA unit offsets separately from the price curves."""
    pool = plan["schedule"]["pool"]
    if not pool or str(pool["memory"]).lower() != "dtcm":
        raise ValueError("Transfer calibration requires a DTCM stream pool")
    cells = []
    groups: dict[str | None, list[Cell]] = {}
    for unit in plan["schedule"]["units"]:
        if unit["engine"] != "dma" or unit["source_memory"].lower() != "psram":
            continue
        size, offset, source = (unit[key] for key in ("bytes", "pool_offset", "source_offset"))
        if any(type(v) is not int or v < 0 or v % 4 for v in (size, offset, source)) or not size:
            raise ValueError("Invalid DMA calibration geometry")
        pieces = (offset % 4096 + size + 4095) // 4096
        cell = Cell(size, pieces, "none", offset, source, unit["id"])
        cells.append(cell)
        groups.setdefault(unit["issue_after"], []).append(cell)
    for members in groups.values():
        if len(members) > 1:
            cells.append(
                Cell(
                    sum(c.size for c in members),
                    sum(c.pieces for c in members),
                    "none",
                    members=tuple(members),
                )
            )
    return tuple(cells)


def stats(values: list[int]) -> dict[str, int | float]:
    return {"min": min(values), "mean": fmean(values), "max": max(values), "samples": len(values)}
