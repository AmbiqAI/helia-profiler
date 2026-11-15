"""Independent transfer observation frames, never PMU presets."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..errors import CaptureError
from ..results.transfer import (
    TransferDiagnostic,
    TransferOverlap,
    TransferStats,
    TransferUnitTiming,
)
from ..wire import HPX_ITER_SENTINEL_RE, HPX_PRESET_SENTINEL_RE, WireKey
from .transfer import _activity, _indexed, _uint
from .transfer_trace import TRACE_KEYS, TransferTraceParser

if TYPE_CHECKING:
    from ..config import ProfilingConfig
    from ..results import PmuResult


def _status(value: str) -> int:
    if not re.fullmatch(r"-?[0-9]{1,10}", value):
        raise ValueError("expected an int32 status")
    number = int(value)
    if not -(1 << 31) <= number < 1 << 31:
        raise ValueError("status exceeds int32")
    return number


class TransferDiagnosticParser:
    """Assemble post-run frames without changing PMU iteration accounting."""

    def __init__(self) -> None:
        self.records: list[TransferDiagnostic] = []
        self._header: tuple[str, int, int, str, int, int] | None = None
        self._units: dict[int, TransferUnitTiming] = {}
        self._stats: TransferStats | None = None
        self._overlap: TransferOverlap | None = None
        self._seen: set[tuple[str, int]] = set()
        self._trace = TransferTraceParser()
        self._trace_bindings: set[int] = set()

    def finish(self) -> None:
        if self._header is not None:
            raise CaptureError("Transfer diagnostic ended without its end record.")

    def feed_line(self, line: str) -> bool:
        key, separator, value = line.partition("=")
        markers = {
            WireKey.TRANSFER_DIAGNOSTIC_STATS.wire,
            WireKey.TRANSFER_DIAGNOSTIC_OVERLAP.wire,
            WireKey.TRANSFER_DIAGNOSTIC_BEGIN.wire,
            WireKey.TRANSFER_DIAGNOSTIC_END.wire,
            WireKey.TRANSFER_TIMING.wire,
        }
        markers.update(TRACE_KEYS)
        if key not in markers:
            if self._header is not None and (
                HPX_ITER_SENTINEL_RE.match(line) or HPX_PRESET_SENTINEL_RE.match(line)
            ):
                raise CaptureError(f"PMU boundary inside transfer diagnostic: {line!r}.")
            return False
        try:
            if not separator:
                raise ValueError("missing equals sign")
            if key == WireKey.TRANSFER_DIAGNOSTIC_BEGIN.wire:
                self._begin(value)
            elif self._header is None:
                raise ValueError("outside a transfer diagnostic")
            elif key in TRACE_KEYS:
                if self._header[0] != "trace":
                    raise ValueError("trace records in a non-trace diagnostic")
                self._trace.feed(key, value, self._header[4])
            elif key == WireKey.TRANSFER_TIMING.wire:
                fields = value.split(",")
                if len(fields) != 5 or self._header[0] != "timing":
                    raise ValueError("expected five timing fields in timing mode")
                unit = _uint(fields[0])
                if unit >= self._header[4] or unit in self._units:
                    raise ValueError("duplicate or out-of-range unit")
                issue = None if fields[1] == "x" else _uint(fields[1])
                issue_status = None if fields[2] == "x" else _status(fields[2])
                wait = None if fields[3] == "x" else _uint(fields[3])
                wait_status = None if fields[4] == "x" else _status(fields[4])
                if (issue is None) != (issue_status is None) or (wait is None) != (
                    wait_status is None
                ):
                    raise ValueError("unpaired call duration/status")
                self._units[unit] = TransferUnitTiming(unit, issue, issue_status, wait, wait_status)
            elif key == WireKey.TRANSFER_DIAGNOSTIC_STATS.wire:
                fields = value.split(",")
                if len(fields) != 6 or self._stats is not None:
                    raise ValueError("expected one six-field stats record")
                self._stats = TransferStats(*(_uint(field) for field in fields))
            elif key == WireKey.TRANSFER_DIAGNOSTIC_OVERLAP.wire:
                if self._header[0] != "overlap" or self._overlap is not None:
                    raise ValueError("unexpected or repeated overlap record")
                units, operators = value.split(";")
                self._overlap = TransferOverlap(
                    _indexed(units, _activity), _indexed(operators, _activity)
                )
            else:
                self._end(value)
        except ValueError as exc:
            raise CaptureError(f"Invalid transfer diagnostic line {line!r}: {exc}.") from exc
        return True

    def _begin(self, value: str) -> None:
        if self._header is not None:
            raise ValueError("nested diagnostic")
        fields = value.split(",")
        if len(fields) != 6:
            raise ValueError("expected purpose, iteration, clock, preparation and unit counts")
        purpose, iteration, cpu, preparation, units, dma = fields
        if purpose not in {"timing", "overlap", "trace"} or preparation not in {
            "configured",
            "cold",
        }:
            raise ValueError("unknown purpose or preparation")
        counts = tuple(_uint(item) for item in (iteration, cpu, units, dma))
        if counts[1] == 0 or counts[2] == 0 or not 0 < counts[3] <= counts[2]:
            raise ValueError("invalid clock or unit counts")
        if (purpose, counts[0]) in self._seen:
            raise ValueError("duplicate diagnostic iteration")
        self._header = purpose, counts[0], counts[1], preparation, counts[2], counts[3]
        self._units = {}
        self._stats = None
        self._overlap = None
        self._trace = TransferTraceParser()

    def _end(self, value: str) -> None:
        assert self._header is not None
        fields = value.split(",")
        if len(fields) != 3 or fields[2] not in {"0", "1"}:
            raise ValueError("expected elapsed cycles, run status and completeness")
        elapsed, status, complete = _uint(fields[0]), _status(fields[1]), fields[2] == "1"
        purpose, iteration, cpu, preparation, count, dma = self._header
        if self._stats is None:
            raise ValueError("missing diagnostic stats")
        if purpose == "timing":
            if len(self._units) != count:
                raise ValueError("missing unit records")
            if complete and (
                elapsed == 0
                or status != 0
                or any(unit.issue_status != 0 for unit in self._units.values())
                or sum(unit.wait_status is not None for unit in self._units.values()) != dma
                or any(unit.wait_status not in (None, 0) for unit in self._units.values())
            ):
                raise ValueError("complete timing contradicts observed calls")
        elif purpose == "overlap" and (
            self._overlap is None
            or len(self._overlap.units) != count
            or any(unit >= count for unit in self._overlap.units)
        ):
            raise ValueError("missing overlap units")
        if complete and (status != 0 or elapsed == 0):
            raise ValueError("complete diagnostic contradicts run status or elapsed clock")
        trace = None
        if purpose == "trace":
            trace = self._trace.finish(
                elapsed=elapsed, complete=complete, units=count, dma=dma, issues=self._stats.issues
            )
            if trace.binding_epoch in self._trace_bindings:
                raise ValueError("repeated trace binding epoch")
            self._trace_bindings.add(trace.binding_epoch)
        self.records.append(
            TransferDiagnostic(
                purpose,
                iteration,
                cpu,
                preparation,
                elapsed,
                status,
                complete,
                tuple(self._units[index] for index in sorted(self._units)),
                self._stats,
                self._overlap,
                trace,
            )
        )
        self._seen.add((purpose, iteration))
        self._header = None


def validate_transfer_diagnostics(result: PmuResult, profiling: ProfilingConfig) -> None:
    """Reject missing or mismatched observations at the live capture boundary."""
    expected = (
        0 if profiling.transfer_diagnostics == "off" else profiling.transfer_diagnostic_iterations
    )
    records = result.transfer_diagnostics
    if len(records) != expected or any(
        record.purpose != profiling.transfer_diagnostics
        or record.iteration != index
        or record.preparation != ("cold" if profiling.stream_evidence else "configured")
        or record.cpu_hz != result.meta.system_clock_hz
        or (
            record.purpose == "trace"
            and (
                record.trace is None
                or record.trace.capacity != profiling.transfer_trace_max_pieces
                or record.trace.binding_epoch != index + 1
                or record.trace.sequence_start != 0
                or (record.complete and record.trace.operator_count != len(result.layers))
            )
        )
        or (record.purpose != "trace" and record.trace is not None)
        for index, record in enumerate(records)
    ):
        raise CaptureError(
            "Transfer diagnostics do not match the requested mode, iterations, preparation or clock.",
            hint="Check firmware identity and transport integrity; do not reuse stale firmware.",
        )
