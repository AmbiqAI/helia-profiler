"""Centralized correctness policy for completed profiling runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..power.diagnostics import (
    assess_clean_window_clock_rate,
    assess_clean_window_stall,
    assess_gate_duration,
    assess_run_window_clock,
    expected_terminal_requested_count,
    firmware_window_clock_is_frozen,
    gate_relative_tolerance_for,
    window_clock_ceiling_from_metadata,
)
from ..results import ResultIssue, ResultValidity

if TYPE_CHECKING:
    from ..pipeline import PipelineContext


@dataclass(frozen=True)
class RunEvaluation:
    """Authoritative validity and structured issues for one completed run."""

    validity: ResultValidity
    issues: tuple[ResultIssue, ...] = ()


def evaluate_run(ctx: PipelineContext) -> RunEvaluation:
    """Evaluate captured results without mutating pipeline state."""
    issues: list[ResultIssue] = []
    if ctx.pmu_result is None:
        issues.append(_error("pmu.missing", "The run has no PMU result."))
    elif ctx.pmu_result.overflow_detected:
        issues.append(
            _error(
                "pmu.counter_overflow",
                "One or more PMU counters overflowed.",
            )
        )

    if ctx.pmu_result is not None:
        meta = ctx.pmu_result.meta
        # A clean window that completed inferences in zero elapsed time was
        # timed by a clock that never moved. The power binary's twin of this
        # rule (firmware_window_clock_is_frozen) only runs at the power
        # terminal, so a PROFILE-only STIMER window -- every Apollo5 profile
        # build, and AP3/AP4 busy_loop -- had no check at all: a dead 32.768
        # kHz crystal yielded silent zeros with no issue code (found by
        # review of #128, which added the settle these windows now rely on).
        # Attribution (dead crystal vs dead debug domain) stays open on #110;
        # detection should not.
        if meta.clean_infer_count and (
            meta.clean_infer_avg_us == 0 or meta.clean_infer_total_cycles == 0
        ):
            issues.append(
                _warning(
                    "profile.clean_window_frozen",
                    "The clean window completed "
                    f"{meta.clean_infer_count} inferences in zero elapsed "
                    "time; the clock timing it never advanced. Latency "
                    "figures from this window are meaningless.",
                    clean_infer_count=meta.clean_infer_count,
                )
            )

        # The profile binary's clean window is DWT-timed on the Cortex-M4F
        # families, and DWT misbehaves whenever no debugger holds the core
        # debug power domain up -- which is exactly what happens between the
        # J-Link reset subprocess exiting and the host attach completing
        # (#121). Two independent checks, because they fail differently.
        #
        # First the one whose reference is NOT DWT: the firmware times a known
        # nsx_delay_us() interval with DWT before the window opens. The
        # in-window counters below are DWT-relative and cancel exactly under a
        # uniform slowdown, so this is the only check that can see one -- and a
        # uniform slowdown is what a timed-out attach wait leaves behind.
        rate = assess_clean_window_clock_rate(
            rate_cycles=meta.clean_dwt_rate_cyc,
            probe_us=meta.clean_dwt_rate_us,
            system_clock_hz=meta.system_clock_hz,
        )
        if rate is not None and rate.is_broken:
            issues.append(
                _warning(
                    "profile.clean_window_clock_rate_low",
                    "The clean window's cycle counter was running far below "
                    "its expected rate when the window opened, measured "
                    "against an independent clock; every timing derived from "
                    "it is short by roughly that factor.",
                    **rate.to_metadata(),
                )
            )
        # Then the in-window counters: they catch a counter that froze or
        # slowed part-way through, which the rate probe (taken before the
        # window) cannot see.
        stall = assess_clean_window_stall(
            stalled_iters=meta.clean_stalled_iters,
            partial_iters=meta.clean_partial_iters,
            clean_infer_count=meta.clean_infer_count,
            ref_cycles=meta.clean_ref_cycles,
        )
        if stall is not None:
            if stall.partial_check_inoperative and stall.affected_iters == 0:
                issues.append(
                    _warning(
                        "profile.clean_window_check_inoperative",
                        "The clean window's partial-stall check could not run: "
                        "its warm reference was zero, so no iteration could "
                        "fall below the floor. Absence of stalls here is not "
                        "evidence of a healthy window.",
                        **stall.to_metadata(),
                    )
                )
            else:
                issues.append(
                    _warning(
                        "profile.clean_window_stalled",
                        "The clean-inference window's cycle counter stalled: "
                        "clean_infer_avg_us understates the true per-inference "
                        "time, and any power window sized from it is short.",
                        **stall.to_metadata(),
                    )
                )

    power_run = ctx.power_run
    if power_run is not None:
        plan = power_run.plan
        observation = power_run.observation
        terminal = power_run.terminal
        on_device = power_run.on_device_summary

        internal_mode = ctx.config.power.mode.value == "internal"
        if observation is None and not internal_mode:
            issues.append(_error("power.observation_missing", "Power observation is missing."))
        if observation is not None:
            if observation.integrity == "invalid":
                issues.append(
                    _error("power.observation_invalid", "Power observation integrity is invalid.")
                )
            elif observation.integrity == "degraded":
                issues.append(
                    _warning(
                        "power.observation_degraded",
                        "Power observation is diagnostic and not valid for efficiency metrics.",
                    )
                )
            if observation.mode == "gpio_gated" and not (
                observation.gate_rise_observed and observation.gate_fall_observed
            ):
                issues.append(
                    _error(
                        "power.gate_edges_missing",
                        "GPIO-gated power capture is missing a gate edge.",
                        gate_rise_observed=observation.gate_rise_observed,
                        gate_fall_observed=observation.gate_fall_observed,
                    )
                )
            duration = observation.result.metadata.get("gate_duration_integrity")
            if isinstance(duration, dict) and not _duration_integrity_valid(duration):
                issues.append(
                    _warning(
                        "power.gate_duration_mismatch",
                        "Measured power-gate duration does not agree with the expected fixed-N window.",
                        **duration,
                    )
                )
            elif duration is None and observation.mode == "gpio_gated":
                duration_issue = _assess_unrecorded_duration(ctx, observation.result.summary.duration_s)
                if duration_issue is not None:
                    issues.append(duration_issue)

        if plan.firmware_mode == "dedicated" and terminal is None:
            issues.append(
                _error(
                    "power.terminal_missing",
                    "Dedicated power firmware did not publish terminal status.",
                )
            )
        if terminal is not None:
            if terminal.status != "ok" or terminal.error_code != 0:
                issues.append(
                    _error(
                        "power.terminal_error",
                        "Power firmware reported an error.",
                        status=terminal.status,
                        error_code=terminal.error_code,
                        final_phase=terminal.final_phase,
                    )
                )
            if terminal.completed_count != terminal.requested_count:
                issues.append(
                    _error(
                        "power.terminal_incomplete",
                        "Power firmware completed a different inference count than requested.",
                        requested_count=terminal.requested_count,
                        completed_count=terminal.completed_count,
                    )
                )
            if not terminal.gate_lowered:
                issues.append(
                    _error("power.gate_not_lowered", "Power firmware did not confirm GATE low.")
                )
            # Same helper the collect stage uses, so the two cannot disagree
            # about what the firmware was supposed to report -- the busy_loop
            # probe runs one spin window rather than N inferences.
            expected_requested = expected_terminal_requested_count(
                inference_count=plan.inference_count,
                clean_window_probe=ctx.config.profiling.clean_window_probe,
            )
            if expected_requested is not None and terminal.requested_count != expected_requested:
                issues.append(
                    _error(
                        "power.plan_count_mismatch",
                        "Power firmware requested count differs from the host plan.",
                        planned_count=expected_requested,
                        requested_count=terminal.requested_count,
                    )
                )
            # Same policy the collect stage applies at capture time, from the
            # same helpers, so the two cannot disagree about severity: a run
            # the stage refuses to accept must not evaluate as VALID here if
            # it reaches this path some other way (a resumed or replayed
            # artifact, or a caller that skipped the stage). This mirrors the
            # on_device_overflow shape below, where the stage's mode-aware
            # fatal/warn split is reproduced rather than restated.
            if firmware_window_clock_is_frozen(
                elapsed_us=terminal.elapsed_us,
                completed_count=terminal.completed_count,
            ):
                # Mode-aware for the same reason on_device_overflow is: whether
                # this is fatal depends entirely on whether the broken number is
                # the measurement of record. Internal mode divides energy by
                # this duration, so the published power is corrupt. External
                # mode gets its power from the instrument and only loses
                # elapsed_us -- invalidating there would block comparability of
                # a capture whose power metrics are sound.
                if internal_mode:
                    issues.append(
                        _error(
                            "power.window_clock_frozen",
                            "Power firmware reported zero elapsed time for completed "
                            "inferences; the on-device measurement derived from it is "
                            "corrupt.",
                            completed_count=terminal.completed_count,
                            elapsed_us=terminal.elapsed_us,
                        )
                    )
                else:
                    issues.append(
                        _warning(
                            "power.window_clock_frozen",
                            "Power firmware reported zero elapsed time for completed "
                            "inferences; the external instrument's power numbers are "
                            "unaffected, but the firmware-reported window duration is "
                            "meaningless.",
                            completed_count=terminal.completed_count,
                            elapsed_us=terminal.elapsed_us,
                        )
                    )
            else:
                agreement = assess_run_window_clock(
                    elapsed_us=terminal.elapsed_us,
                    internal_mode=internal_mode,
                    gated_result=observation.result if observation is not None else None,
                    # Same reference the collect stage uses, unconditionally:
                    # `count x reference_us`. #112 withheld it for probes that
                    # run no inferences, because the plan then multiplied a
                    # per-inference time it had no business using. #125 fixed
                    # the PLAN instead -- a busy_loop window is one unit
                    # lasting window_target_ms -- so the product now IS the
                    # window for every probe, and withholding it here would
                    # leave the replay/evaluate_run path with no duration
                    # check at all while the stage has one.
                    planned_inference_count=plan.inference_count,
                    planned_inference_us=plan.reference_inference_us,
                )
                if agreement is not None and not agreement.agrees:
                    issues.append(
                        _warning(
                            "power.window_clock_mismatch",
                            "Firmware-reported window duration does not agree with "
                            "the independently measured window.",
                            **agreement.to_metadata(),
                        )
                    )
                # Host wall-clock ceiling, recorded by the collect stage (which
                # is the only place that knows "now"). The verdict is
                # re-derived from the stored measurements rather than read from
                # a cached boolean, the same way _duration_integrity_valid()
                # re-derives the gate-duration verdict.
                ceiling_meta = (
                    ctx.power_result.metadata.get("window_clock_ceiling")
                    if ctx.power_result is not None
                    else None
                )
                if isinstance(ceiling_meta, dict):
                    ceiling = window_clock_ceiling_from_metadata(ceiling_meta)
                    if ceiling is not None and ceiling.exceeded:
                        issues.append(
                            _warning(
                                "power.window_clock_exceeds_host_time",
                                "Firmware-reported window is longer than the host "
                                "wall time that contained it.",
                                **ceiling.to_metadata(),
                            )
                        )

        if on_device is not None:
            if on_device.overflow:
                # Fatal only when the monitor is the measurement of record.
                # In external mode the monitor is a bystander (e.g. sense
                # inputs disconnected while a Joulescope measures) and its
                # health flags must not invalidate a good external capture —
                # this mirrors CollectPowerTerminalStage's warn-only path.
                if internal_mode:
                    issues.append(
                        _error(
                            "power.on_device_overflow",
                            "On-device power monitor reported accumulator overflow.",
                        )
                    )
                else:
                    issues.append(
                        _warning(
                            "power.on_device_overflow",
                            "Bystander on-device monitor reported accumulator "
                            "overflow; the external measurement of record is "
                            "unaffected.",
                        )
                    )
            expected_count = terminal.completed_count if terminal is not None else plan.inference_count
            if expected_count is not None and on_device.inference_count != expected_count:
                issues.append(
                    _error(
                        "power.on_device_count_mismatch",
                        "On-device measurement count differs from completed work.",
                        measured_count=on_device.inference_count,
                        expected_count=expected_count,
                    )
                )
        elif internal_mode:
            issues.append(
                _error(
                    "power.on_device_measurement_missing",
                    "Internal power mode has no on-device measurement.",
                )
            )
    elif ctx.power_result is not None:
        integrity = ctx.power_result.metadata.get("integrity")
        if integrity == "invalid":
            issues.append(_error("power.observation_invalid", "Power observation integrity is invalid."))
        elif integrity == "degraded":
            issues.append(
                _warning(
                    "power.observation_degraded",
                    "Power observation is diagnostic and not valid for efficiency metrics.",
                )
            )
        duration = ctx.power_result.metadata.get("gate_duration_integrity")
        if isinstance(duration, dict) and not _duration_integrity_valid(duration):
            issues.append(
                _warning(
                    "power.gate_duration_mismatch",
                    "Measured power-gate duration does not agree with the expected inference window.",
                    **duration,
                )
            )

    return RunEvaluation(validity=_validity_for(issues), issues=tuple(issues))


def _assess_unrecorded_duration(ctx: PipelineContext, measured_s: float) -> ResultIssue | None:
    if ctx.pmu_result is None:
        return None
    plan = ctx.power_run.plan if ctx.power_run is not None else None
    count = plan.inference_count if plan is not None else None
    average_us = plan.reference_inference_us if plan is not None else None
    if count is None:
        count = ctx.pmu_result.meta.clean_infer_count
    if average_us is None:
        average_us = ctx.pmu_result.meta.clean_infer_avg_us
    if not count:
        return None
    if not average_us:
        if ctx.pmu_result.meta.clean_infer_avg_cycles is not None:
            return _warning(
                "power.gate_duration_unverifiable",
                "Power-gate duration cannot be verified because clean inference timing is invalid.",
                inference_count=count,
                clean_infer_avg_us=average_us,
            )
        return None
    integrity = assess_gate_duration(
        measured_s=measured_s,
        clean_infer_count=count,
        clean_infer_avg_us=average_us,
        stats_rate_hz=ctx.config.power.stats_rate_hz,
        # Same policy capture/__init__.py applies, from the same helper: a
        # tolerance that differs between capture time and evaluate time is the
        # divergence class this module already fixed for the window-clock
        # check. Dormant today (capture_gated records gate_duration_integrity
        # whenever it runs, so this fallback needs an artifact that lacks it),
        # but the two must not be free to drift.
        relative_tolerance=gate_relative_tolerance_for(
            ctx.config.profiling.clean_window_probe
        ),
    )
    if integrity.valid:
        return None
    return _warning(
        "power.gate_duration_mismatch",
        "Measured power-gate duration does not agree with the expected fixed-N window.",
        measured_s=integrity.measured_s,
        expected_s=integrity.expected_s,
        tolerance_s=integrity.tolerance_s,
        minimum_s=integrity.minimum_s,
        ratio=integrity.ratio,
    )


def _duration_integrity_valid(data: dict[str, Any]) -> bool:
    try:
        measured = float(data["measured_s"])
        expected = float(data["expected_s"])
        tolerance = float(data["tolerance_s"])
        minimum = float(data.get("minimum_s", 0.0))
    except (KeyError, TypeError, ValueError):
        return False
    return measured >= minimum and abs(measured - expected) <= tolerance


def _validity_for(issues: list[ResultIssue]) -> ResultValidity:
    if any(issue.severity == "error" for issue in issues):
        return ResultValidity.INVALID
    if issues:
        return ResultValidity.DEGRADED
    return ResultValidity.VALID


def _error(code: str, message: str, **context: Any) -> ResultIssue:
    return ResultIssue(code=code, severity="error", message=message, context=context)


def _warning(code: str, message: str, **context: Any) -> ResultIssue:
    return ResultIssue(code=code, severity="warning", message=message, context=context)
