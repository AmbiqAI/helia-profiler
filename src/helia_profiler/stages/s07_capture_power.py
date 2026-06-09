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

from ..errors import PowerError
from ..pipeline import PipelineContext

log = logging.getLogger("hpx")

# Guard periods for estimated-duration auto-terminate
_BOOT_SETTLE_S = 4.0  # power-cycle settle + SBL + firmware init
_SAFETY_MARGIN_S = 3.0  # extra headroom beyond estimated runtime


def _estimate_capture_duration(ctx: PipelineContext) -> float | None:
    """Estimate how long the firmware needs to run from PMU timing data.

    After a power-cycle, the firmware boots from MRAM and re-runs all
    presets × (warmup + profiled iterations).  Each iteration invokes the
    model once.  We know the per-inference cycle count from the PMU result
    and the clock frequency from the resolved CPU clock selection.

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
    num_presets = len(pmu.presets) or 1
    warmup = ctx.config.profiling.warmup
    iterations = ctx.config.profiling.iterations
    total_inferences = num_presets * (warmup + iterations)

    inference_time_s = cycles_per_inference / clock_hz
    firmware_run_s = total_inferences * inference_time_s

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
