"""Collect and reconcile post-GATE power-firmware terminal status."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..errors import PowerError
from ..pipeline import PipelineContext
from ..power.base import PowerResult, PowerSummary
from ..power.metadata import (
    MeasurementScope,
    ObservationMode,
    PowerIntegrity,
    PowerMetadata,
)
from ..power.diagnostics import (
    FROZEN_WINDOW_CLOCK_HINT,
    assess_run_window_clock,
    assess_window_clock_ceiling,
    expected_terminal_requested_count,
    firmware_window_clock_is_frozen,
)


def _host_phase_envelope_s(ctx: PipelineContext) -> float | None:
    """Host wall time from starting the power binary to collecting its record.

    ``DeploymentRecord.deployed_at`` is the cleanest timestamp already in the
    pipeline for "the power phase began": ``FlashPowerFirmwareStage`` stamps it
    immediately after the J-Link recipe programs and releases the target, and
    in internal mode nothing touches the device again before this stage (the
    capture stage is skipped -- see ``CapturePowerStage.should_skip``). No new
    plumbing is needed. Note the record itself is never serialized -- what
    reaches an artifact is the derived ``window_clock_ceiling`` metadata this
    stage stores, not ``deployed_at``.

    The interval is a deliberate over-estimate of the measured window: it also
    contains flash-tool exit, boot, engine/model init, and the post-window
    terminal emit plus this stage's own wait. That makes it a loose ceiling, not
    a duration estimate -- see ``WindowClockCeiling`` for what that costs in
    sensitivity.

    Returns ``None`` when there is no deployment record or the timestamp cannot
    be parsed, so the caller simply has nothing to check rather than inventing
    a bound. Note this reads the host wall clock, not a monotonic one -- a clock
    step between the two reads would skew it, which is a reason this check warns
    rather than fails.
    """
    deployment = ctx.power_run.deployment if ctx.power_run is not None else None
    if deployment is None:
        return None
    try:
        started = datetime.fromisoformat(deployment.deployed_at)
    except (ValueError, TypeError):
        # Unreachable while deployed_at is always an ISO string, but a
        # warn-only diagnostic must never be the thing that crashes a run.
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - started).total_seconds()

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

        expected_requested = expected_terminal_requested_count(
            inference_count=plan.inference_count,
            clean_window_probe=ctx.config.profiling.clean_window_probe,
        )
        if expected_requested is not None and terminal.requested_count != expected_requested:
            raise PowerError(
                "Power terminal requested count does not match the host plan.",
                hint=(
                    f"Firmware reported {terminal.requested_count}, host expected "
                    f"{expected_requested}."
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
        frozen_window_clock = firmware_window_clock_is_frozen(
            elapsed_us=terminal.elapsed_us,
            completed_count=terminal.completed_count,
        )
        if frozen_window_clock and internal_mode:
            # The firmware says it ran N inferences in zero time, and in
            # internal mode that number IS the denominator: the parser requires
            # MEASUREMENT_DURATION_US == ELAPSED_US, so average power and
            # current are wrong by the same factor. The measurement of record
            # is corrupt, which is terminal here for the same reason the
            # all-zero INA228 reading above is.
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
                metadata=PowerMetadata(
                    measurement_scope=MeasurementScope.ON_DEVICE_GATED_INFERENCE,
                    observation_mode=ObservationMode.ON_DEVICE,
                    integrity=PowerIntegrity.VALID,
                    source=measurement.source,
                    inference_count=measurement.inference_count,
                ),
            )
        if frozen_window_clock:
            # External mode: warn, do not raise. The instrument owns every
            # published power number here, so a frozen firmware clock corrupts
            # elapsed_us and nothing else -- the Apollo3 baseline capture
            # reported elapsed_us=0 with average power still correct to 0.19%.
            # Raising would throw away a good capture, and would do it before
            # GenerateReportStage, so the run would produce no artifact at all
            # and the downstream validity issue could never be seen. Same shape
            # as the bystander-overflow path below.
            log.warning(
                "Power firmware reported zero elapsed time for %d completed "
                "inferences: its window clock never advanced. The %s owns this "
                "run's power numbers and they are unaffected; only the "
                "firmware-reported window duration is meaningless. %s",
                terminal.completed_count,
                ctx.config.power.driver,
                FROZEN_WINDOW_CLOCK_HINT,
            )
        # Non-fatal cross-check of the firmware's own window clock against an
        # independent measurement of the same work. Warning-only: the tolerances
        # here are set from a narrow bench envelope -- wide enough that only a
        # real timing fault trips them, not wide enough to promise no false
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
            # Internal mode's reference is `count x reference_us`. #112
            # withheld it for probes that run no inferences, because the plan
            # then multiplied a per-inference time it had no business using --
            # against a ~1 s spin window that reference was ~5 ms, so it fired
            # on every CORRECT run and stayed silent on a mis-sized one.
            #
            # The plan now describes a busy_loop window in the probe's own
            # units (one unit lasting window_target_ms, count_source
            # "probe_window"), so the same product IS the right reference and
            # is passed through. Withholding it here would leave internal mode
            # with no duration check at all -- which is what #125 flagged, and
            # matters because in internal mode `elapsed_us` is the denominator
            # for average power and current.
            #
            # External mode is unaffected either way: its reference is the
            # instrument's own gate, which timed the same physical window
            # whatever ran inside it.
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
        if internal_mode:
            # Plan- and hardware-independent ceiling. The plan comparison above
            # can only ever be as good as a different binary's timing, but the
            # host knows for a fact when it started this binary and when it
            # collected the record -- the measured window is strictly inside
            # that interval. This is what catches the AP4-style ~7x inflation
            # for a user with no external instrument.
            ceiling = assess_window_clock_ceiling(
                elapsed_us=terminal.elapsed_us,
                host_envelope_s=_host_phase_envelope_s(ctx),
            )
            if ceiling is not None:
                if ctx.power_result is not None:
                    # Recorded so evaluation.validity can re-derive the same
                    # verdict later; it has no "now" of its own. Also copied
                    # into summary.json (report/summary.py's power-metadata
                    # allowlist) so the envelope comparison a
                    # power.window_clock_exceeds_host_time warning refers to
                    # is visible from the summary alone, not just the
                    # validity issue's context.
                    ctx.power_result.metadata.window_clock_ceiling = ceiling
                if ceiling.exceeded:
                    log.warning(
                        "Power firmware reported a %.6f s window, but only "
                        "%.6f s of host wall time elapsed between starting the "
                        "power binary and collecting its record (%.1fx). A "
                        "window cannot outlast the interval that contains it, "
                        "so the firmware's window clock is wrong; integrated "
                        "energy and charge are unaffected.",
                        ceiling.elapsed_s,
                        ceiling.host_envelope_s,
                        ceiling.ratio,
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
