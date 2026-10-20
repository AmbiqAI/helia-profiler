"""The run API's inputs, capabilities and model analysis, imported from ``helia_profiler``."""

from __future__ import annotations

import ast
import dataclasses
import enum
import importlib
import inspect
import typing
from pathlib import Path
from typing import Any

import pytest

import helia_profiler as hpx
from helia_profiler.engines import EngineType
from helia_profiler.errors import ConfigError

#: Each run-API type exported by ``helia_profiler``, and the module that defines it.
_RUN_INPUT_TYPES = {
    "TypedFixture": "_fixture_build",
    "FixtureIO": "_fixture_build",
    "FixtureOutput": "_fixture_build",
    "FixtureRole": "_fixture_build",
    "FixtureMethod": "_fixture_build",
    "FixtureTimingScope": "_fixture_build",
    "FixtureDType": "_fixture_build",
    "FixtureCapability": "_fixture_build",
    "FixtureStatePolicy": "_fixture_build",
    "FixtureBuild": "_fixture_build",
    "FixtureCapabilities": "_fixture_facts",
    "QualifiedFixtureTarget": "_fixture_facts",
    "EngineDTypeCapability": "_fixture_facts",
    "FixtureTensor": "fixture_analysis",
    "TypedFixtureModelAnalysis": "fixture_analysis",
    "PerTensorQuantization": "fixture_analysis",
    "PerAxisQuantization": "fixture_analysis",
    "FixtureCaptureGuard": "fixture_capture",
    "FixtureCaptureResult": "fixture_capture",
    "FixtureTiming": "fixture_capture",
    "FixtureMemory": "fixture_capture",
    "FixturePowerSync": "fixture_power",
    "FixtureRailEnergy": "fixture_power",
    "FixtureBatchCompletion": "fixture_power",
    "FixtureRailPowerRequest": "fixture_power",
    "FixtureBoardPowerRequest": "fixture_power",
    "FixturePowerGuard": "fixture_power",
    "FixturePsramWeights": "fixture_psram",
    "FixturePsramPayload": "fixture_psram",
    "FixturePsramObservation": "fixture_psram",
    "FixtureFile": "fixture_runtime",
    "PreparedUpstreamRuntime": "fixture_runtime",
    "VerifiedPreparedRuntime": "fixture_runtime",
    "RuntimeABI": "fixture_runtime",
    "RuntimeHeader": "fixture_runtime",
    "RuntimeProvider": "fixture_runtime",
    "FixtureKernelSource": "fixture_sources",
    "FixtureRuntimeSource": "fixture_sources",
    "FixtureTarget": "fixture_target",
    "EngineSource": "engines.base",
    "ToolchainInfo": "results.models",
    "MemoryPlan": "results.models",
    "ResolverPlan": "firmware.op_resolver",
    "RunIdentity": "identity",
    "RunFacts": "identity",
    "BuildFacts": "identity",
    "EnergyFacts": "identity",
    "KernelIdentity": "identity",
    "RuntimeIdentity": "identity",
}
_NEW = (
    "capabilities",
    "analyze_run_model",
    "analyze_run_model_for_engine",
    "OperatorGuard",
)


@pytest.mark.parametrize("name,module", sorted(_RUN_INPUT_TYPES.items()))
def test_each_run_input_type_is_its_modules_type(name, module):
    defined = importlib.import_module(f"helia_profiler.{module}")
    assert getattr(hpx, name) is getattr(defined, name)


def test_the_prepared_runtime_manifest_is_the_runtime_manifest():
    from helia_profiler.fixture_runtime import RuntimeManifest

    assert hpx.PreparedRuntimeManifest is RuntimeManifest


def test_the_run_inputs_are_experimental_exports():
    names = {*_RUN_INPUT_TYPES, *_NEW, "PreparedRuntimeManifest", "Expect", "Tolerance"}
    assert names <= set(hpx.__all__)
    assert {hpx.__api_stability__[name] for name in names} == {"experimental"}


#: The run API's own modules. The walk descends into their types; a type from another
#: surface, such as ``ProfileConfig``, must be exported where it is met but is not walked.
_RUN_API_MODULES = {
    f"helia_profiler.{name}"
    for name in (
        "compiled_build",
        "_fixture_build",
        "_fixture_facts",
        "fixture_analysis",
        "fixture_capture",
        "fixture_power",
        "fixture_psram",
        "fixture_runtime",
        "fixture_sources",
        "fixture_target",
        "frozen_run",
        "identity",
        "expect",
        "lifecycle",
        "run_guard",
        "run_record",
    )
}
_RUN_API_ENTRY_POINTS = (
    "build",
    "load_build",
    "run_build",
    "load_run",
    "capabilities",
    "analyze_run_model",
    "analyze_run_model_for_engine",
)


def _hints(obj: Any, module_name: str) -> dict[str, Any]:
    """*obj*'s resolved annotations, names imported only under ``TYPE_CHECKING`` included."""
    module = importlib.import_module(module_name)
    namespace = dict(vars(module))
    for node in ast.parse(inspect.getsource(module)).body:
        if isinstance(node, ast.If) and getattr(node.test, "id", None) == "TYPE_CHECKING":
            block = ast.Module(body=node.body, type_ignores=[])
            exec(compile(block, module.__file__ or module_name, "exec"), namespace)
    return typing.get_type_hints(obj, globalns=namespace)


def _classes(annotation: Any) -> typing.Iterator[type]:
    for argument in typing.get_args(annotation):
        yield from _classes(argument)
    origin = typing.get_origin(annotation)
    if isinstance(origin, type):
        yield origin
    elif origin is None and isinstance(annotation, type):
        yield annotation


def _reachable_run_api_types() -> dict[type, str]:
    """Every public HPX class the run API's signatures, fields and return types name."""
    reached: dict[type, str] = {}

    def callable_(function: Any, module_name: str, via: str) -> None:
        for annotation in _hints(function, module_name).values():
            for cls in _classes(annotation):
                class_(cls, via)

    def class_(cls: type, via: str) -> None:
        if not cls.__module__.startswith("helia_profiler") or cls.__name__.startswith("_"):
            return
        if cls in reached:
            return
        reached[cls] = via
        if issubclass(cls, (enum.Enum, BaseException)) or cls.__module__ not in _RUN_API_MODULES:
            return
        if dataclasses.is_dataclass(cls):
            for annotation in _hints(cls, cls.__module__).values():
                for member in _classes(annotation):
                    class_(member, cls.__name__)
        for name, member in vars(cls).items():
            if name.startswith("_"):
                continue
            if isinstance(member, property):
                member = member.fget
            elif isinstance(member, (classmethod, staticmethod)):
                member = member.__func__
            if inspect.isfunction(member):
                callable_(member, cls.__module__, f"{cls.__name__}.{name}")

    for name in _RUN_API_ENTRY_POINTS:
        function = getattr(hpx, name)
        callable_(function, function.__module__, name)
    return reached


def test_every_type_the_run_api_reaches_is_importable_from_helia_profiler():
    exported = {id(getattr(hpx, name)) for name in hpx.__all__}
    reached = _reachable_run_api_types()

    missing = sorted(
        f"{cls.__qualname__} (via {via})" for cls, via in reached.items() if id(cls) not in exported
    )

    assert missing == []
    assert {hpx.FixtureBuild, hpx.FixtureTiming, hpx.RunIdentity, hpx.PerAxisQuantization} <= set(
        reached
    )


def test_capabilities_reports_the_producer_capabilities():
    from helia_profiler._fixture_facts import fixture_capabilities

    reported = hpx.capabilities()
    assert reported == fixture_capabilities()
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


def test_an_unreadable_model_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="Cannot build a run of"):
        hpx.analyze_run_model(tmp_path / "absent.tflite")
    with pytest.raises(ConfigError, match="litert cannot build a run of"):
        hpx.analyze_run_model_for_engine(tmp_path, "litert")


@pytest.mark.parametrize("engine", ["tflm", "cmsis"])
def test_an_unknown_engine_is_a_config_error(engine, tmp_path):
    with pytest.raises(ConfigError, match="Unknown engine"):
        hpx.analyze_run_model_for_engine(tmp_path / "m.tflite", engine)


def test_analyze_run_model_matches_the_fixture_analysis_of_a_real_model():
    pytest.importorskip("ai_edge_litert")
    model = hpx.examples.tiny_cnn()
    analysis = hpx.analyze_run_model(model)
    from helia_profiler.fixture_analysis import analyze_typed_fixture_model

    expected = analyze_typed_fixture_model(model)
    assert isinstance(analysis, hpx.TypedFixtureModelAnalysis)
    assert (analysis.inputs, analysis.outputs) == (expected.inputs, expected.outputs)
