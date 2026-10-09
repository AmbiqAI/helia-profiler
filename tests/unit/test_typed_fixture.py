"""Typed multi-tensor fixed fixtures: declarations, capabilities, budget, build and render."""

import hashlib
import json
from dataclasses import replace
from hashlib import sha256
from types import SimpleNamespace

import pytest

from helia_profiler._fixture_build import (
    FIXTURE_CAPABILITIES,
    FIXTURE_READBACK_BUDGET,
    FixedFixture,
    FixtureCapability,
    FixtureFile,
    FixtureIO,
    FixtureMethod,
    FixtureRenderSpec,
    FixtureTimingScope,
    Int8Tensor,
    PreparedUpstreamRuntime,
    TypedFixture,
    _BindFixtureStage,
    _check_typed_fixture,
    build_fixed_fixture,
)
from helia_profiler.config import (
    ClockSelection,
    EngineConfig,
    ModelConfig,
    ProfileConfig,
    ProfilingConfig,
    TargetConfig,
)
from helia_profiler.engines import EngineType
from helia_profiler.engines.base import ArenaRegion, HeliaAotArtifacts
from helia_profiler.errors import ConfigError
from helia_profiler.firmware.fixture import fixture_template_vars
from helia_profiler.firmware.op_resolver import build_fixture_resolver_plan
from helia_profiler.firmware.render import _jinja_env
from helia_profiler.fixture_analysis import (
    FixtureModelAnalysis,
    FixtureTensor,
    PerAxisQuantization,
    PerTensorQuantization,
    TypedFixtureModelAnalysis,
)
from helia_profiler.modelcost.model_analysis import LayerOps, ModelAnalysis
from helia_profiler.pipeline import PipelineContext
from helia_profiler.placement import ArenaRole, Placement
from helia_profiler.results.models import MemoryPlan, RunMetadata, ToolchainInfo
from helia_profiler.vocab import Toolchain

SIGNAL = FixtureTensor("signal", 0, "int16", (1, 16), PerTensorQuantization(2**-10, 0))
GAIN = FixtureTensor("gain", 1, "float32", (1, 1), None)
LABEL = FixtureTensor("label", 3, "int8", (1, 4), PerTensorQuantization(2**-8, -128))
LEVEL = FixtureTensor("level", 4, "float32", (1, 2), None)


def pin(tmp_path, name, data):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / name
    path.write_bytes(data)
    return FixtureFile(path, sha256(data).hexdigest())


def typed(tmp_path, inputs=(SIGNAL, GAIN), outputs=(LABEL, LEVEL), roles=None):
    roles = roles or {}
    return TypedFixture(
        pin(tmp_path, "model.tflite", b"fixture-model"),
        tuple(
            FixtureIO(
                t,
                pin(tmp_path, f"in-{t.name}.bin", bytes(t.size_bytes)),
                roles.get(t.name, "signal"),
            )
            for t in inputs
        ),
        tuple(
            FixtureIO(t, pin(tmp_path, f"out-{t.name}.bin", bytes([7]) * t.size_bytes))
            for t in outputs
        ),
    )


def analysis_of(fixture, *, has_float16=False):
    ops = ModelAnalysis([LayerOps(0, "FULLY_CONNECTED")], 0, 0, 0)
    return TypedFixtureModelAnalysis(
        tuple(io.tensor for io in fixture.inputs),
        tuple(io.tensor for io in fixture.outputs),
        has_float16,
        ops,
        build_fixture_resolver_plan(ops),
    )


def config_for(tmp_path, fixture, engine=EngineType.TFLM):
    return ProfileConfig(
        model=ModelConfig(
            path=fixture.model.path,
            arena_size=262144,
            arena_location=Placement.SRAM,
            weights_location=Placement.MRAM,
        ),
        engine=EngineConfig(type=engine, backend="cmsis_nn"),
        target=TargetConfig(
            board="apollo510_evb", toolchain=Toolchain.ATFE, clock=ClockSelection(cpu="lp")
        ),
        profiling=ProfilingConfig(iterations=100, warmup=5),
        work_dir=tmp_path / "build",
    )


def runtime(tmp_path):
    archive = pin(tmp_path, "runtime.a", b"!<arch>\nfixture-member")
    header = pin(tmp_path, "header.h", b"header")
    manifest = {
        "schema_version": 1,
        "archive_sha256": archive.sha256,
        "providers": {
            "tflite-micro": {
                "url": "https://github.com/tensorflow/tflite-micro",
                "revision": "a" * 40,
            },
            "cmsis-nn": {"url": "https://github.com/ARM-software/CMSIS-NN", "revision": "b" * 40},
        },
        "abi": {"toolchain": "atfe", "cpu": "cortex-m55", "float_abi": "hard", "short_enums": True},
        "headers": {"header.h": header.sha256},
        "include_dirs": ["."],
    }
    return PreparedUpstreamRuntime(
        archive, tmp_path, pin(tmp_path, "runtime.json", json.dumps(manifest).encode())
    )


METHOD = FixtureMethod(FixtureTimingScope.RESTORE_AND_INVOKE)


# --- declarations -----------------------------------------------------------


def test_verify_checks_every_tensor_extent_and_role(tmp_path):
    f = typed(tmp_path)
    f.verify()
    bad = replace(
        f,
        inputs=(f.inputs[0], replace(f.inputs[1], data=pin(tmp_path, "short.bin", bytes(3)))),
    )
    with pytest.raises(ValueError, match="gain byte extent"):
        bad.verify()
    with pytest.raises(ValueError, match="role"):
        typed(tmp_path, roles={"signal": "state"}).verify()
    with pytest.raises(ValueError, match="role"):
        replace(f, outputs=(replace(f.outputs[0], role="aux"),)).verify()
    with pytest.raises(ValueError, match="inputs and outputs"):
        replace(f, outputs=()).verify()


def test_identity_is_path_free_and_covers_every_declaration(tmp_path):
    a = typed(tmp_path / "a")
    b = typed(tmp_path / "b")
    assert a.identity == b.identity
    assert a.expected == a.outputs[0].data
    variants = [
        typed(tmp_path / "c", roles={"gain": "aux"}),
        typed(tmp_path / "d", outputs=(LABEL, replace(LEVEL, name="energy"))),
        typed(tmp_path / "e", inputs=(GAIN, SIGNAL)),
        typed(tmp_path / "f", outputs=(LABEL,)),
        typed(
            tmp_path / "g",
            inputs=(replace(SIGNAL, quantization=PerTensorQuantization(2**-10, 1)), GAIN),
        ),
    ]
    assert len({a.identity, *(v.identity for v in variants)}) == 1 + len(variants)


# --- producer capability and budget -----------------------------------------


def test_capability_table_covers_every_fixture_engine_and_dtype():
    assert set(FIXTURE_CAPABILITIES) == {EngineType.TFLM, EngineType.HELIA_RT, EngineType.HELIA_AOT}
    for table in FIXTURE_CAPABILITIES.values():
        assert set(table) == {"int8", "int16", "int32", "float16", "float32"}
    assert FIXTURE_CAPABILITIES[EngineType.TFLM]["int8"] is FixtureCapability.QUALIFIED
    assert FIXTURE_CAPABILITIES[EngineType.HELIA_AOT]["int8"] is FixtureCapability.QUALIFIED
    assert FIXTURE_CAPABILITIES[EngineType.TFLM]["float16"] is FixtureCapability.UNSUPPORTED
    # No heliaRT fixture has a device pass yet.
    assert set(FIXTURE_CAPABILITIES[EngineType.HELIA_RT].values()) == {FixtureCapability.SUPPORTED}


@pytest.mark.parametrize("engine", [EngineType.TFLM, EngineType.HELIA_AOT])
def test_check_returns_capabilities_of_the_dtypes_used(tmp_path, engine):
    f = typed(tmp_path)
    # int8 is qualified per engine, but only for one input and one output.
    assert _check_typed_fixture(f, analysis_of(f), engine) == (
        ("float32", FixtureCapability.SUPPORTED),
        ("int16", FixtureCapability.SUPPORTED),
        ("int8", FixtureCapability.SUPPORTED),
    )
    single = typed(tmp_path / "single", inputs=(replace(LABEL, index=0),), outputs=(LABEL,))
    assert _check_typed_fixture(single, analysis_of(single), engine) == (
        ("int8", FixtureCapability.QUALIFIED),
    )


@pytest.mark.parametrize("engine", [EngineType.TFLM, EngineType.HELIA_AOT])
def test_per_axis_io_is_not_qualified_even_for_one_input_and_output(tmp_path, engine):
    per_axis = FixtureTensor("x", 0, "int8", (1, 2), PerAxisQuantization(1, (0.5, 0.25), (0, 1)))
    f = typed(tmp_path, inputs=(per_axis,), outputs=(LABEL,))
    assert _check_typed_fixture(f, analysis_of(f), engine) == (
        ("int8", FixtureCapability.SUPPORTED),
    )


@pytest.mark.parametrize(
    "declared",
    [
        lambda f: replace(f, inputs=f.inputs[::-1]),
        lambda f: replace(f, outputs=f.outputs[:1]),
        lambda f: replace(
            f, inputs=(replace(f.inputs[0], tensor=replace(SIGNAL, index=2)), f.inputs[1])
        ),
        lambda f: replace(
            f,
            outputs=(
                replace(
                    f.outputs[0],
                    tensor=replace(
                        LABEL,
                        quantization=PerAxisQuantization(1, (2**-8,) * 4, (-128,) * 4),
                    ),
                ),
                f.outputs[1],
            ),
        ),
    ],
)
def test_declarations_must_equal_the_model(tmp_path, declared):
    f = typed(tmp_path)
    with pytest.raises(ConfigError, match="declarations differ"):
        _check_typed_fixture(declared(f), analysis_of(f), EngineType.HELIA_AOT)


def test_readback_budget_is_inclusive_and_counts_every_output(tmp_path):
    at = FixtureTensor("a", 3, "float32", (1, FIXTURE_READBACK_BUDGET // 4 - 1), None)
    over = FixtureTensor("a", 3, "float32", (1, FIXTURE_READBACK_BUDGET // 4), None)
    tail = FixtureTensor("b", 4, "int8", (1, 4), PerTensorQuantization(0.5, 0))
    fits = typed(tmp_path / "fits", outputs=(at, tail))
    assert fits.outputs[0].tensor.size_bytes + tail.size_bytes == FIXTURE_READBACK_BUDGET
    _check_typed_fixture(fits, analysis_of(fits), EngineType.HELIA_AOT)
    spills = typed(tmp_path / "spills", outputs=(over, tail))
    with pytest.raises(ConfigError, match="readback budget"):
        _check_typed_fixture(spills, analysis_of(spills), EngineType.HELIA_AOT)


def test_float16_is_refused_on_tflm_even_behind_float32_io(tmp_path):
    f = typed(tmp_path)
    with pytest.raises(ConfigError, match="float16"):
        _check_typed_fixture(f, analysis_of(f, has_float16=True), EngineType.TFLM)
    _check_typed_fixture(f, analysis_of(f, has_float16=True), EngineType.HELIA_AOT)


def test_unsupported_io_dtype_is_refused(tmp_path, monkeypatch):
    f = typed(tmp_path)
    table = dict(FIXTURE_CAPABILITIES[EngineType.HELIA_AOT], int16=FixtureCapability.UNSUPPORTED)
    monkeypatch.setitem(FIXTURE_CAPABILITIES, EngineType.HELIA_AOT, table)
    with pytest.raises(ConfigError, match="do not support int16"):
        _check_typed_fixture(f, analysis_of(f), EngineType.HELIA_AOT)


# --- build ------------------------------------------------------------------


def _runner(tmp_path, calls, regions=()):
    class Runner:
        def __init__(self, stages):
            self.stages = stages
            calls.append(self)

        def run(self, config):
            ctx = PipelineContext(config=config, work_dir=tmp_path)
            self.stages[0].run(ctx)
            bind = next(s for s in self.stages if s.name == "bind_fixture_render")
            bind.run(ctx)
            self.spec = ctx.fixture
            app = tmp_path / "app"
            (app / "src").mkdir(parents=True, exist_ok=True)
            (app / "src" / "main.cc").write_text("main")
            return SimpleNamespace(
                memory_plan=MemoryPlan(engine=config.engine.type),
                engine_artifacts=HeliaAotArtifacts(
                    engine_header="m.h",
                    aot_prefix="m",
                    aot_module_name="m",
                    aot_cmake_target="m",
                    helia_aot_version="test",
                    aot_arena_regions=list(regions),
                )
                if config.engine.type is EngineType.HELIA_AOT
                else None,
                run_metadata=RunMetadata(toolchain=ToolchainInfo(compiler="atfe")),
                resolved_firmware_dir=app,
                profile_run=None,
            )

    return Runner


REGIONS = (
    ArenaRegion(0, "scratch", 4096, 16, ArenaRole.SCRATCH, "sram", Placement.SRAM),
    ArenaRegion(1, "persistent", 64, 16, ArenaRole.PERSISTENT, "sram", Placement.SRAM),
    ArenaRegion(2, "constant", 128, 16, ArenaRole.CONSTANT, "mram", Placement.MRAM, "c.bin"),
)


@pytest.mark.parametrize("engine", [EngineType.TFLM, EngineType.HELIA_AOT])
def test_typed_build_reports_outputs_capabilities_and_scan(tmp_path, monkeypatch, engine):
    f = typed(tmp_path)
    c = config_for(tmp_path, f, engine)
    calls = []
    monkeypatch.setattr(
        "helia_profiler._fixture_build.analyze_typed_fixture_model", lambda _: analysis_of(f)
    )
    monkeypatch.setattr(
        "helia_profiler._fixture_build.analyze_fixture_model",
        lambda _: pytest.fail("typed fixture used the single-INT8 analysis"),
    )
    monkeypatch.setattr(
        "helia_profiler._fixture_build.PipelineRunner", _runner(tmp_path, calls, REGIONS)
    )
    rt = runtime(tmp_path) if engine is EngineType.TFLM else None
    observe = engine is EngineType.HELIA_AOT
    r = build_fixed_fixture(
        c, f, method=METHOD, runtime=rt, compile=False, observe_aot_arenas=observe
    )
    assert r.fixture_identity == f.identity
    assert r.outputs == tuple(io.data for io in f.outputs)
    assert r.expected == f.outputs[0].data
    assert dict(r.capabilities)["int8"] is FixtureCapability.SUPPORTED
    assert r.aot_arena_scan == (((0, 4096),) if observe else ())
    assert calls[-1].spec.observe_aot_arenas is observe


def test_arena_observation_changes_intent_and_binds_the_work_dir(tmp_path, monkeypatch):
    f = typed(tmp_path)
    c = config_for(tmp_path, f, EngineType.HELIA_AOT)
    monkeypatch.setattr(
        "helia_profiler._fixture_build.analyze_typed_fixture_model", lambda _: analysis_of(f)
    )
    monkeypatch.setattr(
        "helia_profiler._fixture_build.PipelineRunner", _runner(tmp_path, [], REGIONS)
    )
    plain = build_fixed_fixture(c, f, method=METHOD, compile=False)
    with pytest.raises(ConfigError, match="different fixture"):
        build_fixed_fixture(c, f, method=METHOD, compile=False, observe_aot_arenas=True)
    observed = build_fixed_fixture(
        replace(c, work_dir=tmp_path / "observed"),
        f,
        method=METHOD,
        compile=False,
        observe_aot_arenas=True,
    )
    assert observed.intent_identity != plain.intent_identity


def test_single_int8_intent_identity_is_unchanged_by_the_scan_option(tmp_path, monkeypatch):
    t_in, t_out = Int8Tensor((1, 4), 0.25, -3, 0), Int8Tensor((1, 4), 0.5, 2, 1)
    f = FixedFixture(
        pin(tmp_path, "model.tflite", b"fixture-model"),
        pin(tmp_path, "input.bin", bytes(4)),
        pin(tmp_path, "output.bin", bytes(4)),
        t_in,
        t_out,
    )
    ops = ModelAnalysis([LayerOps(0, "RELU")], 0, 0, 0)
    single = FixtureModelAnalysis(t_in, t_out, ops, build_fixture_resolver_plan(ops))
    c = config_for(tmp_path, f, EngineType.HELIA_AOT)
    monkeypatch.setattr("helia_profiler._fixture_build.analyze_fixture_model", lambda _: single)
    monkeypatch.setattr("helia_profiler._fixture_build.PipelineRunner", _runner(tmp_path, []))
    r = build_fixed_fixture(c, f, method=METHOD, compile=False)
    assert c.work_dir is not None
    identity = json.loads((c.work_dir / "fixed-fixture-identity.json").read_text())
    assert set(identity) == {"runtime", "timing_scope", "fixture", "profile"}
    assert (
        r.intent_identity
        == hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    )
    assert r.outputs == (f.expected,)
    assert r.capabilities == (("int8", FixtureCapability.QUALIFIED),)


def test_arena_observation_is_heliaaot_only(tmp_path, monkeypatch):
    f = typed(tmp_path)
    monkeypatch.setattr(
        "helia_profiler._fixture_build.analyze_typed_fixture_model", lambda _: analysis_of(f)
    )
    monkeypatch.setattr(
        "helia_profiler._fixture_build.PipelineRunner", lambda *_: pytest.fail("pipeline reached")
    )
    with pytest.raises(ConfigError, match="heliaAOT fixtures only"):
        build_fixed_fixture(
            config_for(tmp_path, f),
            f,
            method=METHOD,
            runtime=runtime(tmp_path),
            observe_aot_arenas=True,
        )


def test_typed_mismatch_or_float16_on_tflm_stops_before_pipeline(tmp_path, monkeypatch):
    f = typed(tmp_path)
    monkeypatch.setattr(
        "helia_profiler._fixture_build.PipelineRunner", lambda *_: pytest.fail("pipeline reached")
    )
    monkeypatch.setattr(
        "helia_profiler._fixture_build.analyze_typed_fixture_model",
        lambda _: analysis_of(f, has_float16=True),
    )
    with pytest.raises(ConfigError, match="float16"):
        build_fixed_fixture(config_for(tmp_path, f), f, method=METHOD, runtime=runtime(tmp_path))
    monkeypatch.setattr(
        "helia_profiler._fixture_build.analyze_typed_fixture_model",
        lambda _: analysis_of(typed(tmp_path / "other", outputs=(LABEL,))),
    )
    with pytest.raises(ConfigError, match="declarations differ"):
        build_fixed_fixture(config_for(tmp_path, f), f, method=METHOD, runtime=runtime(tmp_path))


# --- render -----------------------------------------------------------------


def render(tmp_path, f, engine, *, observe=False, regions=()):
    c = config_for(tmp_path, f, engine)
    ctx = PipelineContext(config=c, work_dir=tmp_path)
    _BindFixtureStage(FixtureRenderSpec(f, METHOD, analysis_of(f), observe)).run(ctx)
    return _jinja_env.get_template("fixed_fixture.cc.j2").render(
        **fixture_template_vars(ctx, list(regions)),
        aot_prefix="m",
        allocate_arenas=True,
        arena_regions=[],
    )


def test_tflm_render_checks_each_tensor_type_and_only_per_tensor_quantization(tmp_path):
    per_axis = FixtureTensor("pa", 5, "int8", (1, 2), PerAxisQuantization(1, (0.5, 0.25), (0, 1)))
    f = typed(tmp_path, outputs=(LABEL, LEVEL, per_axis))
    source = render(tmp_path, f, EngineType.TFLM)
    assert "graph->inputs()->size() != 2 || graph->outputs()->size() != 3" in source
    assert "input->type != kTfLiteInt16 || input->bytes != 32" in source
    assert "input->params.scale != 0x1.0000000000000p-10f" in source
    assert (
        "input_1->type != kTfLiteFloat32 || input_1->bytes != 4 ||\n        input_1->dims->size != 2) return"
        in source
    )
    assert "graph->outputs()->Get(2) != 5" in source
    assert "output_2->params" not in source
    assert "auto *input_data = input->data.i16;" in source
    assert "reinterpret_cast<const uint8_t *>(output_1->data.f)" in source
    assert "deployment_arena_scan" not in source


@pytest.mark.parametrize("engine", [EngineType.TFLM, EngineType.HELIA_AOT])
def test_render_restores_every_input_and_reads_every_output(tmp_path, engine):
    source = render(tmp_path, typed(tmp_path), engine)
    restores = [
        "std::memcpy(input_data, fixed_input, sizeof(fixed_input));",
        "std::memcpy(input_data_1, fixed_input_1, sizeof(fixed_input_1));",
    ]
    warmup, measured = source.split("int32_t invocation_status = 0;")
    for restore in restores:
        assert warmup.count(restore) == 1 and measured.count(restore) == 1
    assert "volatile uint8_t deployment_output[4];" in source
    assert "volatile uint8_t deployment_output_1[8];" in source
    assert "deployment_output_1[i] = output_data_1[i];" in source
    checksum = source.split("uint32_t checksum = 0;")[1].split("deployment_checksum =")[0]
    assert checksum.index("deployment_output[i]") < checksum.index("deployment_output_1[i]")
    if engine is EngineType.HELIA_AOT:
        assert "m_num_inputs != 2 || m_num_outputs != 2" in source
        assert "model_ctx.inputs[1].size != 4" in source
        assert "model_ctx.outputs[1].size != 8" in source


def test_aot_scan_paints_scratch_after_init_and_scans_after_timing(tmp_path):
    source = render(tmp_path, typed(tmp_path), EngineType.HELIA_AOT, observe=True, regions=REGIONS)
    assert "volatile uint32_t deployment_arena_scan[2];" in source
    paint = "std::memset(const_cast<void *>(m_arena_buffers[0]), 0xA5, 4096);"
    assert source.count("std::memset(") == 1 and paint in source
    assert "m_arena_buffers[1]" not in source and "m_arena_buffers[2]" not in source
    assert source.index("m_model_init(") < source.index(paint) < source.index("hpx_stimer_init()")
    scan = source.index("static_cast<const uint8_t *>(m_arena_buffers[0])")
    assert source.index("return -9;") < scan < source.index("deployment_timing[0] = ticks;")
    assert "for (uint32_t i = 0; i < 4096U; ++i)" in source
    assert "deployment_arena_scan[0] = touched;" in source
    assert "deployment_arena_scan[1] = high_water;" in source


def test_aot_scan_is_absent_unless_requested(tmp_path):
    source = render(tmp_path, typed(tmp_path), EngineType.HELIA_AOT, regions=REGIONS)
    assert "arena_scan" not in source and "0xA5" not in source


def test_heliaaot_region_outside_sram_or_mram_is_refused_at_render(tmp_path):
    region = ArenaRegion(0, "scratch", 4096, 16, ArenaRole.SCRATCH, "tcm", Placement.TCM)
    with pytest.raises(ConfigError, match="placement differs from fixture request"):
        render(tmp_path, typed(tmp_path), EngineType.HELIA_AOT, regions=(region,))


@pytest.mark.parametrize(("board", "clock"), [("apollo510_evb", "hp"), ("apollo4p_evb", "lp")])
def test_fixture_refuses_any_board_or_clock_but_the_fixture_target(
    tmp_path, monkeypatch, board, clock
):
    f = typed(tmp_path)
    monkeypatch.setattr(
        "helia_profiler._fixture_build.PipelineRunner", lambda *_: pytest.fail("pipeline reached")
    )
    c = config_for(tmp_path, f)
    target = replace(c.target, board=board, clock=ClockSelection(cpu=clock))
    with pytest.raises(ConfigError, match="supports apollo510_evb at the lp clock only"):
        build_fixed_fixture(replace(c, target=target), f, method=METHOD, runtime=runtime(tmp_path))


TOKENS = FixtureTensor("tokens", 0, "int32", (1, 256), None)
SCORES = FixtureTensor(
    "scores", 1, "int8", (1, 256, 256), PerTensorQuantization(0.05708871781826019, -12)
)


@pytest.mark.parametrize("engine", list(FIXTURE_CAPABILITIES))
def test_int32_input_is_supported_without_device_qualification(tmp_path, engine):
    f = typed(tmp_path, inputs=(TOKENS,), outputs=(SCORES,))
    f.verify()
    assert _check_typed_fixture(f, analysis_of(f), engine) == (
        ("int32", FixtureCapability.SUPPORTED),
        ("int8", FixtureCapability.SUPPORTED),
    )
    assert f.inputs[0].tensor.size_bytes == 1024
    assert f.outputs[0].tensor.size_bytes == 65536 < FIXTURE_READBACK_BUDGET


def test_int32_render_preserves_signed_payload_and_complete_output(tmp_path):
    import struct

    values = (-2147483648, -1, 0, 2147483647) * 64
    raw = struct.pack("<256i", *values)
    f = typed(tmp_path, inputs=(TOKENS,), outputs=(SCORES,))
    f = replace(f, inputs=(replace(f.inputs[0], data=pin(tmp_path, "signed.bin", raw)),))
    f.verify()
    ctx = PipelineContext(config=config_for(tmp_path, f), work_dir=tmp_path)
    ctx.fixture = FixtureRenderSpec(f, METHOD, analysis_of(f))
    variables = fixture_template_vars(ctx, [])
    inputs, outputs = variables["fixture_inputs"], variables["fixture_outputs"]
    assert isinstance(inputs, list) and isinstance(outputs, list)
    entry = inputs[0]
    assert bytes(map(int, entry["initializer"].split(","))) == raw
    assert struct.unpack("<256i", raw) == values
    assert (entry["tflite_type"], entry["member"], entry["size"]) == ("kTfLiteInt32", "i32", 1024)
    assert outputs[0]["size"] == 65536


@pytest.mark.parametrize("fault", ["dtype", "input_extent", "output_extent", "int32_output"])
def test_int32_contract_rejects_wrong_type_and_incomplete_tensors(tmp_path, fault):
    f = typed(tmp_path, inputs=(TOKENS,), outputs=(SCORES,))
    model = analysis_of(f)
    if fault == "dtype":
        f = replace(f, inputs=(replace(f.inputs[0], tensor=replace(TOKENS, dtype="float32")),))
        with pytest.raises(ConfigError, match="declarations differ"):
            _check_typed_fixture(f, model, EngineType.TFLM)
    elif fault == "int32_output":
        f = typed(tmp_path, inputs=(TOKENS,), outputs=(TOKENS,))
        with pytest.raises(ConfigError, match="input-only"):
            _check_typed_fixture(f, analysis_of(f), EngineType.TFLM)
    else:
        direction = "inputs" if fault == "input_extent" else "outputs"
        io = getattr(f, direction)[0]
        f = replace(
            f,
            **{
                direction: (
                    replace(io, data=pin(tmp_path, "short.bin", bytes(io.tensor.size_bytes - 1))),
                )
            },
        )
        with pytest.raises(ValueError, match="byte extent"):
            f.verify()


def test_int32_emitted_control_checks_bytes_status_and_full_output(tmp_path):
    """Execute the emitted orchestration with a byte-checking host backend stub."""
    import shutil
    import struct
    import subprocess
    from pathlib import Path

    compiler = shutil.which("g++")
    if compiler is None:
        pytest.skip("host C++ compiler required")
    values = (-2147483648, -1, 0, 2147483647) * 64
    raw = struct.pack("<256i", *values)
    f = typed(tmp_path, inputs=(TOKENS,), outputs=(SCORES,))
    f = replace(f, inputs=(replace(f.inputs[0], data=pin(tmp_path, "signed.bin", raw)),))
    config = config_for(tmp_path, f)
    config = replace(config, profiling=ProfilingConfig(iterations=2, warmup=0))
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ctx.fixture = FixtureRenderSpec(f, METHOD, analysis_of(f))
    rendered = _jinja_env.get_template("fixed_fixture.cc.j2").render(
        **fixture_template_vars(ctx, [])
    )
    # The actual inference function is tested; target boot and idle stay outside this host control.
    body = rendered[
        rendered.index("static const uint8_t fixed_input") : rendered.index("\nint main()")
    ]
    prelude = r"""
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include "hpx_stub_tflm_common.h"
#define NSX_MEM_SRAM_BSS
#define TFLITE_SCHEMA_VERSION 3
static int fault, calls;
static int32_t input_data_stub[256];
static int8_t output_data_stub[65536];
static const int32_t expected[] = { EXPECTED_VALUES };
static TfLiteIntArray input_dims = {2, {1,256}};
static TfLiteIntArray output_dims = {3, {1,256,256}};
static TfLiteTensor input_stub = {kTfLiteInt32, &input_dims, {0,0}, 1024, {.i32=input_data_stub}};
static TfLiteTensor output_stub = {kTfLiteInt8, &output_dims, {0.05708871781826019f,-12}, 65536, {.int8=output_data_stub}};
static const uint8_t model_data[] = {0};
namespace tflite {
struct List { int index; int size() const {return 1;} int Get(int) const {return index;} };
struct Graph { List in{0},out{1}; const List* inputs() const {return &in;} const List* outputs() const {return &out;} };
struct Graphs { Graph graph; int size() const {return 1;} const Graph* Get(int) const {return &graph;} };
struct Model { Graphs graphs; int version() const {return 3;} const Graphs* subgraphs() const {return &graphs;} };
static Model model;
const Model* GetModel(const uint8_t*) {return &model;}
template<int N> struct MicroMutableOpResolver { int AddFullyConnected() {return kTfLiteOk;} };
struct MicroInterpreter {
 template<class R> MicroInterpreter(const Model*, R&, uint8_t*, size_t) {}
 int AllocateTensors() {return kTfLiteOk;}
 TfLiteTensor* input(int) {if(fault==1) input_stub.type=kTfLiteFloat32; if(fault==2) --input_stub.bytes; return &input_stub;}
 TfLiteTensor* output(int) {if(fault==3) --output_stub.bytes; return &output_stub;}
 size_t arena_used_bytes() {return 64;}
 int Invoke() {
  ++calls;
  if(std::memcmp(input_data_stub,expected,sizeof(expected))) return kTfLiteError;
  std::memset(output_data_stub,7,sizeof(output_data_stub));
  std::memset(input_data_stub,0,sizeof(input_data_stub));
  return fault==4 ? kTfLiteError : kTfLiteOk;
 }
};
}
constexpr unsigned HPX_STIMER_HZ=1000000;
static uint32_t SystemCoreClock=96000000,g_hpx_stimer_last_ticks;
bool hpx_stimer_init() {return true;}
uint32_t hpx_stimer_ticks() {return ++g_hpx_stimer_last_ticks;}
void hpx_fixture_memory_init(size_t) {}
void hpx_fixture_memory_snapshot(unsigned,size_t) {}
void hpx_fixture_memory_finish() {}
""".replace("EXPECTED_VALUES", ",".join(map(str, values)))
    trailer = r"""
int main(int argc,char**argv) {
 fault=argc>1 ? std::atoi(argv[1]) : 0;
 int result=infer_fixture();
 int wanted=fault==4 ? -7 : fault ? -6 : 0;
 if(result!=wanted) return 10;
 if(fault==0) {
  if(calls!=2 || sizeof(deployment_output)!=65536) return 11;
  for(auto byte:deployment_output) if(byte!=7) return 12;
 } else if(fault<4 && calls) return 13;
 return 0;
}
"""
    stub_dir = Path(__file__).parents[1] / "fixtures" / "compile_stubs"
    variants = {
        "original": body,
        "missing_restore": body.replace(
            "std::memcpy(input_data, fixed_input, sizeof(fixed_input));", ""
        ),
        "discarded_status": body.replace("if (invocation_status != 0) return -7;", ""),
    }
    for name, source in variants.items():
        assert name == "original" or source != body
        path = tmp_path / (name + ".cc")
        executable = tmp_path / name
        path.write_text(prelude + source + trailer)
        compiled = subprocess.run(
            [compiler, "-std=gnu++17", "-I", str(stub_dir), str(path), "-o", str(executable)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert compiled.returncode == 0, compiled.stderr
        faults = range(5) if name == "original" else [0 if name == "missing_restore" else 4]
        for fault in faults:
            result = subprocess.run([str(executable), str(fault)], timeout=5)
            assert (result.returncode == 0) == (name == "original")
