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


@dataclass(frozen=True)
class TransferUnitTiming:
    """Observed call durations in CPU cycles; an absent call is not a zero wait."""

    unit: int
    issue_cycles: int | None
    issue_status: int | None
    wait_cycles: int | None
    wait_status: int | None


@dataclass(frozen=True)
class TransferTraceSpan:
    """One software call or operator interval, relative to its invocation origin."""

    kind: str
    id: int
    start_cycles: int | None
    end_cycles: int | None
    status: int | None


@dataclass(frozen=True)
class TransferCompletion:
    """A delivered completion notification, not a physical bus finish."""

    sequence: int
    ticket: int
    byte_count: int
    status: int
    cycles: int


@dataclass(frozen=True)
class TransferTrace:
    """Bounded chronological evidence from one PMU-free invocation."""

    binding_epoch: int
    sequence_start: int
    capacity: int
    storage_bytes: int
    operator_count: int
    observed_count: int
    dropped_count: int
    clock_valid: bool
    quiescent: bool
    spans: tuple[TransferTraceSpan, ...]
    completions: tuple[TransferCompletion, ...]
    schema: str = "hpx.transfer-trace"
    schema_version: int = 1


@dataclass(frozen=True)
class TransferDiagnostic:
    """One independent diagnostic iteration, outside PMU presets and averages."""

    purpose: str
    iteration: int
    cpu_hz: int
    preparation: str
    elapsed_cycles: int
    run_status: int
    complete: bool
    units: tuple[TransferUnitTiming, ...] = ()
    stats: TransferStats | None = None
    overlap: TransferOverlap | None = None
    trace: TransferTrace | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}
