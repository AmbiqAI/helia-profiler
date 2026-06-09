"""Tests for the pipeline primitives and stage sequencing."""

from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.config import load_config
from helia_profiler.errors import CaptureError, HpxError
from helia_profiler.pipeline import PipelineContext, PipelineRunner

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
            "engine": {"type": "tflm"},
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

    def test_context_is_mutable(self, tmp_path: Path):
        config = _make_config(tmp_path)
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        from helia_profiler.results import FirmwareMeta, PmuResult

        ctx.pmu_result = PmuResult(meta=FirmwareMeta(), layers=[])
        assert ctx.pmu_result is not None
        assert ctx.pmu_result.layers == []
