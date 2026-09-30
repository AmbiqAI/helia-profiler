"""Fixture static storage participates in the normal pre-link capacity gate."""

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from helia_profiler.config import load_config
from helia_profiler.engines import EngineType
from helia_profiler.engines.base import HeliaAotArtifacts
from helia_profiler.errors import PlatformError
from helia_profiler._fixture_build import (
    FixedFixture,
    FixtureFile,
    Int8Tensor,
    FixtureMethod,
    FixtureTimingScope,
    FixtureRenderSpec,
)
from helia_profiler.fixture_analysis import FixtureModelAnalysis
from helia_profiler.firmware.op_resolver import ResolverPlan
from helia_profiler.modelcost import ModelAnalysis
from helia_profiler.pipeline import PipelineContext
from helia_profiler.results import MemoryPlan
from helia_profiler.stages.plan_memory import PlanMemoryStage, _add_hpx_owned_consumers
from helia_profiler.stages.resolve_platform import ResolvePlatformStage


def context(
    tmp_path: Path, engine: EngineType, output_size: int, transport: str = "swo"
) -> PipelineContext:
    def pin(name, data):
        path = tmp_path / name
        path.write_bytes(data)
        return FixtureFile(path, sha256(data).hexdigest())

    fixture = FixedFixture(
        pin("model.tflite", b"model"),
        pin("input.bin", bytes(490)),
        pin("output.bin", bytes(output_size)),
        Int8Tensor((1, 490), 0.25, 0, 0),
        Int8Tensor((1, output_size), 0.25, 0, 1),
    )
    config = load_config(
        None,
        {
            "model": {
                "path": str(fixture.model.path),
                "arena_size": 65536,
                "arena_location": "sram",
                "weights_location": "mram",
            },
            "engine": {"type": engine.value},
            "target": {"board": "apollo510_evb", "transport": transport},
            "work_dir": str(tmp_path / "work"),
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path / "work")
    ResolvePlatformStage().run(ctx)
    assert ctx.soc is not None
    ctx.soc = replace(ctx.soc, memory=replace(ctx.soc.memory, dtcm_kb=17))
    ctx.fixture = FixtureRenderSpec(
        fixture,
        FixtureMethod(FixtureTimingScope.RESTORE_AND_INVOKE),
        FixtureModelAnalysis(
            fixture.input_tensor,
            fixture.output_tensor,
            ModelAnalysis([], 0, 0, 0),
            ResolverPlan("auto", ()),
        ),
    )
    if engine is EngineType.HELIA_AOT:
        ctx.engine_artifacts = HeliaAotArtifacts(
            engine_header="hpx_model.h",
            aot_prefix="hpx",
            aot_module_name="hpx_model",
            aot_cmake_target="nsx::hpx_model",
            helia_aot_version="test",
            memory_plan=MemoryPlan(engine=engine),
        )
    return ctx


@pytest.mark.parametrize(
    "engine,output_size", [(EngineType.TFLM, 952), (EngineType.HELIA_AOT, 984)]
)
def test_fixture_statics_fit_at_boundary_and_overflow_one_byte_later(tmp_path, engine, output_size):
    ctx = context(tmp_path, engine, output_size)
    PlanMemoryStage().run(ctx)
    assert ctx.memory_plan is not None
    dtcm = ctx.memory_plan.region("DTCM")
    assert dtcm is not None and dtcm.used == 17 * 1024 and not dtcm.overflow
    larger = context(tmp_path, engine, output_size + 1)
    with pytest.raises(PlatformError, match="over by 1 B"):
        PlanMemoryStage().run(larger)


@pytest.mark.parametrize("transport", ["swo", "rtt", "usb_cdc"])
@pytest.mark.parametrize("engine", [EngineType.TFLM, EngineType.HELIA_RT, EngineType.HELIA_AOT])
def test_fixture_consumers_have_actual_regions_and_no_inactive_profiler_buffers(
    tmp_path, engine, transport
):
    ctx = context(tmp_path, engine, 12, transport)
    PlanMemoryStage().run(ctx)
    plan = ctx.memory_plan
    assert plan is not None
    dtcm = plan.region("DTCM")
    mram = plan.region("MRAM")
    sram = plan.region("SRAM")
    assert dtcm is not None and mram is not None
    sizes = {c.name: c.size for c in dtcm.consumers}
    expected = {
        "boot_stack": 16384,
        "fixture_output": 12,
        "fixture_status": 4,
        "fixture_checksum": 4,
        "fixture_timing": 28,
        "fixture_timer_state": 4,
    }
    if engine is not EngineType.HELIA_AOT:
        expected["fixture_memory"] = 32
    assert sizes == expected
    assert {c.name: c.size for c in mram.consumers}["fixture_input"] == 490
    names = {c.name for region in plan.regions for c in region.consumers}
    assert not names & {"pmu_layer_records", "rtt_buffers", "usb_buffers"}
    if engine is not EngineType.HELIA_AOT:
        assert sram is not None
        assert {c.name: c.size for c in sram.consumers} == {"tensor_arena": 65536}
    again = _add_hpx_owned_consumers(plan, ctx)
    assert again == plan


@pytest.mark.parametrize("observe", [False, True])
def test_observed_aot_scratch_arenas_reserve_their_scan_sink(tmp_path, observe):
    from helia_profiler.engines.base import ArenaRegion
    from helia_profiler.placement import ArenaRole, Placement

    ctx = context(tmp_path, EngineType.HELIA_AOT, 12)
    assert ctx.fixture is not None and isinstance(ctx.engine_artifacts, HeliaAotArtifacts)
    ctx.fixture = replace(ctx.fixture, observe_aot_arenas=observe)
    ctx.engine_artifacts = replace(
        ctx.engine_artifacts,
        aot_arena_regions=[
            ArenaRegion(0, "s0", "S0", 256, 16, ArenaRole.SCRATCH, "sram", Placement.SRAM),
            ArenaRegion(1, "p", "P", 64, 16, ArenaRole.PERSISTENT, "sram", Placement.SRAM),
            ArenaRegion(2, "s1", "S1", 128, 16, ArenaRole.SCRATCH, "sram", Placement.SRAM),
        ],
    )
    PlanMemoryStage().run(ctx)
    assert ctx.memory_plan is not None
    dtcm = ctx.memory_plan.region("DTCM")
    assert dtcm is not None
    sizes = {c.name: c.size for c in dtcm.consumers}
    assert sizes.get("fixture_arena_scan") == (16 if observe else None)
