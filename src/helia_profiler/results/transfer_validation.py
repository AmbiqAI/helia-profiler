"""Chronological rules shared by wire capture and saved transfer evidence."""

from __future__ import annotations

from .transfer import TransferStats, TransferTrace


def validate_trace_accounting(stats: TransferStats, dma: int) -> None:
    if stats.waits != dma or stats.aborts != 0 or stats.unsampled != 0 or stats.peak_in_flight == 0:
        raise ValueError("complete trace contradicts provider accounting")


def fresh_binding_peak_valid(stats: TransferStats | None) -> bool:
    return stats is not None and stats.peak_in_flight <= stats.issues


def validate_complete_trace(
    trace: TransferTrace, *, elapsed: int, units: int, dma: int, issues: int
) -> None:
    if (
        not trace.clock_valid
        or not trace.quiescent
        or trace.dropped_count
        or trace.observed_count != issues
        or issues == 0
    ):
        raise ValueError("complete trace contradicts clock, lifecycle or piece counts")
    kinds = {kind: [] for kind in ("issue", "wait", "operator")}
    intervals: list[tuple[str, int, int, int]] = []
    for span in trace.spans:
        kinds[span.kind].append(span.id)
        start, end = span.start_cycles, span.end_cycles
        if start is None or end is None or not 0 <= start <= end <= elapsed or span.status != 0:
            raise ValueError("complete trace has unpaired, failed or out-of-span boundaries")
        intervals.append((span.kind, span.id, start, end))
    if (
        len(kinds["issue"]) != units
        or len(kinds["wait"]) != dma
        or len(kinds["operator"]) != trace.operator_count
    ):
        raise ValueError("complete trace has missing boundaries")
    validate_trace_observations(trace)
    offsets = sorted((p.sequence - trace.sequence_start) & 0xFFFFFFFF for p in trace.completions)
    if any(offset != index for index, offset in enumerate(offsets)):
        raise ValueError("complete trace has missing piece sequences")
    issue_spans = {s[1]: s for s in intervals if s[0] == "issue"}
    wait_spans = {s[1]: s for s in intervals if s[0] == "wait"}
    for unit, wait in wait_spans.items():
        issue = issue_spans.get(unit)
        if issue is None:
            raise ValueError("complete trace waits before unit issue returns")
    if {piece.ticket for piece in trace.completions} != set(wait_spans):
        raise ValueError("complete trace has no completion for a waited DMA unit")
    for piece in trace.completions:
        issue = issue_spans.get(piece.ticket)
        wait = wait_spans.get(piece.ticket)
        if piece.status != 0 or piece.cycles > elapsed or issue is None or wait is None:
            raise ValueError("complete trace has failed or causally inconsistent completion")


def validate_trace_observations(trace: TransferTrace) -> None:
    """Use known endpoints as constraints without supplying missing durations."""
    intervals: list[tuple[str, int, int, int]] = []
    for span in trace.spans:
        bounds = [v for v in (span.start_cycles, span.end_cycles) if v is not None]
        if bounds:
            intervals.append((span.kind, span.id, min(bounds), max(bounds)))
    ordered = sorted(intervals, key=lambda span: (span[2], span[3]))
    if any(previous[3] > following[2] for previous, following in zip(ordered, ordered[1:])):
        raise ValueError("trace overlaps sequential foreground calls")
    operators = sorted((s for s in intervals if s[0] == "operator"), key=lambda s: s[1])
    if any(previous[3] > following[2] for previous, following in zip(operators, operators[1:])):
        raise ValueError("trace reverses operator execution positions")
    if any(
        previous.cycles > following.cycles
        for previous, following in zip(trace.completions, trace.completions[1:])
    ):
        raise ValueError("trace reverses completion notification time")

    validate_known_trace_causes(trace)


def validate_known_trace_causes(trace: TransferTrace) -> None:
    issues = {s.id: s for s in trace.spans if s.kind == "issue"}
    waits = {s.id: s for s in trace.spans if s.kind == "wait"}
    for unit, wait in waits.items():
        issue = issues.get(unit)
        if (
            issue is not None
            and issue.end_cycles is not None
            and wait.start_cycles is not None
            and issue.end_cycles > wait.start_cycles
        ):
            raise ValueError("trace waits before unit issue returns")
    for piece in trace.completions:
        issue, wait = issues.get(piece.ticket), waits.get(piece.ticket)
        if (
            issue is not None
            and issue.start_cycles is not None
            and piece.cycles < issue.start_cycles
        ) or (
            wait is not None
            and wait.status == 0
            and wait.end_cycles is not None
            and piece.cycles > wait.end_cycles
        ):
            raise ValueError("trace has causally inconsistent completion")

    # Accepted sequences follow submission order: https://github.com/AmbiqAI/helia-aot/issues/1008.
    earliest_acceptance = 0
    for piece in sorted(
        trace.completions, key=lambda p: (p.sequence - trace.sequence_start) & 0xFFFFFFFF
    ):
        issue = issues.get(piece.ticket)
        if issue is not None and issue.start_cycles is not None:
            earliest_acceptance = max(earliest_acceptance, issue.start_cycles)
        latest_acceptance = piece.cycles
        if issue is not None and issue.end_cycles is not None:
            latest_acceptance = min(latest_acceptance, issue.end_cycles)
        if earliest_acceptance > latest_acceptance:
            raise ValueError("trace sequence contradicts issue windows")
