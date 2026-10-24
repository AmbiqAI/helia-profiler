"""Gated measurement reduction and result assembly from drained capture evidence."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any

from ...errors import PowerError
from ..base import GatedPowerWindow, PowerResult, PowerSummary
from ..diagnostics import (
    BOOT_SETTLE_S,
    GateDurationIntegrity,
    GateFailure,
    GateTransitionTiming,
    classify_gate_failure,
    longest_accepted_window_s,
)
from ..metadata import MeasurementScope, PowerMetadata
from ._gated_selection import _GateSelection
from ._gated_state import _GateEvidence
from .diagnostics import (
    _attach_capture_diagnostics,
    _degraded_observation_result,
    _gated_stats_diagnostics,
    _poll_edge_uncertainty_s,
    _with_gpi_poll_failures,
)
from .stats import _counter_rate_ratio, _process_gated_stats

log = logging.getLogger("hpx")


@dataclass(frozen=True)
class _CaptureInfo:
    family: str
    device_path: str
    io_voltage: float
    sync_input_index: int
    stats_rate_hz: int
    scnt: int
    duration_s: float


@dataclass(frozen=True)
class _MeasurementPlan:
    minimum_gate_s: float
    plan_floor_s: float
    clean_infer_count: int | None
    clean_infer_avg_us: int | None
    gate_relative_tolerance: float
    work_noun: str
    lockstep: bool | None
    lockstep_wiring_available: bool
    pre_window_s: float


@dataclass(frozen=True)
class _ReducedMeasurement:
    windows: list[GatedPowerWindow]
    summary: PowerSummary
    diagnostics: dict[str, Any]
    failure: GateFailure | None = None
    integrity: GateDurationIntegrity | None = None


def reduce_measurement(
    *,
    packets: list[dict[str, Any]],
    selection: _GateSelection,
    evidence: _GateEvidence,
    info: _CaptureInfo,
    plan: _MeasurementPlan,
    gpi_stream_enabled: bool,
    gate_retained_bytes: int,
    gpi_poll_failures: int,
    wait_ended_at: float | None,
    fr_requested: bool,
    fr_xcheck: bool,
) -> _ReducedMeasurement:
    gating_diagnostics = _gated_stats_diagnostics(
        packets=packets,
        poll_samples=selection.aligned_poll_samples,
        prefer_device_time=selection.use_device_time_axis,
        windows_override=selection.selected_windows,
        gate_edge_source=selection.gate_edge_source,
        stream_segment_count=len(selection.raw_streamed) if gpi_stream_enabled else None,
        counter_origin=selection.counter_origin,
    )
    if fr_requested and not fr_xcheck:
        gating_diagnostics["fullrate_xcheck_unavailable_reason"] = "stream_setup_failed"
    if gpi_poll_failures:
        gating_diagnostics["gpi_poll_failures"] = gpi_poll_failures
    if evidence.below_plan_highs:
        gating_diagnostics["below_plan_highs"] = evidence.below_plan_highs
    if selection.gate_edge_source == "gpi_snapshot_poll":
        gating_diagnostics["poll_edge_uncertainty_s"] = _poll_edge_uncertainty_s(
            list(evidence.poll_reads),
            minimum_window_s=plan.minimum_gate_s,
            plan_floor_s=plan.plan_floor_s,
        )

    gating_diagnostics["packed_gpi_retained_bytes"] = gate_retained_bytes
    gating_diagnostics["packed_gpi_error"] = selection.gate_error
    gating_diagnostics["counter_gate_evidence"] = selection.counter_evidence
    if selection.counter_trace is not None:
        gating_diagnostics["counter_clock_mapping"] = selection.counter_trace.clock_diagnostics
    # The filter that scales utc, read straight from the packets (#249).
    counter_rate = _counter_rate_ratio(packets)
    if counter_rate is not None:
        gating_diagnostics["instrument_time_map"] = counter_rate

    windows, gated_summary = _process_gated_stats(
        packets=packets,
        poll_samples=selection.aligned_poll_samples,
        io_voltage=info.io_voltage,
        prefer_device_time=selection.use_device_time_axis,
        minimum_window_s=plan.minimum_gate_s,
        windows_override=selection.selected_windows,
        counter_origin=selection.counter_origin,
    )
    if not windows:
        if fr_requested:
            gating_diagnostics.setdefault(
                "fullrate_xcheck_unavailable_reason", "no_integrable_gate_samples"
            )
        planned_window_s = (
            plan.clean_infer_count * plan.clean_infer_avg_us / 1_000_000
            if plan.clean_infer_count and plan.clean_infer_avg_us
            else None
        )
        longest_window_s = (
            longest_accepted_window_s(
                clean_infer_count=plan.clean_infer_count,
                clean_infer_avg_us=plan.clean_infer_avg_us,
                stats_rate_hz=info.stats_rate_hz,
                relative_tolerance=plan.gate_relative_tolerance,
            )
            if plan.clean_infer_count and plan.clean_infer_avg_us
            else None
        )
        gate_high_s = (
            max(0.0, wait_ended_at - evidence.first_high_at)
            if wait_ended_at is not None and evidence.first_high_at is not None
            else None
        )
        # Without lock-step the wait starts at reset, so the window opens
        # only after boot and warm-up.
        rise_due_s = BOOT_SETTLE_S + plan.pre_window_s if plan.lockstep is False else None
        failure = classify_gate_failure(
            saw_gate_rise=evidence.saw_any_gate_rise,
            saw_gate_fall=selection.fall_seen,
            duration_s=info.duration_s,
            lockstep=plan.lockstep,
            lockstep_wiring_available=plan.lockstep_wiring_available,
            planned_window_s=planned_window_s,
            gate_high_s=gate_high_s,
            longest_window_s=longest_window_s,
            rise_due_s=rise_due_s,
        )
        failure = _with_gpi_poll_failures(failure, gpi_poll_failures)
        if not packets:
            raise PowerError(failure.message, hint=failure.hint)
        return _ReducedMeasurement(windows, gated_summary, gating_diagnostics, failure=failure)
    if evidence.short_pulses_ignored:
        log.warning(
            "Ignored %d GPIO-high pulse(s) shorter than the %.3fs minimum "
            "before the valid power gate (phases=%s, first=%.3fs, last=%.3fs)",
            evidence.short_pulses_ignored,
            plan.minimum_gate_s,
            dict(evidence.short_pulse_phases),
            evidence.short_pulse_first_s or 0.0,
            evidence.short_pulse_last_s or 0.0,
        )

    gate_integrity = None
    if (
        plan.clean_infer_count is not None
        and plan.clean_infer_count > 0
        and plan.clean_infer_avg_us is not None
        and plan.clean_infer_avg_us > 0
    ):
        from ..diagnostics import assess_gate_duration

        gate_integrity = assess_gate_duration(
            measured_s=gated_summary.duration_s,
            clean_infer_count=plan.clean_infer_count,
            clean_infer_avg_us=plan.clean_infer_avg_us,
            stats_rate_hz=info.stats_rate_hz,
            minimum_s=plan.minimum_gate_s,
            relative_tolerance=plan.gate_relative_tolerance,
        )
        if not gate_integrity.valid:
            # Deliberately NOT a raise (#142/#181): the est*count
            # expectation straddles boots and thermal states, so a healthy
            # cold boot can undershoot it. evaluation.validity arbitrates
            # with the firmware's own STIMER window (same boot, same
            # physical window); raising here would discard the capture
            # before its best evidence arrives and leave no artifact.
            log.warning(
                "Gated window duration %.6fs differs from the planned "
                "%d %s at %dus each (expected %.6fs, band %.6fs, "
                "minimum %.3fs). Recording for validity evaluation; the "
                "power-terminal envelope arbitrates whether this is "
                "clock drift or a broken gate.",
                gate_integrity.measured_s,
                plan.clean_infer_count,
                plan.work_noun,
                plan.clean_infer_avg_us,
                gate_integrity.expected_s,
                gate_integrity.tolerance_s,
                plan.minimum_gate_s,
            )

    return _ReducedMeasurement(windows, gated_summary, gating_diagnostics, integrity=gate_integrity)


def assemble_result(
    *,
    packets: list[dict[str, Any]],
    gpi_stream_frames: list[dict[str, Any]],
    state_frames: list[dict[str, Any]],
    state_error: str | None,
    selection: _GateSelection,
    reduced: _ReducedMeasurement,
    evidence: _GateEvidence,
    info: _CaptureInfo,
    plan: _MeasurementPlan,
    capture_start: float,
    go_release_at: float | None,
    captured_s: float,
    statistics_timebase_hz: int,
    fr_xcheck: bool,
    fr: dict[str, Any] | None,
    reason: str | None,
    fullrate_chunks: int,
    fullrate_anchors: int,
) -> PowerResult:
    first_high_at = evidence.first_high_at
    first_low_after_high_at = evidence.first_low_after_high_at
    below_plan_rise_at = evidence.below_plan_rise_at
    below_plan_fall_at = evidence.below_plan_fall_at
    if reduced.failure is not None:
        result = _degraded_observation_result(
            packets=packets,
            family=info.family,
            device_path=info.device_path,
            io_voltage=info.io_voltage,
            sync_input_index=info.sync_input_index,
            stats_rate_hz=info.stats_rate_hz,
            scnt=info.scnt,
            poll_count=len(evidence.poll_samples),
            duration_s=info.duration_s,
            captured_s=captured_s,
            saw_gate_rise=evidence.saw_any_gate_rise,
            saw_gate_fall=selection.fall_seen,
            short_pulses_ignored=evidence.short_pulses_ignored,
            failure=reduced.failure,
            gating_diagnostics=reduced.diagnostics,
        )
        # The hint is logged, not just stored in metadata: on the degraded
        # path there is no PowerError to carry it, so the terminal warning
        # is the only place the user is told what to change (issue #114).
        log.warning(
            "%s; retaining %.3fs free-form capture for post-run diagnostics. %s",
            reduced.failure.message,
            result.summary.duration_s,
            reduced.failure.hint,
        )
        return replace(
            result,
            gate_frames=gpi_stream_frames,
            gate_stream_error=selection.gate_error,
            gate_samples=selection.aligned_poll_samples,
            statistics_timebase_hz=int(statistics_timebase_hz),
            statistics_device_time=selection.use_device_time_axis,
            state_frames=state_frames,
            state_stream_error=state_error,
        )
    metadata = PowerMetadata(
        driver=f"joulescope-{info.family}",
        device=info.device_path,
        io_voltage=info.io_voltage,
        measurement_scope=MeasurementScope.GPIO_GATED_CLEAN_WINDOW,
        gating_method=f"{selection.gate_edge_source}+host_stats_integral",
        sync_input_index=info.sync_input_index,
        stats_rate_hz=info.stats_rate_hz,
        stats_scnt=info.scnt,
        window_count=len(reduced.windows),
        gpi_poll_count=len(evidence.poll_samples),
        stat_packets=len(packets),
        early_stopped=evidence.early_stopped,
        capture_window_s=round(captured_s, 4),
        capture_safety_bound_s=info.duration_s,
        short_gate_pulses_ignored=evidence.short_pulses_ignored,
    )
    if evidence.short_pulses_ignored:
        metadata.short_gate_pulse_diagnostics = {
            "by_phase": dict(evidence.short_pulse_phases),
            "first_rise_s": round(evidence.short_pulse_first_s or 0.0, 6),
            "last_rise_s": round(evidence.short_pulse_last_s or 0.0, 6),
        }
    if first_high_at is None and below_plan_rise_at is not None:
        # No high reached the plan, so the fallback kept the last shorter
        # one: report its edges.
        first_high_at, first_low_after_high_at = below_plan_rise_at, below_plan_fall_at
    gate_timing = GateTransitionTiming(
        capture_to_gate_rise_s=(
            round(first_high_at - capture_start, 6) if first_high_at is not None else None
        ),
        capture_to_gate_fall_s=(
            round(first_low_after_high_at - capture_start, 6)
            if first_low_after_high_at is not None
            else None
        ),
        go_release_to_gate_rise_s=(
            round(first_high_at - go_release_at, 6)
            if go_release_at is not None and first_high_at is not None
            else None
        ),
    )
    if gate_timing.to_metadata():
        metadata.sync_timing_s = gate_timing
    if plan.clean_infer_count is not None:
        metadata.clean_infer_count = plan.clean_infer_count
    if reduced.integrity is not None:
        metadata.gate_duration_integrity = replace(
            reduced.integrity, relative_tolerance=plan.gate_relative_tolerance
        )
    if fr_xcheck:
        if reason is not None:
            reduced.diagnostics["fullrate_xcheck_unavailable_reason"] = reason
        if fr:
            metadata.fullrate_xcheck = fr
            stats_energy_per = (
                reduced.summary.energy_j / len(reduced.windows) if reduced.windows else 0.0
            )
            fr_energy_per = fr["energy_per_window_j"]
            log.info(
                "Joulescope FULL-RATE xcheck: mean=%.3f mA, power=%.3f mW, "
                "energy/window=%.1f uJ over %.2f ms @ %.0f Hz (%d samples) | "
                "stats-sum: mean=%.3f mA, energy/window=%.1f uJ | ratio(full/stats)=%.2fx",
                fr["mean_current_a"] * 1000.0,
                fr["mean_power_w"] * 1000.0,
                fr_energy_per * 1e6,
                fr["duration_s"] / max(1, fr["window_count"]) * 1000.0,
                fr["sample_rate_hz"],
                fr["sample_count"],
                reduced.summary.avg_current_a * 1000.0,
                stats_energy_per * 1e6,
                (fr_energy_per / stats_energy_per) if stats_energy_per else float("nan"),
            )
        else:
            reduced.diagnostics.setdefault(
                "fullrate_xcheck_unavailable_reason", "no_integrable_gate_samples"
            )
            log.warning(
                "Full-rate cross-check requested but produced no result (chunks=%d, anchors=%d)",
                fullrate_chunks,
                fullrate_anchors,
            )
    _attach_capture_diagnostics(
        metadata, packets, reduced.summary, reduced.diagnostics, info.stats_rate_hz
    )

    log.info(
        "Joulescope gated: windows=%d, gated_dur=%.3f ms, energy=%.6f J, "
        "%d stat packets @ ~%d Hz (%s)",
        len(reduced.windows),
        reduced.summary.duration_s * 1000.0,
        reduced.summary.energy_j,
        len(packets),
        info.stats_rate_hz,
        info.family.upper(),
    )

    return PowerResult(
        summary=reduced.summary,
        gated_windows=reduced.windows,
        metadata=metadata,
        state_frames=state_frames,
        state_stream_error=state_error,
        statistics=packets,
        gate_samples=selection.aligned_poll_samples,
        statistics_timebase_hz=int(statistics_timebase_hz),
        statistics_device_time=selection.use_device_time_axis,
        gate_frames=gpi_stream_frames,
        gate_stream_error=selection.gate_error,
        counter_gate_evidence=selection.counter_evidence,
        counter_domain="joulescope_sensor_sample_id" if selection.counter_evidence else None,
    )
