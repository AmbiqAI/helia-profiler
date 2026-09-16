"""Tests for AnalyzeModelStage's Vela/ethos-u consistency check."""

from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.config import load_config
from helia_profiler.errors import ConfigError
from helia_profiler.evaluation import ETHOS_U_OP_NAME, LayerOps, ModelAnalysis
from helia_profiler.stages.analyze_model import _check_ethos_u_consistency


def _cfg(tmp_path: Path, *, backend: str | None = None):
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    engine: dict = {"type": "helia-rt"}
    if backend is not None:
        engine["backend"] = backend
    return load_config(None, {"model": {"path": str(model)}, "engine": engine})


def _analysis(*ops: str) -> ModelAnalysis:
    return ModelAnalysis(
        layers=[LayerOps(id=i, op=op) for i, op in enumerate(ops)],
        total_macs=0,
        total_ops=0,
        num_parameters=0,
    )


def test_vela_model_without_ethos_u_backend_fails(tmp_path: Path):
    cfg = _cfg(tmp_path)
    with pytest.raises(ConfigError, match="Vela-compiled"):
        _check_ethos_u_consistency(cfg, _analysis(ETHOS_U_OP_NAME, "SOFTMAX"))


def test_ethos_u_backend_without_vela_model_fails(tmp_path: Path):
    cfg = _cfg(tmp_path, backend="ethos_u")
    with pytest.raises(ConfigError, match="not compiled by Vela"):
        _check_ethos_u_consistency(cfg, _analysis("CONV_2D", "SOFTMAX"))


def test_matched_vela_model_and_backend_passes(tmp_path: Path):
    cfg = _cfg(tmp_path, backend="ethos_u")
    _check_ethos_u_consistency(cfg, _analysis(ETHOS_U_OP_NAME))


def test_plain_model_and_default_backend_passes(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _check_ethos_u_consistency(cfg, _analysis("CONV_2D"))


def test_unanalyzable_model_with_ethos_u_backend_fails(tmp_path: Path, monkeypatch):
    """The no-result early return must not skip the fail-fast gate."""
    from helia_profiler.pipeline import PipelineContext
    from helia_profiler.stages import analyze_model as stage_mod

    cfg = _cfg(tmp_path, backend="ethos_u")
    ctx = PipelineContext(config=cfg, work_dir=tmp_path)
    monkeypatch.setattr(stage_mod, "analyze_model", lambda _path: None)
    with pytest.raises(ConfigError, match="could not be analyzed"):
        stage_mod.AnalyzeModelStage().run(ctx)


def test_unanalyzable_model_without_npu_backend_still_skips(tmp_path: Path, monkeypatch):
    from helia_profiler.pipeline import PipelineContext
    from helia_profiler.stages import analyze_model as stage_mod

    cfg = _cfg(tmp_path)
    ctx = PipelineContext(config=cfg, work_dir=tmp_path)
    monkeypatch.setattr(stage_mod, "analyze_model", lambda _path: None)
    stage_mod.AnalyzeModelStage().run(ctx)
    assert ctx.model_analysis is None
