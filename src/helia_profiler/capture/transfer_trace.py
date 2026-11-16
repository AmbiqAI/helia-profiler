"""Strict parsing of bounded post-run chronological transfer records."""

from __future__ import annotations

from ..results.transfer import TransferCompletion, TransferTrace, TransferTraceSpan
from ..results.transfer_validation import validate_complete_trace
from ..wire import WireKey
from .transfer import _uint

TRACE_KEYS = frozenset(
    {
        WireKey.TRANSFER_TRACE_BEGIN.wire,
        WireKey.TRANSFER_TRACE_SPAN.wire,
        WireKey.TRANSFER_TRACE_PIECE.wire,
        WireKey.TRANSFER_TRACE_END.wire,
    }
)


def _optional(value: str) -> int | None:
    return None if value == "x" else _uint(value)


def _signed(value: str) -> int:
    negative = value.startswith("-")
    number = _uint(value[1:] if negative else value)
    number = -number if negative else number
    if not -(1 << 31) <= number < (1 << 31):
        raise ValueError("trace status exceeds int32")
    return number


class TransferTraceParser:
    """Storage grows with received records, never with declared header counts."""

    def __init__(self) -> None:
        self.header: tuple[int, int, int, int, int] | None = None
        self.spans: dict[tuple[str, int], TransferTraceSpan] = {}
        self.pieces: dict[int, TransferCompletion] = {}
        self.result: TransferTrace | None = None

    def feed(self, key: str, value: str, units: int) -> None:
        fields = value.split(",")
        if self.result is not None:
            raise ValueError("trace record after trace end")
        if key == WireKey.TRANSFER_TRACE_BEGIN.wire:
            if self.header is not None or len(fields) != 6:
                raise ValueError("expected one six-field trace header")
            version, epoch, sequence, capacity, storage, operators = map(_uint, fields)
            if version != 1 or epoch == 0 or not 1 <= capacity <= 4096 or storage == 0:
                raise ValueError("invalid trace version, binding, capacity or storage")
            self.header = epoch, sequence, capacity, storage, operators
            return
        if self.header is None:
            raise ValueError("trace record without trace header")
        epoch, sequence, capacity, storage, operators = self.header
        if key == WireKey.TRANSFER_TRACE_SPAN.wire:
            if len(fields) != 5:
                raise ValueError("expected five span fields")
            kind, index, start, end, status = fields
            index_number = _uint(index)
            if kind not in {"issue", "wait", "operator"}:
                raise ValueError("unknown trace span kind")
            if index_number >= (operators if kind == "operator" else units):
                raise ValueError("trace span id outside declared domain")
            identity = kind, index_number
            if identity in self.spans:
                raise ValueError("duplicate trace span")
            self.spans[identity] = TransferTraceSpan(
                kind,
                index_number,
                _optional(start),
                _optional(end),
                None if status == "x" else _signed(status),
            )
        elif key == WireKey.TRANSFER_TRACE_PIECE.wire:
            if len(fields) != 5 or len(self.pieces) >= capacity:
                raise ValueError("invalid piece fields or exceeded trace capacity")
            seq, ticket, byte_count, status, cycles = map(_uint, fields)
            if seq in self.pieces or ticket >= units or byte_count == 0:
                raise ValueError("duplicate or invalid completion identity")
            self.pieces[seq] = TransferCompletion(seq, ticket, byte_count, status, cycles)
        elif key == WireKey.TRANSFER_TRACE_END.wire:
            if len(fields) != 4 or fields[2] not in {"0", "1"} or fields[3] not in {"0", "1"}:
                raise ValueError("expected trace counts and clock/quiescence flags")
            observed, dropped = map(_uint, fields[:2])
            if observed != len(self.pieces) + dropped:
                raise ValueError("trace count contradicts retained/dropped records")
            self.result = TransferTrace(
                epoch,
                sequence,
                capacity,
                storage,
                operators,
                observed,
                dropped,
                fields[2] == "1",
                fields[3] == "1",
                tuple(self.spans.values()),
                tuple(self.pieces.values()),
            )
        else:
            raise ValueError("unknown trace key")

    def finish(
        self, *, elapsed: int, complete: bool, units: int, dma: int, issues: int
    ) -> TransferTrace:
        trace = self.result
        if trace is None:
            raise ValueError("missing trace end")
        if complete:
            validate_complete_trace(trace, elapsed=elapsed, units=units, dma=dma, issues=issues)
        return trace
