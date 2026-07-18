"""Tests for the pipeline primitives and stage sequencing."""

from __future__ import annotations

from pathlib import Path
from dataclasses import FrozenInstanceError

import pytest

from helia_profiler.artifacts import (
    DeploymentRecord,
    FirmwareArtifact,
    PowerRunPlan,
)
from helia_profiler.config import load_config
from helia_profiler.errors import CaptureError, HpxError
from helia_profiler.pipeline import PipelineContext, PipelineRunner, ProgressUpdate

# ---------------------------------------------------------------------------
# Helpers: minimal stage implementations for testing
# ---------------------------------------------------------------------------


class PassStage:
    """A stage that always runs and does nothing."""

    def __init__(self, name: str = "pass_stage"):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        pass


class SkipStage:
    """A stage that always skips."""

    @property
    def name(self) -> str:
        return "skip_stage"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return True

    def run(self, ctx: PipelineContext) -> None:
        raise AssertionError("should not be called")


class FailStage:
    """A stage that raises an HpxError."""

    def __init__(self, error: HpxError | None = None):
        self._error = error or CaptureError("boom")

    @property
    def name(self) -> str:
        return "fail_stage"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        raise self._error


class UnexpectedFailStage:
    """A stage that raises a non-HpxError exception."""

    @property
    def name(self) -> str:
        return "unexpected_fail"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        raise RuntimeError("segfault or something")


class RecordingStage:
    """A stage that records when it ran."""

    def __init__(self, name: str, log: list[str]):
        self._name = name
        self._log = log

    @property
    def name(self) -> str:
        return self._name

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        self._log.append(self._name)


class ProgressStage(PassStage):
    def run(self, ctx: PipelineContext) -> None:
        ctx.report_progress(
            "Running inferences",
            completed=2,
            total=10,
            unit="iterations",
            eta_s=4.5,
        )


class RecordingConsole:
    def __init__(self) -> None:
        self.starts: list[tuple[str, int, int]] = []
        self.updates: list[ProgressUpdate] = []

    def stage_start(self, name: str, index: int, total: int) -> None:
        self.starts.append((name, index, total))

    def progress_update(self, update: ProgressUpdate) -> None:
        self.updates.append(update)

    def stage_done(self, name: str) -> None:
        del name

    def stage_skip(self, name: str) -> None:
        del name

    def pipeline_done(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path):
    """Build a minimal ProfileConfig for testing."""
    model_file = tmp_path / "test.tflite"
    model_file.write_bytes(b"\x00")  # dummy
    return load_config(
        None,
        {
            "model": {"path": str(model_file)},
            "engine": {"type": "helia-rt"},
            "work_dir": str(tmp_path / "work"),
        },
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPipelineRunner:
    def test_empty_pipeline(self, tmp_path: Path):
        config = _make_config(tmp_path)
        runner = PipelineRunner([])
        ctx = runner.run(config)
        assert isinstance(ctx, PipelineContext)

    def test_stages_run_in_order(self, tmp_path: Path):
        config = _make_config(tmp_path)
        log: list[str] = []
        stages = [
            RecordingStage("first", log),
            RecordingStage("second", log),
            RecordingStage("third", log),
        ]
        runner = PipelineRunner(stages)
        runner.run(config)
        assert log == ["first", "second", "third"]

    def test_runner_reports_true_stage_positions_and_progress(self, tmp_path: Path):
        config = _make_config(tmp_path)
        console = RecordingConsole()
        runner = PipelineRunner([PassStage("first"), ProgressStage("second")], console=console)

        runner.run(config)

        assert console.starts == [("first", 1, 2), ("second", 2, 2)]
        assert console.updates == [
            ProgressUpdate(
                message="Running inferences",
                completed=2,
                total=10,
                unit="iterations",
                eta_s=4.5,
            )
        ]

    def test_skip_stage_not_executed(self, tmp_path: Path):
        config = _make_config(tmp_path)
        log: list[str] = []
        stages = [
            RecordingStage("before", log),
            SkipStage(),
            RecordingStage("after", log),
        ]
        runner = PipelineRunner(stages)
        runner.run(config)
        assert log == ["before", "after"]

    def test_hpx_error_propagates(self, tmp_path: Path):
        config = _make_config(tmp_path)
        error = CaptureError("serial timeout", hint="check cable")
        stages = [PassStage(), FailStage(error)]
        runner = PipelineRunner(stages)
        with pytest.raises(CaptureError, match="serial timeout"):
            runner.run(config)

    def test_unexpected_error_wrapped(self, tmp_path: Path):
        config = _make_config(tmp_path)
        stages = [PassStage(), UnexpectedFailStage()]
        runner = PipelineRunner(stages)
        with pytest.raises(HpxError, match="Unexpected error.*unexpected_fail"):
            runner.run(config)

    def test_context_has_work_dir(self, tmp_path: Path):
        config = _make_config(tmp_path)
        runner = PipelineRunner([PassStage()])
        ctx = runner.run(config)
        assert ctx.work_dir.exists()

    def test_stages_after_failure_not_run(self, tmp_path: Path):
        config = _make_config(tmp_path)
        log: list[str] = []
        stages = [
            RecordingStage("before", log),
            FailStage(),
            RecordingStage("after", log),
        ]
        runner = PipelineRunner(stages)
        with pytest.raises(HpxError):
            runner.run(config)
        assert log == ["before"]


class TestPipelineContext:
    def test_initial_state_is_none(self, tmp_path: Path):
        config = _make_config(tmp_path)
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        assert ctx.soc is None
        assert ctx.board is None
        assert ctx.engine_adapter is None
        assert ctx.engine_artifacts is None
        assert ctx.firmware_dir is None
        assert ctx.build_dir is None
        assert ctx.binary_path is None
        assert ctx.pmu_result is None
        assert ctx.power_result is None
        assert ctx.report_paths == []

    def test_explicit_artifacts_start_empty(self, tmp_path: Path):
        config = _make_config(tmp_path)
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        assert ctx.profile_firmware is None
        assert ctx.power_firmware is None
        assert ctx.deployed_power_firmware is None
        assert ctx.power_plan is None
        assert ctx.profile_run is None
        assert ctx.power_run is None

    def test_profile_run_transitions_are_immutable_and_mirrored(self, tmp_path: Path):
        from helia_profiler.results import FirmwareMeta, PmuResult

        ctx = PipelineContext(config=_make_config(tmp_path), work_dir=tmp_path)
        binary = tmp_path / "hpx_profiler"
        binary.touch()
        firmware = FirmwareArtifact(
            role="profile",
            target_name="hpx_profiler",
            app_dir=tmp_path,
            build_dir=tmp_path,
            binary_path=binary,
        )
        ctx.publish_profile_firmware(firmware)
        built_run = ctx.profile_run
        assert built_run is not None
        assert ctx.profile_firmware is firmware
        with pytest.raises(FrozenInstanceError):
            built_run.result = PmuResult(meta=FirmwareMeta())

        deployment = DeploymentRecord(
            firmware=firmware,
            target_id="apollo510_evb",
            deployed_at="2026-07-18T00:00:00+00:00",
        )
        ctx.publish_profile_deployment(deployment)
        assert ctx.profile_run is not built_run
        assert ctx.profile_run is not None
        assert ctx.profile_run.deployment is deployment

        result = PmuResult(meta=FirmwareMeta(clean_infer_count=3))
        ctx.publish_profile_result(result)
        assert ctx.profile_run.result is result
        assert ctx.pmu_result is result

    def test_power_run_transitions_clear_stale_deployment_and_mirror(self, tmp_path: Path):
        from helia_profiler.power.base import PowerResult, PowerSummary

        ctx = PipelineContext(config=_make_config(tmp_path), work_dir=tmp_path)
        plan = PowerRunPlan(
            firmware_mode="dedicated",
            inference_count=5,
            count_source="configured",
        )
        ctx.publish_power_plan(plan)
        assert ctx.power_run is not None
        assert ctx.power_run.plan is plan
        assert ctx.power_plan is plan

        binary = tmp_path / "hpx_profiler_power"
        binary.touch()
        firmware = FirmwareArtifact(
            role="power",
            target_name="hpx_profiler_power",
            app_dir=tmp_path,
            build_dir=tmp_path,
            binary_path=binary,
        )
        ctx.publish_power_firmware(firmware)
        deployment = DeploymentRecord(
            firmware=firmware,
            target_id="apollo510_evb",
            deployed_at="2026-07-18T00:00:00+00:00",
        )
        ctx.publish_power_deployment(deployment)
        assert ctx.deployed_power_firmware is firmware

        ctx.publish_power_firmware(firmware)
        assert ctx.power_run.deployment is None
        assert ctx.deployed_power_firmware is None
        ctx.publish_power_deployment(deployment)

        result = PowerResult(
            summary=PowerSummary(0.01, 0.02, 0.03, 0.04, 1.0, 10)
        )
        ctx.publish_power_result(result)
        assert ctx.power_run.observation is not None
        assert ctx.power_run.observation.result is result
        assert ctx.power_run.observation.mode == "free_form"
        assert ctx.power_run.observation.integrity == "degraded"
        assert ctx.power_result is result

    def test_deployment_must_reference_current_artifact(self, tmp_path: Path):
        ctx = PipelineContext(config=_make_config(tmp_path), work_dir=tmp_path)
        profile_binary = tmp_path / "profile"
        other_binary = tmp_path / "other"
        profile_binary.touch()
        other_binary.touch()
        current = FirmwareArtifact(
            role="profile",
            target_name="hpx_profiler",
            app_dir=tmp_path,
            build_dir=tmp_path,
            binary_path=profile_binary,
        )
        other = FirmwareArtifact(
            role="profile",
            target_name="hpx_profiler",
            app_dir=tmp_path,
            build_dir=tmp_path,
            binary_path=other_binary,
        )
        ctx.publish_profile_firmware(current)

        with pytest.raises(ValueError, match="current firmware artifact"):
            ctx.publish_profile_deployment(
                DeploymentRecord(
                    firmware=other,
                    target_id="apollo510_evb",
                    deployed_at="2026-07-18T00:00:00+00:00",
                )
            )

    def test_dedicated_result_requires_deployment(self, tmp_path: Path):
        from helia_profiler.power.base import PowerResult, PowerSummary

        ctx = PipelineContext(config=_make_config(tmp_path), work_dir=tmp_path)
        ctx.publish_power_plan(PowerRunPlan(firmware_mode="dedicated"))

        with pytest.raises(ValueError, match="must be deployed"):
            ctx.publish_power_result(
                PowerResult(summary=PowerSummary(0.01, 0.02, 0.03, 0.04, 1.0, 10))
            )

    def test_replanning_clears_legacy_power_state(self, tmp_path: Path):
        from helia_profiler.power.base import PowerResult, PowerSummary

        ctx = PipelineContext(config=_make_config(tmp_path), work_dir=tmp_path)
        ctx.power_binary_path = tmp_path / "old-power"
        ctx.power_firmware = FirmwareArtifact(
            role="power",
            target_name="hpx_profiler_power",
            app_dir=tmp_path,
            build_dir=tmp_path,
            binary_path=ctx.power_binary_path,
        )
        ctx.deployed_power_firmware = ctx.power_firmware
        ctx.power_result = PowerResult(
            summary=PowerSummary(0.01, 0.02, 0.03, 0.04, 1.0, 10)
        )

        ctx.publish_power_plan(PowerRunPlan(firmware_mode="dedicated", inference_count=7))

        assert ctx.power_binary_path is None
        assert ctx.power_firmware is None
        assert ctx.deployed_power_firmware is None
        assert ctx.power_result is None

    def test_context_is_mutable(self, tmp_path: Path):
        config = _make_config(tmp_path)
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        from helia_profiler.results import FirmwareMeta, PmuResult

        ctx.pmu_result = PmuResult(meta=FirmwareMeta(), layers=[])
        assert ctx.pmu_result is not None
        assert ctx.pmu_result.layers == []

    def test_progress_is_optional_and_ui_independent(self, tmp_path: Path):
        config = _make_config(tmp_path)
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        updates: list[ProgressUpdate] = []
        ctx.report_progress("ignored without a sink")
        ctx.progress_sink = updates.append

        ctx.report_progress("Profile ready", kind="checkpoint", min_verbosity=1)

        assert updates == [
            ProgressUpdate(
                message="Profile ready",
                kind="checkpoint",
                min_verbosity=1,
            )
        ]


def test_default_pipeline_exposes_profile_then_power_steps():
    from helia_profiler.profiler import build_default_pipeline

    names = [stage.name for stage in build_default_pipeline()._stages]
    assert names.index("capture_pmu") < names.index("plan_power_run")
    assert names.index("plan_power_run") < names.index("build_power_firmware")
    assert names.index("build_power_firmware") < names.index("flash_power_firmware")
    assert names.index("flash_power_firmware") < names.index("capture_power")
    assert names.index("capture_power") < names.index("collect_power_terminal")


def test_pipeline_runner_installs_progress_sink_before_stages(tmp_path: Path):
    config = _make_config(tmp_path)
    updates: list[ProgressUpdate] = []

    class ProgressStage:
        name = "progress_probe"

        def should_skip(self, ctx):
            return False

        def run(self, ctx):
            ctx.report_progress("stage running")

    PipelineRunner([ProgressStage()], progress_sink=updates.append).run(config)

    assert updates == [ProgressUpdate(message="stage running")]
