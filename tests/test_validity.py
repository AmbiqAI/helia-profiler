from __future__ import annotations

from dataclasses import replace
from pathlib import Path

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
from helia_profiler.power.diagnostics import (
    WINDOW_CLOCK_CEILING_SLACK_S,
    WindowClockCeiling,
)
from helia_profiler.results import ResultValidity
from helia_profiler.results import FirmwareMeta, PmuResult
from helia_profiler.evaluation import evaluate_run


def _context(tmp_path: Path, *, mode: str = "external") -> PipelineContext:
    config = load_config(
        None,
        {
            "model": {"path": "test.tflite"},
            "engine": {"type": "helia-rt"},
            "power": {
                "enabled": True,
                "mode": mode,
                **({"driver": "ondevice"} if mode == "internal" else {}),
            },
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ctx.pmu_result = PmuResult(meta=FirmwareMeta(), layers=[])
    result = PowerResult(
        summary=PowerSummary(0.01, 0.02, 0.03, 0.1, 1.0, 10),
        metadata={"measurement_scope": "gpio_gated_clean_window"},
    )
    observation = PowerObservation(
        mode="gpio_gated",
        result=result,
        gate_rise_observed=True,
        gate_fall_observed=True,
        deadline_s=2.0,
        integrity="valid",
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
    observation.result.metadata["gate_duration_integrity"] = {
        "measured_s": 0.1,
        "expected_s": 1.0,
        "tolerance_s": 0.01,
        "minimum_s": 0.5,
    }
    ctx.power_run = PowerRun(
        plan=ctx.power_run.plan,
        observation=PowerObservation(
            mode="free_form",
            result=observation.result,
            gate_rise_observed=False,
            gate_fall_observed=False,
            deadline_s=2.0,
            integrity="degraded",
        ),
        terminal=ctx.power_run.terminal,
    )

    evaluation = evaluate_run(ctx)

    assert evaluation.validity is ResultValidity.DEGRADED
    assert {issue.code for issue in evaluation.issues} == {
        "power.observation_degraded",
        "power.gate_duration_mismatch",
    }


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
        "power.plan_count_mismatch",
        "power.on_device_count_mismatch",
    }


def test_pmu_overflow_is_invalid_without_power(tmp_path: Path):
    ctx = _context(tmp_path)
    ctx.power_run = None
    ctx.pmu_result = PmuResult(meta=FirmwareMeta(), overflow_detected=True)

    evaluation = evaluate_run(ctx)

    assert evaluation.validity is ResultValidity.INVALID
    assert [issue.code for issue in evaluation.issues] == ["pmu.counter_overflow"]


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
            gated_windows=[
                GatedPowerWindow(0.0, gate_s, gate_s, 0.0, 0.0, 0.0, 0.0, 0.0, 0)
            ],
            metadata=dict(observation.result.metadata),
        )
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
            ctx.power_result = PowerResult(
                summary=replace(observation.result.summary, duration_s=gate_s),
                metadata={
                    "measurement_scope": "on_device_gated_inference",
                    "window_clock_ceiling": WindowClockCeiling(
                        elapsed_us=elapsed_us,
                        host_envelope_s=host_envelope_s,
                        slack_s=WINDOW_CLOCK_CEILING_SLACK_S,
                    ).to_metadata(),
                },
            )

    def test_bench_agreement_is_valid(self, tmp_path: Path):
        ctx = _context(tmp_path)
        self._bench_run(ctx, elapsed_us=self.BENCH_ELAPSED_US)

        evaluation = evaluate_run(ctx)

        codes = {issue.code for issue in evaluation.issues}
        assert "power.window_clock_frozen" not in codes
        assert "power.window_clock_mismatch" not in codes

    def test_frozen_window_clock_is_an_error_in_internal_mode(self, tmp_path: Path):
        """Internal mode divides energy by this duration, so the published
        power is corrupt -- the measurement of record is unusable."""
        ctx = _context(tmp_path, mode="internal")
        self._bench_run(ctx, elapsed_us=0, gate_s=4.963, internal=True)

        evaluation = evaluate_run(ctx)

        assert evaluation.validity is ResultValidity.INVALID
        frozen = [
            issue for issue in evaluation.issues if issue.code == "power.window_clock_frozen"
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
            issue for issue in evaluation.issues if issue.code == "power.window_clock_frozen"
        ]
        assert len(frozen) == 1
        assert frozen[0].severity == "warning"
        assert evaluation.validity is ResultValidity.DEGRADED

    def test_frozen_window_clock_suppresses_the_agreement_warning(self, tmp_path: Path):
        """Zero elapsed is reported once, as the error that explains it -- not
        also as a 100%-apart mismatch warning saying the same thing worse."""
        ctx = _context(tmp_path)
        self._bench_run(ctx, elapsed_us=0, gate_s=4.963)

        codes = [issue.code for issue in evaluate_run(ctx).issues]

        assert codes.count("power.window_clock_mismatch") == 0

    def test_external_disagreement_is_a_warning_not_an_error(self, tmp_path: Path):
        """Apollo4's ~7x inflation: degraded, not invalid -- two boards is not
        a wide enough envelope to fail a run on."""
        ctx = _context(tmp_path)
        self._bench_run(ctx, elapsed_us=int(self.BENCH_ELAPSED_US * 6027 / 866.6))

        evaluation = evaluate_run(ctx)

        assert evaluation.validity is ResultValidity.DEGRADED
        mismatch = [
            issue for issue in evaluation.issues if issue.code == "power.window_clock_mismatch"
        ]
        assert len(mismatch) == 1
        assert mismatch[0].severity == "warning"
        assert mismatch[0].context["reference_source"] == "gated_windows"

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
            metadata={"measurement_scope": "free_form_capture"},
        )
        ctx.power_run = PowerRun(
            plan=PowerRunPlan(
                firmware_mode="dedicated",
                inference_count=self.BENCH_COUNT,
                reference_inference_us=self.BENCH_REFERENCE_US,
            ),
            observation=PowerObservation(
                mode="free_form",
                result=degraded,
                gate_rise_observed=True,
                gate_fall_observed=False,
                deadline_s=45.0,
                integrity="degraded",
            ),
            terminal=replace(
                ctx.power_run.terminal,
                requested_count=self.BENCH_COUNT,
                completed_count=self.BENCH_COUNT,
                elapsed_us=self.BENCH_ELAPSED_US,
            ),
        )

        codes = {issue.code for issue in evaluate_run(ctx).issues}

        assert codes == {"power.observation_degraded"}

    def test_the_two_modes_apply_different_tolerances(self, tmp_path: Path):
        """One 14% deviation, two verdicts: a real fault against a host-timed
        gate, expected cross-binary noise against the plan."""
        external = _context(tmp_path)
        self._bench_run(external, elapsed_us=int(self.BENCH_GATE_S * 1e6 * 1.14))
        assert any(
            issue.code == "power.window_clock_mismatch"
            for issue in evaluate_run(external).issues
        )

        internal = _context(tmp_path, mode="internal")
        self._bench_run(
            internal,
            elapsed_us=int(self.BENCH_COUNT * self.BENCH_REFERENCE_US * 1.14),
            internal=True,
        )
        assert not any(
            issue.code == "power.window_clock_mismatch"
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
            if issue.code == "power.window_clock_mismatch"
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
            issue.code == "power.window_clock_mismatch" for issue in evaluate_run(near).issues
        )

        far = _context(tmp_path, mode="internal")
        self._bench_run(
            far,
            elapsed_us=int(self.BENCH_COUNT * self.BENCH_REFERENCE_US * 1.30),
            internal=True,
        )
        assert any(
            issue.code == "power.window_clock_mismatch" for issue in evaluate_run(far).issues
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
            if issue.code == "power.window_clock_exceeds_host_time"
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
            issue.code == "power.window_clock_exceeds_host_time"
            for issue in evaluate_run(ctx).issues
        )


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
    assert any(issue.code == "power.gate_duration_mismatch" for issue in evaluation.issues)


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
        ctx.power_result = None
        ctx.pmu_result = PmuResult(
            meta=FirmwareMeta(clean_infer_count=count, clean_infer_avg_us=avg_us),
            layers=[],
        )
        return ctx

    def test_zero_elapsed_with_completed_inferences_is_flagged(self, tmp_path):
        ctx = self._profile_only(tmp_path, count=200, avg_us=0)

        result = evaluate_run(ctx)

        assert "profile.clean_window_frozen" in [i.code for i in result.issues]
        assert result.validity is ResultValidity.DEGRADED

    def test_healthy_window_is_not_flagged(self, tmp_path):
        ctx = self._profile_only(tmp_path, count=200, avg_us=868)

        result = evaluate_run(ctx)

        assert "profile.clean_window_frozen" not in [i.code for i in result.issues]

    def test_absent_fields_stay_unknown_not_frozen(self, tmp_path):
        """A run with no clean-window record must not be accused of freezing."""
        ctx = self._profile_only(tmp_path, count=None, avg_us=None)

        result = evaluate_run(ctx)

        assert "profile.clean_window_frozen" not in [i.code for i in result.issues]


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
        ctx.power_result = None
        ctx.pmu_result = PmuResult(
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
        )
        return ctx

    def test_stalled_clean_window_is_reported(self, tmp_path: Path):
        ctx = self._profile_only(tmp_path, stalled=233)

        evaluation = evaluate_run(ctx)

        stalls = [
            issue
            for issue in evaluation.issues
            if issue.code == "profile.clean_window_stalled"
        ]
        assert len(stalls) == 1
        assert stalls[0].severity == "warning"
        assert stalls[0].context["stalled_iters"] == 233
        assert stalls[0].context["total_iters"] == 1092
        # ~21% low, matching the measured Apollo4 shortfall.
        assert 0.20 < stalls[0].context["understatement_lower_bound"] < 0.22
        assert evaluation.validity is ResultValidity.DEGRADED

    def test_issue_fires_only_when_the_firmware_reports_a_stall(
        self, tmp_path: Path
    ):
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
                issue.code == "profile.clean_window_stalled"
                for issue in evaluation.issues
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
            if issue.code == "profile.clean_window_stalled"
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

    def test_impossible_counts_are_flagged_not_published_as_nonsense(
        self, tmp_path: Path
    ):
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
            if issue.code == "profile.clean_window_stalled"
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
            if issue.code == "profile.clean_window_stalled"
        )

        assert stall.context["affected_fraction"] == 1.0
        assert stall.context["understatement_lower_bound"] >= 0.87

    def test_detector_does_not_over_fire_on_an_inflated_warm_reference(
        self, tmp_path: Path
    ):
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

    def test_uniform_slowdown_is_caught_by_the_independent_clock(
        self, tmp_path: Path
    ):
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

        assert "profile.clean_window_clock_rate_low" in codes, (
            "a uniform slowdown that both in-window counters are blind to went "
            "unreported"
        )
        rate = next(
            i for i in evaluation.issues
            if i.code == "profile.clean_window_clock_rate_low"
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

        assert "profile.clean_window_check_inoperative" in codes

    def test_a_torn_count_line_does_not_crash_evaluation(self, tmp_path: Path):
        """The parser passes through values it cannot int() as strings.

        ``HPX_CLEAN_PARTIAL_ITERS=1 7`` from a torn transport line therefore
        arrives as text, and int() on it would take down evaluate_run() for the
        whole run -- in the function whose docstring names torn lines as its
        motivation.
        """
        ctx = self._profile_only(tmp_path, stalled=233, partial=0)
        ctx.pmu_result = PmuResult(
            meta=replace(ctx.pmu_result.meta, clean_partial_iters="1 7"),
            layers=[],
        )

        evaluation = evaluate_run(ctx)

        stall = next(
            i for i in evaluation.issues if i.code == "profile.clean_window_stalled"
        )
        assert stall.context["stalled_iters"] == 233
        assert stall.context["partial_iters"] == 0
