"""Stage 6 — Capture PMU data from target hardware."""

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
            pmu_raw = capture_pmu(ctx)
        except CaptureError:
            raise
        except Exception as exc:
            raise CaptureError(
                f"PMU data capture failed: {exc}",
                hint="Check serial/SWO connection to the target board.",
            ) from exc

        ctx.pmu_raw = pmu_raw
        layer_count = len(pmu_raw.get("layers", []))
        log.info("Captured PMU data: %d layers", layer_count)
