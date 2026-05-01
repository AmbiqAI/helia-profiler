"""Tests for concrete pipeline stages (unit-testable ones)."""

from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.config import load_config
from helia_profiler.errors import ConfigError, EngineError, FirmwareError
from helia_profiler.pipeline import PipelineContext
from helia_profiler.stages.s01_resolve_platform import ResolvePlatformStage
from helia_profiler.stages.s02_prepare_engine import PrepareEngineStage
from helia_profiler.stages.s03_generate_firmware import GenerateFirmwareStage


def _make_ctx(tmp_path: Path, overrides: dict | None = None) -> PipelineContext:
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    base = {
        "model": {"path": str(model)},
        "engine": {"type": "tflm"},
        "work_dir": str(tmp_path / "work"),
    }
    if overrides:
        base.update(overrides)
    config = load_config(None, base)
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    return PipelineContext(config=config, work_dir=work_dir)


class TestResolvePlatformStage:
    def test_resolves_apollo510(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        stage = ResolvePlatformStage()
        stage.run(ctx)
        assert ctx.soc is not None
        assert ctx.soc.name == "apollo510"
        assert ctx.board is not None
        assert ctx.board.name == "apollo510_evb"

    def test_resolves_apollo3p(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, {"target": {"board": "apollo3p_evb"}})
        stage = ResolvePlatformStage()
        stage.run(ctx)
        assert ctx.soc is not None
        assert ctx.soc.name == "apollo3p"

    def test_unknown_board_raises_config_error(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, {"target": {"board": "nonexistent_board"}})
        stage = ResolvePlatformStage()
        with pytest.raises(ConfigError, match="Unknown board"):
            stage.run(ctx)

    def test_missing_model_raises_config_error(self, tmp_path: Path):
        config = load_config(
            None,
            {
                "model": {"path": str(tmp_path / "missing.tflite")},
                "engine": {"type": "tflm"},
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        stage = ResolvePlatformStage()
        with pytest.raises(ConfigError, match="Model file not found"):
            stage.run(ctx)

    def test_never_skips(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        stage = ResolvePlatformStage()
        assert not stage.should_skip(ctx)


class TestPrepareEngineStage:
    def test_tflm_adapter(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        ResolvePlatformStage().run(ctx)
        stage = PrepareEngineStage()
        stage.run(ctx)
        assert ctx.engine_adapter is not None
        assert ctx.engine_adapter.name == "Stock TFLM (CMSIS-NN)"
        assert ctx.engine_artifacts is not None

    def test_helia_rt_adapter(self, tmp_path: Path, fake_dist: Path):
        ctx = _make_ctx(
            tmp_path,
            {
                "engine": {
                    "type": "helia-rt",
                    "config": {"dist_path": str(fake_dist)},
                },
            },
        )
        ResolvePlatformStage().run(ctx)
        stage = PrepareEngineStage()
        stage.run(ctx)
        assert ctx.engine_adapter is not None
        assert ctx.engine_adapter.name == "heliaRT"

    def test_helia_aot_adapter(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, {"engine": {"type": "helia-aot"}})
        ResolvePlatformStage().run(ctx)
        stage = PrepareEngineStage()
        # Without helia-aot CLI + CMSIS-NN path, prepare() raises EngineError.
        # Verify the adapter is correctly instantiated by checking the error.
        with pytest.raises(EngineError, match="heliaAOT|CMSIS-NN"):
            stage.run(ctx)

    def test_never_skips(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        stage = PrepareEngineStage()
        assert not stage.should_skip(ctx)


class TestGenerateFirmwareStage:
    def test_no_artifacts_raises_firmware_error(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        # Don't run engine stage → engine_artifacts is None
        stage = GenerateFirmwareStage()
        with pytest.raises(FirmwareError, match="No engine artifacts"):
            stage.run(ctx)
