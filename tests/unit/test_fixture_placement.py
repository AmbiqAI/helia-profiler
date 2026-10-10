"""Explicit paired placements keep request identity and rendered regions aligned."""

from dataclasses import replace

import pytest
from test_fixture_request import _request
from test_typed_fixture import METHOD, analysis_of, config_for, typed

from helia_profiler._fixture_build import FixtureRenderSpec, _validate
from helia_profiler.engines import EngineType
from helia_profiler.engines.base import ArenaRegion
from helia_profiler.errors import ConfigError
from helia_profiler.firmware.fixture import fixture_template_vars
from helia_profiler.firmware.render import _jinja_env
from helia_profiler.fixture import FixturePlacement, Placement
from helia_profiler.pipeline import PipelineContext
from helia_profiler.placement import ArenaRole


def test_paired_tcm_request_has_distinct_intent_and_config(tmp_path):
    sram = _request(tmp_path)
    tcm = replace(sram, placement=FixturePlacement(Placement.TCM, Placement.TCM))
    assert tcm.intent_identity != sram.intent_identity
    config = tcm.to_config()
    assert config.model.arena_location == config.model.weights_location == Placement.TCM
    _validate(config, tcm.fixture)


@pytest.mark.parametrize("engine", [EngineType.TFLM, EngineType.HELIA_AOT])
@pytest.mark.parametrize("placement", [Placement.SRAM, Placement.TCM])
def test_complete_fixture_render_uses_requested_regions(tmp_path, engine, placement):
    fixture = typed(tmp_path)
    config = config_for(tmp_path, fixture, engine)
    config = replace(
        config,
        model=replace(
            config.model,
            arena_location=placement,
            weights_location=Placement.MRAM if placement is Placement.SRAM else Placement.TCM,
        ),
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ctx.fixture = FixtureRenderSpec(fixture, METHOD, analysis_of(fixture))
    regions = [
        ArenaRegion(
            0,
            "scratch",
            4096,
            16,
            ArenaRole.SCRATCH,
            "dtcm" if placement is Placement.TCM else "sram",
            placement,
        )
    ]
    variables = fixture_template_vars(ctx, regions if engine is EngineType.HELIA_AOT else [])
    source = _jinja_env.get_template("fixed_fixture.cc.j2").render(
        **variables,
        aot_prefix="m",
        allocate_arenas=True,
        arena_regions=[],
    )
    for suffix in ("", "_1"):
        restore = (
            f"std::memcpy(input_data{suffix}, fixed_input{suffix}, sizeof(fixed_input{suffix}));"
        )
        assert source.count(restore) == 2
    assert "deployment_output_1[8]" in source
    if engine is EngineType.TFLM:
        macro = "NSX_MEM_FAST_BSS" if placement is Placement.TCM else "NSX_MEM_SRAM_BSS"
        assert f"{macro} alignas(16) static uint8_t arena[262144];" in source


@pytest.mark.parametrize(
    "arena,weights",
    [
        (Placement.TCM, Placement.MRAM),
        (Placement.SRAM, Placement.TCM),
        (Placement.PSRAM, Placement.MRAM),
        (None, Placement.MRAM),
    ],
)
def test_other_placement_pairs_stop_before_pipeline(tmp_path, arena, weights):
    fixture = typed(tmp_path)
    config = config_for(tmp_path, fixture)
    config = replace(
        config, model=replace(config.model, arena_location=arena, weights_location=weights)
    )
    with pytest.raises(ConfigError, match="placement"):
        _validate(config, fixture)


@pytest.mark.parametrize("fault", ["wrong_placement", "wrong_bank", "writable_mram"])
def test_aot_region_drift_is_rejected(tmp_path, fault):
    fixture = typed(tmp_path)
    config = config_for(tmp_path, fixture, EngineType.HELIA_AOT)
    config = replace(
        config,
        model=replace(config.model, arena_location=Placement.TCM, weights_location=Placement.TCM),
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ctx.fixture = FixtureRenderSpec(fixture, METHOD, analysis_of(fixture))
    region = ArenaRegion(0, "scratch", 4096, 16, ArenaRole.SCRATCH, "dtcm", Placement.TCM)
    if fault == "wrong_placement":
        region = replace(region, placement=Placement.SRAM)
    elif fault == "wrong_bank":
        region = replace(region, memory="sram")
    else:
        region = replace(region, placement=Placement.MRAM, memory="mram")
    with pytest.raises(ConfigError):
        fixture_template_vars(ctx, [region])


@pytest.mark.parametrize("placement,memory", [(Placement.SRAM, "dtcm"), (Placement.TCM, "sram")])
@pytest.mark.parametrize("role", [ArenaRole.SCRATCH, ArenaRole.PERSISTENT, ArenaRole.CONSTANT])
def test_aot_physical_bank_cannot_be_hidden_by_logical_override(tmp_path, placement, memory, role):
    fixture = typed(tmp_path)
    config = config_for(tmp_path, fixture, EngineType.HELIA_AOT)
    weights = Placement.MRAM if placement is Placement.SRAM else Placement.TCM
    config = replace(
        config, model=replace(config.model, arena_location=placement, weights_location=weights)
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ctx.fixture = FixtureRenderSpec(fixture, METHOD, analysis_of(fixture))
    reported = weights if role is ArenaRole.CONSTANT else placement
    region = ArenaRegion(
        0,
        "region",
        4096,
        16,
        role,
        memory,
        reported,
        "weights.bin" if role is ArenaRole.CONSTANT else None,
    )
    with pytest.raises(ConfigError, match="physical memory differs"):
        fixture_template_vars(ctx, [region])
