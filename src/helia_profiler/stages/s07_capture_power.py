"""Stage 7 — Capture power data via Joulescope (optional)."""

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

        try:
            power_raw = capture_power(ctx)
        except ImportError as exc:
            raise PowerError(
                f"Joulescope package not installed: {exc}",
                hint="Install with: pip install 'helia-profiler[power]' or "
                     "pip install joulescope",
            ) from exc
        except PowerError:
            raise
        except Exception as exc:
            raise PowerError(
                f"Power capture failed: {exc}",
                hint="Check that the Joulescope is connected and powered on.",
            ) from exc

        ctx.power_raw = power_raw
        log.info("Captured power data (%.1fs)", ctx.config.power.duration_s)
