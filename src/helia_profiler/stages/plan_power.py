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


#: Minimum number of INA228 accumulator updates the measured window must
#: contain. The ENERGY/CHARGE accumulators advance only once per completed
#: averaged conversion set (averaging_count x (t_shunt + t_bus)), so a window
#: shorter than a few updates quantizes badly — and a window shorter than ONE
#: update reads exactly zero while looking perfectly healthy (no DIAG bit).
#: 20 updates bounds the boundary-phase error at ~5%.
MIN_INA228_ACCUMULATOR_UPDATES = 20


def _check_ina228_cadence(ctx: PipelineContext, plan: PowerRunPlan) -> None:
    """Reject internal-mode plans whose window undersamples the accumulator."""
    ina = ctx.config.power.ina228
    if ina is None or ctx.config.power.mode.value != "internal":
        return
    # CONT_BUS_SHUNT: one shunt + one bus conversion per averaging sample.
    update_period_us = ina.averaging_count * 2 * ina.conversion_time_us
    if plan.inference_count is not None and plan.reference_inference_us is not None:
        window_us = plan.inference_count * plan.reference_inference_us
        window_source = "planned window"
    else:
        window_us = plan.target_duration_ms * 1000
        window_source = "target duration"
    updates = window_us // update_period_us
    if updates < MIN_INA228_ACCUMULATOR_UPDATES:
        raise PowerError(
            f"INA228 accumulator would update only {updates}x in the "
            f"{window_source} ({window_us / 1e6:.2f} s): one update takes "
            f"averaging_count x 2 x conversion_time_us = "
            f"{update_period_us / 1e3:.1f} ms.",
            hint=(
                "The energy/charge accumulators advance once per completed "
                "averaged conversion set; too few updates quantizes the "
                "measurement (zero updates reads exactly 0 with no error "
                "flag). Reduce power.ina228.averaging_count or "
                "conversion_time_us, or lengthen the window "
                f"(need >= {MIN_INA228_ACCUMULATOR_UPDATES} updates)."
            ),
        )


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

    plan = PowerRunPlan(
        firmware_mode=ctx.config.power.firmware,
        inference_count=inference_count,
        reference_inference_us=reference_us,
        target_duration_ms=target_duration_ms,
        count_source=count_source,
    )
    _check_ina228_cadence(ctx, plan)
    return plan


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
