"""Request construction has no cache IO; one selected adapter prepares the build."""

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_fixture_request import _request

from helia_profiler import EngineType, TypedFixture
from helia_profiler.config import EngineConfig
from helia_profiler.engines import get_adapter
from helia_profiler.engines.litert import LiteRtAdapter
from helia_profiler.pipeline import PipelineContext
from helia_profiler.placement import Placement
from helia_profiler.stages.prepare_engine import PrepareEngineStage
from tests.runtime.helpers import runtime


def test_selected_archive_does_not_prepare_and_replace_ordinary_engine(tmp_path, monkeypatch):
    (tmp_path / "provider").mkdir()
    cached = runtime(tmp_path / "provider").verify()
    normal = Mock(side_effect=AssertionError("ordinary provider must not prepare"))
    monkeypatch.setattr(LiteRtAdapter, "prepare", normal)
    config = replace(
        _request(tmp_path / "input").to_config(),
        engine=EngineConfig(type=EngineType.LITERT, backend="cmsis_nn"),
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path / "work")
    PrepareEngineStage(runtime=cached).run(ctx)
    normal.assert_not_called()
    assert ctx.engine_artifacts is not None and ctx.run_metadata.engine is not None
    assert [m.name for m in ctx.engine_artifacts.extra_modules] == ["hpx-upstream-runtime"]
    assert ctx.engine_artifacts.cmake_vars == {}
    assert ctx.run_metadata.engine.type == "litert"


def test_builder_applies_adapter_placement_settings_and_pins_its_evidence(tmp_path, monkeypatch):
    """The generic builder hashes the adapter-derived config and records what prepare() reports."""
    from helia_profiler import _fixture_build as builder
    from helia_profiler.engines.base import EngineSource, HeliaAotArtifacts
    from helia_profiler.firmware.op_resolver import ResolverPlan
    from helia_profiler.fixture_analysis import TypedFixtureModelAnalysis
    from helia_profiler.modelcost import ModelAnalysis
    from helia_profiler.pipeline import serialize_config

    request = _request(tmp_path)
    fixture = request.fixture
    assert isinstance(fixture, TypedFixture)
    analysis = TypedFixtureModelAnalysis(
        tuple(io.tensor for io in fixture.inputs),
        tuple(io.tensor for io in fixture.outputs),
        False,
        ModelAnalysis([], 0, 0, 0),
        ResolverPlan("engine", ()),
    )
    monkeypatch.setattr(builder, "analyze_typed_fixture_model_for_engine", lambda *a, **k: analysis)
    derived_args = {"memory": {"allocate_arenas": False}}

    def derive(config):
        engine = replace(config.engine, config={**config.engine.config, "aot_args": derived_args})
        return replace(config, engine=engine)

    adapter = SimpleNamespace(placement_config=Mock(side_effect=derive))
    monkeypatch.setattr(builder, "get_adapter", lambda _engine: adapter)
    # Outside the work directory: only the adapter's report can name it.
    report = tmp_path / "elsewhere" / "hpx_plan.json"
    report.parent.mkdir()
    report.write_text("{}")
    source = EngineSource("helia-aot", "1.2.3", "a" * 40)
    observed = []

    class Runner:
        def __init__(self, stages):
            self.stages = stages

        def run(self, config):
            observed.append(config)
            self.stages[0].run(None)
            app = tmp_path / "app"
            (app / "src").mkdir(parents=True)
            (app / "src" / "main.cc").write_text("fake")
            return SimpleNamespace(
                resolved_firmware_dir=app,
                config=config,
                memory_plan=None,
                profile_run=None,
                engine_artifacts=HeliaAotArtifacts(
                    engine_header="hpx_model.h",
                    aot_prefix="hpx",
                    aot_module_name="hpx_model",
                    aot_cmake_target="nsx::hpx_model",
                    helia_aot_version="1.2.3",
                    source=source,
                    report_files=(report,),
                ),
            )

    monkeypatch.setattr(builder, "PipelineRunner", Runner)
    build = builder.build_fixed_fixture(
        request.to_config(), fixture, method=request.method, compile=False
    )

    adapter.placement_config.assert_called_once()
    assert observed[0].engine.config["aot_args"] == derived_args
    identity = json.loads((request.work_dir / "fixed-fixture-identity.json").read_text())
    serialized = json.dumps(serialize_config(observed[0]), sort_keys=True).encode()
    assert identity["profile"] == hashlib.sha256(serialized).hexdigest()
    assert build.engine_source == source
    assert [(f.path, f.sha256) for f in build.aot_outputs] == [
        (report, hashlib.sha256(b"{}").hexdigest())
    ]


@pytest.mark.parametrize("engine", [e for e in EngineType if e is not EngineType.HELIA_AOT])
def test_single_arena_engines_add_no_placement_settings(tmp_path, engine):
    config = _request(tmp_path).to_config()
    config = replace(config, model=replace(config.model, weights_location=Placement.PSRAM))
    assert get_adapter(engine).placement_config(config) is config
