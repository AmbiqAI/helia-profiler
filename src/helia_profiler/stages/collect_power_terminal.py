"""Collect and reconcile post-GATE power-firmware terminal status."""

from __future__ import annotations

import logging

from ..errors import PowerError
from ..pipeline import PipelineContext
from ..power.base import PowerResult, PowerSummary
from ..power.diagnostics import (
    FROZEN_WINDOW_CLOCK_HINT,
    assess_run_window_clock,
    firmware_window_clock_is_frozen,
)

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
        if firmware_window_clock_is_frozen(
            elapsed_us=terminal.elapsed_us,
            completed_count=terminal.completed_count,
        ):
            # Terminal, in every mode, with no instrument required: the
            # firmware says it ran N inferences in zero time. Everything else
            # about the run still looks healthy (status ok, counts matched,
            # both gate edges seen, energy integrated in hardware), which is
            # precisely why this needs its own gate -- an Apollo3 capture with
            # this exact signature passed every other check and published
            # average power derived from a frozen counter.
            raise PowerError(
                "Power firmware reported zero elapsed time for "
                f"{terminal.completed_count} completed inferences.",
                hint=FROZEN_WINDOW_CLOCK_HINT,
            )
        if envelope.measurement is not None and envelope.measurement.overflow:
            if not internal_mode:
                # The monitor is present but is not this run's measurement of
                # record (e.g. an INA228 left on the bus while a Joulescope
                # measures, or with its sense inputs disconnected). Its health
                # flags are informational — failing the run would discard a
                # perfectly good external capture.
                log.warning(
                    "On-device power monitor reported accumulator overflow; "
                    "ignoring because %s is the measurement of record.",
                    ctx.config.power.driver,
                )
            else:
                measurement = envelope.measurement
                raise PowerError(
                    "On-device power monitor reported accumulator overflow.",
                    hint=(
                        f"Monitor saw energy={measurement.energy_nj} nJ, "
                        f"charge={measurement.charge_nc} nC, "
                        f"bus_voltage={measurement.bus_voltage_uv} uV over "
                        f"{measurement.duration_us} us. An implausible bus "
                        "voltage usually means the VBUS sense input is "
                        "floating — wire it to the target-side rail node. "
                        "Check the 'Power firmware diagnostic' log line for "
                        "the raw DIAG bits (0x200=MATHOF, 0x400=CHARGEOF, "
                        "0x800=ENERGYOF)."
                    ),
                )
        if internal_mode and envelope.measurement is None:
            raise PowerError("Internal power mode requires an on-device measurement payload.")
        if internal_mode and envelope.measurement is not None:
            measurement = envelope.measurement
            # Plausibility gates for the measurement of record. Both
            # signatures pass every register-level health check (no DIAG bit,
            # firmware status ok), so without these the run would publish a
            # confidently wrong number.
            if measurement.energy_nj == 0 and measurement.charge_nc == 0:
                # A window shorter than one accumulator update, or a dead
                # sense path, reads exactly zero while bus voltage (which
                # needs no calibration) still looks perfect.
                raise PowerError(
                    "On-device power monitor measured exactly zero energy and charge.",
                    hint=(
                        "Either the window contained no completed accumulator "
                        "update (averaging_count x 2 x conversion_time_us "
                        "longer than the window) or no current flows through "
                        "the shunt. Check the IN+/IN- sense wiring and the "
                        "conversion/averaging settings."
                    ),
                )
            # energy_nj and charge_nc are rounded to integers independently
            # on-device, so a true charge below the 1 nC step reports as 0
            # legitimately. Bound the energy such a charge could have carried
            # (E = Q x V) and require an ample margin over it, so a genuinely
            # tiny measurement is never misdiagnosed as miswiring. Real
            # reversed wiring overshoots this by many orders of magnitude.
            rounding_energy_nj = 0.5 * (measurement.bus_voltage_uv or 0) / 1_000_000
            if measurement.energy_nj > 100 * rounding_energy_nj and measurement.charge_nc == 0:
                # ENERGY integrates |power| while CHARGE is signed: reversed
                # IN+/IN- accumulates negative charge, which the firmware
                # clamps to zero. Substantial energy with zero charge is that
                # miswiring's exact signature.
                raise PowerError(
                    "On-device power monitor reports energy but zero charge.",
                    hint=(
                        "This is the signature of reversed sense wiring: "
                        "swap the INA228's IN+/IN- connections (current must "
                        "flow IN+ -> IN-)."
                    ),
                )

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
        # Non-fatal cross-check of the firmware's own window clock against an
        # independent measurement of the same work. Warning-only: the frozen
        # (0x) case above is already terminal, and the tolerances here are set
        # from a two-board bench envelope -- wide enough that only a real
        # timing fault trips them, not wide enough to promise no false
        # positives on hardware nobody has run yet. See
        # power.diagnostics.EXTERNAL_/INTERNAL_WINDOW_CLOCK_TOLERANCE.
        agreement = assess_run_window_clock(
            elapsed_us=terminal.elapsed_us,
            internal_mode=internal_mode,
            gated_result=(
                ctx.power_run.observation.result
                if ctx.power_run.observation is not None
                else None
            ),
            planned_inference_count=plan.inference_count,
            planned_inference_us=plan.reference_inference_us,
        )
        if agreement is not None and not agreement.agrees:
            log.warning(
                "Power firmware reported a %.6f s window but the %s measured "
                "%.6f s (%.1f%% apart, tolerance %.0f%%). The firmware's "
                "window clock and the reference disagree, so elapsed time, "
                "average power and average current derived from it are "
                "suspect; integrated energy and charge are not.",
                agreement.elapsed_s,
                agreement.reference_source,
                agreement.reference_s,
                agreement.relative_error * 100.0,
                agreement.relative_tolerance * 100.0,
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
