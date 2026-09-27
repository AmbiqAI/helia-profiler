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
from helia_profiler.fixture import (
    FixtureFile,
    Int8Tensor,
    FixedFixture,
    PreparedUpstreamRuntime,
    build_fixed_fixture,
    _GenerateFixtureStage,
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
            board="apollo510_evb", toolchain="atfe", clock=ClockSelection(cpu="lp")
        ),
        profiling=ProfilingConfig(iterations=100, warmup=5),
        work_dir=tmp_path / "build",
    )
    return c, f


def runtime(tmp_path):
    def pin(name, data):
        p = tmp_path / name
        p.write_bytes(data)
        return FixtureFile(p, sha256(data).hexdigest())

    a = pin("runtime.a", b"archive")
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
            replace(c, engine=EngineConfig(type=EngineType.HELIA_RT)), f, runtime=rt
        )
    f.input.path.write_bytes(bytes(3359))
    with pytest.raises(ValueError, match="hash mismatch"):
        build_fixed_fixture(c, f, runtime=rt)


def test_host_only_stage_selection_and_source_receipt(tmp_path, monkeypatch):
    c, f = fixture(tmp_path)
    rt = runtime(tmp_path)
    calls = []

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
            binary.write_bytes(b"elf")
            binary.with_suffix(".bin").write_bytes(b"bin")
            binary.with_suffix(".map").write_text("hpx-upstream-runtime/runtime.a(member.o)")
            module = app / "modules/hpx-upstream-runtime"
            module.mkdir(parents=True, exist_ok=True)
            (module / "runtime.a").write_bytes(rt.archive.read())
            (module / "provider-manifest.json").write_bytes(rt.manifest.read())
            (app / "nsx.lock").write_text(
                "targets:\n  apollo510_evb:\n    modules:\n      hpx-upstream-runtime: {}\n"
            )
            return SimpleNamespace(
                resolved_firmware_dir=app,
                profile_run=SimpleNamespace(firmware=SimpleNamespace(binary_path=binary)),
            )

    monkeypatch.setattr("helia_profiler.fixture.PipelineRunner", Runner)
    r = build_fixed_fixture(c, f, runtime=rt, compile=False)
    assert not r.built and r.binary is None and len(r.generated_sources) == 4
    assert calls[-1] == [
        "bind_fixed_fixture",
        "resolve_platform",
        "prepare_engine",
        "prepare_upstream_runtime",
        "analyze_model",
        "plan_memory",
        "generate_fixed_fixture",
    ]
    r = build_fixed_fixture(c, f, runtime=rt)
    assert r.built and r.binary.read() == b"elf"
    assert calls[-1][-1] == "build_firmware"
    with pytest.raises(Exception, match="different fixture"):
        build_fixed_fixture(replace(c, model=replace(c.model, arena_size=131072)), f, runtime=rt)


def test_render_uses_full_input_and_exact_sink_extents(tmp_path, monkeypatch):
    from contextlib import nullcontext

    c, f = fixture(tmp_path)
    rt = runtime(tmp_path)
    app = tmp_path / "app"
    (app / "src").mkdir(parents=True)
    monkeypatch.setattr(
        "helia_profiler.stages.generate_firmware.GenerateFirmwareStage.run", lambda self, ctx: None
    )
    monkeypatch.setattr("helia_profiler.deps.dependencies.workspace_mutex", lambda _: nullcontext())
    ctx = SimpleNamespace(config=c, resolved_firmware_dir=app, resolved_workspace=None)
    _GenerateFixtureStage(f).run(ctx)
    source = (app / "src/main.cc").read_text()
    assert "deployment_output[480]" in source and "input->bytes != 3360" in source
    assert "output->dims->data[2] != 2" in source
    assert source.count("std::memcpy(input->data.int8, fixed_input, sizeof(fixed_input))") == 2
    assert source.index("const uint32_t ticks =") < source.index("hpx_fixture_memory_snapshot(5")
    assert "MicroMutableOpResolver<6>" in source and "RecordingMicroInterpreter" not in source


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
        with pytest.raises(ValueError):
            rt._path(path)
