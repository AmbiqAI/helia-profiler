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
    "TypedFixture": "fixtures.types",
    "FixtureIO": "fixtures.types",
    "FixtureOutput": "fixtures.types",
    "FixtureRole": "fixtures.types",
    "FixtureMethod": "fixtures.types",
    "FixtureTimingScope": "fixtures.types",
    "FixtureDType": "fixtures.io",
    "FixtureCapability": "fixtures.io",
    "FixtureStatePolicy": "fixtures.types",
    "FixtureBuild": "fixtures.types",
    "FixtureCapabilities": "fixtures.capabilities",
    "QualifiedFixtureTarget": "fixtures.capabilities",
    "EngineDTypeCapability": "fixtures.capabilities",
    "FixtureTensor": "fixtures.analysis",
    "TypedFixtureModelAnalysis": "fixtures.analysis",
    "PerTensorQuantization": "fixtures.analysis",
    "PerAxisQuantization": "fixtures.analysis",
    "FixtureCaptureGuard": "fixtures.capture",
    "FixtureCaptureResult": "fixtures.capture",
    "FixtureTiming": "fixtures.capture",
    "FixtureMemory": "fixtures.capture",
    "FixturePowerSync": "fixtures.power",
    "FixtureRailEnergy": "fixtures.power",
    "FixtureBatchCompletion": "fixtures.power",
    "FixtureRailPowerRequest": "fixtures.power",
    "FixtureBoardPowerRequest": "fixtures.power",
    "FixturePowerGuard": "fixtures.power",
    "FixturePsramWeights": "fixtures.psram",
    "FixturePsramPayload": "fixtures.psram",
    "FixturePsramObservation": "fixtures.psram",
    "FixtureFile": "_files",
    "PreparedUpstreamRuntime": "runtime.archive",
    "VerifiedPreparedRuntime": "runtime.archive",
    "RuntimeABI": "runtime.archive",
    "RuntimeHeader": "runtime.archive",
    "RuntimeProvider": "runtime.archive",
    "FixtureKernelSource": "fixtures.sources",
    "FixtureRuntimeSource": "fixtures.sources",
    "FixtureTarget": "fixtures.target",
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
    from helia_profiler.runtime.archive import RuntimeManifest

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
        "runs.compiled",
        "fixtures.build",
        "fixtures.types",
        "fixtures.io",
        "fixtures.capabilities",
        "fixtures.analysis",
        "fixtures.capture",
        "fixtures.power",
        "fixtures.psram",
        "runtime.archive",
        "_files",
        "fixtures.sources",
        "fixtures.target",
        "runs.plan",
        "identity",
        "runs.expect",
        "lifecycle",
        "runs.guard",
        "runs.record",
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
    from helia_profiler.fixtures.capabilities import fixture_capabilities

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

    monkeypatch.setattr("helia_profiler.fixtures.analysis.analyze_typed_fixture_model", analyze)
    assert hpx.analyze_run_model(str(tmp_path / "m.tflite")) is analysis
    assert seen == [tmp_path / "m.tflite"]


def test_a_model_a_run_cannot_pin_is_a_config_error(monkeypatch, tmp_path):
    def analyze(path: Path) -> object:
        raise ValueError("Dynamic fixture tensor shapes are unsupported")

    monkeypatch.setattr("helia_profiler.fixtures.analysis.analyze_typed_fixture_model", analyze)
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
        "helia_profiler.fixtures.analysis.analyze_typed_fixture_model_for_engine", analyze
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
    from helia_profiler.fixtures.analysis import analyze_typed_fixture_model

    expected = analyze_typed_fixture_model(model)
    assert isinstance(analysis, hpx.TypedFixtureModelAnalysis)
    assert (analysis.inputs, analysis.outputs) == (expected.inputs, expected.outputs)
