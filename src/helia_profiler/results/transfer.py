"""Transfer-provider evidence from individual instrumented runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class TransferStats:
    """Per-run counters, except peak and wait maxima which are since binding."""

    issues: int
    waits: int
    aborts: int
    peak_in_flight: int
    wait_checks_max: int
    unsampled: int


@dataclass(frozen=True)
class TransferActivity:
    """Both-end observations; an unreadable DMA sample is unknown, not false."""

    dma_in_progress: bool | None
    reads_pending: bool


@dataclass(frozen=True)
class TransferOverlap:
    """Activity keyed by transfer-unit ID and operator execution position."""

    units: dict[int, TransferActivity]
    operators: dict[int, TransferActivity]


@dataclass(frozen=True)
class TransferRefills:
    """L1D refill counts in unit-issue and operator-run windows."""

    units: dict[int, int]
    operators: dict[int, int]


@dataclass(frozen=True)
class TransferEvidence:
    """Available evidence for one pass/iteration, without aggregation."""

    preset: str
    iteration: int
    stats: TransferStats | None = None
    overlap: TransferOverlap | None = None
    refills: TransferRefills | None = None

    def to_dict(self) -> dict[str, Any]:
        """Omit missing records while retaining unknown DMA flags as JSON null."""
        return {key: value for key, value in asdict(self).items() if value is not None}
