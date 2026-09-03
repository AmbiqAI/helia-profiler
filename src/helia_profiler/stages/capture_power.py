"""Capture power data via configured power driver (optional).

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
from ..power.base import PowerDriver
from ..power.diagnostics import count_noun, probe_runs_inferences
from ..power.metadata import classify_observation
from ..target.lifecycle import CapturePhase, prepare_target_for_phase

log = logging.getLogger("hpx")

# Guard periods for estimated-duration auto-terminate
_BOOT_SETTLE_S = 8.0  # reset/SBL/firmware init allowance
_SAFETY_MARGIN_S = 6.0  # extra headroom beyond estimated runtime


#: Auto window mode warms the clean pass with 3 uninstrumented reps before
#: timing (_main_base.cc.j2), independent of profiling.warmup; every
#: fixed-mode measuring arm floors its warmup at the same 3 (#164, #170), so
#: the estimate below floors too.
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

    Both phases must be covered: the clean window can be made arbitrarily
    long, and under-covering lets the Joulescope poller's safety bound elapse
    mid-window and miss the falling edge.

    Returns ``None`` if there is not enough information to estimate.
    """
    pmu = ctx.pmu_result
    soc = ctx.soc
    platform = ctx.run_metadata.platform
    if pmu is None or soc is None:
        return None
    if platform is None:
        # ResolvePlatformStage sets platform whenever it sets soc, so this
        # arm is unreachable in the shipped pipeline -- but a silent None
        # would quietly size the capture from the default duration, so the
        # fallback announces itself.
        log.warning(
            "Cannot estimate the capture duration (run_metadata.platform is "
            "not set); falling back to the configured window duration."
        )
        return None

    total_cycles = sum(layer.cycles or 0 for layer in pmu.layers)
    if total_cycles <= 0:
        return None

    # Use the CPU clock actually selected for this run (resolved in stage 1),
    # not the SoC's top frequency.
    clock_hz = platform.cpu_clock_mhz * 1_000_000
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
        # This probe's window is a calibrated CPU spin sized from the target in
        # both window modes, so an inference-count estimate describes nothing
        # it runs; size from the probe's own window. The warm reps still cost
        # real inference time (main.cc.j2's warm loop runs before the spin),
        # so keep them in the margin.
        clean_warmup_reps = (
            _AUTO_WINDOW_WARMUP_REPS
            if profiling.window_mode is WindowMode.AUTO
            else max(1, profiling.warmup)
        )
        clean_run_s = (
            ctx.config.effective_window_target_ms / 1000.0 + clean_warmup_reps * inference_time_s
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

        def _prepare_target(driver: PowerDriver, resolved_driver_name: str):
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
        # Tighten the capture window from PMU timing only when the user left
        # duration unset: an explicit power.duration_s is an operator override
        # and must win -- the PMU-phase estimate can be wrong about the
        # power-phase boot, and a silently-shrunk bound blocks overrides
        # during diagnosis.  duration_s is None when not explicitly set.
        estimated = _estimate_capture_duration(ctx)
        user_overrode_duration = ctx.config.power.duration_s is not None
        configured = (
            ctx.config.power.duration_s if user_overrode_duration else DEFAULT_POWER_DURATION_S
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
        planned_us = ctx.power_plan.reference_inference_us if ctx.power_plan is not None else None
        message = "Arming instrument and resetting target"
        if planned_count is not None:
            noun = count_noun(ctx.config.profiling.clean_window_probe, planned_count)
            message += f" · {planned_count:,} {noun}"
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

        # Mode/integrity/edges derive from capture metadata in one place so
        # this log and publish_power_result cannot disagree; the deadline
        # stays this stage's own budget.
        obs_mode, obs_integrity, rise, fall, _ = classify_observation(power_result.metadata)
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
