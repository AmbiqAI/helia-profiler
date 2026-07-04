"""Stage 7 — Capture power data via configured power driver (optional).

When using an external Joulescope driver, this stage performs a clean
power-cycle reset (via the Joulescope relay) before capturing.  This
eliminates the ~300 µA debug-domain overhead that a J-Link reset leaves
behind, giving accurate baseline power numbers.

If PMU results are available from a preceding capture stage, the capture
duration is automatically tightened to `boot_time + firmware_run_time +
margin` so that short models don't wait for the full `duration_s` timeout.
"""

from __future__ import annotations

import logging

from ..config import DEFAULT_POWER_WINDOW_TARGET_MS
from ..errors import PowerError
from ..pipeline import PipelineContext

log = logging.getLogger("hpx")

# Guard periods for estimated-duration auto-terminate
_BOOT_SETTLE_S = 8.0  # power-cycle settle + SBL + firmware init
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
        from ..power import get_driver

        driver_name = ctx.config.power.driver
        mode = ctx.config.power.mode
        log.info("Power driver: %s (mode: %s)", driver_name, mode)

        # --- Power-cycle reset for accurate measurement ---
        # Let the driver decide whether it supports power cycling.
        # External Joulescope drivers cut and restore target power,
        # giving a clean boot with zero debug-domain overhead.
        # Drivers that can't power-cycle (e.g. ondevice) raise PowerError.
        driver = get_driver(driver_name, serial=ctx.config.power.serial)
        try:
            driver.power_cycle(off_time_s=0.5, settle_time_s=2.0)
            log.info("Clean power-cycle reset — no debug-domain overhead")
        except PowerError:
            log.warning(
                "Power-cycle reset not available for '%s' — "
                "power numbers may include ~300 µA debug-domain overhead.",
                driver_name,
            )

        # --- Re-launch the firmware so the gated window fires under the poller ---
        # PMU/clean capture already consumed the firmware's single gated pass.
        # Boards that draw bench power from USB are not rebooted by the relay
        # cut above, so the clean window never re-runs and the Joulescope sees no
        # GPIO-high strobe.  A J-Link reset restarts the firmware deterministically
        # (it parks at hpx_sync_wait_go in lock-step), so power capture arms GO
        # and watches the window from the start.  USB CDC also needs the reset —
        # DTR release only frees the *first* boot, so after PMU the device is
        # idle; capture re-opens the CDC port to release the post-reset boot.
        if ctx.soc and ctx.soc.jlink_device:
            from ..jlink import reset_target, reset_target_poi
            from ..platform import SocFamily

            reset_target(
                device=ctx.soc.jlink_device,
                jlink_serial=ctx.resolved_jlink_serial or ctx.config.target.jlink_serial,
            )
            # Apollo5-family only: a debug-level reset alone leaves PMU/
            # power-management registers untouched, which was found
            # (2026-07-02, AP510 KWS LP) to measurably inflate steady-state
            # power (~8.2 mW vs ~6.9 mW for identical firmware) relative to
            # a true power-on-initialization reset.  neuralSPOT AutoDeploy
            # performs this exact extra reset before every power
            # measurement; mirror it here so HPX numbers are not biased
            # high by leftover debug-domain/PMU state.  Not yet validated
            # on Apollo3/Apollo4, so scoped to AP5 only.
            if ctx.soc.family is SocFamily.AP5:
                reset_target_poi(
                    device=ctx.soc.jlink_device,
                    jlink_serial=ctx.resolved_jlink_serial or ctx.config.target.jlink_serial,
                )

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
            power_result = capture_power(ctx, duration_override_s=capture_duration)
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
