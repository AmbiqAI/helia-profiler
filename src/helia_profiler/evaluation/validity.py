"""Centralized correctness policy for completed profiling runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..power.diagnostics import assess_gate_duration
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
            if plan.inference_count is not None and terminal.requested_count != plan.inference_count:
                issues.append(
                    _error(
                        "power.plan_count_mismatch",
                        "Power firmware requested count differs from the host plan.",
                        planned_count=plan.inference_count,
                        requested_count=terminal.requested_count,
                    )
                )

        if on_device is not None:
            if on_device.overflow:
                issues.append(
                    _error(
                        "power.on_device_overflow",
                        "On-device power monitor reported accumulator overflow.",
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
