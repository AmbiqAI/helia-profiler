"""Validate a complete transfer capture before publishing its cost curves."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..wire import WireKey
from .matrix import REPETITIONS, Cell, matrix, stats


def _numbers(payload: str, count: int) -> list[int]:
    fields = payload.split(",")
    if len(fields) != count or not all(f.isascii() and f.isdecimal() for f in fields):
        raise ValueError("Malformed transfer probe record")
    values = [int(f) for f in fields]
    if max(values) > 0xFFFFFFFF:
        raise ValueError("Transfer probe counter exceeds 32 bits")
    return values


def read_rows(
    lines: Sequence[str], cells: tuple[Cell, ...] | None = None
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    cells = matrix() if cells is None else cells
    rows: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    meta: dict[str, int] = {}
    finished = False
    for line in lines:
        if not line.startswith("HPX_XFER_"):
            continue
        tag, _, payload = line.strip().partition("=")
        if finished:
            raise ValueError("Unexpected transfer record after completion")
        if tag == WireKey.XFER_START.wire and not meta:
            cpu, psram, depth, slots, bracket, observer = _numbers(payload, 6)
            if not all((cpu, psram, depth, slots)):
                raise ValueError("Missing transfer clock or queue capacity")
            meta = dict(
                cpu_hz=cpu,
                psram_hz=psram,
                queue_depth=depth,
                provider_slots=slots,
                bracket_cycles=bracket,
                observer_call_cycles=observer,
            )
            continue
        if tag == WireKey.XFER_END.wire and meta and not samples and len(rows) == len(cells):
            if _numbers(payload, 1) != [len(rows)]:
                raise ValueError("Wrong terminal transfer row count")
            finished = True
            continue
        if not meta or len(rows) >= len(cells):
            raise ValueError("Unexpected transfer record outside the matrix")
        cell = cells[len(rows)]
        if tag == WireKey.XFER_FAIL.wire:
            i, status, detail = _numbers(payload, 3)
            failure = {
                1: "not_run",
                2: "issue_failed",
                3: "dma_timeout",
                4: "mismatch",
                5: "not_run",
                6: "not_run",
                7: "issue_failed",
                8: "not_run",
            }.get(status)
            if i != len(rows) or failure is None:
                raise ValueError("Unexpected failed transfer cell")
            reason = {
                1: "piece geometry",
                2: "provider issue",
                3: "provider wait or observer timeout",
                4: "destination verification",
                5: "foreground coverage",
                6: "provider capacity",
                7: "cache preparation",
                8: "source extent",
            }[status]
            rows.append(_row(cell, failure, f"{reason} ({detail})", []))
            samples = []
            continue
        if tag != WireKey.XFER_SAMPLE.wire:
            raise ValueError(f"Unexpected transfer probe tag {tag}")
        (
            i,
            rep,
            issue,
            complete,
            wait,
            arrival,
            floor,
            loops,
            alone,
            active,
            memory_alone,
            memory_active,
            *callbacks,
        ) = _numbers(payload, 12 + cell.pieces)
        if (i, rep) != (len(rows), len(samples)) or not complete or not cell.lengths:
            raise ValueError("Incomplete or unordered transfer timing")
        if cell.pieces:
            if not (
                loops
                and alone
                and active
                and memory_alone
                and memory_active
                and issue
                and wait
                and floor
                and arrival > 0
                and all(0 < offset <= issue + active for offset in callbacks)
            ):
                raise ValueError("Invalid DMA timing or foreground coverage")
        elif any((issue, wait, arrival, floor, loops, alone, active, memory_alone, memory_active)):
            raise ValueError("Synchronous CPU copy carries DMA timing")
        samples.append(
            dict(
                issue=issue,
                complete=complete,
                wait=wait,
                reader_arrival=arrival,
                ready_wait=floor,
                iterations=loops,
                alone=alone,
                active=active,
                memory_alone=memory_alone,
                memory_active=memory_active,
                callback_offsets=callbacks,
            )
        )
        if len(samples) == REPETITIONS:
            rows.append(_row(cell, "ok", None, samples))
            samples = []
    if not finished:
        raise ValueError("Transfer capture ended before the complete matrix")
    return rows, meta


def _row(
    cell: Cell, status: str, reason: str | None, samples: list[dict[str, Any]]
) -> dict[str, Any]:
    dma = bool(cell.pieces)
    row: dict[str, Any] = dict(
        source="psram",
        destination="dtcm",
        engine="dma" if dma else "cpu",
        implementation="aot_nsx_psram" if dma else "memcpy",
        cache="none" if dma else "on",
        cache_regime=cell.regime,
        bytes=cell.size,
        status=status,
        reason=reason,
        issue_cycles=None,
        complete_cycles=None,
        wait_cycles=None,
        inflight_core_loss_cycles=None,
        evidence=dict(
            source_offset=cell.source_offset,
            destination_offset=cell.offset,
            piece_bytes=list(cell.lengths),
            repetitions=samples,
        ),
    )
    if dma:
        row["pieces"] = cell.pieces
    if samples:
        row["complete_cycles"] = stats([s["complete"] for s in samples])
        if dma:
            row["issue_cycles"] = stats([s["issue"] for s in samples])
            row["wait_cycles"] = stats([s["wait"] for s in samples])
            row["inflight_core_loss_cycles"] = stats([s["active"] - s["alone"] for s in samples])
    return row
