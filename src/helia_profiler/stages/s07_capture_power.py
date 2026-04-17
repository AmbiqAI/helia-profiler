"""Stage 7 — Capture power data via configured power driver (optional)."""

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
        return not ctx.config.power.enabled

    def run(self, ctx: PipelineContext) -> None:
        from ..capture import capture_power

        driver_name = ctx.config.power.driver
        mode = ctx.config.power.mode
        log.info("Power driver: %s (mode: %s)", driver_name, mode)

        try:
            power_raw = capture_power(ctx)
        except PowerError:
            raise
        except Exception as exc:
            raise PowerError(
                f"Power capture failed: {exc}",
                hint=(f"Check that the {driver_name} is connected and powered on. Mode: {mode}."),
            ) from exc

        ctx.power_raw = power_raw
        log.info(
            "Captured power data (%.1fs, driver=%s, mode=%s)",
            ctx.config.power.duration_s,
            driver_name,
            mode,
        )
