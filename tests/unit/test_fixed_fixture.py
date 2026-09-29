from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import pytest
import json

from helia_profiler.config import (
    ProfileConfig,
    ModelConfig,
    EngineConfig,
    TargetConfig,
    ClockSelection,
    ProfilingConfig,
)
from helia_profiler.engines import EngineType
from helia_profiler.pipeline import PipelineContext
from helia_profiler.results.models import ToolchainInfo, RunMetadata, MemoryPlan
from helia_profiler.fixture_analysis import FixtureModelAnalysis
from helia_profiler.modelcost.model_analysis import ModelAnalysis, LayerOps
from helia_profiler.firmware.op_resolver import build_fixture_resolver_plan
from helia_profiler.vocab import Toolchain
from helia_profiler.fixture import (
    FixtureFile,
    Int8Tensor,
    FixedFixture,
    PreparedUpstreamRuntime,
    build_fixed_fixture,
    FixtureMethod,
    FixtureTimingScope,
    FixtureRenderSpec,
    _BindFixtureStage,
)


def fixture(tmp_path):
    def pin(name, data):
        p = tmp_path / name
        p.write_bytes(data)
        return FixtureFile(p, sha256(data).hexdigest())

    f = FixedFixture(
        pin("model.tflite", b"fixture-model"),
        pin("input.bin", bytes(3360)),
        pin("output.bin", bytes(480)),
        Int8Tensor((1, 240, 14), 0.007843011990189552, -1, 0),
        Int8Tensor((1, 240, 2), 0.003640471724793315, -31, 72),
    )
    c = ProfileConfig(
        model=ModelConfig(
            path=f.model.path, arena_size=262144, arena_location="sram", weights_location="mram"
        ),
        engine=EngineConfig(type=EngineType.TFLM, backend="cmsis_nn"),
        target=TargetConfig(
            board="apollo510_evb", toolchain=Toolchain.ATFE, clock=ClockSelection(cpu="lp")
        ),
        profiling=ProfilingConfig(iterations=100, warmup=5),
        work_dir=tmp_path / "build",
    )
    return c, f


METHOD = FixtureMethod(FixtureTimingScope.RESTORE_AND_INVOKE)


def analyzed(f, ops=("CONV_2D", "RESHAPE")):
    analysis = ModelAnalysis([LayerOps(i, op) for i, op in enumerate(ops)], 0, 0, 0)
    return FixtureModelAnalysis(
        f.input_tensor, f.output_tensor, analysis, build_fixture_resolver_plan(analysis)
    )


def mock_analysis(monkeypatch, f):
    monkeypatch.setattr("helia_profiler.fixture.analyze_fixture_model", lambda _: analyzed(f))


def runtime(tmp_path):
    def pin(name, data):
        p = tmp_path / name
        p.write_bytes(data)
        return FixtureFile(p, sha256(data).hexdigest())

    a = pin("runtime.a", b"!<arch>\nfixture-member")
    h = pin("header.h", b"header")
    data = {
        "schema_version": 1,
        "archive_sha256": a.sha256,
        "providers": {
            "tflite-micro": {
                "url": "https://github.com/tensorflow/tflite-micro",
                "revision": "a" * 40,
            },
            "cmsis-nn": {"url": "https://github.com/ARM-software/CMSIS-NN", "revision": "b" * 40},
        },
        "abi": {"toolchain": "atfe", "cpu": "cortex-m55", "float_abi": "hard", "short_enums": True},
        "headers": {"header.h": h.sha256},
        "include_dirs": ["."],
    }
    return PreparedUpstreamRuntime(a, tmp_path, pin("runtime.json", json.dumps(data).encode()))


def test_hash_or_unsupported_config_stops_before_pipeline(tmp_path, monkeypatch):
    c, f = fixture(tmp_path)
    rt = runtime(tmp_path)

    def forbidden(*a, **kw):
        pytest.fail("pipeline reached")

    monkeypatch.setattr("helia_profiler.fixture.PipelineRunner", forbidden)
    with pytest.raises(Exception, match="upstream"):
        build_fixed_fixture(
            replace(c, engine=EngineConfig(type=EngineType.EXECUTORCH)),
            f,
            method=METHOD,
            runtime=rt,
        )
    f.input.path.write_bytes(bytes(3359))
    with pytest.raises(ValueError, match="hash mismatch"):
        build_fixed_fixture(c, f, method=METHOD, runtime=rt)


@pytest.mark.parametrize("engine", [EngineType.TFLM, EngineType.HELIA_AOT])
@pytest.mark.parametrize("aot_plan_available", [False, True])
def test_host_only_stage_selection_and_source_receipt(
    tmp_path, monkeypatch, engine, aot_plan_available
):
    c, f = fixture(tmp_path)
    rt = runtime(tmp_path)
    mock_analysis(monkeypatch, f)
    c = replace(c, engine=EngineConfig(type=engine, backend="cmsis_nn"))
    selected_runtime = rt if engine is EngineType.TFLM else None
    calls = []
    elf_bytes = b"elf"
    compiler_version = "qualified-compiler"
    lock_suffix = ""

    class Runner:
        def __init__(self, stages):
            self.stages = stages
            calls.append([s.name for s in stages])

        def run(self, config):
            self.stages[0].run(None)
            app = tmp_path / "app"
            (app / "src").mkdir(parents=True, exist_ok=True)
            for name in (
                "main.cc",
                "model_data.h",
                "fixed_fixture_memory.h",
                "fixed_fixture_clock.h",
            ):
                (app / "src" / name).write_text(name)
            binary = app / "hpx_profiler"
            binary.write_bytes(elf_bytes)
            binary.with_suffix(".bin").write_bytes(b"bin")
            binary.with_suffix(".map").write_text("hpx-upstream-runtime/runtime.a(member.o)")
            module = app / "modules/hpx-upstream-runtime"
            module.mkdir(parents=True, exist_ok=True)
            (module / "runtime.a").write_bytes(rt.archive.read())
            (module / "provider-manifest.json").write_bytes(rt.manifest.read())
            (app / "nsx.lock").write_text(
                "targets:\n  apollo510_evb:\n    modules:\n      hpx-upstream-runtime: {}\n"
                + lock_suffix
            )
            return SimpleNamespace(
                memory_plan=MemoryPlan(engine=engine),
                engine_artifacts=(
                    SimpleNamespace(memory_plan=MemoryPlan(engine=engine))
                    if aot_plan_available
                    else None
                ),
                run_metadata=RunMetadata(
                    toolchain=ToolchainInfo(compiler="atfe", compiler_version=compiler_version)
                ),
                resolved_firmware_dir=app,
                profile_run=SimpleNamespace(firmware=SimpleNamespace(binary_path=binary)),
            )

    monkeypatch.setattr("helia_profiler.fixture.PipelineRunner", Runner)
    r = build_fixed_fixture(c, f, method=METHOD, runtime=selected_runtime, compile=False)
    assert not r.built and r.binary is None and len(r.generated_sources) == 4
    assert r.build_identity is None
    assert (r.planned_memory is None) == (engine is EngineType.HELIA_AOT and not aot_plan_available)
    assert (r.planned_memory_reason is not None) == (
        engine is EngineType.HELIA_AOT and not aot_plan_available
    )
    assert calls[-1] == [
        "bind_fixed_fixture",
        "resolve_platform",
        "prepare_engine",
        *(["prepare_upstream_runtime"] if engine is EngineType.TFLM else []),
        "bind_fixture_render",
        "plan_memory",
        "generate_firmware",
    ]
    r = build_fixed_fixture(c, f, method=METHOD, runtime=selected_runtime)
    assert r.built
    assert r.binary is not None
    assert r.binary.read() == b"elf"
    assert calls[-1][-1] == "build_firmware"
    assert r.build_identity is not None and r.build_identity != r.intent_identity
    original_identity = r.build_identity
    elf_bytes = b"different-elf"
    changed = build_fixed_fixture(c, f, method=METHOD, runtime=selected_runtime)
    assert changed.intent_identity == r.intent_identity
    assert changed.build_identity != original_identity
    elf_bytes = b"elf"
    compiler_version = "different-compiler"
    changed = build_fixed_fixture(c, f, method=METHOD, runtime=selected_runtime)
    assert changed.intent_identity == r.intent_identity
    assert changed.build_identity != original_identity
    compiler_version = "qualified-compiler"
    lock_suffix = "# different resolved dependency graph\n"
    changed = build_fixed_fixture(c, f, method=METHOD, runtime=selected_runtime)
    assert changed.intent_identity == r.intent_identity
    assert changed.build_identity != original_identity
    assert r.target.board == "apollo510_evb"

    with pytest.raises(Exception, match="different fixture"):
        build_fixed_fixture(
            replace(c, model=replace(c.model, arena_size=131072)),
            f,
            method=METHOD,
            runtime=selected_runtime,
        )

    assert r.engine is engine
    assert (r.runtime_manifest is not None) == (engine is EngineType.TFLM)
    for altered_config, altered_method in (
        (c, FixtureMethod(FixtureTimingScope.INVOKE_ONLY)),
        (replace(c, profiling=ProfilingConfig(iterations=7, warmup=2)), METHOD),
        (replace(c, model=replace(c.model, weights_location="sram")), METHOD),
    ):
        with pytest.raises(Exception, match="different fixture|MRAM"):
            build_fixed_fixture(altered_config, f, method=altered_method, runtime=selected_runtime)
    if engine is EngineType.HELIA_AOT:
        with pytest.raises(Exception, match="cannot consume"):
            build_fixed_fixture(c, f, method=METHOD, runtime=rt)


@pytest.mark.parametrize("scope", list(FixtureTimingScope))
@pytest.mark.parametrize("kind", ["tcn", "kws"])
def test_render_derives_extents_ops_counts_and_scope(tmp_path, kind, scope):
    from helia_profiler.firmware.fixture import fixture_template_vars
    from helia_profiler.firmware.render import _jinja_env

    c, f = fixture(tmp_path)
    if kind == "kws":
        inp = bytes(490)
        out = bytes(12)
        f.input.path.write_bytes(inp)
        f.expected.path.write_bytes(out)
        f = replace(
            f,
            input=FixtureFile(f.input.path, sha256(inp).hexdigest()),
            expected=FixtureFile(f.expected.path, sha256(out).hexdigest()),
            input_tensor=Int8Tensor((1, 49, 10, 1), 0.125, -4, 0),
            output_tensor=Int8Tensor((1, 12), 0.0625, -128, 4),
        )
    c = replace(c, profiling=ProfilingConfig(iterations=17, warmup=3))
    model = analyzed(f, ("FULLY_CONNECTED", "SOFTMAX") if kind == "kws" else ("CONV_2D", "RESHAPE"))
    ctx = PipelineContext(config=c, work_dir=tmp_path)
    _BindFixtureStage(FixtureRenderSpec(f, FixtureMethod(scope), model)).run(ctx)
    values = fixture_template_vars(ctx, [])
    inputs, outputs = values["fixture_inputs"], values["fixture_outputs"]
    assert isinstance(inputs, list) and isinstance(outputs, list)
    (fixture_input,) = inputs
    (fixture_output,) = outputs
    assert (fixture_input["tensor_index"], fixture_input["size"], fixture_input["shape"]) == (
        f.input_tensor.tensor_index,
        f.input_tensor.size,
        f.input_tensor.shape,
    )
    assert (fixture_output["tensor_index"], fixture_output["size"]) == (
        f.output_tensor.tensor_index,
        f.output_tensor.size,
    )
    assert values["fixture_iterations"] == 17
    assert values["fixture_warmups"] == 3
    assert values["fixture_timing_scope"] == scope.value
    assert len(str(fixture_input["initializer"]).split(",")) == f.input_tensor.size
    source = _jinja_env.get_template("fixed_fixture.cc.j2").render(**values)
    assert f"deployment_output[{f.output_tensor.size}]" in source
    assert f"input->bytes != {f.input_tensor.size}" in source
    for registration in model.resolver.registrations:
        assert registration.removeprefix("r.").removesuffix(";") in source
    assert ("AddFullyConnected()" in source) == (kind == "kws")
    assert ("AddConv2D()" in source) == (kind == "tcn")
    assert "RecordingMicroInterpreter" not in source
    assert "deployment_timing[1] = 17;" in source
    assert "deployment_timing[2] = 3;" in source
    measured_loop = source.index("for (unsigned i = 0; i < 17;")
    timer_start = source.index("const uint32_t t0 = hpx_stimer_ticks();")
    restore = source.index("std::memcpy(input_data", measured_loop)
    invocation = source.index("invocation_status |= invoke();", measured_loop)
    if scope is FixtureTimingScope.INVOKE_ONLY:
        assert measured_loop < restore < timer_start < invocation
        assert "ticks += hpx_stimer_ticks() - t0;" in source
    else:
        assert timer_start < measured_loop < restore < invocation
        assert "ticks = hpx_stimer_ticks() - t0;" in source


def test_declaration_mismatch_stops_before_pipeline(tmp_path, monkeypatch):
    c, f = fixture(tmp_path)
    model = analyzed(f)
    monkeypatch.setattr("helia_profiler.fixture.analyze_fixture_model", lambda _: model)
    wrong = replace(f, input_tensor=replace(f.input_tensor, scale=0.5))
    with pytest.raises(Exception, match="declarations differ"):
        build_fixed_fixture(c, wrong, method=METHOD, runtime=runtime(tmp_path))


def test_invalid_model_stops_before_pipeline(tmp_path, monkeypatch):
    c, f = fixture(tmp_path)
    monkeypatch.setattr(
        "helia_profiler.fixture.PipelineRunner", lambda *_: pytest.fail("pipeline reached")
    )
    with pytest.raises(ValueError, match="fixture model|analysis extra"):
        build_fixed_fixture(c, f, method=METHOD, runtime=runtime(tmp_path))


def test_method_requires_explicit_scope():
    with pytest.raises(ValueError, match="Explicit"):
        FixtureMethod("invoke_only")  # ty: ignore[invalid-argument-type]


def test_runtime_rejects_header_tamper_and_provider_substitution(tmp_path):
    rt = runtime(tmp_path)
    rt.verify()
    (tmp_path / "header.h").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        rt.verify()
    rt = runtime(tmp_path)
    data = json.loads(rt.manifest.read())
    data["providers"]["tflite-micro"]["url"] = "https://github.com/AmbiqAI/helia-rt"
    rt.manifest.path.write_text(json.dumps(data))
    bad = replace(
        rt,
        manifest=FixtureFile(rt.manifest.path, sha256(rt.manifest.path.read_bytes()).hexdigest()),
    )
    with pytest.raises(ValueError, match="source identity"):
        bad.verify()


def test_runtime_rejects_path_escape(tmp_path):
    rt = runtime(tmp_path)
    for path in ("../outside.h", "/etc/passwd", "x;message(test)"):
        data = json.loads(rt.manifest.read())
        data["headers"] = {path: "0" * 64}
        rt.manifest.path.write_text(json.dumps(data))
        candidate = replace(
            rt,
            manifest=FixtureFile(
                rt.manifest.path, sha256(rt.manifest.path.read_bytes()).hexdigest()
            ),
        )
        with pytest.raises(ValueError):
            candidate.verify()
        rt = runtime(tmp_path)


def test_memory_terminal_has_external_symbol_linkage():
    from helia_profiler.firmware.render import _jinja_env

    source = _jinja_env.get_template("fixed_fixture_memory.h.j2").render()
    assert "volatile uint32_t deployment_memory[8];" in source
    assert "static volatile uint32_t deployment_memory" not in source


def test_aot_render_uses_compiled_model_without_interpreter(tmp_path):
    from helia_profiler.firmware.fixture import fixture_template_vars
    from helia_profiler.firmware.render import _jinja_env

    c, f = fixture(tmp_path)
    c = replace(c, engine=EngineConfig(type=EngineType.HELIA_AOT))
    ctx = PipelineContext(config=c, work_dir=tmp_path)
    _BindFixtureStage(FixtureRenderSpec(f, METHOD, analyzed(f))).run(ctx)
    source = _jinja_env.get_template("fixed_fixture.cc.j2").render(
        **fixture_template_vars(ctx, []),
        aot_prefix="compiled_tcn",
        allocate_arenas=True,
        arena_regions=[],
    )
    assert "compiled_tcn_model_init(&model_ctx)" in source
    assert "compiled_tcn_model_run(&model_ctx)" in source
    assert "model_ctx.inputs[0].size != 3360" in source
    assert "model_ctx.outputs[0].size != 480" in source
    assert "deployment_output[480]" in source
    assert "MicroInterpreter" not in source
    assert "MicroMutableOpResolver" not in source
    assert "model_data.h" not in source


def test_external_aot_arena_mode_rejected(tmp_path):
    from helia_profiler.engines.base import HeliaAotArtifacts
    from helia_profiler.firmware.fixture import fixture_template_vars

    c, f = fixture(tmp_path)
    ctx = PipelineContext(
        config=replace(c, engine=EngineConfig(type=EngineType.HELIA_AOT)), work_dir=tmp_path
    )
    _BindFixtureStage(FixtureRenderSpec(f, METHOD, analyzed(f))).run(ctx)
    ctx.engine_artifacts = HeliaAotArtifacts(
        engine_header="model.h",
        aot_prefix="model",
        aot_module_name="model",
        aot_cmake_target="model",
        helia_aot_version="test",
        aot_allocate_arenas=False,
    )
    with pytest.raises(Exception, match="External AOT arenas"):
        fixture_template_vars(ctx, [])
