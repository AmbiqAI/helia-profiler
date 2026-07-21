"""Resolve independent inputs for a power firmware/capture run."""

from __future__ import annotations

import logging

from ..results import PowerRunPlan
from ..config import DEFAULT_POWER_WINDOW_TARGET_MS
from ..errors import PowerError
from ..pipeline import PipelineContext

log = logging.getLogger("hpx")


def _derive_inference_count(
    *,
    clean_infer_avg_us: int | None,
    target_duration_ms: int,
    window_min: int,
    window_max: int,
) -> int | None:
    """Choose enough iterations to meet the target duration, then clamp."""
    if clean_infer_avg_us is None or clean_infer_avg_us <= 0:
        return None
    target_us = target_duration_ms * 1000
    count = (target_us + clean_infer_avg_us - 1) // clean_infer_avg_us
    return max(window_min, min(window_max, count))


def plan_power_run(
    ctx: PipelineContext,
    *,
    inference_count: int | None = None,
) -> PowerRunPlan:
    """Create a standalone power plan, optionally guided by an external N."""
    if inference_count is not None and inference_count < 1:
        raise PowerError("Power inference count must be at least 1.")

    reference_us = None
    if ctx.pmu_result is not None:
        reference_us = ctx.pmu_result.meta.clean_infer_avg_us

    target_duration_ms = max(
        ctx.config.profiling.window_target_ms,
        DEFAULT_POWER_WINDOW_TARGET_MS,
    )
    if ctx.config.power.firmware != "dedicated":
        inference_count = None
        count_source = "firmware_auto"
    elif inference_count is None:
        inference_count = _derive_inference_count(
            clean_infer_avg_us=reference_us,
            target_duration_ms=target_duration_ms,
            window_min=ctx.config.profiling.window_min,
            window_max=ctx.config.profiling.window_max,
        )
        count_source = "profile_guided" if inference_count is not None else "firmware_auto"
    else:
        count_source = "configured"

    return PowerRunPlan(
        firmware_mode=ctx.config.power.firmware,
        inference_count=inference_count,
        reference_inference_us=reference_us,
        target_duration_ms=target_duration_ms,
        count_source=count_source,
    )


class PlanPowerRunStage:
    def __init__(self, *, inference_count: int | None = None) -> None:
        self._inference_count = inference_count

    @property
    def name(self) -> str:
        return "plan_power_run"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return not ctx.config.power.enabled

    def run(self, ctx: PipelineContext) -> None:
        from ..power import get_driver

        driver = get_driver(
            ctx.config.power.driver,
            serial=ctx.config.power.serial,
        )
        if driver.mode is not ctx.config.power.mode:
            raise PowerError(
                f"Power driver '{ctx.config.power.driver}' uses mode "
                f"'{driver.mode.value}', but power.mode is "
                f"'{ctx.config.power.mode.value}'.",
                hint="Select a driver and power.mode with matching ownership.",
            )
        if (
            driver.mode.value == "internal"
            and not getattr(driver, "supports_firmware_measurement", False)
        ):
            raise PowerError(
                f"Power driver '{ctx.config.power.driver}' has no firmware-side "
                "measurement producer yet.",
                hint=(
                    "Implement its fixed-N monitor start/stop and "
                    "PowerTerminalEnvelope emission before enabling internal mode."
                ),
            )
        ctx.publish_power_plan(
            plan_power_run(ctx, inference_count=self._inference_count)
        )
        log.info(
            "Power plan: firmware=%s count=%s source=%s",
            ctx.power_plan.firmware_mode,
            ctx.power_plan.inference_count or "auto",
            ctx.power_plan.count_source,
        )
        count = ctx.power_plan.inference_count
        reference_us = ctx.power_plan.reference_inference_us
        if count is not None and reference_us is not None:
            runtime_s = count * reference_us / 1_000_000
            ctx.report_progress(
                f"Power run planned · {count:,} inferences",
                kind="checkpoint",
                eta_s=runtime_s,
                min_verbosity=0,
            )
