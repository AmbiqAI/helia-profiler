"""Stage 7 — Capture power data via configured power driver (optional).

When using an external Joulescope driver, this stage performs a clean
power-cycle reset (via the Joulescope relay) before capturing.  This
eliminates the ~300 µA debug-domain overhead that a J-Link reset leaves
behind, giving accurate baseline power numbers.
"""

from __future__ import annotations

import logging

from ..errors import PowerError
from ..pipeline import PipelineContext

log = logging.getLogger("hpx")


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
        try:
            power_result = capture_power(ctx)
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
            ctx.config.power.duration_s,
            driver_name,
            mode,
        )
