"""Pure PSRAM placement contracts; no compiler or target dependencies."""

import hashlib
import struct
from dataclasses import replace

import pytest

from helia_profiler.errors import ConfigError
from helia_profiler.fixture import (
    EngineType,
    FixtureBuildRequest,
    FixtureFile,
    FixtureIO,
    FixtureMethod,
    FixturePlacement,
    FixtureTensor,
    FixtureTimingScope,
    PerTensorQuantization,
    Placement,
    TypedFixture,
)
from helia_profiler.fixture_image import ContractError
from helia_profiler.fixture_psram import (
    FixturePsramPayload,
    FixturePsramWeights,
    decode_psram_weights,
    payload_checksum,
)
from helia_profiler.fixture_target import fixture_app_region
from helia_profiler.platform.memory_map import MemoryRegion
from tests.unit import test_fixture_capture as capture_helpers
from tests.unit.test_fixture_capture import _SINKS, image_files
from tests.unit.test_fixture_capture import rig as rig


def fixture(tmp_path):
    def pin(name, data):
        p = tmp_path / name
        p.write_bytes(data)
        return FixtureFile(p, hashlib.sha256(data).hexdigest())

    t = FixtureTensor("signal", 0, "int16", (1, 2), PerTensorQuantization(0.5, 0))
    return TypedFixture(
        pin("model", b"model"),
        (FixtureIO(t, pin("in", bytes(4))),),
        (FixtureIO(t, pin("out", bytes(4))),),
    )


def test_psram_aot_public_constructor(tmp_path):
    request = FixtureBuildRequest(
        fixture(tmp_path),
        FixtureMethod(FixtureTimingScope.INVOKE_ONLY),
        tmp_path / "work",
        EngineType.HELIA_AOT,
        32768,
        100,
        5,
        placement=FixturePlacement(Placement.SRAM, Placement.PSRAM),
    )
    assert request.to_config().model.weights_location is Placement.PSRAM
    assert request.intent_identity != replace(request, placement=FixturePlacement()).intent_identity


def terminal(expected, *, base=None, addresses=None):
    base = fixture_app_region(MemoryRegion.PSRAM)[0] if base is None else base
    addresses = addresses or [base + 64 * i for i in range(len(expected.payloads))]
    words = [0x50535231, 1, 0, base, 64 * 1024 * 1024, 96000000, 1, len(expected.payloads)]
    for payload, address in zip(expected.payloads, addresses):
        words.extend([payload.region_id, address, payload.size, payload.checksum])
    return struct.pack(f"<{len(words)}I", *words)


def test_psram_terminal_accepts_actual_active_pointer():
    expected = FixturePsramWeights(
        (FixturePsramPayload(0, 4, payload_checksum(b"abcd")),), "1" * 64
    )
    observed = decode_psram_weights(terminal(expected), expected)
    assert observed.active_addresses == (fixture_app_region(MemoryRegion.PSRAM)[0],)


@pytest.mark.parametrize(
    "word,value",
    [
        (0, 0),
        (1, 2),
        (2, 1),
        (3, 0x410000),
        (4, 0xFFFFFFFF),
        (5, 0),
        (6, 0),
        (7, 2),
        (8, 1),
        (9, 0x410000),
        (10, 5),
        (11, 0),
    ],
)
def test_psram_terminal_refuses_invalid_observation(word, value):
    expected = FixturePsramWeights(
        (FixturePsramPayload(0, 4, payload_checksum(b"abcd")),), "1" * 64
    )
    raw = bytearray(terminal(expected))
    struct.pack_into("<I", raw, 4 * word, value)
    with pytest.raises(ContractError):
        decode_psram_weights(bytes(raw), expected)


def test_psram_terminal_refuses_overlapping_payloads():
    expected = FixturePsramWeights(
        (FixturePsramPayload(0, 100, 0), FixturePsramPayload(1, 100, 0)), "1" * 64
    )
    with pytest.raises(ContractError, match="Overlapping"):
        decode_psram_weights(terminal(expected), expected)


def render_context(tmp_path, engine):
    from helia_profiler._fixture_build import FixtureRenderSpec
    from helia_profiler.config import load_config
    from helia_profiler.engines.base import ArenaRegion, HeliaAotArtifacts, TflmArtifacts
    from helia_profiler.firmware.op_resolver import ResolverPlan
    from helia_profiler.fixture_analysis import TypedFixtureModelAnalysis
    from helia_profiler.modelcost import ModelAnalysis
    from helia_profiler.pipeline import PipelineContext
    from helia_profiler.placement import ArenaRole
    from helia_profiler.results.models import NsxModuleRef

    f = fixture(tmp_path)
    config = load_config(
        None,
        {
            "model": {
                "path": str(f.model.path),
                "arena_size": 32768,
                "arena_location": "sram",
                "weights_location": "psram",
            },
            "engine": {"type": engine.value},
            "profiling": {"iterations": 100, "warmup": 5},
            "target": {"board": "apollo510_evb", "toolchain": "atfe", "clock": {"cpu": "lp"}},
            "work_dir": str(tmp_path / "work"),
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    analysis = TypedFixtureModelAnalysis(
        tuple(i.tensor for i in f.inputs),
        tuple(i.tensor for i in f.outputs),
        False,
        ModelAnalysis([], 0, 0, 0),
        ResolverPlan("auto", ("r.AddFullyConnected();",)),
    )
    ctx.fixture = FixtureRenderSpec(f, FixtureMethod(FixtureTimingScope.INVOKE_ONLY), analysis)
    regions = []
    if engine is EngineType.HELIA_AOT:
        (tmp_path / "weights.bin").write_bytes(b"abcd")
        regions = [
            ArenaRegion(0, "scratch", 32, 16, ArenaRole.SCRATCH, "sram", Placement.SRAM),
            ArenaRegion(
                1, "constant", 4, 16, ArenaRole.CONSTANT, "psram", Placement.PSRAM, "weights.bin"
            ),
        ]
        ctx.engine_artifacts = HeliaAotArtifacts(
            engine_header="fake_model.h",
            aot_prefix="fake",
            aot_module_name="fake",
            aot_cmake_target="nsx::fake",
            helia_aot_version="test",
            aot_allocate_arenas=False,
            aot_arena_regions=regions,
            extra_modules=[NsxModuleRef("fake", tmp_path)],
        )
    else:
        ctx.engine_artifacts = TflmArtifacts(engine_header="model_data.h")
    return ctx, regions


@pytest.mark.parametrize("engine", [EngineType.HELIA_AOT, EngineType.TFLM])
def test_psram_production_render_uses_active_consumer(tmp_path, engine):
    from helia_profiler.firmware.fixture import fixture_template_vars
    from helia_profiler.firmware.render import _jinja_env

    ctx, regions = render_context(tmp_path, engine)
    values = fixture_template_vars(ctx, regions)
    source = _jinja_env.get_template("fixed_fixture.cc.j2").render(
        **values, psram_clock_hz=96000000, aot_prefix="fake", arena_regions=regions
    )
    assert source.index("if (!hpx_psram_start())") < source.index("if (!hpx_stimer_init())")
    assert "nsx_psram_write(offset, source, size)" in source
    assert "size > hpx_psram_info.size_bytes - offset" in source
    if engine is EngineType.HELIA_AOT:
        assert source.index("fake_bind_arena") < source.index("fake_model_init")
        assert "model_ctx.arena_buffers[1] != hpx_arena_buf_1" in source
        assert "model_ctx.tensor_ptrs[i] != reinterpret_cast<const int8_t *>" in source
        assert "NSX_MEM_SRAM_BSS alignas(16) static uint8_t hpx_arena_buf_storage_0[32]" in source
    else:
        assert source.index("hpx_psram_load(0") < source.index(
            "tflite::GetModel(active_model_data)"
        )
        assert "MicroInterpreter interpreter(model, resolver, arena, sizeof(arena))" in source
        assert "tflite::GetModel(model_data)" not in source


@pytest.mark.parametrize("fault", ["internal", "missing", "size", "placement", "memory"])
def test_psram_aot_render_refuses_inconsistent_artifacts(tmp_path, fault):
    from helia_profiler.firmware.fixture import fixture_template_vars

    ctx, regions = render_context(tmp_path, EngineType.HELIA_AOT)
    if fault == "internal":
        ctx.engine_artifacts = replace(ctx.engine_artifacts, aot_allocate_arenas=True)
    if fault == "missing":
        (tmp_path / "weights.bin").unlink()
    if fault == "size":
        (tmp_path / "weights.bin").write_bytes(b"abc")
    if fault == "placement":
        regions[1] = replace(regions[1], placement=Placement.MRAM)
    if fault == "memory":
        regions[1] = replace(regions[1], memory="mram")
    with pytest.raises((ConfigError, OSError)):
        fixture_template_vars(ctx, regions)


def test_psram_aot_fake_pipeline_derives_setting_before_prepare(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from helia_profiler import _fixture_build as builder
    from helia_profiler.firmware.fixture import fixture_template_vars

    ctx, regions = render_context(tmp_path, EngineType.HELIA_AOT)
    monkeypatch.setattr(
        builder, "analyze_typed_fixture_model_for_engine", lambda *a, **kw: ctx.fixture.model
    )
    observed = []

    class Runner:
        def __init__(self, stages):
            assert all(stage.name != "build_firmware" for stage in stages)

        def run(self, config):
            observed.append(config)
            assert config.engine.config["aot_args"]["memory"]["allocate_arenas"] is False
            # Fake artifacts only: no stage, converter, compiler or driver runs.
            ctx.config = config
            fixture_template_vars(ctx, regions)
            app = tmp_path / "app"
            (app / "src").mkdir(parents=True)
            (app / "src/main.cc").write_text("fake")
            return SimpleNamespace(
                resolved_firmware_dir=app,
                engine_artifacts=ctx.engine_artifacts,
                memory_plan=None,
                profile_run=None,
                config=config,
                fixture=ctx.fixture,
            )

    monkeypatch.setattr(builder, "PipelineRunner", Runner)
    result = builder.build_fixed_fixture(
        ctx.config, ctx.fixture.fixture, method=ctx.fixture.method, compile=False
    )
    assert result.psram_weights is not None
    assert result.psram_weights.payloads == (FixturePsramPayload(1, 4, payload_checksum(b"abcd")),)
    assert result.psram_weights.image_sha256 is None
    assert result.binary is None and not result.built
    assert observed[0].model.weights_location is Placement.PSRAM


def test_psram_capture_rejects_unbound_descriptor_before_any_session(tmp_path, monkeypatch):
    from helia_profiler import fixture_capture as capture
    from helia_profiler.fixture_target import supported_fixture_target

    p = tmp_path / "stub"
    p.write_bytes(b"x")
    file = FixtureFile(p, hashlib.sha256(b"x").hexdigest())
    request = capture.FixtureCaptureRequest(
        file,
        file,
        0x410000,
        4,
        "AP510NFA-CBR",
        "123",
        tmp_path / "out",
        1,
        FixtureTimingScope.INVOKE_ONLY,
        supported_fixture_target(),
        psram_weights=FixturePsramWeights((FixturePsramPayload(0, 4, 0),)),
    )
    monkeypatch.setattr(capture, "attached_session", lambda **kw: pytest.fail("real session"))
    with pytest.raises(ContractError, match="bound to the capture image"):
        capture.capture_fixture(request, guard=capture_helpers.Guard())


def test_psram_fake_session_captures_bound_terminal(capture_rig, tmp_path):
    from helia_profiler import fixture_capture as capture

    request, memory, saved_terminal, writes, flashes, guard = capture_rig
    expected = FixturePsramWeights((FixturePsramPayload(0, 4, 2987074),), request.image.sha256)
    elf, image, _ = image_files(tmp_path, dict(_SINKS, deployment_psram_weights=expected.sink_size))
    saved_terminal[0x20000028] = terminal(expected)
    request = replace(request, elf=elf, image=image, psram_weights=expected)
    result = capture.capture_fixture(request, guard=guard)
    assert result.state == "success", result.error
    assert result.psram_weights is not None
    assert result.psram_weights.active_addresses == (fixture_app_region(MemoryRegion.PSRAM)[0],)
    assert len(flashes) == 1
    assert any(f.path.name == "deployment_psram_weights.bin" for f in result.artifacts)


def test_psram_fake_session_refuses_undeclared_sink(capture_rig, tmp_path):
    from helia_profiler import fixture_capture as capture

    request, memory, saved_terminal, writes, flashes, guard = capture_rig
    elf, image, _ = image_files(tmp_path, dict(_SINKS, deployment_psram_weights=48))
    result = capture.capture_fixture(replace(request, elf=elf, image=image), guard=guard)
    assert result.state == "failure" and result.error is not None
    assert "undeclared fixture sink" in result.error
    assert flashes == []


def test_psram_checksum_matches_independent_literal():
    assert payload_checksum(b"abcd") == 2987074


@pytest.mark.parametrize("engine", [EngineType.HELIA_AOT, EngineType.TFLM])
def test_psram_other_placements_still_refuse(tmp_path, engine):
    from helia_profiler._fixture_build import _validate_fixture_placement

    for arena, weights in [
        (Placement.PSRAM, Placement.PSRAM),
        (Placement.TCM, Placement.PSRAM),
        (Placement.SRAM, Placement.TCM),
    ]:
        with pytest.raises(ConfigError):
            _validate_fixture_placement(arena, weights, engine)
    with pytest.raises(ConfigError):
        _validate_fixture_placement(Placement.SRAM, Placement.PSRAM, EngineType.HELIA_RT)


@pytest.fixture
def capture_rig(tmp_path, monkeypatch):
    return getattr(capture_helpers.rig, "__wrapped__")(tmp_path, monkeypatch)


@pytest.mark.parametrize("engine", ["helia-aot", "tflm"])
@pytest.mark.parametrize(
    "placement", [FixturePlacement(), FixturePlacement(Placement.TCM, Placement.TCM)]
)
def test_existing_fixture_placements_keep_original_render(engine, placement):
    from tests.contracts.fixture_compile_cases import render_fixture

    source, headers = render_fixture("kws", engine, "invoke_only", placement=placement)
    assert "deployment_psram_weights" not in source
    assert "nsx_psram_init" not in source
    assert "fixed_fixture_clock.h" in headers
    if engine == "tflm":
        assert "tflite::GetModel(model_data)" in source
    else:
        assert "fake_model_init(&model_ctx)" in source
        assert "fake_bind_arena" not in source
