"""The run API's inputs, capabilities and model analysis, imported from ``helia_profiler``."""

from __future__ import annotations

from pathlib import Path

import pytest

import helia_profiler as hpx
import helia_profiler.fixture as facade
from helia_profiler.engines import EngineType
from helia_profiler.errors import ConfigError

#: Top-level name -> the ``helia_profiler.fixture`` name it is.
_SAME_AS_FACADE = {
    "TypedFixture": "TypedFixture",
    "FixtureIO": "FixtureIO",
    "FixtureOutput": "FixtureOutput",
    "FixtureRole": "FixtureRole",
    "FixtureFile": "FixtureFile",
    "FixtureTensor": "FixtureTensor",
    "FixtureMethod": "FixtureMethod",
    "FixtureTimingScope": "FixtureTimingScope",
    "FixtureCaptureGuard": "FixtureCaptureGuard",
    "FixtureTarget": "FixtureTarget",
    "PreparedUpstreamRuntime": "PreparedUpstreamRuntime",
    "TypedFixtureModelAnalysis": "TypedFixtureModelAnalysis",
    "FixtureCapabilities": "FixtureCapabilities",
    "QualifiedFixtureTarget": "QualifiedFixtureTarget",
    "EngineDTypeCapability": "EngineDTypeCapability",
}
_NEW = ("capabilities", "analyze_run_model", "analyze_run_model_for_engine", "OperatorGuard")


@pytest.mark.parametrize("name,facade_name", sorted(_SAME_AS_FACADE.items()))
def test_each_input_type_is_the_fixture_type(name, facade_name):
    assert getattr(hpx, name) is getattr(facade, facade_name)


def test_the_run_inputs_are_experimental_exports():
    names = {*_SAME_AS_FACADE, *_NEW, "Expect", "Tolerance"}
    assert names <= set(hpx.__all__)
    assert {hpx.__api_stability__[name] for name in names} == {"experimental"}


def test_capabilities_reports_what_the_fixture_api_reports():
    reported = hpx.capabilities()
    assert reported == facade.fixture_capabilities()
    assert isinstance(reported, hpx.FixtureCapabilities)
    assert {target.board for target in reported.targets} >= {"apollo510_evb"}


def test_analyze_run_model_reads_the_typed_analysis(monkeypatch, tmp_path):
    seen: list[Path] = []
    analysis = object()

    def analyze(path: Path) -> object:
        seen.append(path)
        return analysis

    monkeypatch.setattr("helia_profiler.fixture_analysis.analyze_typed_fixture_model", analyze)
    assert hpx.analyze_run_model(str(tmp_path / "m.tflite")) is analysis
    assert seen == [tmp_path / "m.tflite"]


def test_a_model_a_run_cannot_pin_is_a_config_error(monkeypatch, tmp_path):
    def analyze(path: Path) -> object:
        raise ValueError("Dynamic fixture tensor shapes are unsupported")

    monkeypatch.setattr("helia_profiler.fixture_analysis.analyze_typed_fixture_model", analyze)
    with pytest.raises(ConfigError, match="Dynamic fixture tensor shapes"):
        hpx.analyze_run_model(tmp_path / "m.tflite")


def test_analyze_run_model_for_engine_takes_an_engine_name(monkeypatch, tmp_path):
    seen: list[tuple[Path, EngineType]] = []

    def analyze(path: Path, *, engine: EngineType) -> object:
        seen.append((path, engine))
        if engine is EngineType.HELIA_RT:
            raise ValueError("not admitted")
        return "analysis"

    monkeypatch.setattr(
        "helia_profiler.fixture_analysis.analyze_typed_fixture_model_for_engine", analyze
    )
    assert hpx.analyze_run_model_for_engine(tmp_path / "m.tflite", "litert") == "analysis"
    assert hpx.analyze_run_model_for_engine(tmp_path / "m.tflite", EngineType.HELIA_AOT)
    assert [engine for _, engine in seen] == [EngineType.LITERT, EngineType.HELIA_AOT]
    with pytest.raises(ConfigError, match="helia-rt cannot build a run"):
        hpx.analyze_run_model_for_engine(tmp_path / "m.tflite", "helia-rt")


@pytest.mark.parametrize("engine", ["tflm", "cmsis"])
def test_an_unknown_engine_is_a_config_error(engine, tmp_path):
    with pytest.raises(ConfigError, match="Unknown engine"):
        hpx.analyze_run_model_for_engine(tmp_path / "m.tflite", engine)


def test_analyze_run_model_matches_the_fixture_analysis_of_a_real_model():
    pytest.importorskip("ai_edge_litert")
    model = hpx.examples.tiny_cnn()
    analysis = hpx.analyze_run_model(model)
    expected = facade.analyze_typed_fixture_model(model)
    assert isinstance(analysis, hpx.TypedFixtureModelAnalysis)
    assert (analysis.inputs, analysis.outputs) == (expected.inputs, expected.outputs)
