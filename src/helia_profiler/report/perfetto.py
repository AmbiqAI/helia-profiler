"""Offline presentation of recorded software intervals and completion notifications."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..readers.transfer import read_transfer_traces
from ..results.transfer import TransferDiagnostic

if TYPE_CHECKING:
    from ..readers.native import ProfileResultRecord
    from ..runs.record import RunResultRecord

_TIME_BASIS = "Independent invocation-relative CPU offsets; groups do not share a wall clock."
_NOTIFICATION_BASIS = (
    "Observer-entry notifications include queue/IRQ effects, not physical bus service."
)


def _thread_id(pid: int, lane: int) -> int:
    return pid * 4 + lane + 1


def _trace_events(
    record: TransferDiagnostic, names: dict[int, str], pid: int
) -> list[dict[str, Any]]:
    trace = record.trace
    assert trace is not None
    events: list[dict[str, Any]] = [
        {
            "ph": "M",
            "name": "process_name",
            "pid": pid,
            "tid": _thread_id(pid, 0),
            "args": {
                "name": f"iteration {record.iteration}, epoch {trace.binding_epoch} (independent origin)"
            },
        }
    ]
    for tid, name in enumerate(
        ("Invocation", "Foreground calls", "Completion notifications", "Trace warnings")
    ):
        events.append(
            {
                "ph": "M",
                "name": "thread_name",
                "pid": pid,
                "tid": _thread_id(pid, tid),
                "args": {"name": name},
            }
        )

    def instant(name: str, tid: int, cycles: int, args: dict[str, Any]) -> None:
        events.append(
            {
                "ph": "I",
                "s": "t",
                "cat": "hpx.transfer",
                "name": name,
                "pid": pid,
                "tid": _thread_id(pid, tid),
                "ts": cycles * 1_000_000 / record.cpu_hz,
                "args": {"cycles": cycles, **args},
            }
        )

    def interval(name: str, tid: int, start: int, end: int, args: dict[str, Any]) -> None:
        events.append(
            {
                "ph": "X",
                "cat": "hpx.transfer",
                "name": name,
                "pid": pid,
                "tid": _thread_id(pid, tid),
                "ts": start * 1_000_000 / record.cpu_hz,
                "dur": (end - start) * 1_000_000 / record.cpu_hz,
                "args": {"start_cycles": start, "end_cycles": end, **args},
            }
        )

    interval(
        "Invocation" if record.complete else "INCOMPLETE invocation",
        0,
        0,
        record.elapsed_cycles,
        {
            "iteration": record.iteration,
            "binding_epoch": trace.binding_epoch,
            "cpu_hz": record.cpu_hz,
            "preparation": record.preparation,
            "complete": record.complete,
            "run_status": record.run_status,
            "observed_count": trace.observed_count,
            "dropped_count": trace.dropped_count,
            "capacity": trace.capacity,
            "storage_bytes": trace.storage_bytes,
            "clock_valid": trace.clock_valid,
            "quiescent": trace.quiescent,
            "sequence_start": trace.sequence_start,
            "operator_count": trace.operator_count,
            "time_basis": _TIME_BASIS,
            "notification_basis": _NOTIFICATION_BASIS,
        },
    )
    if not record.complete:
        instant(
            f"INCOMPLETE: observed {trace.observed_count}, dropped {trace.dropped_count}",
            3,
            0,
            {
                "complete": False,
                "run_status": record.run_status,
                "quiescent": trace.quiescent,
            },
        )
    for span in sorted(
        trace.spans,
        key=lambda s: (
            s.start_cycles if s.start_cycles is not None else s.end_cycles or 0,
            s.end_cycles if s.end_cycles is not None else s.start_cycles or 0,
        ),
    ):
        name = (
            f"operator {span.id}: {names.get(span.id, 'unnamed')}"
            if span.kind == "operator"
            else f"{span.kind} unit {span.id}"
        )
        if span.status not in (None, 0):
            name = "FAILED " + name
        args = {"kind": span.kind, "id": span.id, "status": span.status}
        if span.start_cycles is not None and span.end_cycles is not None:
            interval(name, 1, span.start_cycles, span.end_cycles, args)
        else:
            for boundary, cycles in (("start", span.start_cycles), ("end", span.end_cycles)):
                if cycles is not None:
                    instant(
                        f"UNPAIRED {name} {boundary}", 1, cycles, {**args, "boundary": boundary}
                    )
    for piece in trace.completions:
        instant(
            "Completion notification" if piece.status == 0 else "FAILED completion notification",
            2,
            piece.cycles,
            {
                "sequence": piece.sequence,
                "ticket": piece.ticket,
                "bytes": piece.byte_count,
                "status": piece.status,
                "binding_epoch": trace.binding_epoch,
            },
        )
    return events


def export_transfer_trace(
    record: ProfileResultRecord | RunResultRecord, destination: str | Path
) -> Path:
    """Experimentally export a verified native profile's chronological traces.

    Each invocation has an independent origin. Incomplete records stay visibly
    incomplete; clocks or known boundaries that cannot be represented are refused.
    Existing destinations are never overwritten, and capture is never invoked.
    """
    saved = read_transfer_traces(record)
    source = saved.source
    events = []
    for pid, diagnostic in enumerate(saved.diagnostics, 1):
        invocation = _trace_events(diagnostic, dict(saved.operator_names), pid)
        for event in invocation:
            if event.get("ph") == "X" and "complete" in event["args"]:
                event["args"].update(
                    source_status=source.manifest.status.value,
                    source_validity=source.manifest.validity.value,
                    run_id=source.manifest.run_id,
                    manifest_sha256=source.manifest_sha256,
                    artifact_sha256=saved.artifact.sha256,
                )
        if source.manifest.status.value != "complete" or source.manifest.validity.value != "valid":
            invocation.append(
                {
                    "ph": "I",
                    "s": "t",
                    "cat": "hpx.transfer",
                    "pid": pid,
                    "tid": _thread_id(pid, 3),
                    "ts": 0,
                    "name": f"SOURCE {source.manifest.status.value}/{source.manifest.validity.value}",
                    "args": {"issues": [i.to_dict() for i in source.manifest.issues]},
                }
            )
        events.extend(invocation)
    payload = {
        "traceEvents": events,
        "displayTimeUnit": "ns",
        "otherData": {
            "hpx": {
                "time_basis": _TIME_BASIS,
                "notification_basis": _NOTIFICATION_BASIS,
                "native_context_limit": "Native v1 omits wire total-unit and DMA-unit counts; validation uses recorded slots and accounting.",
                "manifest_sha256": source.manifest_sha256,
                "artifact_sha256": saved.artifact.sha256,
                "run_id": source.manifest.run_id,
                "status": source.manifest.status.value,
                "validity": source.manifest.validity.value,
                "issues": [i.to_dict() for i in source.manifest.issues],
                "provenance": source.manifest.provenance,
                "metadata": source.metadata,
            }
        },
    }
    serialized = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    path = Path(destination)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(serialized)
    return path
