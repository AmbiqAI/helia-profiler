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
        if ctx.profile_run is None or ctx.profile_run.deployment is None:
            raise CaptureError("No profile firmware deployed — build/flash stages did not run.")

        from ..capture import capture_pmu

        ctx.report_progress("Resetting target and waiting for profile output")

        try:
            pmu_result = capture_pmu(ctx)
        except CaptureError:
            raise
        except Exception as exc:
            raise CaptureError(
                f"Capture failed: {exc}",
                hint="Check serial/SWO connection to the target board.",
            ) from exc

        ctx.publish_profile_result(pmu_result)
        log.info("Captured PMU data: %d layers", len(pmu_result.layers))
        clean_us = pmu_result.meta.clean_infer_avg_us
        timing = f" · {clean_us / 1000:.3f} ms/inference" if clean_us else ""
        ctx.report_progress(
            f"Profile captured · {len(pmu_result.layers)} layers{timing}",
            kind="checkpoint",
            min_verbosity=0,
        )
