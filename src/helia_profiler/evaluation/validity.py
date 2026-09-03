"""Centralized correctness policy for completed profiling runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..power.diagnostics import (
    DRIFT_PLAUSIBLE_RATIO_DEVIATION,
    GateArbitration,
    assess_clean_window_clock_rate,
    count_noun,
    assess_clean_window_stall,
    assess_gate_duration,
    assess_gate_observer,
    assess_run_window_clock,
    expected_terminal_requested_count,
    firmware_window_clock_is_frozen,
    gate_relative_tolerance_for,
    probe_runs_inferences,
    window_inference_count,
)
from ..power.metadata import MeasurementScope
from ..errors import ReportError
from ..results import ISSUE_REGISTRY, IssueCode, ResultIssue, ResultValidity, Severity

if TYPE_CHECKING:
    from ..pipeline import PipelineContext
    from ..power.base import PowerResult
    from ..power.diagnostics import GateDurationIntegrity


@dataclass(frozen=True)
class RunEvaluation:
    """Authoritative validity and structured issues for one completed run."""

    validity: ResultValidity
    issues: tuple[ResultIssue, ...] = ()
    #: The composed #142/#181 gate verdict (None when the run has no gated
    #: power capture to arbitrate). Issues above are emitted FROM it; the
    #: summary renders it -- one composition, two consumers (#202).
    gate_arbitration: GateArbitration | None = None


def _rederive_integrity(
    ctx: PipelineContext, result: "PowerResult"
) -> "GateDurationIntegrity | None":
    """Advisory est*count verdict for a result that recorded none.

    Mirrors the report's historical inputs exactly: gated scope, an
    inference-running probe, the plan-preferred count/reference, and the
    ``assess_gate_duration`` advisory default band. ``None`` when the inputs
    do not exist -- the observer check does not need this term.
    """
    if result.metadata.measurement_scope != MeasurementScope.GPIO_GATED_CLEAN_WINDOW:
        return None
    if not probe_runs_inferences(ctx.config.profiling.clean_window_probe):
        return None
    meta = ctx.pmu_result.meta if ctx.pmu_result is not None else None
    if meta is None or meta.clean_infer_count is None or meta.clean_infer_count <= 0:
        return None
    # One resolution of the window's inference count (#240): energy/inference
    # (report.summary), TOPS, and this gate-duration check share it.
    effective_count = window_inference_count(ctx) or meta.clean_infer_count
    effective_avg_us = meta.clean_infer_avg_us
    plan_meta = result.metadata.power_plan
    if isinstance(plan_meta, dict) and plan_meta.get("reference_inference_us"):
        effective_avg_us = int(plan_meta["reference_inference_us"])
    if not effective_avg_us or effective_avg_us <= 0 or result.summary.duration_s <= 0:
        return None
    return assess_gate_duration(
        measured_s=result.summary.duration_s,
        clean_infer_count=effective_count,
        clean_infer_avg_us=effective_avg_us,
        stats_rate_hz=ctx.config.power.stats_rate_hz,
    )


def _build_gate_arbitration(ctx: PipelineContext) -> GateArbitration | None:
    """Compose the gate facts exactly once per run.

    Sources the gated result from the observation when one exists;
    ``ctx.power_result`` is a derived property returning exactly
    ``power_run.observation.result`` (ea4e8af), so there is no second
    source to reconcile.

    The integrity term prefers the capture's own record (probe-keyed band,
    1 s floor). For an artifact that recorded none, it re-derives at the
    advisory band ``assess_gate_duration`` defaults to -- deliberately
    tighter, because that verdict only ever feeds the summary's advisory
    ``suspect`` flag, never a validity issue (``integrity_recorded`` is how
    validity tells the difference).
    """
    result = None
    if ctx.power_run is not None and ctx.power_run.observation is not None:
        result = ctx.power_run.observation.result
    if result is None:
        return None

    integrity = result.metadata.gate_duration_integrity
    integrity_recorded = integrity is not None
    if integrity is None:
        integrity = _rederive_integrity(ctx, result)

    terminal = ctx.power_run.terminal if ctx.power_run is not None else None
    terminal_unhealthy = terminal is not None and (
        terminal.status != "ok"
        or terminal.error_code != 0
        or terminal.completed_count != terminal.requested_count
    )
    observer = (
        assess_gate_observer(
            elapsed_us=terminal.elapsed_us,
            gated_result=result,
            stats_rate_hz=ctx.config.power.stats_rate_hz,
        )
        if terminal is not None and not terminal_unhealthy
        else None
    )
    if integrity is None and observer is None:
        # Neither an est*count term nor a two-clock comparison exists --
        # there is nothing gated to arbitrate (an internal-mode run with a
        # terminal hiccup lands here), and RunEvaluation.gate_arbitration
        # promises None for exactly that (#204: a non-None arbitration for a
        # non-gated run would poison consumers keying "gated" on its presence).
        return None
    return GateArbitration(
        integrity=integrity,
        integrity_recorded=integrity_recorded,
        observer=observer,
        terminal_unhealthy=terminal_unhealthy,
    )


def evaluate_run(ctx: PipelineContext) -> RunEvaluation:
    """Evaluate captured results without mutating pipeline state."""
    issues: list[ResultIssue] = []
    arbitration = _build_gate_arbitration(ctx)
    if ctx.pmu_result is None:
        issues.append(_error(IssueCode.PMU_MISSING, "The run has no PMU result."))
    elif ctx.pmu_result.overflow_detected:
        issues.append(
            _error(
                IssueCode.PMU_COUNTER_OVERFLOW,
                "One or more PMU counters overflowed.",
            )
        )

    if ctx.pmu_result is not None:
        meta = ctx.pmu_result.meta
        # A clean window that completed inferences in zero elapsed time was
        # timed by a clock that never moved. The power binary's twin of this
        # rule (firmware_window_clock_is_frozen) runs only at the power
        # terminal, so a PROFILE-only STIMER window needs its own check
        # (#128). STIMER-window binaries report stimer_dead before the window
        # when the crystal is unfit (#110), so reaching this check without
        # that error points at a lost line on a lossy transport.
        if meta.clean_infer_count and (
            meta.clean_infer_avg_us == 0 or meta.clean_infer_total_cycles == 0
        ):
            issues.append(
                _warning(
                    IssueCode.PROFILE_CLEAN_WINDOW_FROZEN,
                    "The clean window completed "
                    f"{meta.clean_infer_count} "
                    f"{count_noun(ctx.config.profiling.clean_window_probe, meta.clean_infer_count)} "
                    "in zero elapsed "
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
                    IssueCode.PROFILE_CLEAN_WINDOW_CLOCK_RATE_LOW,
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
                        IssueCode.PROFILE_CLEAN_WINDOW_CHECK_INOPERATIVE,
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
                        IssueCode.PROFILE_CLEAN_WINDOW_STALLED,
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
            issues.append(_error(IssueCode.POWER_OBSERVATION_MISSING, "Power observation is missing."))
        if observation is not None:
            if observation.integrity == "invalid":
                issues.append(
                    _error(IssueCode.POWER_OBSERVATION_INVALID, "Power observation integrity is invalid.")
                )
            elif observation.integrity == "degraded":
                issues.append(
                    _warning(
                        IssueCode.POWER_OBSERVATION_DEGRADED,
                        "Power observation is diagnostic and not valid for efficiency metrics.",
                    )
                )
            if observation.mode == "gpio_gated" and not (
                observation.gate_rise_observed and observation.gate_fall_observed
            ):
                issues.append(
                    _error(
                        IssueCode.POWER_GATE_EDGES_MISSING,
                        "GPIO-gated power capture is missing a gate edge.",
                        gate_rise_observed=observation.gate_rise_observed,
                        gate_fall_observed=observation.gate_fall_observed,
                    )
                )
        # The est*count duration verdict is DEFERRED until after the terminal
        # block below (#142/#181): whether a band miss is an error depends on
        # what the firmware's own window clock says, and that arbitration is
        # computed there. Tracked as a tri-state -- None means the observer
        # check could not run, which hands the est*count fallback its
        # authority back; it is never treated as a pass.
        observer_agrees: bool | None = None

        if plan.firmware_mode == "dedicated" and terminal is None:
            issues.append(
                _error(
                    IssueCode.POWER_TERMINAL_MISSING,
                    "Dedicated power firmware did not publish terminal status.",
                )
            )
        if terminal is not None:
            if terminal.status != "ok" or terminal.error_code != 0:
                issues.append(
                    _error(
                        IssueCode.POWER_TERMINAL_ERROR,
                        "Power firmware reported an error.",
                        status=terminal.status,
                        error_code=terminal.error_code,
                        final_phase=terminal.final_phase,
                    )
                )
            if terminal.completed_count != terminal.requested_count:
                issues.append(
                    _error(
                        IssueCode.POWER_TERMINAL_INCOMPLETE,
                        "Power firmware completed a different inference count than requested.",
                        requested_count=terminal.requested_count,
                        completed_count=terminal.completed_count,
                    )
                )
            if not terminal.gate_lowered:
                issues.append(
                    _error(IssueCode.POWER_GATE_NOT_LOWERED, "Power firmware did not confirm GATE low.")
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
                        IssueCode.POWER_PLAN_COUNT_MISMATCH,
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
                # count_noun(..., N or 2): when the count is unknown/zero the
                # generic plural reads right ("completed busy-loop passes");
                # a real count of 1 gets the singular.
                if internal_mode:
                    issues.append(
                        _error(
                            IssueCode.POWER_WINDOW_CLOCK_FROZEN,
                            "Power firmware reported zero elapsed time for "
                            "completed "
                            f"{count_noun(ctx.config.profiling.clean_window_probe, terminal.completed_count or 2)}; "
                            "the on-device measurement derived from it is "
                            "corrupt.",
                            completed_count=terminal.completed_count,
                            elapsed_us=terminal.elapsed_us,
                        )
                    )
                else:
                    issues.append(
                        _warning(
                            IssueCode.POWER_WINDOW_CLOCK_FROZEN,
                            "Power firmware reported zero elapsed time for "
                            "completed "
                            f"{count_noun(ctx.config.profiling.clean_window_probe, terminal.completed_count or 2)}; "
                            "the external instrument's power numbers are "
                            "unaffected, but the firmware-reported window duration is "
                            "meaningless.",
                            completed_count=terminal.completed_count,
                            elapsed_us=terminal.elapsed_us,
                        )
                    )
            elif internal_mode:
                agreement = assess_run_window_clock(
                    elapsed_us=terminal.elapsed_us,
                    internal_mode=True,
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
                    stats_rate_hz=ctx.config.power.stats_rate_hz,
                )
                if agreement is not None and not agreement.agrees:
                    # Plan-referenced (count x a different boot's timing,
                    # 25% band): a genuine cross-boot reference check, so
                    # it stays advisory.
                    issues.append(
                        _warning(
                            IssueCode.POWER_WINDOW_CLOCK_MISMATCH,
                            "Firmware-reported window duration does not agree with "
                            "the independently measured window.",
                            **agreement.to_metadata(),
                        )
                    )
            else:
                # External mode reads the observer off the shared arbitration
                # (same helper chain as before -- assess_gate_observer -- but
                # composed once, and now gated on terminal health: an envelope
                # reporting failed or incomplete work cannot arbitrate, it
                # times whatever short window it did run).
                observer = arbitration.observer if arbitration is not None else None
                if observer is not None:
                    observer_agrees = observer.agrees
                if observer is not None and not observer.agrees:
                    # Two independent oscillators watched the SAME window
                    # in the SAME boot (instrument sample clock vs the
                    # STIMER XTAL, liveness-verified by #110's settle
                    # probe), so drift cannot explain a miss: the gate did
                    # not bracket what the firmware timed, and every
                    # per-inference figure divided out of it inherits the
                    # error. This is the authoritative window-integrity
                    # verdict (#142/#181) -- the est*count band below is
                    # only a reference diagnostic once this check has run.
                    issues.append(
                        _error(
                            IssueCode.POWER_WINDOW_OBSERVER_MISMATCH,
                            "The instrument-timed gate and the firmware's own "
                            "window clock disagree about the same physical "
                            "window; per-inference power metrics are not "
                            "trustworthy.",
                            **observer.to_metadata(),
                        )
                    )
            if not firmware_window_clock_is_frozen(
                elapsed_us=terminal.elapsed_us,
                completed_count=terminal.completed_count,
            ):
                # Host wall-clock ceiling, recorded by the collect stage (which
                # is the only place that knows "now"). The verdict is a
                # property derived from the stored measurements, never a
                # cached boolean — same rule as GateDurationIntegrity.valid.
                # Runs for both modes (ceiling is only ever recorded on
                # internal runs today, but that is the recorder's knowledge,
                # not this check's).
                ceiling = (
                    ctx.power_result.metadata.window_clock_ceiling
                    if ctx.power_result is not None
                    else None
                )
                if ceiling is not None:
                    if ceiling.exceeded:
                        issues.append(
                            _warning(
                                IssueCode.POWER_WINDOW_CLOCK_EXCEEDS_HOST_TIME,
                                "Firmware-reported window is longer than the host "
                                "wall time that contained it.",
                                **ceiling.to_metadata(),
                            )
                        )

        # Deferred est*count verdict (#142/#181). The expectation multiplies a
        # count by a per-inference time measured in a DIFFERENT boot and
        # thermal state, and the LP core clock is HFRC-derived: a cold power
        # boot runs fast enough to undershoot the band while being perfectly
        # healthy. The observer check above arbitrates:
        #   * observer agreed AND the miss is drift-scale (within
        #     DRIFT_PLAUSIBLE_RATIO_DEVIATION) -> the window is real and
        #     self-consistent; the reference is stale (cold-boot HFRC drift,
        #     #181). That is data, not a defect -- the summary publishes the
        #     ratio and drift note. Beyond drift scale the warning stands:
        #     see the inline comment on that branch.
        #   * observer disagreed -> POWER_WINDOW_OBSERVER_MISMATCH (ERROR)
        #     already carries the story; a second issue would restate it.
        #   * observer could not run (shared firmware, lost or frozen
        #     terminal) -> est*count keeps its original WARNING authority.
        # The 1s floor is independent of arbitration: a below-floor gate is
        # too short for the stats integral to be trusted regardless of what
        # the firmware clock says.
        if observation is not None:
            # Consume the arbitration's integrity term, not the metadata:
            # ``integrity_recorded`` is the chokepoint that keeps validity
            # issues off the advisory re-derived band (#204).
            duration = (
                arbitration.integrity
                if arbitration is not None and arbitration.integrity_recorded
                else None
            )
            if duration is not None:
                if duration.below_minimum:
                    issues.append(
                        _error(
                            IssueCode.POWER_GATE_BELOW_MINIMUM,
                            "Measured power gate is shorter than the minimum accepted window.",
                            **duration.to_metadata(),
                        )
                    )
                elif not duration.valid:
                    if observer_agrees is None:
                        issues.append(
                            _warning(
                                IssueCode.POWER_GATE_DURATION_MISMATCH,
                                "Measured power-gate duration does not agree with the expected fixed-N window.",
                                **duration.to_metadata(),
                            )
                        )
                    elif (
                        observer_agrees
                        and arbitration is not None
                        and arbitration.reference_deviation is not None
                        and arbitration.reference_deviation
                        > DRIFT_PLAUSIBLE_RATIO_DEVIATION
                    ):
                        # Observer agreement only proves the gate brackets what
                        # the firmware timed -- it cannot see a window whose
                        # CONTENT changed (init left inside the gate, wrong
                        # clock config), because both clocks watch the same
                        # span regardless of what ran in it. Beyond the
                        # drift-plausible envelope the est*count check is the
                        # only tie to the profile phase's timing, so its
                        # warning stands: unbounded absolution would let a
                        # ratio-0.5 window evaluate VALID.
                        issues.append(
                            _warning(
                                IssueCode.POWER_GATE_DURATION_MISMATCH,
                                "Measured power-gate duration deviates from the "
                                "profile-phase reference beyond what thermal "
                                "drift can explain, although the firmware's own "
                                "window clock confirms the gate is "
                                "self-consistent.",
                                **duration.to_metadata(),
                            )
                        )
                    # Inside the envelope with observer agreement: a stale
                    # reference, not a defect -- the summary publishes the
                    # ratio and drift note. Observer disagreement: the ERROR
                    # above already carries the story.
            elif observation.mode == "gpio_gated" and observer_agrees is None:
                duration_issue = _assess_unrecorded_duration(ctx, observation.result.summary.duration_s)
                if duration_issue is not None:
                    issues.append(duration_issue)

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
                            IssueCode.POWER_ON_DEVICE_OVERFLOW,
                            "On-device power monitor reported accumulator overflow.",
                        )
                    )
                else:
                    issues.append(
                        _warning(
                            IssueCode.POWER_ON_DEVICE_OVERFLOW,
                            "Bystander on-device monitor reported accumulator "
                            "overflow; the external measurement of record is "
                            "unaffected.",
                        )
                    )
            expected_count = terminal.completed_count if terminal is not None else plan.inference_count
            if expected_count is not None and on_device.inference_count != expected_count:
                issues.append(
                    _error(
                        IssueCode.POWER_ON_DEVICE_COUNT_MISMATCH,
                        "On-device measurement count differs from completed work.",
                        measured_count=on_device.inference_count,
                        expected_count=expected_count,
                    )
                )
        elif internal_mode:
            issues.append(
                _error(
                    IssueCode.POWER_ON_DEVICE_MEASUREMENT_MISSING,
                    "Internal power mode has no on-device measurement.",
                )
            )
    elif ctx.power_result is not None:
        integrity = ctx.power_result.metadata.integrity
        if integrity == "invalid":
            issues.append(_error(IssueCode.POWER_OBSERVATION_INVALID, "Power observation integrity is invalid."))
        elif integrity == "degraded":
            issues.append(
                _warning(
                    IssueCode.POWER_OBSERVATION_DEGRADED,
                    "Power observation is diagnostic and not valid for efficiency metrics.",
                )
            )
        duration = ctx.power_result.metadata.gate_duration_integrity
        if duration is not None and duration.below_minimum:
            # No power_run means no terminal to arbitrate, but the floor
            # never needed arbitration -- same severity as the run path.
            issues.append(
                _error(
                    IssueCode.POWER_GATE_BELOW_MINIMUM,
                    "Measured power gate is shorter than the minimum accepted window.",
                    **duration.to_metadata(),
                )
            )
        elif duration is not None and not duration.valid:
            issues.append(
                _warning(
                    IssueCode.POWER_GATE_DURATION_MISMATCH,
                    "Measured power-gate duration does not agree with the expected inference window.",
                    **duration.to_metadata(),
                )
            )

    return RunEvaluation(
        validity=_validity_for(issues),
        issues=tuple(issues),
        gate_arbitration=arbitration,
    )


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
                IssueCode.POWER_GATE_DURATION_UNVERIFIABLE,
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
        IssueCode.POWER_GATE_DURATION_MISMATCH,
        "Measured power-gate duration does not agree with the expected fixed-N window.",
        measured_s=integrity.measured_s,
        expected_s=integrity.expected_s,
        tolerance_s=integrity.tolerance_s,
        minimum_s=integrity.minimum_s,
        ratio=integrity.ratio,
    )


def _validity_for(issues: list[ResultIssue]) -> ResultValidity:
    if any(issue.severity == "error" for issue in issues):
        return ResultValidity.INVALID
    if issues:
        return ResultValidity.DEGRADED
    return ResultValidity.VALID


def _error(code: IssueCode, message: str, **context: Any) -> ResultIssue:
    return _issue(code, Severity.ERROR, message, context)


def _warning(code: IssueCode, message: str, **context: Any) -> ResultIssue:
    return _issue(code, Severity.WARNING, message, context)


def _issue(code: IssueCode, severity: Severity, message: str, context: dict[str, Any]) -> ResultIssue:
    """Single construction chokepoint: a code cannot ship at a severity its
    registry entry does not allow, so severity drift fails a test instead of
    landing in an artifact."""
    spec = ISSUE_REGISTRY[code]
    if severity not in spec.allowed_severities():
        raise ReportError(
            f"Issue code '{code}' may not be emitted at severity "
            f"'{severity}' (registry allows: "
            f"{', '.join(sorted(spec.allowed_severities()))})."
        )
    return ResultIssue(code=str(code), severity=str(severity), message=message, context=context)
