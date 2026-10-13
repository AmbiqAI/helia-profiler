"""Request construction has no cache IO; one selected adapter prepares the build."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

from test_fixture_request import _request
from test_fixture_runtime import runtime

from helia_profiler.config import EngineConfig
from helia_profiler.engines.tflm import TFLMAdapter
from helia_profiler.fixture import EngineType, FixtureBackend, build_fixture
from helia_profiler.pipeline import PipelineContext
from helia_profiler.stages.prepare_engine import PrepareEngineStage


def test_request_is_pure_and_build_resolves_selected_cached_archive(tmp_path, monkeypatch):
    (tmp_path / "provider").mkdir()
    cached = runtime(tmp_path / "provider")
    lookup = Mock(return_value=SimpleNamespace(runtime=cached))
    monkeypatch.setattr("helia_profiler._fixture_request.prepared_runtime", lookup)
    request = replace(
        _request(tmp_path / "input"), engine=EngineType.TFLM, backend=FixtureBackend.CMSIS_NN
    )
    assert request.runtime is None
    lookup.assert_not_called()
    operation = Mock(return_value="built")
    monkeypatch.setattr("helia_profiler._fixture_request._build", operation)
    assert build_fixture(request) == "built"
    lookup.assert_called_once_with("tflm")
    assert operation.call_args.kwargs["runtime"] is cached
    assert (
        operation.call_args.kwargs["intent_identity"]
        == replace(request, runtime=cached).intent_identity
    )
    assert request.runtime is None


def test_selected_archive_does_not_prepare_and_replace_ordinary_engine(tmp_path, monkeypatch):
    (tmp_path / "provider").mkdir()
    cached = runtime(tmp_path / "provider").verify()
    normal = Mock(side_effect=AssertionError("ordinary provider must not prepare"))
    monkeypatch.setattr(TFLMAdapter, "prepare", normal)
    config = replace(
        _request(tmp_path / "input").to_config(),
        engine=EngineConfig(type=EngineType.TFLM, backend="cmsis_nn"),
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path / "work")
    PrepareEngineStage(runtime=cached).run(ctx)
    normal.assert_not_called()
    assert ctx.engine_artifacts is not None and ctx.run_metadata.engine is not None
    assert [m.name for m in ctx.engine_artifacts.extra_modules] == ["hpx-upstream-runtime"]
    assert ctx.engine_artifacts.cmake_vars == {}
    assert ctx.run_metadata.engine.type == "tflm"
