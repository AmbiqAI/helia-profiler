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

from ..config import DEFAULT_POWER_WINDOW_TARGET_MS
from ..errors import PowerError
from ..pipeline import PipelineContext
from ..target.lifecycle import CapturePhase, prepare_target_for_phase

log = logging.getLogger("hpx")

# Guard periods for estimated-duration auto-terminate
_BOOT_SETTLE_S = 8.0  # reset/SBL/firmware init allowance
_SAFETY_MARGIN_S = 6.0  # extra headroom beyond estimated runtime


#: Auto window mode warms the clean pass with 3 hardcoded uninstrumented
#: reps before timing (main.cc.j2 / main_aot.cc.j2), independent of
#: profiling.warmup which only applies to the per-layer PMU passes.
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

    profiling = ctx.config.profiling
    num_presets = len(pmu.presets) or 1
    profiled_inferences = num_presets * (profiling.warmup + profiling.iterations)
    profiled_run_s = profiled_inferences * inference_time_s

    if profiling.window_mode == "auto":
        target_ms = max(profiling.window_target_ms, DEFAULT_POWER_WINDOW_TARGET_MS)
        target_s = target_ms / 1000.0
        clean_iters = target_s / inference_time_s if inference_time_s > 0 else profiling.window_min
        clean_iters = max(profiling.window_min, min(profiling.window_max, clean_iters))
        clean_warmup_reps = _AUTO_WINDOW_WARMUP_REPS
    else:
        clean_iters = max(1, profiling.iterations)
        clean_warmup_reps = max(1, profiling.warmup)

    clean_run_s = (clean_iters + clean_warmup_reps) * inference_time_s

    firmware_run_s = profiled_run_s + clean_run_s

    estimated = _BOOT_SETTLE_S + firmware_run_s + _SAFETY_MARGIN_S
    return estimated


class CapturePowerStage:
    @property
    def name(self) -> str:
        return "capture_power"

    def should_skip(self, ctx: PipelineContext) -> bool:
        if not ctx.config.power.enabled:
            return True
        return False

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
        # Tighten capture window if PMU timing data is available.
        estimated = _estimate_capture_duration(ctx)
        configured = ctx.config.power.duration_s
        if estimated is not None and estimated < configured:
            log.info(
                "Auto-tuned capture duration: %.1fs (estimated) vs %.1fs (configured)",
                estimated,
                configured,
            )
            capture_duration = estimated
        else:
            capture_duration = configured

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

        ctx.power_result = power_result
        log.info(
            "Captured power data (%.1fs, driver=%s, mode=%s)",
            capture_duration,
            driver_name,
            mode,
        )
