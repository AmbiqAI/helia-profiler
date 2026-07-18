"""Collect and reconcile post-GATE power-firmware terminal status."""

from __future__ import annotations

import logging

from ..errors import PowerError
from ..pipeline import PipelineContext
from ..power.base import PowerResult, PowerSummary

log = logging.getLogger("hpx")


class CollectPowerTerminalStage:
    @property
    def name(self) -> str:
        return "collect_power_terminal"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return (
            not ctx.config.power.enabled
            or ctx.power_run is None
            or ctx.power_run.plan.firmware_mode != "dedicated"
        )

    def run(self, ctx: PipelineContext) -> None:
        from ..power.terminal_transport import get_power_terminal_transport

        if ctx.power_run is None or ctx.power_run.firmware is None:
            raise PowerError("Cannot collect terminal status without power firmware.")
        internal_mode = ctx.config.power.mode.value == "internal"
        if ctx.power_run.observation is None and not internal_mode:
            raise PowerError("Cannot collect terminal status before power observation.")
        if ctx.soc is None:
            raise PowerError("Cannot collect terminal status before platform resolution.")

        plan = ctx.power_run.plan
        if ctx.power_run.observation is not None:
            timeout_s = max(2.0, min(10.0, ctx.power_run.observation.deadline_s / 10.0))
        else:
            planned_s = (
                plan.inference_count * plan.reference_inference_us / 1_000_000
                if plan.inference_count is not None and plan.reference_inference_us is not None
                else 5.0
            )
            timeout_s = max(5.0, min(30.0, planned_s * 2.0 + 5.0))
        ctx.report_progress("Collecting post-GATE firmware diagnostics", eta_s=timeout_s)
        collector = get_power_terminal_transport(ctx.config.target.transport)
        envelope = collector.collect(ctx, timeout_s=timeout_s)
        terminal = envelope.terminal

        if plan.inference_count is not None and terminal.requested_count != plan.inference_count:
            raise PowerError(
                "Power terminal requested count does not match the host plan.",
                hint=(
                    f"Firmware reported {terminal.requested_count}, host planned "
                    f"{plan.inference_count}."
                ),
            )
        if terminal.status != "ok":
            raise PowerError(
                f"Power firmware reported error {terminal.error_code} in phase "
                f"{terminal.final_phase} after {terminal.completed_count}/"
                f"{terminal.requested_count} inferences."
            )
        if terminal.completed_count != terminal.requested_count:
            raise PowerError(
                "Power firmware reported incomplete inference execution.",
                hint=(
                    f"Completed {terminal.completed_count}/"
                    f"{terminal.requested_count} inferences."
                ),
            )
        if not terminal.gate_lowered:
            raise PowerError("Power firmware did not confirm that GATE was lowered.")
        if envelope.measurement is not None and envelope.measurement.overflow:
            raise PowerError("On-device power monitor reported accumulator overflow.")
        if internal_mode and envelope.measurement is None:
            raise PowerError("Internal power mode requires an on-device measurement payload.")

        ctx.publish_power_terminal_envelope(envelope)
        if internal_mode and envelope.measurement is not None:
            measurement = envelope.measurement
            duration_s = measurement.duration_us / 1_000_000.0
            energy_j = measurement.energy_nj / 1_000_000_000.0
            average_power_w = energy_j / duration_s if duration_s > 0 else 0.0
            average_current_a = (
                measurement.charge_nc / 1_000_000_000.0 / duration_s
                if measurement.charge_nc is not None and duration_s > 0
                else 0.0
            )
            ctx.power_result = PowerResult(
                summary=PowerSummary(
                    avg_current_a=average_current_a,
                    avg_power_w=average_power_w,
                    peak_current_a=0.0,
                    energy_j=energy_j,
                    duration_s=duration_s,
                    sample_count=measurement.sample_count or 0,
                ),
                metadata={
                    "measurement_scope": "on_device_gated_inference",
                    "observation_mode": "on_device",
                    "integrity": "valid",
                    "source": measurement.source,
                    "inference_count": measurement.inference_count,
                },
            )
        log.info(
            "Power terminal: status=%s count=%d elapsed_us=%s phase=%s",
            terminal.status,
            terminal.completed_count,
            terminal.elapsed_us,
            terminal.final_phase,
        )
        ctx.report_progress(
            f"Firmware confirmed {terminal.completed_count:,} inferences",
            kind="checkpoint",
            min_verbosity=0,
        )
