"""Stage 7 — Capture power data via configured power driver (optional).

Power capture relaunches firmware through an explicit target lifecycle policy
before arming the measurement.  The default policy uses reset primitives, not
instrument rail cycling; Joulescope power cycling remains an explicit recovery
or bring-up experiment only.

If PMU results are available from a preceding capture stage, the capture
duration is automatically tightened to `boot_time + firmware_run_time +
margin` so that short models don't wait for the full `duration_s` timeout.
"""

from __future__ import annotations

import logging

from ..results import PowerObservation
from ..config import DEFAULT_POWER_DURATION_S, WindowMode
from ..errors import PowerError
from ..pipeline import PipelineContext
from ..power.diagnostics import probe_runs_inferences
from ..power.metadata import classify_observation
from ..target.lifecycle import CapturePhase, prepare_target_for_phase

log = logging.getLogger("hpx")

# Guard periods for estimated-duration auto-terminate
_BOOT_SETTLE_S = 8.0  # reset/SBL/firmware init allowance
_SAFETY_MARGIN_S = 6.0  # extra headroom beyond estimated runtime


#: Auto window mode warms the clean pass with 3 hardcoded uninstrumented
#: reps before timing (_main_base.cc.j2), independent of profiling.warmup
#: which only applies to the per-layer PMU passes. Every fixed-mode
#: measuring arm (STIMER since #164, DWT since #170) floors its warmup at
#: the same 3, so the fixed-mode estimate below floors too and matches the
#: firmware exactly.
_AUTO_WINDOW_WARMUP_REPS = 3


def _estimate_capture_duration(ctx: PipelineContext) -> float | None:
    """Estimate how long the firmware needs to run from PMU timing data.

    After a power-cycle, the firmware boots from MRAM and runs two distinct
    phases before HPX_END:

    1. The GPIO-gated *clean* window — ``iterations`` clean inferences in
       ``window_mode: fixed``, or a runtime-sized loop targeting
       ``window_target_ms`` of wall-time in ``window_mode: auto`` (clamped to
       ``[window_min, window_max]``).
    2. The per-layer PMU-instrumented passes — ``presets × (warmup +
       iterations)`` inferences.

    Both phases must be covered by the estimate; the clean window in
    particular can be made arbitrarily long (e.g. to build a multi-second
    Joulescope integration window), and previously this function only
    accounted for the PMU passes, causing the Joulescope poller's safety
    bound to elapse mid-window and miss the falling edge entirely.

    Returns ``None`` if there is not enough information to estimate.
    """
    pmu = ctx.pmu_result
    soc = ctx.soc
    if pmu is None or soc is None:
        return None

    total_cycles = sum(layer.cycles or 0 for layer in pmu.layers)
    if total_cycles <= 0:
        return None

    # Use the CPU clock actually selected for this run (resolved in stage 1),
    # not the SoC's top frequency.
    clock_hz = ctx.run_metadata.platform.cpu_clock_mhz * 1_000_000
    if clock_hz <= 0:
        return None

    cycles_per_inference = total_cycles
    inference_time_s = cycles_per_inference / clock_hz

    if (
        ctx.power_plan is not None
        and ctx.power_plan.inference_count is not None
        and ctx.power_plan.reference_inference_us is not None
    ):
        planned_run_s = (
            ctx.power_plan.inference_count * ctx.power_plan.reference_inference_us
        ) / 1_000_000.0
        return _BOOT_SETTLE_S + planned_run_s + _SAFETY_MARGIN_S

    profiling = ctx.config.profiling
    num_presets = len(pmu.presets) or 1
    profiled_inferences = num_presets * (profiling.warmup + profiling.iterations)
    profiled_run_s = profiled_inferences * inference_time_s

    if not probe_runs_inferences(profiling.clean_window_probe):
        # This probe's window is a calibrated CPU spin, not inferences, and it
        # is sized from the target in BOTH window modes -- so an
        # inference-count estimate describes nothing it runs. The fixed-mode
        # branch below sized it as iterations x inference_time, which on a
        # shared run (no plan, so this fallback is what bounds the capture)
        # put the poller's deadline inside the window for any target the
        # inference count did not happen to cover: exactly the failure this
        # function's docstring says it exists to prevent (found by review).
        #
        # The warm reps still cost real inference time: main.cc.j2's warm loop
        # sits ABOVE the `{% if busy_loop_probe %}` spin and runs whatever the
        # probe is, so the spin replaces the measured window only, not the
        # priming before it. Omitting them narrows the margin by
        # warmup x per-inference, which is noise on a 21 ms model and 15 s on
        # a 3 s one (found by review).
        clean_warmup_reps = (
            _AUTO_WINDOW_WARMUP_REPS
            if profiling.window_mode is WindowMode.AUTO
            else max(1, profiling.warmup)
        )
        clean_run_s = (
            ctx.config.effective_window_target_ms / 1000.0
            + clean_warmup_reps * inference_time_s
        )
    else:
        if profiling.window_mode is WindowMode.AUTO:
            target_s = ctx.config.effective_window_target_ms / 1000.0
            clean_iters = (
                target_s / inference_time_s if inference_time_s > 0 else profiling.window_min
            )
            clean_iters = max(profiling.window_min, min(profiling.window_max, clean_iters))
            clean_warmup_reps = _AUTO_WINDOW_WARMUP_REPS
        else:
            clean_iters = max(1, profiling.iterations)
            clean_warmup_reps = max(_AUTO_WINDOW_WARMUP_REPS, profiling.warmup)
        clean_run_s = (clean_iters + clean_warmup_reps) * inference_time_s

    firmware_run_s = profiled_run_s + clean_run_s

    estimated = _BOOT_SETTLE_S + firmware_run_s + _SAFETY_MARGIN_S
    return estimated


class CapturePowerStage:
    @property
    def name(self) -> str:
        return "capture_power"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return not ctx.config.power.enabled or ctx.config.power.mode.value == "internal"

    def run(self, ctx: PipelineContext) -> None:
        from ..capture import capture_power

        driver_name = ctx.config.power.driver
        mode = ctx.config.power.mode
        log.info("Power driver: %s (mode: %s)", driver_name, mode)

        def _prepare_target(driver: object, resolved_driver_name: str):
            lifecycle_plan = prepare_target_for_phase(
                ctx,
                phase=CapturePhase.POWER,
                power_driver=driver,
                power_driver_name=resolved_driver_name,
            )
            log.info(
                "Power lifecycle: power_cycle=%s reset=%s",
                (
                    "ok"
                    if lifecycle_plan.power_cycle_succeeded
                    else "failed"
                    if lifecycle_plan.power_cycle_attempted
                    else "not-requested"
                ),
                lifecycle_plan.reset_action.value,
            )
            return lifecycle_plan

        # --- Capture ---
        # Tighten capture window if PMU timing data is available — but only
        # when the user left duration unset.  An explicit --power-duration /
        # power.duration_s (even one equal to the default value) is an
        # operator override and must win over the estimate: the estimate is
        # derived from PMU-phase timing, which the AP5 combo-reset
        # investigation showed can be wildly wrong about the power-phase
        # boot, and a silently-shrunk bound made the override impossible to
        # apply during diagnosis.  duration_s is None when not explicitly set.
        estimated = _estimate_capture_duration(ctx)
        user_overrode_duration = ctx.config.power.duration_s is not None
        configured = (
            ctx.config.power.duration_s
            if user_overrode_duration
            else DEFAULT_POWER_DURATION_S
        )
        if estimated is not None and estimated < configured and not user_overrode_duration:
            log.info(
                "Auto-tuned capture duration: %.1fs (estimated) vs %.1fs (configured)",
                estimated,
                configured,
            )
            capture_duration = estimated
        else:
            capture_duration = configured

        planned_count = ctx.power_plan.inference_count if ctx.power_plan is not None else None
        planned_us = (
            ctx.power_plan.reference_inference_us if ctx.power_plan is not None else None
        )
        message = "Arming instrument and resetting target"
        if planned_count is not None:
            message += f" · {planned_count:,} inferences"
        ctx.report_progress(
            message,
            eta_s=(
                planned_count * planned_us / 1_000_000
                if planned_count is not None and planned_us is not None
                else capture_duration
            ),
        )

        try:
            power_result = capture_power(
                ctx,
                duration_override_s=capture_duration,
                prepare_target=_prepare_target,
            )
        except PowerError:
            raise
        except Exception as exc:
            raise PowerError(
                f"Power capture failed: {exc}",
                hint=(f"Check that the {driver_name} is connected and powered on. Mode: {mode}."),
            ) from exc

        # Mode/integrity/edges derive from capture metadata in one place
        # (previously duplicated here and in publish_power_result with
        # different defaults). The deadline stays this stage's own capture
        # budget: classify_observation's fallback is for callers that have
        # no budget of their own.
        obs_mode, obs_integrity, rise, fall, _ = classify_observation(
            power_result.metadata
        )
        observation = PowerObservation(
            mode=obs_mode,
            result=power_result,
            gate_rise_observed=rise,
            gate_fall_observed=fall,
            deadline_s=capture_duration,
            integrity=obs_integrity,
        )
        ctx.publish_power_observation(observation)
        log.info(
            "Captured power data (%.1fs, driver=%s, mode=%s)",
            capture_duration,
            driver_name,
            mode,
        )
        ctx.report_progress(
            "Power capture complete",
            kind="checkpoint",
            min_verbosity=0,
        )
