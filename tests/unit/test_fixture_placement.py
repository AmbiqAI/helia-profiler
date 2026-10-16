"""Explicit paired placements keep request identity and rendered regions aligned."""

import json
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


@pytest.mark.parametrize("board", ["apollo510_evb", "apollo510b_evb"])
@pytest.mark.parametrize("engine", [EngineType.TFLM, EngineType.HELIA_AOT])
@pytest.mark.parametrize("placement", [Placement.SRAM, Placement.TCM])
def test_complete_fixture_render_uses_requested_regions(tmp_path, engine, placement, board):
    fixture = typed(tmp_path)
    config = config_for(tmp_path, fixture, engine)
    config = replace(
        config,
        target=replace(config.target, board=board),
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
        (Placement.MRAM, Placement.MRAM),
        (Placement.PSRAM, Placement.MRAM),
        (None, Placement.MRAM),
    ],
)
def test_other_placement_pairs_stop_before_pipeline(tmp_path, arena, weights):
    fixture = typed(tmp_path)
    config = config_for(tmp_path, fixture)
    with pytest.raises(ConfigError, match="arena_location|placement"):
        config = replace(
            config, model=replace(config.model, arena_location=arena, weights_location=weights)
        )
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


def mixed_context(tmp_path):
    from helia_profiler.engines.base import HeliaAotArtifacts
    from helia_profiler.placement import MemoryRegion
    from helia_profiler.results import MemoryPlan, MemoryRegionUsage

    fixture = typed(tmp_path)
    config = config_for(tmp_path, fixture, EngineType.HELIA_AOT)
    config = replace(
        config,
        engine=replace(
            config.engine,
            config={
                "aot_args": {
                    "memory": {
                        "tensors": [
                            {"type": "scratch", "attributes": {"memory": "dtcm"}},
                            {"type": "scratch", "id": "slow", "attributes": {"memory": "sram"}},
                        ]
                    }
                }
            },
        ),
    )
    regions = [
        ArenaRegion(0, "fast", 256, 16, ArenaRole.SCRATCH, "dtcm", Placement.TCM),
        ArenaRegion(1, "slow", 512, 16, ArenaRole.SCRATCH, "sram", Placement.SRAM),
        ArenaRegion(2, "weights", 128, 16, ArenaRole.CONSTANT, "mram", Placement.MRAM, "c.bin"),
    ]
    plan = MemoryPlan(
        engine=EngineType.HELIA_AOT,
        regions=tuple(
            MemoryRegionUsage(MemoryRegion(bank), 0, size)
            for bank, size in (("DTCM", 256), ("SRAM", 512), ("MRAM", 128))
        ),
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ctx.fixture = FixtureRenderSpec(fixture, METHOD, analysis_of(fixture))
    ctx.memory_plan = plan
    ctx.engine_artifacts = HeliaAotArtifacts(
        engine_header="m.h",
        aot_prefix="m",
        aot_module_name="m",
        aot_cmake_target="m",
        helia_aot_version="test",
        aot_user_memory_config=True,
        aot_memory_intent_json=json.dumps(config.engine.config["aot_args"]["memory"]),
        aot_arena_regions=regions,
        memory_plan=plan,
    )
    return ctx, regions


def test_explicit_mixed_aot_regions_keep_emitted_banks(tmp_path):
    ctx, regions = mixed_context(tmp_path)
    fixture_template_vars(ctx, regions)
    assert [r.memory for r in regions] == ["dtcm", "sram", "mram"]


@pytest.mark.parametrize(
    "fault",
    [
        "coarse",
        "missing_plan",
        "missing_region",
        "wrong_bank",
        "writable_mram",
        "oversized",
        "zero_size",
        "bad_alignment",
        "external",
    ],
)
def test_mixed_regions_preserve_admission_guards(tmp_path, fault):
    ctx, regions = mixed_context(tmp_path)
    if fault == "coarse":
        ctx.engine_artifacts = replace(ctx.engine_artifacts, aot_memory_intent_json="{}")
    elif fault == "missing_plan":
        ctx.memory_plan = None
    elif fault == "missing_region":
        regions = regions[:-1]
    elif fault == "external":
        ctx.engine_artifacts = replace(ctx.engine_artifacts, aot_allocate_arenas=False)
    else:
        changes = {
            "wrong_bank": {"memory": "sram"},
            "writable_mram": {"memory": "mram", "placement": Placement.MRAM},
            "oversized": {"size": 10_000_000},
            "zero_size": {"size": 0},
            "bad_alignment": {"alignment": 3},
        }
        regions[0] = replace(regions[0], **changes[fault])
    with pytest.raises(ConfigError):
        fixture_template_vars(ctx, regions)


def test_mixed_region_evidence_survives_record_and_rejects_tampering(tmp_path):
    from test_fixture_record import _build, _pin

    from helia_profiler._fixture_placement import validate_fixture_regions
    from helia_profiler._fixture_record import compiled_fixture_identity
    from helia_profiler.fixture import dump_fixture_build, load_fixture_build

    ctx, regions = mixed_context(tmp_path)
    evidence = validate_fixture_regions(ctx, regions)
    assert evidence is not None
    assert [r["memory"] for r in evidence["regions"]] == ["dtcm", "sram", "mram"]
    assert evidence["memory_intent"]["tensors"][1]["id"] == "slow"
    pinned = _pin(tmp_path / "fixture-aot-regions.json", json.dumps(evidence).encode())
    build = replace(_build(tmp_path), aot_outputs=(pinned,), planned_memory=ctx.memory_plan)
    build = replace(build, build_identity=compiled_fixture_identity(build))
    record = dump_fixture_build(build, roots={"run": tmp_path})
    assert load_fixture_build(record, roots={"run": tmp_path}) == build
    pinned.path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        load_fixture_build(record, roots={"run": tmp_path})


def test_mixed_evidence_does_not_reread_config_path(tmp_path):
    from helia_profiler._fixture_placement import validate_fixture_regions

    ctx, regions = mixed_context(tmp_path)
    ctx.config = replace(
        ctx.config,
        engine=replace(
            ctx.config.engine, config_path=tmp_path / "no-longer-present.yaml", config={}
        ),
    )
    evidence = validate_fixture_regions(ctx, regions)
    assert evidence is not None
    assert evidence["memory_intent"]["tensors"][0]["attributes"]["memory"] == "dtcm"
