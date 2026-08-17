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
from helia_profiler.power.base import PowerResult, PowerSummary
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
                if internal
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

    def test_frozen_window_clock_is_an_error(self, tmp_path: Path):
        ctx = _context(tmp_path)
        self._bench_run(ctx, elapsed_us=0, gate_s=4.963)

        evaluation = evaluate_run(ctx)

        assert evaluation.validity is ResultValidity.INVALID
        frozen = [
            issue for issue in evaluation.issues if issue.code == "power.window_clock_frozen"
        ]
        assert len(frozen) == 1
        assert frozen[0].severity == "error"
        assert frozen[0].context["completed_count"] == self.BENCH_COUNT

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
        assert mismatch[0].context["reference_source"] == "capture_summary"

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
