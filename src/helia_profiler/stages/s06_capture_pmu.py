"""Stage 6 — Capture PMU data via SWO."""

from __future__ import annotations

import logging

from ..errors import CaptureError
from ..pipeline import PipelineContext

log = logging.getLogger("hpx")


class CapturePmuStage:
    @property
    def name(self) -> str:
        return "capture_pmu"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        if ctx.binary_path is None:
            raise CaptureError("No binary deployed — build/flash stages did not run.")

        from ..capture import capture_pmu

        try:
            pmu_result = capture_pmu(ctx)
        except CaptureError:
            raise
        except Exception as exc:
            raise CaptureError(
                f"Capture failed: {exc}",
                hint="Check serial/SWO connection to the target board.",
            ) from exc

        ctx.pmu_result = pmu_result
        log.info("Captured PMU data: %d layers", len(pmu_result.layers))
