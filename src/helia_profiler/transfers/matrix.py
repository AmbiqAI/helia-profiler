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

    @property
    def lengths(self) -> tuple[int, ...]:
        """Aligned consecutive pieces, each within a 4 KiB DTCM window."""
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
    cells = []
    for unit in plan["schedule"]["units"]:
        if unit["engine"] != "dma" or unit["source_memory"].lower() != "psram":
            continue
        size, offset, source = (unit[key] for key in ("bytes", "pool_offset", "source_offset"))
        if any(type(v) is not int or v < 0 or v % 4 for v in (size, offset, source)) or not size:
            raise ValueError("Invalid DMA calibration geometry")
        pieces = (offset % 4096 + size + 4095) // 4096
        cells.append(Cell(size, pieces, "none", offset, source))
    return tuple(cells)


def stats(values: list[int]) -> dict[str, int | float]:
    return {"min": min(values), "mean": fmean(values), "max": max(values), "samples": len(values)}
