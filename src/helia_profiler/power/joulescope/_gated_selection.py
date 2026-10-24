"""Post-drain gate selection on retained sensor or host observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..completion import CounterCompletion, resolve_counter_completion
from ..counter_gate import CounterGateTrace, counter_gate_trace
from .stats import (
    _map_poll_samples_to_packet_time,
    _plan_ranked_candidates,
    _segment_gpi_windows,
    _select_streamed_window,
)


@dataclass(frozen=True)
class _GateSelection:
    aligned_poll_samples: list[tuple[int, int]]
    selected_windows: list[tuple[float, float]] | None
    streamed_gate_windows: list[tuple[float, float]] | None
    raw_streamed: list[tuple[float, float]]
    gate_edge_source: str
    counter_origin: tuple[int, int] | None
    counter_trace: CounterGateTrace | None
    counter_evidence: list[dict[str, Any]]
    gate_error: str | None
    fall_seen: bool
    use_device_time_axis: bool


def completed_gate_trace(
    *,
    packets: list[dict[str, Any]],
    gate_frames: list[dict[str, Any]],
    state_frames: list[dict[str, Any]],
    sync_input_index: int,
    state_input_index: int,
    gate_error: str | None,
    state_error: str | None,
) -> tuple[CounterGateTrace, CounterCompletion]:
    if gate_error or state_error:
        raise ValueError("Invalid retained completion stream")
    captured_packets = list(packets)
    gate = counter_gate_trace(list(gate_frames), captured_packets, channel=sync_input_index)
    state = counter_gate_trace(list(state_frames), captured_packets, channel=state_input_index)
    return gate, resolve_counter_completion(gate, state)


def select_gate(
    *,
    packets: list[dict[str, Any]],
    gpi_stream_frames: list[dict[str, Any]],
    state_frames: list[dict[str, Any]],
    poll_samples: list[tuple[int, int]],
    family: str,
    sync_input_index: int,
    state_input_index: int,
    statistics_timebase_hz: int,
    minimum_gate_s: float,
    plan_floor_s: float,
    elapsed_high_s: float | None,
    saw_any_gate_fall: bool,
    gpi_stream_enabled: bool,
    require_done_state: bool,
    gate_error: str | None,
    state_error: str | None,
) -> _GateSelection:
    aligned_poll_samples = poll_samples
    use_device_time_axis = family in ("js220", "js320")

    # Prefer instrument-clock gate edges from the GPI stream whenever it
    # is available. Invalid retained evidence forces an empty selection;
    # only an unavailable stream can use snapshot-poll segmentation.
    # A high open at the stop is the window the bound cut short: with a plan,
    # no shorter high stands in for it and it degrades as no_gate_fall.
    open_high = elapsed_high_s is not None and elapsed_high_s >= minimum_gate_s
    allow_fallback = not (open_high and plan_floor_s > minimum_gate_s)
    fall_seen = saw_any_gate_fall and allow_fallback
    streamed_gate_windows: list[tuple[float, float]] | None = None
    raw_streamed: list[tuple[float, float]] = []
    gate_edge_source = "gpi_snapshot_poll"
    counter_origin = None
    counter_trace = None
    counter_evidence: list[dict[str, Any]] = []
    if gpi_stream_enabled:
        try:
            if gate_error:
                raise ValueError(gate_error)
            # These sensors supply statistics and all signal sample IDs
            # from the same native counter. Do not infer another origin via UTC.
            if family not in ("js220", "js320"):
                raise ValueError("Shared GPI/statistics counter contract unavailable")
            counter_trace = counter_gate_trace(gpi_stream_frames, packets, channel=sync_input_index)
            counter_origin = (counter_trace.origin, counter_trace.rate)
            raw_streamed = [
                (
                    (a - counter_trace.origin) * statistics_timebase_hz / counter_trace.rate,
                    (b - counter_trace.origin) * statistics_timebase_hz / counter_trace.rate,
                )
                for a, b in counter_trace.windows
            ]
            if require_done_state:
                counter_trace, completed = completed_gate_trace(
                    packets=packets,
                    gate_frames=gpi_stream_frames,
                    state_frames=state_frames,
                    sync_input_index=sync_input_index,
                    state_input_index=state_input_index,
                    gate_error=gate_error,
                    state_error=state_error,
                )
                index = counter_trace.windows.index((completed.rise, completed.fall))
            else:
                selected = _select_streamed_window(
                    raw_streamed,
                    minimum_gate_s=minimum_gate_s,
                    plan_floor_s=plan_floor_s,
                    allow_fallback=allow_fallback,
                )
                if selected is None:
                    raise ValueError("No complete qualifying packed GPI gate")
                index = raw_streamed.index(selected[0])
            streamed_gate_windows = [raw_streamed[index]]
            counter_evidence.append(counter_trace.window_evidence(*counter_trace.windows[index]))
            gate_edge_source = "gpi_stream_sample_counter"
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            gate_error = str(exc)
            counter_origin = None
            streamed_gate_windows = []

    if use_device_time_axis and streamed_gate_windows is None:
        aligned_poll_samples = _map_poll_samples_to_packet_time(
            packets=packets,
            poll_samples=poll_samples,
            minimum_window_s=minimum_gate_s,
        )

    selected_windows = streamed_gate_windows
    if selected_windows is None and plan_floor_s > minimum_gate_s:
        # The poll samples can hold a high the plan rejected as well as the
        # real window: select by the stream's rule instead of summing both.
        polled = _plan_ranked_candidates(
            _segment_gpi_windows(aligned_poll_samples),
            minimum_gate_s=minimum_gate_s,
            plan_floor_s=plan_floor_s,
            allow_fallback=allow_fallback,
        )
        # An empty override forces the degraded path when an open window
        # withheld the fallback; otherwise no candidate keeps the legacy one.
        selected_windows = polled[-1:] if polled or not allow_fallback else None

    return _GateSelection(
        aligned_poll_samples=aligned_poll_samples,
        selected_windows=selected_windows,
        streamed_gate_windows=streamed_gate_windows,
        raw_streamed=raw_streamed,
        gate_edge_source=gate_edge_source,
        counter_origin=counter_origin,
        counter_trace=counter_trace,
        counter_evidence=counter_evidence,
        gate_error=gate_error,
        fall_seen=fall_seen,
        use_device_time_axis=use_device_time_axis,
    )
