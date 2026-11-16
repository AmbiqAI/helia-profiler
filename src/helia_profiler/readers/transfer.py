"""Strict saved-native transfer traces, separate from portable observations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..results.manifest import RESULT_PUBLICATION_INCOMPLETE, ResultArtifact
from ..results.transfer import (
    TransferCompletion,
    TransferDiagnostic,
    TransferStats,
    TransferTrace,
    TransferTraceSpan,
)
from ..results.transfer_validation import (
    validate_complete_trace,
    validate_trace_accounting,
    validate_trace_order,
)
from .native import ProfileResultRecord, ResultReadError, _document, _schema, load_result

if TYPE_CHECKING:
    from ..runs.record import RunResultRecord


@dataclass(frozen=True)
class TransferTraceRecord:
    source: ProfileResultRecord
    artifact: ResultArtifact
    operator_names: tuple[tuple[int, str], ...]
    diagnostics: tuple[TransferDiagnostic, ...]


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("expected a transfer object")
    return value


def _array(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError("expected a transfer array")
    return value


def _uint(value: Any, *, minimum: int = 0, maximum: int = 0xFFFFFFFF) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"expected an integer in {minimum}..{maximum}")
    return value


def _bool(value: Any) -> bool:
    if type(value) is not bool:
        raise ValueError("expected a transfer boolean")
    return value


def _optional_uint(value: Any) -> int | None:
    return None if value is None else _uint(value)


def _status(value: Any) -> int | None:
    return None if value is None else _uint(value, minimum=-(1 << 31), maximum=(1 << 31) - 1)


def _span(value: Any) -> TransferTraceSpan:
    data = _object(value)
    kind = data.get("kind")
    if kind not in ("operator", "issue", "wait"):
        raise ValueError("unknown transfer span kind")
    return TransferTraceSpan(
        kind,
        _uint(data.get("id")),
        _optional_uint(data.get("start_cycles")),
        _optional_uint(data.get("end_cycles")),
        _status(data.get("status")),
    )


def _trace(value: Any) -> TransferTrace:
    data = _object(value)
    _schema(data, "hpx.transfer-trace", 1)
    pieces = []
    for value in _array(data.get("completions")):
        piece = _object(value)
        pieces.append(
            TransferCompletion(
                _uint(piece.get("sequence")),
                _uint(piece.get("ticket")),
                _uint(piece.get("byte_count"), minimum=1),
                _uint(piece.get("status")),
                _uint(piece.get("cycles")),
            )
        )
    trace = TransferTrace(
        _uint(data.get("binding_epoch"), minimum=1),
        _uint(data.get("sequence_start")),
        _uint(data.get("capacity"), minimum=1, maximum=4096),
        _uint(data.get("storage_bytes"), minimum=1),
        _uint(data.get("operator_count")),
        _uint(data.get("observed_count")),
        _uint(data.get("dropped_count")),
        _bool(data.get("clock_valid")),
        _bool(data.get("quiescent")),
        tuple(_span(s) for s in _array(data.get("spans"))),
        tuple(pieces),
    )
    if (
        len(pieces) > trace.capacity
        or trace.observed_count != len(pieces) + trace.dropped_count
        or len({p.sequence for p in pieces}) != len(pieces)
        or len({(s.kind, s.id) for s in trace.spans}) != len(trace.spans)
        or any(s.kind == "operator" and s.id >= trace.operator_count for s in trace.spans)
    ):
        raise ValueError("contradictory transfer counts or identities")
    return trace


def _diagnostic(value: Any, clock: int, layer_ids: set[int]) -> TransferDiagnostic:
    data = _object(value)
    if data.get("purpose") != "trace" or data.get("preparation") not in ("cold", "configured"):
        raise ValueError("expected chronological transfer diagnostics")
    if data.get("units", []) != [] or data.get("overlap") is not None:
        raise ValueError("non-trace observations in a chronological diagnostic")
    stats = _object(data.get("stats"))
    record = TransferDiagnostic(
        "trace",
        _uint(data.get("iteration")),
        _uint(data.get("cpu_hz"), minimum=1),
        data["preparation"],
        _uint(data.get("elapsed_cycles"), minimum=1),
        _uint(data.get("run_status"), minimum=-(1 << 31), maximum=(1 << 31) - 1),
        _bool(data.get("complete")),
        stats=TransferStats(
            *(
                _uint(stats.get(k))
                for k in (
                    "issues",
                    "waits",
                    "aborts",
                    "peak_in_flight",
                    "wait_checks_max",
                    "unsampled",
                )
            )
        ),
        trace=_trace(data.get("trace")),
    )
    trace = record.trace
    assert trace is not None and record.stats is not None
    if record.cpu_hz != clock or not trace.clock_valid:
        raise ValueError("trace clock is invalid or differs from the recorded firmware clock")
    for span in trace.spans:
        if any(
            v is not None and v > record.elapsed_cycles
            for v in (span.start_cycles, span.end_cycles)
        ) or (
            span.start_cycles is not None
            and span.end_cycles is not None
            and span.start_cycles > span.end_cycles
        ):
            raise ValueError("trace boundary lies outside its invocation or runs backwards")
    if any(p.cycles > record.elapsed_cycles for p in trace.completions):
        raise ValueError("completion lies outside its invocation")
    if record.complete:
        issue_ids = sorted(s.id for s in trace.spans if s.kind == "issue")
        waits = [s for s in trace.spans if s.kind == "wait"]
        if (
            record.run_status != 0
            or not issue_ids
            or not waits
            or any(i != value for i, value in enumerate(issue_ids))
            or len(layer_ids) != trace.operator_count
            or any(i != value for i, value in enumerate(sorted(layer_ids)))
        ):
            raise ValueError("complete trace contradicts run status or recorded domains")
        # Native v1 does not retain the wire header's unit counts: these are observed slots.
        validate_trace_accounting(record.stats, len(waits))
        validate_complete_trace(
            trace,
            elapsed=record.elapsed_cycles,
            units=len(issue_ids),
            dma=len(waits),
            issues=record.stats.issues,
        )
    else:
        validate_trace_order(trace)
    return record


def decode_transfer_diagnostics(document: dict[str, Any]) -> tuple[TransferDiagnostic, ...]:
    """Decode the trace-bearing native table; integrity is the saved reader's responsibility."""
    try:
        values = _array(document.get("transfer_diagnostics", []))
        if not values or not any(_object(v).get("purpose") == "trace" for v in values):
            raise ResultReadError(
                "no_transfer_trace", "No chronological transfer diagnostics recorded"
            )
        clock = _uint(_object(document.get("summary")).get("system_clock_hz"), minimum=1)
        layers = [_object(value) for value in _array(document.get("layers"))]
        ids = [_uint(layer.get("id")) for layer in layers]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate recorded layer identity")
        records = tuple(_diagnostic(value, clock, set(ids)) for value in values)
        profiling = _object(_object(document.get("metadata", {})).get("config", {})).get(
            "profiling", {}
        )
        profiling = _object(profiling)
        if "transfer_diagnostics" in profiling and profiling["transfer_diagnostics"] != "trace":
            raise ValueError("trace differs from the recorded diagnostic mode")
        if "transfer_diagnostic_iterations" in profiling:
            count = _uint(profiling["transfer_diagnostic_iterations"], minimum=1)
            if len(records) != count or any(r.iteration != i for i, r in enumerate(records)):
                raise ValueError("trace differs from the recorded diagnostic iterations")
        if "transfer_trace_max_pieces" in profiling:
            capacity = _uint(profiling["transfer_trace_max_pieces"], minimum=1, maximum=4096)
            if any(r.trace is None or r.trace.capacity != capacity for r in records):
                raise ValueError("trace differs from the recorded capacity")
        if "stream_evidence" in profiling:
            preparation = "cold" if _bool(profiling["stream_evidence"]) else "configured"
            if any(r.preparation != preparation for r in records):
                raise ValueError("trace differs from the recorded preparation")
        if len({r.iteration for r in records}) != len(records) or len(
            {r.trace.binding_epoch for r in records if r.trace}
        ) != len(records):
            raise ValueError("repeated transfer invocation or binding epoch")
        return records
    except (TypeError, ValueError) as exc:
        raise ResultReadError("invalid_transfer_trace", str(exc)) from exc


def read_transfer_traces(record: ProfileResultRecord | RunResultRecord) -> TransferTraceRecord:
    """Recheck the saved source before admitting its native chronology."""
    try:
        return _read_transfer_traces(record)
    except (TypeError, ValueError) as exc:
        raise ResultReadError("invalid_transfer_trace", str(exc)) from exc


def _read_transfer_traces(record: ProfileResultRecord | RunResultRecord) -> TransferTraceRecord:
    if not isinstance(record, ProfileResultRecord) or record.manifest.bundle_type != "profile":
        raise ResultReadError(
            "unsupported_kind", "Perfetto requires a native per-layer profile bundle"
        )
    fresh = load_result(record.manifest_path)
    if (
        not isinstance(fresh, ProfileResultRecord)
        or fresh.manifest_sha256 != record.manifest_sha256
    ):
        raise ResultReadError("publication_changed", "Native manifest changed since it was loaded")
    selected = [
        a
        for a in fresh.manifest.artifacts
        if a.name == "hpx.profile-layers" and a.media_type == "application/json"
    ]
    if len(selected) != 1:
        raise ResultReadError("no_transfer_trace", "Expected one native JSON profile table")
    artifact = selected[0]
    data = _document(fresh.location, artifact)
    _schema(data, "hpx.profile-results", 1)
    metadata = _object(data.get("metadata"))
    if fresh.metadata is None or any(fresh.metadata.get(k) != v for k, v in metadata.items()):
        raise ResultReadError("identity_mismatch", "Profile table metadata differs from its bundle")
    if (
        metadata.get("run_id") != fresh.manifest.run_id
        or metadata.get("hpx_version") != fresh.manifest.hpx_version
    ):
        raise ResultReadError("identity_mismatch", "Profile table has a different run identity")
    diagnostics = decode_transfer_diagnostics(data)
    clock = _object(data.get("summary")).get("system_clock_hz")
    firmware = fresh.metadata.get("firmware", {})
    if isinstance(firmware, dict) and firmware.get("system_clock_hz", clock) != clock:
        raise ResultReadError("identity_mismatch", "Profile table has a different firmware clock")
    layers = _array(data.get("layers"))
    if fresh.summary is not None and fresh.summary.layers != len(layers):
        raise ResultReadError(
            "identity_mismatch", "Profile table layer count differs from its summary"
        )
    names = tuple((layer["id"], layer.get("op") or f"operator {layer['id']}") for layer in layers)
    if any(not isinstance(name, str) for _, name in names):
        raise ResultReadError("invalid_transfer_trace", "Invalid operator label")
    marker = fresh.location / RESULT_PUBLICATION_INCOMPLETE
    raw = fresh.manifest_path.read_bytes()
    if (
        marker.exists()
        or marker.is_symlink()
        or hashlib.sha256(raw).hexdigest() != fresh.manifest_sha256
    ):
        raise ResultReadError(
            "publication_changed", "Native publication changed while reading its trace"
        )
    return TransferTraceRecord(fresh, artifact, names, diagnostics)
