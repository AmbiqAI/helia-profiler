from __future__ import annotations

from tests.pipeline_context_helpers import set_power_result, set_profile_result

from dataclasses import replace
from pathlib import Path
from typing import Literal

from helia_profiler.results import (
    OnDevicePowerSummary,
    PowerObservation,
    PowerRun,
    PowerRunPlan,
    PowerTerminalRecord,
)
from helia_profiler.config import load_config
from helia_profiler.pipeline import PipelineContext
from helia_profiler.power.base import GatedPowerWindow, PowerResult, PowerSummary
from helia_profiler.power.metadata import (
    MeasurementScope,
    ObservationMode,
    PowerIntegrity,
    PowerMetadata,
)
from helia_profiler.power.diagnostics import (
    WINDOW_CLOCK_CEILING_SLACK_S,
    GateDurationIntegrity,
    WindowClockCeiling,
)
from helia_profiler.results import ResultValidity
from helia_profiler.results import FirmwareMeta, PmuResult
from helia_profiler.evaluation import evaluate_run
from helia_profiler.results.issues import IssueCode


def _context(tmp_path: Path, *, mode: str = "external", probe: str = "infer") -> PipelineContext:
    config = load_config(
        None,
        {
            "model": {"path": "test.tflite"},
            "engine": {"type": "helia-rt"},
            "profiling": {"clean_window_probe": probe, "window_target_ms": 1000},
            "power": {
                "enabled": True,
                "mode": mode,
                **({"driver": "ondevice"} if mode == "internal" else {}),
            },
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    set_profile_result(ctx, PmuResult(meta=FirmwareMeta(), layers=[]))
    result = PowerResult(
        summary=PowerSummary(0.01, 0.02, 0.03, 0.1, 1.0, 10),
        metadata=PowerMetadata(measurement_scope=MeasurementScope.GPIO_GATED_CLEAN_WINDOW),
    )
    observation = PowerObservation(
        mode=ObservationMode.GPIO_GATED,
        result=result,
        gate_rise_observed=True,
        gate_fall_observed=True,
        deadline_s=2.0,
        integrity=PowerIntegrity.VALID,
    )
    terminal = PowerTerminalRecord(
        version=1,
        status="ok",
        requested_count=10,
        completed_count=10,
        elapsed_us=1_000_000,
        final_phase="done",
        error_code=0,
        gate_asserted=True,
        gate_lowered=True,
    )
    ctx.power_run = PowerRun(
        plan=PowerRunPlan(firmware_mode="dedicated", inference_count=10),
        observation=observation,
        terminal=terminal,
    )
    return ctx


def test_valid_run_has_no_issues(tmp_path: Path):
    evaluation = evaluate_run(_context(tmp_path))

    assert evaluation.validity is ResultValidity.VALID
    assert evaluation.issues == ()


def test_degraded_observation_and_duration_mismatch_are_structured(tmp_path: Path):
    ctx = _context(tmp_path)
    assert ctx.power_run is not None and ctx.power_run.observation is not None
    observation = ctx.power_run.observation
    # Above the floor but outside the band, with no gated window for the
    # observer check to arbitrate -- the est*count fallback keeps authority.
    observation.result.metadata.gate_duration_integrity = GateDurationIntegrity(
        measured_s=0.85,
        expected_s=1.0,
        tolerance_s=0.01,
        minimum_s=0.5,
    )
    ctx.power_run = PowerRun(
        plan=ctx.power_run.plan,
        observation=PowerObservation(
            mode=ObservationMode.FREE_FORM,
            result=observation.result,
            gate_rise_observed=False,
            gate_fall_observed=False,
            deadline_s=2.0,
            integrity=PowerIntegrity.DEGRADED,
        ),
        terminal=ctx.power_run.terminal,
    )

    evaluation = evaluate_run(ctx)

    assert evaluation.validity is ResultValidity.DEGRADED
    assert {issue.code for issue in evaluation.issues} == {
        IssueCode.POWER_OBSERVATION_DEGRADED,
        IssueCode.POWER_GATE_DURATION_MISMATCH,
    }


def test_below_minimum_gate_is_an_error(tmp_path: Path):
    """The 1 s floor never needed arbitration: a below-floor gate is too
    short for the stats integral to be trusted regardless of what the
    firmware clock says (#142/#181 D1 -- previously this aborted the run at
    capture time with no artifact at all)."""
    ctx = _context(tmp_path)
    assert ctx.power_run is not None and ctx.power_run.observation is not None
    observation = ctx.power_run.observation
    observation.result.metadata.gate_duration_integrity = GateDurationIntegrity(
        measured_s=0.1,
        expected_s=1.0,
        tolerance_s=0.01,
        minimum_s=0.5,
    )

    evaluation = evaluate_run(ctx)

    assert evaluation.validity is ResultValidity.INVALID
    codes = [issue.code for issue in evaluation.issues]
    assert codes.count(IssueCode.POWER_GATE_BELOW_MINIMUM) == 1
    # One defect, one issue: the floor breach subsumes the band miss.
    assert IssueCode.POWER_GATE_DURATION_MISMATCH not in codes


def test_terminal_plan_and_on_device_mismatches_are_invalid(tmp_path: Path):
    ctx = _context(tmp_path)
    assert ctx.power_run is not None
    terminal = PowerTerminalRecord(
        version=1,
        status="ok",
        requested_count=9,
        completed_count=9,
        elapsed_us=1_000_000,
        final_phase="done",
        error_code=0,
        gate_asserted=True,
        gate_lowered=True,
    )
    ctx.power_run = PowerRun(
        plan=ctx.power_run.plan,
        observation=ctx.power_run.observation,
        terminal=terminal,
        on_device_summary=OnDevicePowerSummary(
            source="ina228",
            scope="fixed_n_inference",
            energy_nj=100,
            duration_us=1000,
            inference_count=8,
            overflow=False,
        ),
    )

    evaluation = evaluate_run(ctx)

    assert evaluation.validity is ResultValidity.INVALID
    assert {issue.code for issue in evaluation.issues} == {
        IssueCode.POWER_PLAN_COUNT_MISMATCH,
        IssueCode.POWER_ON_DEVICE_COUNT_MISMATCH,
    }


def test_pmu_overflow_is_invalid_without_power(tmp_path: Path):
    ctx = _context(tmp_path)
    ctx.power_run = None
    set_profile_result(ctx, PmuResult(meta=FirmwareMeta(), overflow_detected=True))

    evaluation = evaluate_run(ctx)

    assert evaluation.validity is ResultValidity.INVALID
    assert [issue.code for issue in evaluation.issues] == [IssueCode.PMU_COUNTER_OVERFLOW]


class TestWindowClockValidity:
    """The collect stage refuses a frozen window clock at capture time; this
    module is the downstream authority over an already-captured run and must
    reach the SAME verdict. A run the stage would reject must never evaluate
    as VALID here just because it arrived by another path (a replayed or
    resumed artifact, or a caller that skipped the stage) -- the half-fix where
    a stage gate and this policy disagreed has already happened once in this
    repo (see power.on_device_overflow).

    Values are the Apollo3 Blue Plus bench pair (2026-08): pre-fix reported
    elapsed_us=0 for 24/24 against a 4.963 s gate; fixed reported 4.970184 s
    against 4.967 s.
    """

    BENCH_COUNT = 24
    BENCH_REFERENCE_US = 208_744
    BENCH_GATE_S = 4.967
    BENCH_ELAPSED_US = 4_970_184

    def _bench_run(
        self,
        ctx: PipelineContext,
        *,
        elapsed_us: int,
        gate_s: float = BENCH_GATE_S,
        internal: bool = False,
        host_envelope_s: float | None = None,
    ) -> None:
        assert ctx.power_run is not None and ctx.power_run.observation is not None
        observation = ctx.power_run.observation
        result = PowerResult(
            summary=replace(observation.result.summary, duration_s=gate_s),
            # gated_windows is the ONLY source the window-clock check accepts;
            # a gated observation without one is the degraded shape, which is
            # exercised separately in test_degraded_capture_gains_no_window_*.
            gated_windows=[GatedPowerWindow(0.0, gate_s, gate_s, 0.0, 0.0, 0.0, 0.0, 0.0, 0)],
            metadata=replace(observation.result.metadata),
        )
        assert ctx.power_run.terminal is not None
        terminal = replace(
            ctx.power_run.terminal,
            requested_count=self.BENCH_COUNT,
            completed_count=self.BENCH_COUNT,
            elapsed_us=elapsed_us,
        )
        ctx.power_run = PowerRun(
            plan=PowerRunPlan(
                firmware_mode="dedicated",
                inference_count=self.BENCH_COUNT,
                reference_inference_us=self.BENCH_REFERENCE_US,
            ),
            observation=None if internal else replace(observation, result=result),
            terminal=terminal,
            # Internal mode requires an on-device measurement, or the run is
            # invalid for an unrelated reason and the window-clock verdict
            # cannot be read off the overall validity.
            on_device_summary=(
                OnDevicePowerSummary(
                    source="ina228",
                    scope="fixed_n_inference",
                    energy_nj=11_848_248,
                    duration_us=elapsed_us,
                    inference_count=self.BENCH_COUNT,
                    overflow=False,
                    charge_nc=6_582_360,
                    bus_voltage_uv=1_800_000,
                )
                # OnDevicePowerSummary itself forbids duration_us == 0 with
                # completed work, so a frozen internal run can never carry one
                # -- the stage raises before it would be built. Reproduce that
                # shape rather than an impossible one.
                if internal and elapsed_us > 0
                else None
            ),
        )
        if host_envelope_s is not None:
            # Exactly the shape the collect stage records; validity re-derives
            # `exceeded` from these numbers rather than trusting a verdict.
            set_power_result(
                ctx,
                PowerResult(
                    summary=replace(observation.result.summary, duration_s=gate_s),
                    metadata=PowerMetadata(
                        measurement_scope=MeasurementScope.ON_DEVICE_GATED_INFERENCE,
                        window_clock_ceiling=WindowClockCeiling(
                            elapsed_us=elapsed_us,
                            host_envelope_s=host_envelope_s,
                            slack_s=WINDOW_CLOCK_CEILING_SLACK_S,
                        ),
                    ),
                ),
            )

    def test_bench_agreement_is_valid(self, tmp_path: Path):
        ctx = _context(tmp_path)
        self._bench_run(ctx, elapsed_us=self.BENCH_ELAPSED_US)

        evaluation = evaluate_run(ctx)

        codes = {issue.code for issue in evaluation.issues}
        assert IssueCode.POWER_WINDOW_CLOCK_FROZEN not in codes
        assert IssueCode.POWER_WINDOW_CLOCK_MISMATCH not in codes

    def test_frozen_window_clock_is_an_error_in_internal_mode(self, tmp_path: Path):
        """Internal mode divides energy by this duration, so the published
        power is corrupt -- the measurement of record is unusable."""
        ctx = _context(tmp_path, mode="internal")
        self._bench_run(ctx, elapsed_us=0, gate_s=4.963, internal=True)

        evaluation = evaluate_run(ctx)

        assert evaluation.validity is ResultValidity.INVALID
        frozen = [
            issue
            for issue in evaluation.issues
            if issue.code == IssueCode.POWER_WINDOW_CLOCK_FROZEN
        ]
        assert len(frozen) == 1
        assert frozen[0].severity == "error"
        assert frozen[0].context["completed_count"] == self.BENCH_COUNT

    def test_frozen_window_clock_is_only_a_warning_in_external_mode(self, tmp_path: Path):
        """The instrument owns the power numbers and they are fine -- the
        Apollo3 baseline capture had elapsed_us=0 and average power correct to
        0.19%. Invalidating would block comparability of a sound capture, so
        this degrades rather than invalidates (same shape as the bystander
        on_device_overflow split)."""
        ctx = _context(tmp_path)
        self._bench_run(ctx, elapsed_us=0, gate_s=4.963)

        evaluation = evaluate_run(ctx)

        frozen = [
            issue
            for issue in evaluation.issues
            if issue.code == IssueCode.POWER_WINDOW_CLOCK_FROZEN
        ]
        assert len(frozen) == 1
        assert frozen[0].severity == "warning"
        assert evaluation.validity is ResultValidity.DEGRADED

    def test_frozen_window_clock_message_is_probe_aware(self, tmp_path: Path):
        """#172 round-3: the ninth 'completed inferences' site — a busy-loop
        run completes busy-loop passes, and this is exactly the diagnostic a
        user reads while already distrusting the numbers."""
        ctx = _context(tmp_path, probe="busy_loop")
        self._bench_run(ctx, elapsed_us=0, gate_s=4.963)

        evaluation = evaluate_run(ctx)

        frozen = [
            issue
            for issue in evaluation.issues
            if issue.code == IssueCode.POWER_WINDOW_CLOCK_FROZEN
        ]
        assert len(frozen) == 1
        assert "busy-loop pass" in frozen[0].message
        assert "inference" not in frozen[0].message

    def test_frozen_window_clock_suppresses_the_agreement_warning(self, tmp_path: Path):
        """Zero elapsed is reported once, as the error that explains it -- not
        also as a 100%-apart mismatch warning saying the same thing worse."""
        ctx = _context(tmp_path)
        self._bench_run(ctx, elapsed_us=0, gate_s=4.963)

        codes = [issue.code for issue in evaluate_run(ctx).issues]

        assert codes.count(IssueCode.POWER_WINDOW_CLOCK_MISMATCH) == 0

    def test_external_disagreement_is_the_observer_error(self, tmp_path: Path):
        """Apollo4's ~7x inflation: two independent clocks watched the SAME
        physical window in the same boot, so drift cannot explain a miss --
        the gate did not bracket what the firmware timed, and every
        per-inference figure divided out of it inherits the error. Promoted
        from warning to the authoritative ERROR by the #142/#181 redesign
        (cross-family evidence at diagnostics.py's tolerance comment)."""
        ctx = _context(tmp_path)
        self._bench_run(ctx, elapsed_us=int(self.BENCH_ELAPSED_US * 6027 / 866.6))

        evaluation = evaluate_run(ctx)

        assert evaluation.validity is ResultValidity.INVALID
        mismatch = [
            issue
            for issue in evaluation.issues
            if issue.code == IssueCode.POWER_WINDOW_OBSERVER_MISMATCH
        ]
        assert len(mismatch) == 1
        assert mismatch[0].severity == "error"
        assert mismatch[0].context["reference_source"] == "gated_windows"
        # The old external warning code must not double up on the same defect.
        codes = [issue.code for issue in evaluation.issues]
        assert IssueCode.POWER_WINDOW_CLOCK_MISMATCH not in codes

    def test_degraded_capture_gains_no_window_clock_issue(self, tmp_path: Path):
        """A degraded capture has no gated window, only a whole-capture
        free-form summary. Comparing the firmware clock against THAT invents a
        disagreement out of an unrelated interval: on two real Apollo4
        artifacts the firmware clock was accurate to 0.16% while the free-form
        capture ran 19.2 s against a ~5 s window -- a fabricated 73.9%
        mismatch stacked on top of the power.observation_degraded that already
        described the real failure. The run must carry exactly one issue."""
        ctx = _context(tmp_path)
        assert ctx.power_run is not None and ctx.power_run.observation is not None
        observation = ctx.power_run.observation
        degraded = PowerResult(
            # Whole 19.2 s capture retained for diagnostics; no gated window.
            summary=replace(observation.result.summary, duration_s=19.2),
            gated_windows=[],
            metadata=PowerMetadata(measurement_scope=MeasurementScope.FREE_FORM_CAPTURE),
        )
        assert ctx.power_run.terminal is not None
        ctx.power_run = PowerRun(
            plan=PowerRunPlan(
                firmware_mode="dedicated",
                inference_count=self.BENCH_COUNT,
                reference_inference_us=self.BENCH_REFERENCE_US,
            ),
            observation=PowerObservation(
                mode=ObservationMode.FREE_FORM,
                result=degraded,
                gate_rise_observed=True,
                gate_fall_observed=False,
                deadline_s=45.0,
                integrity=PowerIntegrity.DEGRADED,
            ),
            terminal=replace(
                ctx.power_run.terminal,
                requested_count=self.BENCH_COUNT,
                completed_count=self.BENCH_COUNT,
                elapsed_us=self.BENCH_ELAPSED_US,
            ),
        )

        codes = {issue.code for issue in evaluate_run(ctx).issues}

        assert codes == {IssueCode.POWER_OBSERVATION_DEGRADED}

    def test_the_two_modes_apply_different_tolerances(self, tmp_path: Path):
        """One 14% deviation, two verdicts: a real fault against a host-timed
        gate (same window, two observers -- the error code), expected
        cross-binary noise against the plan (warning code, and only past
        25%)."""
        external = _context(tmp_path)
        self._bench_run(external, elapsed_us=int(self.BENCH_GATE_S * 1e6 * 1.14))
        assert any(
            issue.code == IssueCode.POWER_WINDOW_OBSERVER_MISMATCH
            for issue in evaluate_run(external).issues
        )

        internal = _context(tmp_path, mode="internal")
        self._bench_run(
            internal,
            elapsed_us=int(self.BENCH_COUNT * self.BENCH_REFERENCE_US * 1.14),
            internal=True,
        )
        assert not any(
            issue.code == IssueCode.POWER_WINDOW_CLOCK_MISMATCH
            for issue in evaluate_run(internal).issues
        )

        # ...and the internal path is genuinely live rather than silently
        # returning "no reference": 7x against the same plan still warns.
        broken = _context(tmp_path, mode="internal")
        self._bench_run(
            broken,
            elapsed_us=int(self.BENCH_COUNT * self.BENCH_REFERENCE_US * 6.95),
            internal=True,
        )
        mismatch = [
            issue
            for issue in evaluate_run(broken).issues
            if issue.code == IssueCode.POWER_WINDOW_CLOCK_MISMATCH
        ]
        assert len(mismatch) == 1
        assert mismatch[0].context["reference_source"] == "planned_window"

    def test_internal_threshold_is_25_percent_not_50(self, tmp_path: Path):
        """30% from the plan warns, 14% does not -- the band the threshold
        revision moved. Mirrors the stage-side pin so a revert in either layer
        alone shows up here too."""
        near = _context(tmp_path, mode="internal")
        self._bench_run(
            near,
            elapsed_us=int(self.BENCH_COUNT * self.BENCH_REFERENCE_US * 1.14),
            internal=True,
        )
        assert not any(
            issue.code == IssueCode.POWER_WINDOW_CLOCK_MISMATCH
            for issue in evaluate_run(near).issues
        )

        far = _context(tmp_path, mode="internal")
        self._bench_run(
            far,
            elapsed_us=int(self.BENCH_COUNT * self.BENCH_REFERENCE_US * 1.30),
            internal=True,
        )
        assert any(
            issue.code == IssueCode.POWER_WINDOW_CLOCK_MISMATCH
            for issue in evaluate_run(far).issues
        )

    def test_window_longer_than_host_wall_time_is_a_warning(self, tmp_path: Path):
        """Physically impossible, but warning severity: a timestamp-plumbing
        bug would otherwise false-fail an otherwise good run. Revisit after
        soak time."""
        ctx = _context(tmp_path, mode="internal")
        self._bench_run(
            ctx,
            elapsed_us=int(self.BENCH_ELAPSED_US * 6027 / 866.6),
            internal=True,
            host_envelope_s=10.0,
        )

        evaluation = evaluate_run(ctx)

        exceeded = [
            issue
            for issue in evaluation.issues
            if issue.code == IssueCode.POWER_WINDOW_CLOCK_EXCEEDS_HOST_TIME
        ]
        assert len(exceeded) == 1
        assert exceeded[0].severity == "warning"
        assert exceeded[0].context["host_envelope_s"] == 10.0
        assert evaluation.validity is not ResultValidity.INVALID

    def test_window_inside_host_wall_time_raises_no_ceiling_issue(self, tmp_path: Path):
        ctx = _context(tmp_path, mode="internal")
        self._bench_run(
            ctx,
            elapsed_us=self.BENCH_ELAPSED_US,
            internal=True,
            host_envelope_s=10.0,
        )

        assert not any(
            issue.code == IssueCode.POWER_WINDOW_CLOCK_EXCEEDS_HOST_TIME
            for issue in evaluate_run(ctx).issues
        )


class TestGateArbitration:
    """#142/#181 redesign: the est*count band is a reference diagnostic and
    the firmware's STIMER window time arbitrates. Values are the AP510 EVB
    first-run-after-idle rejection from #181: gate 4.427 s against an
    est*count expectation of 5.017 s (-11.8%, outside the 10% band) while the
    window itself was healthy -- cold silicon clocks fast because the LP core
    clock is HFRC-derived, and total_cycles was constant to 2.5 ppm across
    the whole drift sweep."""

    DRIFT_COUNT = 233
    DRIFT_REFERENCE_US = 21_532  # profile boot: est*count = 5.017 s
    DRIFT_GATE_S = 4.427  # first run after long idle (#181)
    DRIFT_ELAPSED_US = 4_427_500  # firmware STIMER: agrees with the gate

    def _drift_run(
        self,
        ctx: PipelineContext,
        *,
        elapsed_us: int | None,
        minimum_s: float = 1.0,
        gate_s: float = DRIFT_GATE_S,
        completed_count: int | None = None,
    ) -> None:
        assert ctx.power_run is not None and ctx.power_run.observation is not None
        observation = ctx.power_run.observation
        result = PowerResult(
            summary=replace(observation.result.summary, duration_s=gate_s),
            gated_windows=[GatedPowerWindow(0.0, gate_s, gate_s, 0.0, 0.0, 0.0, 0.0, 0.0, 0)],
            metadata=replace(observation.result.metadata),
        )
        expected_s = self.DRIFT_COUNT * self.DRIFT_REFERENCE_US / 1_000_000.0
        result.metadata.gate_duration_integrity = GateDurationIntegrity(
            measured_s=gate_s,
            expected_s=expected_s,
            tolerance_s=expected_s * 0.10,
            minimum_s=minimum_s,
            relative_tolerance=0.10,
        )
        assert ctx.power_run.terminal is not None
        ctx.power_run = PowerRun(
            plan=PowerRunPlan(
                # No terminal envelope exists in shared mode -- the exact case
                # where the est*count fallback must keep its authority.
                firmware_mode="dedicated" if elapsed_us is not None else "shared",
                inference_count=self.DRIFT_COUNT,
                reference_inference_us=self.DRIFT_REFERENCE_US,
            ),
            observation=replace(observation, result=result),
            terminal=(
                replace(
                    ctx.power_run.terminal,
                    requested_count=self.DRIFT_COUNT,
                    completed_count=(
                        self.DRIFT_COUNT if completed_count is None else completed_count
                    ),
                    elapsed_us=elapsed_us,
                )
                if elapsed_us is not None
                else None
            ),
        )

    def test_cold_boot_drift_with_observer_agreement_is_valid(self, tmp_path: Path):
        """THE #181 scenario: est*count missed by 11.8%, but the firmware's
        own window clock confirms the gate bracketed exactly what it timed.
        The reference is stale, the capture is sound, and the per-inference
        denominator (the count) is untouched by drift -- a fully valid run.
        Before the redesign this aborted at capture time with no artifact."""
        ctx = _context(tmp_path)
        self._drift_run(ctx, elapsed_us=self.DRIFT_ELAPSED_US)

        evaluation = evaluate_run(ctx)

        assert evaluation.validity is ResultValidity.VALID
        assert evaluation.issues == ()

    def test_band_miss_without_envelope_keeps_its_warning(self, tmp_path: Path):
        """Shared firmware publishes no terminal envelope, so nothing can
        arbitrate: est*count keeps its original WARNING authority."""
        ctx = _context(tmp_path)
        self._drift_run(ctx, elapsed_us=None)

        evaluation = evaluate_run(ctx)

        assert evaluation.validity is ResultValidity.DEGRADED
        mismatch = [
            issue
            for issue in evaluation.issues
            if issue.code == IssueCode.POWER_GATE_DURATION_MISMATCH
        ]
        assert len(mismatch) == 1
        assert mismatch[0].severity == "warning"

    def test_observer_mismatch_is_reported_once(self, tmp_path: Path):
        """When the two observers disagree, the ERROR carries the whole
        story; the est*count warning must not pile on a restatement."""
        ctx = _context(tmp_path)
        self._drift_run(ctx, elapsed_us=int(self.DRIFT_GATE_S * 1e6 * 1.14))

        evaluation = evaluate_run(ctx)

        assert evaluation.validity is ResultValidity.INVALID
        codes = [issue.code for issue in evaluation.issues]
        assert codes.count(IssueCode.POWER_WINDOW_OBSERVER_MISMATCH) == 1
        assert IssueCode.POWER_GATE_DURATION_MISMATCH not in codes

    def test_frozen_envelope_returns_authority_to_the_band(self, tmp_path: Path):
        """A frozen firmware clock cannot arbitrate anything: the frozen
        warning fires (external mode) AND the est*count band keeps its
        fallback authority over the duration question."""
        ctx = _context(tmp_path)
        self._drift_run(ctx, elapsed_us=0)

        evaluation = evaluate_run(ctx)

        codes = [issue.code for issue in evaluation.issues]
        assert codes.count(IssueCode.POWER_WINDOW_CLOCK_FROZEN) == 1
        assert codes.count(IssueCode.POWER_GATE_DURATION_MISMATCH) == 1
        assert IssueCode.POWER_WINDOW_OBSERVER_MISMATCH not in codes

    def test_beyond_drift_band_agreement_keeps_the_warning(self, tmp_path: Path):
        """Observer agreement is bounded absolution: it proves the gate
        brackets what the firmware timed, but cannot see a window whose
        CONTENT changed (init inside the gate, wrong clock config) -- both
        clocks watch the same span regardless of what ran in it. A
        self-consistent window at half the expected length is NOT thermal
        drift, so est*count keeps its warning even with agreement."""
        ctx = _context(tmp_path)
        self._drift_run(ctx, elapsed_us=2_500_500, gate_s=2.5)

        evaluation = evaluate_run(ctx)

        assert evaluation.validity is ResultValidity.DEGRADED
        codes = [issue.code for issue in evaluation.issues]
        assert codes.count(IssueCode.POWER_GATE_DURATION_MISMATCH) == 1
        assert IssueCode.POWER_WINDOW_OBSERVER_MISMATCH not in codes
        mismatch = next(
            issue
            for issue in evaluation.issues
            if issue.code == IssueCode.POWER_GATE_DURATION_MISMATCH
        )
        assert "thermal" in mismatch.message

    def test_unhealthy_terminal_cannot_arbitrate(self, tmp_path: Path):
        """An early-exit firmware agrees with its own gate BY CONSTRUCTION
        (it times the span it gated), so a terminal reporting incomplete work
        must not silence the est*count warning or earn the observer's
        authority (#202 harmonization: validity now applies the same
        terminal-health gate the summary got in the #195 review round)."""
        ctx = _context(tmp_path)
        # Gate and elapsed agree (both 4.427 s) but only 116/233 completed.
        self._drift_run(ctx, elapsed_us=self.DRIFT_ELAPSED_US, completed_count=116)

        evaluation = evaluate_run(ctx)

        assert evaluation.validity is ResultValidity.INVALID
        codes = [issue.code for issue in evaluation.issues]
        assert IssueCode.POWER_TERMINAL_INCOMPLETE in codes
        # The observer never ran: no agreement-based silence, no observer
        # error -- the incomplete-work ERROR carries the story and the
        # est*count fallback warning keeps its authority.
        assert IssueCode.POWER_WINDOW_OBSERVER_MISMATCH not in codes
        assert IssueCode.POWER_GATE_DURATION_MISMATCH in codes
        arb = evaluation.gate_arbitration
        assert arb is not None
        assert arb.terminal_unhealthy is True
        assert arb.observer is None
        assert arb.suppress_per_inference is True

    def test_unhealthy_terminal_downgrades_the_observer_error(self, tmp_path: Path):
        """The other half of the harmonization (#204 review, cell 03b): with
        an unhealthy terminal whose elapsed_us ALSO disagrees with the gate,
        validity previously emitted the observer ERROR from an envelope that
        had no standing to arbitrate. Now the observer is withheld, the
        est*count fallback WARNING carries the duration story, and the
        terminal ERRORs carry the failure -- the run stays INVALID."""
        ctx = _context(tmp_path)
        # elapsed 5.017s vs gate 4.427s (would disagree), only 116/233 done.
        self._drift_run(ctx, elapsed_us=5_017_000, completed_count=116)

        evaluation = evaluate_run(ctx)

        assert evaluation.validity is ResultValidity.INVALID
        codes = [issue.code for issue in evaluation.issues]
        assert IssueCode.POWER_TERMINAL_INCOMPLETE in codes
        assert IssueCode.POWER_WINDOW_OBSERVER_MISMATCH not in codes
        assert codes.count(IssueCode.POWER_GATE_DURATION_MISMATCH) == 1

    def test_advisory_rederived_band_never_reaches_validity(self, tmp_path: Path):
        """#204 lens-2 M9 kill: an artifact with NO recorded integrity gets
        the advisory 1% re-derived band for the summary's suspect flag ONLY.
        A gate inside the probe-keyed 10% band but outside 1% (2.3% short
        here) must produce zero duration issues -- feeding the advisory band
        into validity is exactly the recorded/advisory confusion the
        integrity_recorded chokepoint exists to kill."""
        ctx = _context(tmp_path)
        assert ctx.power_run is not None and ctx.power_run.observation is not None
        observation = ctx.power_run.observation
        gate_s = 4.90  # vs 233 x 21532us = 5.017s expected: 2.3% short
        result = PowerResult(
            summary=replace(observation.result.summary, duration_s=gate_s),
            gated_windows=[GatedPowerWindow(0.0, gate_s, gate_s, 0.0, 0.0, 0.0, 0.0, 0.0, 0)],
            # No recorded gate_duration_integrity.
            metadata=replace(observation.result.metadata),
        )
        set_profile_result(
            ctx,
            PmuResult(
                meta=FirmwareMeta(
                    clean_infer_count=self.DRIFT_COUNT,
                    clean_infer_avg_us=self.DRIFT_REFERENCE_US,
                ),
                layers=[],
            ),
        )
        ctx.power_run = PowerRun(
            plan=PowerRunPlan(
                firmware_mode="shared",
                inference_count=self.DRIFT_COUNT,
                reference_inference_us=self.DRIFT_REFERENCE_US,
            ),
            observation=replace(observation, result=result),
            terminal=None,
        )

        evaluation = evaluate_run(ctx)

        codes = [issue.code for issue in evaluation.issues]
        assert IssueCode.POWER_GATE_DURATION_MISMATCH not in codes
        assert evaluation.validity is ResultValidity.VALID
        arb = evaluation.gate_arbitration
        assert arb is not None
        assert arb.integrity_recorded is False
        # The advisory term itself missed its 1% band -- proving the verdict
        # existed and was correctly withheld from validity.
        assert arb.integrity is not None and not arb.integrity.valid

    def test_non_gated_runs_carry_no_arbitration(self, tmp_path: Path):
        """#204 lens-2 F3: RunEvaluation.gate_arbitration promises None when
        there is nothing gated to arbitrate. An internal-mode run with a
        terminal hiccup must not manufacture a suppressing arbitration."""
        ctx = _context(tmp_path, mode="internal")
        assert ctx.power_run is not None
        set_power_result(
            ctx,
            PowerResult(
                summary=PowerSummary(0.01, 0.02, 0.03, 0.1, 1.0, 10),
                metadata=PowerMetadata(
                    measurement_scope=MeasurementScope.ON_DEVICE_GATED_INFERENCE
                ),
            ),
        )
        assert ctx.power_run.terminal is not None
        ctx.power_run = PowerRun(
            plan=PowerRunPlan(firmware_mode="dedicated", inference_count=10),
            observation=None,
            terminal=replace(ctx.power_run.terminal, completed_count=5),
        )

        assert evaluate_run(ctx).gate_arbitration is None

    def test_arbitration_rides_the_evaluation(self, tmp_path: Path):
        """The composed verdict is carried on RunEvaluation for the summary
        to render -- the drift note the artifact publishes IS this string."""
        ctx = _context(tmp_path)
        self._drift_run(ctx, elapsed_us=self.DRIFT_ELAPSED_US)

        arb = evaluate_run(ctx).gate_arbitration

        assert arb is not None
        assert arb.integrity_recorded is True
        assert arb.observer_agrees is True
        assert arb.suppress_per_inference is False
        assert arb.suppression_reason is None
        assert arb.drift_note is not None and "HFRC" in arb.drift_note

    def test_below_minimum_is_an_error_even_when_the_observer_agrees(self, tmp_path: Path):
        """The floor is independent of arbitration: a sub-minimum gate is too
        short for the stats integral whatever the firmware clock says."""
        ctx = _context(tmp_path)
        self._drift_run(ctx, elapsed_us=self.DRIFT_ELAPSED_US, minimum_s=self.DRIFT_GATE_S + 1.0)

        evaluation = evaluate_run(ctx)

        assert evaluation.validity is ResultValidity.INVALID
        assert any(issue.code == IssueCode.POWER_GATE_BELOW_MINIMUM for issue in evaluation.issues)


def test_duration_fallback_matches_summary_policy(tmp_path: Path):
    ctx = _context(tmp_path)
    assert ctx.power_run is not None and ctx.power_run.observation is not None
    observation = ctx.power_run.observation
    result = PowerResult(
        summary=replace(observation.result.summary, duration_s=0.1),
        metadata=observation.result.metadata,
    )
    ctx.power_run = PowerRun(
        plan=PowerRunPlan(
            firmware_mode="dedicated",
            inference_count=10,
            reference_inference_us=100_000,
        ),
        observation=replace(observation, result=result),
        terminal=ctx.power_run.terminal,
    )

    evaluation = evaluate_run(ctx)

    assert evaluation.validity is ResultValidity.DEGRADED
    assert any(issue.code == IssueCode.POWER_GATE_DURATION_MISMATCH for issue in evaluation.issues)


class TestProfileCleanWindowFrozen:
    """``profile.clean_window_frozen`` — zero elapsed time, completed work.

    The power binary's frozen-clock rule (firmware_window_clock_is_frozen)
    only runs at the power terminal. Profile-only STIMER windows -- every
    Apollo5 profile build, and AP3/AP4 busy_loop -- had NO dead-clock check
    at all: a dead 32.768 kHz crystal yielded silent zeros with no issue code
    (found by review of #128). Detection belongs everywhere; attributing the
    fault (dead crystal vs dead debug domain) stays open on #110.
    """

    def _profile_only(
        self, tmp_path: Path, *, count: int | None, avg_us: int | None
    ) -> PipelineContext:
        ctx = _context(tmp_path)
        ctx.power_run = None
        set_power_result(ctx, None)
        set_profile_result(
            ctx,
            PmuResult(
                meta=FirmwareMeta(clean_infer_count=count, clean_infer_avg_us=avg_us),
                layers=[],
            ),
        )
        return ctx

    def test_zero_elapsed_with_completed_inferences_is_flagged(self, tmp_path):
        ctx = self._profile_only(tmp_path, count=200, avg_us=0)

        result = evaluate_run(ctx)

        assert IssueCode.PROFILE_CLEAN_WINDOW_FROZEN in [i.code for i in result.issues]
        assert result.validity is ResultValidity.DEGRADED

    def test_healthy_window_is_not_flagged(self, tmp_path):
        ctx = self._profile_only(tmp_path, count=200, avg_us=868)

        result = evaluate_run(ctx)

        assert IssueCode.PROFILE_CLEAN_WINDOW_FROZEN not in [i.code for i in result.issues]

    def test_absent_fields_stay_unknown_not_frozen(self, tmp_path):
        """A run with no clean-window record must not be accused of freezing."""
        ctx = self._profile_only(tmp_path, count=None, avg_us=None)

        result = evaluate_run(ctx)

        assert IssueCode.PROFILE_CLEAN_WINDOW_FROZEN not in [i.code for i in result.issues]


class TestCleanWindowStall:
    """``profile.clean_window_stalled`` — the #121 detector's host half.

    The profile binary's clean window is DWT-timed on the Cortex-M4F families,
    and DWT stops whenever no debugger holds the core debug power domain up.
    Iterations wholly inside such a stall accumulate a delta of exactly zero,
    so ``clean_infer_avg_us`` comes back low by the stalled fraction -- 21% on
    the Apollo4 runs in #121, against a 3.9% legitimate build-to-build spread.
    Nothing else in the result looks wrong, which is precisely why the firmware
    counts the stalls and this turns the count into an issue.
    """

    def _profile_only(
        self,
        tmp_path: Path,
        *,
        stalled: int | None,
        partial: int | None = 0,
        count: int | None = 1092,
        ref_cycles: int | None = 83_300,
        rate_cyc: int | None = 96_000,
    ) -> PipelineContext:
        """A profile-phase-only run, so validity reflects this issue alone.

        The shared fixture carries a gated power run whose own duration checks
        would otherwise fire on these clean-window numbers and mask what is
        being asserted.
        """
        ctx = _context(tmp_path)
        ctx.power_run = None
        set_power_result(ctx, None)
        set_profile_result(
            ctx,
            PmuResult(
                meta=FirmwareMeta(
                    clean_infer_count=count,
                    clean_infer_avg_us=684,
                    clean_stalled_iters=stalled,
                    clean_partial_iters=partial,
                    clean_ref_cycles=ref_cycles,
                    clean_dwt_rate_cyc=rate_cyc,
                    clean_dwt_rate_us=1000,
                    system_clock_hz=96_000_000,
                ),
                layers=[],
            ),
        )
        return ctx

    def test_stalled_clean_window_is_reported(self, tmp_path: Path):
        ctx = self._profile_only(tmp_path, stalled=233)

        evaluation = evaluate_run(ctx)

        stalls = [
            issue
            for issue in evaluation.issues
            if issue.code == IssueCode.PROFILE_CLEAN_WINDOW_STALLED
        ]
        assert len(stalls) == 1
        assert stalls[0].severity == "warning"
        assert stalls[0].context["stalled_iters"] == 233
        assert stalls[0].context["total_iters"] == 1092
        # ~21% low, matching the measured Apollo4 shortfall.
        assert 0.20 < stalls[0].context["understatement_lower_bound"] < 0.22
        assert evaluation.validity is ResultValidity.DEGRADED

    def test_issue_fires_only_when_the_firmware_reports_a_stall(self, tmp_path: Path):
        """Present when stalled, absent when not -- asserted together.

        The negative rows are deliberately not their own tests: an
        "issue absent" assertion also passes against a build with no detector
        at all, so on its own it would guard nothing. Paired with the positive
        row it both fails against the unfixed code and pins that the check
        cannot over-fire on a healthy run or on firmware that never reports.
        """
        cases = {
            233: True,  # stalled
            0: False,  # checked, healthy
            None: False,  # not reported -- unknown, not an issue
        }
        for stalled, expected in cases.items():
            evaluation = evaluate_run(self._profile_only(tmp_path, stalled=stalled))
            raised = any(
                issue.code == IssueCode.PROFILE_CLEAN_WINDOW_STALLED for issue in evaluation.issues
            )
            assert raised is expected, f"clean_stalled_iters={stalled!r}"
            if not expected:
                assert evaluation.validity is ResultValidity.VALID, stalled

    def test_partial_counting_is_caught_when_nothing_froze(self, tmp_path: Path):
        """The shape the exact-zero test cannot see.

        A dropped debug domain usually stops DWT outright, but it has been
        observed on Apollo4 merely slowing it -- ~0.6% of the expected rate --
        which produces deltas that are small but non-zero. Those satisfy no
        zero test, accumulate into the total uncounted, and would leave the run
        asserting "checked, clean" while still being wrong. That is worse than
        the pre-fix silence, so a partial-only run must still raise.
        """
        ctx = self._profile_only(tmp_path, stalled=0, partial=233)

        evaluation = evaluate_run(ctx)

        stalls = [
            issue
            for issue in evaluation.issues
            if issue.code == IssueCode.PROFILE_CLEAN_WINDOW_STALLED
        ]
        assert len(stalls) == 1, "a slow-but-running counter went unreported"
        assert stalls[0].context["partial_iters"] == 233
        assert stalls[0].context["stalled_iters"] == 0
        # Both magnitudes are real. The affected fraction counts every
        # touched iteration; the understatement bound discounts partials to
        # 0.875 each, since a partial contributed *something* but by
        # construction less than an eighth of the warm reference.
        assert 0.20 < stalls[0].context["affected_fraction"] < 0.22
        assert 0.18 < stalls[0].context["understatement_lower_bound"] < 0.19

    def test_impossible_counts_are_flagged_not_published_as_nonsense(self, tmp_path: Path):
        """More affected iterations than the window ran means a corrupt report.

        A torn transport line can inflate one field while the other parses
        cleanly. The raw division would publish "reads 457.9% low", which is
        both meaningless and more alarming than the truth. Clamp the fractions,
        keep the raw counts, and say the record is inconsistent.
        """
        ctx = self._profile_only(tmp_path, stalled=5000, partial=0, count=1092)

        evaluation = evaluate_run(ctx)

        stall = next(
            issue
            for issue in evaluation.issues
            if issue.code == IssueCode.PROFILE_CLEAN_WINDOW_STALLED
        )
        assert stall.context["stalled_iters"] == 5000
        assert stall.context["total_iters"] == 1092
        assert stall.context["understatement_lower_bound"] == 1.0
        assert stall.context["affected_fraction"] == 1.0
        assert stall.context["counts_are_inconsistent"] is True

    def test_pure_partial_stall_reports_a_real_magnitude(self, tmp_path: Path):
        """A window where every iteration is partial must not read "~0.0% low".

        The bound was frozen-only, so a stall that slowed the counter without
        ever freezing it reported a magnitude of zero next to the words "short
        by about the same factor". Partials are bounded above by the floor
        (an eighth of the warm reference), so each costs at least 0.875 of an
        inference -- a sound bound, and one that is not zero.
        """
        ctx = self._profile_only(tmp_path, stalled=0, partial=1091, count=1091)

        stall = next(
            issue
            for issue in evaluate_run(ctx).issues
            if issue.code == IssueCode.PROFILE_CLEAN_WINDOW_STALLED
        )

        assert stall.context["affected_fraction"] == 1.0
        assert stall.context["understatement_lower_bound"] >= 0.87

    def test_detector_does_not_over_fire_on_an_inflated_warm_reference(self, tmp_path: Path):
        """The direction every other test here misses: healthy runs stay quiet.

        The floor comes from the warm reference, and the reference is taken
        from the LOWEST non-zero warm sample precisely so a single cold-cache
        sample cannot inflate it and mark a whole healthy window partial. This
        pins the host half: a run reporting zero of both counts, an operative
        floor and a healthy clock rate raises nothing at all.
        """
        ctx = self._profile_only(tmp_path, stalled=0, partial=0)

        evaluation = evaluate_run(ctx)

        assert evaluation.validity is ResultValidity.VALID
        assert evaluation.issues == ()

    def test_uniform_slowdown_is_caught_by_the_independent_clock(self, tmp_path: Path):
        """The blocker: a stall that scales BOTH the reference and the window.

        The in-window counters compare each iteration against a warm sample
        taken with the same counter, moments earlier, in the same fault window.
        Multiply both by k and the comparison is unchanged -- so a uniform
        slowdown reports zero stalled, zero partial, and a plausible average.
        Replaying the pre-fix bug at the measured ~0.6% rate gives exactly
        that. Only the rate probe, timed by a clock DWT's fault cannot reach,
        sees it.
        """
        ctx = self._profile_only(
            tmp_path,
            stalled=0,
            partial=0,
            rate_cyc=576,  # 0.6% of the expected 96,000
        )

        evaluation = evaluate_run(ctx)
        codes = {issue.code for issue in evaluation.issues}

        assert IssueCode.PROFILE_CLEAN_WINDOW_CLOCK_RATE_LOW in codes, (
            "a uniform slowdown that both in-window counters are blind to went unreported"
        )
        rate = next(
            i for i in evaluation.issues if i.code == IssueCode.PROFILE_CLEAN_WINDOW_CLOCK_RATE_LOW
        )
        assert rate.context["ratio"] < 0.01
        assert rate.context["expected_cycles"] == 96_000.0

    def test_dead_partial_floor_is_not_reported_as_healthy(self, tmp_path: Path):
        """A zero warm reference makes the partial floor zero, so no unsigned
        delta can fall below it and the check cannot fire at all.

        That happens when every warm sample was itself frozen -- the documented
        usual case of this very fault -- so zero counts there must not read as
        "checked, clean".
        """
        ctx = self._profile_only(tmp_path, stalled=0, partial=0, ref_cycles=0)

        codes = {issue.code for issue in evaluate_run(ctx).issues}

        assert IssueCode.PROFILE_CLEAN_WINDOW_CHECK_INOPERATIVE in codes

    def test_a_torn_count_line_does_not_crash_evaluation(self, tmp_path: Path):
        """The parser passes through values it cannot int() as strings.

        ``HPX_CLEAN_PARTIAL_ITERS=1 7`` from a torn transport line therefore
        arrives as text, and int() on it would take down evaluate_run() for the
        whole run -- in the function whose docstring names torn lines as its
        motivation.
        """
        ctx = self._profile_only(tmp_path, stalled=233, partial=0)
        assert ctx.pmu_result is not None
        set_profile_result(
            ctx,
            PmuResult(
                meta=replace(ctx.pmu_result.meta, clean_partial_iters="1 7"),
                layers=[],
            ),
        )

        evaluation = evaluate_run(ctx)

        stall = next(
            i for i in evaluation.issues if i.code == IssueCode.PROFILE_CLEAN_WINDOW_STALLED
        )
        assert stall.context["stalled_iters"] == 233
        assert stall.context["partial_iters"] == 0


class TestNoInferenceProbeWindowDuration:
    """The replay path must check a busy_loop window, like the stage does.

    #125 item 2: with the plan-derived reference withheld, an internal-mode
    busy_loop run had NO duration check anywhere -- and `elapsed_us` is the
    denominator for average power and current, so a mis-sized window scales
    both. Restoring the reference in the collect stage fixed capture time;
    `evaluate_run` is the second consumer, and it is the one `hpx compare`
    and replayed artifacts go through. Leaving it withheld here also made the
    two modules disagree, which the comment above the call denies is possible.

    Verified before the fix: a 7x inflated busy_loop window evaluated VALID
    with no issues while the collect stage warned; the same window under
    `infer` evaluated DEGRADED with power.window_clock_mismatch.
    """

    #: The #125 plan shape for busy_loop: one unit of work lasting the target
    #: window, `count_source="probe_window"`.
    PLAN_COUNT = 1
    PLAN_REFERENCE_US = 1_000_000

    def _run(self, ctx: PipelineContext, *, elapsed_us: int) -> None:
        assert ctx.power_run is not None
        assert ctx.power_run.terminal is not None
        # Firmware reports 1 requested / 1 completed under this probe (#112).
        terminal = replace(
            ctx.power_run.terminal,
            requested_count=1,
            completed_count=1,
            elapsed_us=elapsed_us,
        )
        ctx.power_run = PowerRun(
            plan=PowerRunPlan(
                firmware_mode="dedicated",
                inference_count=self.PLAN_COUNT,
                reference_inference_us=self.PLAN_REFERENCE_US,
                count_source="probe_window",
            ),
            observation=None,
            terminal=terminal,
            on_device_summary=OnDevicePowerSummary(
                source="ina228",
                scope="fixed_n_inference",
                energy_nj=1_000_000,
                duration_us=elapsed_us,
                inference_count=1,
                overflow=False,
            ),
        )

    def test_mis_sized_busy_loop_window_is_flagged(self, tmp_path: Path):
        """7 s of window against a 1 s plan -- the calibration-fallback shape
        `_busy_loop_calibration.j2` warns about, and the one the one-sided
        ceiling check can never see."""
        ctx = _context(tmp_path, mode="internal", probe="busy_loop")
        self._run(ctx, elapsed_us=7_000_000)

        evaluation = evaluate_run(ctx)

        mismatch = [
            issue
            for issue in evaluation.issues
            if issue.code == IssueCode.POWER_WINDOW_CLOCK_MISMATCH
        ]
        assert len(mismatch) == 1
        assert evaluation.validity is ResultValidity.DEGRADED

    def test_correctly_sized_busy_loop_window_stays_valid(self, tmp_path: Path):
        """And the check must not fire on a correct run -- the spurious warning
        #112 removed. The plan now describes the window, so they agree."""
        ctx = _context(tmp_path, mode="internal", probe="busy_loop")
        self._run(ctx, elapsed_us=self.PLAN_REFERENCE_US)

        evaluation = evaluate_run(ctx)

        codes = {issue.code for issue in evaluation.issues}
        assert IssueCode.POWER_WINDOW_CLOCK_MISMATCH not in codes
        assert evaluation.validity is ResultValidity.VALID


class TestGateToleranceAgreesAcrossCaptureAndEvaluate:
    """The fallback duration check must use the tolerance capture used.

    `_assess_unrecorded_duration` runs only for an artifact with no recorded
    `gate_duration_integrity` -- an older or replayed capture. It took
    `assess_gate_duration`'s conservative 1% default while capture picked the
    band from `count_source`, so the same window could be accepted at capture
    time and warned about here. That is the capture-vs-evaluate divergence
    this module already closed for the window-clock check; leaving it open for
    the sibling check is the same bug in the same shape (found by review
    of #136).
    """

    def _ctx_with_plan(
        self,
        tmp_path: Path,
        count_source: Literal["firmware_auto", "configured", "profile_guided", "probe_window"],
    ) -> PipelineContext:
        ctx = _context(tmp_path, probe="busy_loop")
        assert ctx.power_run is not None
        ctx.power_run = PowerRun(
            plan=PowerRunPlan(
                firmware_mode="dedicated",
                inference_count=1,
                reference_inference_us=5_000_000,
                target_duration_ms=5000,
                count_source=count_source,
            ),
            observation=ctx.power_run.observation,
            terminal=ctx.power_run.terminal,
        )
        return ctx

    def test_a_predicted_window_gets_its_own_band_not_the_conservative_default(
        self, tmp_path: Path
    ):
        """A 16% overrun on a 5 s spin is inside the 25% busy_loop band."""
        from helia_profiler.evaluation.validity import _assess_unrecorded_duration

        ctx = self._ctx_with_plan(tmp_path, "probe_window")

        assert _assess_unrecorded_duration(ctx, 5.8) is None

    def test_the_same_window_is_still_flagged_when_it_is_genuinely_wrong(self, tmp_path: Path):
        """Loosening the band must not disarm the check."""
        from helia_profiler.evaluation.validity import _assess_unrecorded_duration

        ctx = self._ctx_with_plan(tmp_path, "probe_window")

        issue = _assess_unrecorded_duration(ctx, 9.0)

        assert issue is not None
        assert issue.code == IssueCode.POWER_GATE_DURATION_MISMATCH


class TestReplayedBusyLoopPlanCount:
    """`evaluate_run` must use the probe-aware expected count, like the stage.

    #125 item 5. `expected_terminal_requested_count()` returns 1 for a probe
    that runs no inferences, whatever the plan says, because the firmware
    reports one unit of work (#112). Reverting `validity.py` to read
    `plan.inference_count` directly left the whole suite green, because on
    every plan `plan_power_run` can now produce the two agree -- busy_loop
    plans exactly 1.

    They part company on a plan this build did not make: a busy_loop artifact
    stored BEFORE #136, whose plan carries the old derived count of 10 against
    a firmware report of 1/1. That is the replay contract validity.py's own
    comment says it defends, and `evaluate_run` is what `hpx compare` and
    every stored-artifact path go through.
    """

    def test_a_pre_fix_artifact_is_not_reported_as_a_count_mismatch(self, tmp_path: Path):
        ctx = _context(tmp_path, probe="busy_loop")
        assert ctx.power_run is not None
        assert ctx.power_run.terminal is not None
        ctx.power_run = PowerRun(
            plan=PowerRunPlan(
                firmware_mode="dedicated",
                inference_count=10,
                reference_inference_us=5_000_000,
            ),
            observation=ctx.power_run.observation,
            terminal=replace(ctx.power_run.terminal, requested_count=1, completed_count=1),
        )

        codes = [issue.code for issue in evaluate_run(ctx).issues]

        assert IssueCode.POWER_PLAN_COUNT_MISMATCH not in codes, (
            "the firmware reported the one unit of work this probe runs; "
            "reading the plan's count directly calls that a mismatch"
        )

    def test_a_real_count_mismatch_is_still_reported(self, tmp_path: Path):
        """The probe-aware expectation must not disarm the check.

        Under the default probe the helper returns the plan's count unchanged,
        so a firmware that ran a different number of inferences is still an
        error.
        """
        ctx = _context(tmp_path)
        assert ctx.power_run is not None
        assert ctx.power_run.terminal is not None
        ctx.power_run = PowerRun(
            plan=PowerRunPlan(firmware_mode="dedicated", inference_count=10),
            observation=ctx.power_run.observation,
            terminal=replace(ctx.power_run.terminal, requested_count=9, completed_count=9),
        )

        codes = [issue.code for issue in evaluate_run(ctx).issues]

        assert IssueCode.POWER_PLAN_COUNT_MISMATCH in codes
