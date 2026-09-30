"""heliaRT fixed fixtures link a schema-2 prepared runtime archive and prove it."""

from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from helia_profiler.config import (
    ClockSelection,
    EngineConfig,
    ModelConfig,
    ProfileConfig,
    ProfilingConfig,
    TargetConfig,
)
from helia_profiler.engines import EngineType
from helia_profiler.engines.base import HeliaRtArtifacts
from helia_profiler.errors import ConfigError
from helia_profiler.firmware.op_resolver import build_fixture_resolver_plan
from helia_profiler._fixture_build import (
    FixedFixture,
    FixtureCapability,
    FixtureFile,
    FixtureMethod,
    FixtureTimingScope,
    Int8Tensor,
    PreparedUpstreamRuntime,
    build_fixed_fixture,
)
from helia_profiler.fixture_analysis import FixtureModelAnalysis
from helia_profiler.fixture_runtime import _PreparedRuntimeStage
from helia_profiler.modelcost.model_analysis import LayerOps, ModelAnalysis
from helia_profiler.pipeline import PipelineContext
from helia_profiler.results import NsxModuleRef
from helia_profiler.results.models import EngineInfo, MemoryPlan, RunMetadata, ToolchainInfo
from helia_profiler.vocab import Toolchain

METHOD = FixtureMethod(FixtureTimingScope.RESTORE_AND_INVOKE)
HELIA_DEFINES = ["TF_LITE_STATIC_MEMORY", "CMSIS_NN", "ARM_NN_ENABLE_F32=1", "ARM_NN_ENABLE_F16=1"]


def pin(root, name, data):
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_bytes(data)
    return FixtureFile(path, sha256(data).hexdigest())


def manifest(archive, header, *, schema=2, **change):
    if schema == 1:
        data = {
            "schema_version": 1,
            "archive_sha256": archive.sha256,
            "providers": {
                "tflite-micro": {
                    "url": "https://github.com/tensorflow/tflite-micro",
                    "revision": "a" * 40,
                },
                "cmsis-nn": {
                    "url": "https://github.com/ARM-software/CMSIS-NN",
                    "revision": "b" * 40,
                },
            },
        }
    else:
        data = {
            "schema_version": 2,
            "stack": "helia-rt",
            "archive_sha256": archive.sha256,
            "providers": {
                "helia-rt": {"url": "https://github.com/AmbiqAI/helia-rt", "revision": "c" * 40},
                "ns-cmsis-nn": {
                    "url": "https://github.com/AmbiqAI/ns-cmsis-nn",
                    "revision": "d" * 40,
                },
            },
            "build": {"consumer_defines": list(HELIA_DEFINES), "kernel_dir": "helia"},
        }
    data.update(
        abi={"toolchain": "atfe", "cpu": "cortex-m55", "float_abi": "hard", "short_enums": True},
        headers={"header.h": header.sha256},
        include_dirs=["."],
    )
    data.update(change)
    return data


def runtime(root, *, schema=2, **change):
    archive = pin(root, "runtime.a", b"!<arch>\nfixture-member-" + str(schema).encode())
    header = pin(root, "header.h", b"header")
    data = manifest(archive, header, schema=schema, **change)
    return PreparedUpstreamRuntime(
        archive, root, pin(root, "runtime.json", json.dumps(data).encode())
    )


# --- manifest ingress --------------------------------------------------------


def test_schema_two_records_stack_defines_and_kernel_dir(tmp_path):
    record = runtime(tmp_path).verify().record
    assert (record.schema_version, record.stack, record.kernel_dir) == (2, "helia-rt", "helia")
    assert record.consumer_defines == tuple(HELIA_DEFINES)
    assert [p.name for p in record.providers] == ["helia-rt", "ns-cmsis-nn"]
    upstream = runtime(tmp_path / "v1", schema=1).verify().record
    assert (upstream.schema_version, upstream.stack, upstream.kernel_dir) == (1, "upstream", None)
    assert upstream.consumer_defines == (
        "TF_LITE_STATIC_MEMORY",
        "CMSIS_NN",
        "ARM_NN_ENABLE_F16=0",
        "ARM_NN_ENABLE_F32=0",
    )


@pytest.mark.parametrize(
    "change,match",
    [
        ({"schema_version": 3}, "schema version"),
        ({"stack": "upstream"}, "stack"),
        ({"stack": "helia-aot"}, "stack"),
        ({"stack": []}, "stack"),
        (
            {
                "providers": {
                    "tflite-micro": {
                        "url": "https://github.com/tensorflow/tflite-micro",
                        "revision": "a" * 40,
                    },
                    "cmsis-nn": {
                        "url": "https://github.com/ARM-software/CMSIS-NN",
                        "revision": "b" * 40,
                    },
                }
            },
            "providers",
        ),
        (
            {
                "providers": {
                    "helia-rt": {"url": "https://github.com/fork/helia-rt", "revision": "c" * 40},
                    "ns-cmsis-nn": {
                        "url": "https://github.com/AmbiqAI/ns-cmsis-nn",
                        "revision": "d" * 40,
                    },
                }
            },
            "source identity",
        ),
        ({"build": {"consumer_defines": HELIA_DEFINES}}, "build fields"),
        ({"build": {"consumer_defines": HELIA_DEFINES, "kernel_dir": "cmsis_nn"}}, "kernel"),
        ({"build": {"consumer_defines": ["CMSIS_NN"], "kernel_dir": "helia"}}, "defines"),
        (
            {"build": {"consumer_defines": [*HELIA_DEFINES, "CMSIS_NN=0"], "kernel_dir": "helia"}},
            "defines",
        ),
        (
            {"build": {"consumer_defines": [*HELIA_DEFINES, "x;y"], "kernel_dir": "helia"}},
            "defines",
        ),
        ({"build": {"consumer_defines": "CMSIS_NN", "kernel_dir": "helia"}}, "defines"),
    ],
)
def test_schema_two_rejects_foreign_or_malformed_declarations(tmp_path, change, match):
    with pytest.raises(ValueError, match=match):
        runtime(tmp_path, **change).verify()


def test_schema_one_rejects_schema_two_fields(tmp_path):
    with pytest.raises(ValueError, match="manifest fields"):
        runtime(tmp_path, schema=1, stack="upstream").verify()
    data = manifest(pin(tmp_path, "runtime.a", b"!<arch>\nm"), pin(tmp_path, "header.h", b"header"))
    del data["stack"]
    bad = PreparedUpstreamRuntime(
        pin(tmp_path, "runtime.a", b"!<arch>\nm"),
        tmp_path,
        pin(tmp_path, "runtime.json", json.dumps(data).encode()),
    )
    with pytest.raises(ValueError, match="manifest fields"):
        bad.verify()


# --- NSX staging ------------------------------------------------------------


def _stage(tmp_path, rt):
    ctx = PipelineContext(
        config=_config(tmp_path, _fixture(tmp_path)[0]), work_dir=tmp_path / "work"
    )
    ctx.engine_artifacts = HeliaRtArtifacts(
        engine_type=EngineType.HELIA_RT,
        extra_modules=[
            NsxModuleRef(name="nsx-helia-rt", path=tmp_path, local=False),
            NsxModuleRef(name="nsx-cmsis-nn", path=tmp_path, local=False),
        ],
        cmake_vars={"HELIA_RT_VARIANT": "release"},
        engine_header="tensorflow/lite/micro/micro_interpreter.h",
        engine_backend="helia",
        heliart_version="1.21.2",
        heliart_variant="release",
        heliart_toolchain_tag="atfe",
    )
    ctx.run_metadata.engine = EngineInfo(type="helia-rt", version="1.21.0", backend="helia")
    stage = _PreparedRuntimeStage(rt.verify())
    stage.run(ctx)
    return stage, ctx


def test_upstream_staging_is_unchanged(tmp_path):
    rt = runtime(tmp_path / "rt", schema=1)
    stage, ctx = _stage(tmp_path, rt)
    module = tmp_path / "work" / "prepared-upstream-runtime" / rt.manifest.sha256
    assert stage.name == "prepare_upstream_runtime"
    assert (module / "CMakeLists.txt").read_text() == (
        "add_library(hpx_upstream_runtime STATIC IMPORTED GLOBAL)\n"
        'set_target_properties(hpx_upstream_runtime PROPERTIES IMPORTED_LOCATION "${CMAKE_CURRENT_LIST_DIR}/runtime.a")\n'
        "target_include_directories(hpx_upstream_runtime INTERFACE\n"
        '  "${CMAKE_CURRENT_LIST_DIR}/include/."\n)\n'
        "target_compile_definitions(hpx_upstream_runtime INTERFACE TF_LITE_STATIC_MEMORY CMSIS_NN ARM_NN_ENABLE_F16=0 ARM_NN_ENABLE_F32=0)\n"
        "add_library(nsx::tflite_micro ALIAS hpx_upstream_runtime)\n"
    )
    assert (module / "nsx-module.yaml").read_text() == (
        "schema_version: 1\nmodule:\n  name: hpx-upstream-runtime\n  type: runtime\n  version: '0.1.0'\n"
        "support:\n  ambiqsuite: true\n  zephyr: false\n"
        "build:\n  cmake:\n    targets: [nsx::tflite_micro]\n"
        "depends:\n  required: [nsx-core, nsx-soc-hal]\n"
    )
    assert [m.name for m in ctx.engine_artifacts.extra_modules] == ["hpx-upstream-runtime"]
    assert ctx.run_metadata.engine is not None and ctx.run_metadata.engine.version == "1.21.0"


def test_heliart_staging_replaces_the_engine_modules_with_the_archive(tmp_path):
    rt = runtime(tmp_path / "rt")
    stage, ctx = _stage(tmp_path, rt)
    module = tmp_path / "work" / "prepared-heliart-runtime" / rt.manifest.sha256
    assert stage.name == "prepare_heliart_runtime"
    cmake = (module / "CMakeLists.txt").read_text()
    assert "add_library(hpx_heliart_runtime STATIC IMPORTED GLOBAL)" in cmake
    assert (
        "target_compile_definitions(hpx_heliart_runtime INTERFACE TF_LITE_STATIC_MEMORY "
        "CMSIS_NN ARM_NN_ENABLE_F32=1 ARM_NN_ENABLE_F16=1)" in cmake
    )
    assert cmake.endswith("add_library(nsx::helia_rt ALIAS hpx_heliart_runtime)\n")
    assert "name: hpx-heliart-runtime" in (module / "nsx-module.yaml").read_text()
    assert "targets: [nsx::helia_rt]" in (module / "nsx-module.yaml").read_text()
    assert (module / "runtime.a").read_bytes() == rt.archive.read()
    assert (module / "include" / "header.h").read_bytes() == b"header"
    assert [(m.name, m.local) for m in ctx.engine_artifacts.extra_modules] == [
        ("hpx-heliart-runtime", True)
    ]
    assert ctx.engine_artifacts.cmake_vars == {}
    assert isinstance(ctx.engine_artifacts, HeliaRtArtifacts)
    assert ctx.engine_artifacts.heliart_version == "prepared:" + "c" * 40
    assert ctx.engine_artifacts.heliart_variant == "prepared"
    assert ctx.run_metadata.engine is not None
    assert ctx.run_metadata.engine.version == "prepared:" + "c" * 40


# --- fixture build ----------------------------------------------------------


def _fixture(tmp_path):
    t_in, t_out = Int8Tensor((1, 4), 0.25, -3, 0), Int8Tensor((1, 4), 0.5, 2, 1)
    f = FixedFixture(
        pin(tmp_path, "model.tflite", b"fixture-model"),
        pin(tmp_path, "input.bin", bytes(4)),
        pin(tmp_path, "output.bin", bytes(4)),
        t_in,
        t_out,
    )
    ops = ModelAnalysis([LayerOps(0, "RELU")], 0, 0, 0)
    return f, FixtureModelAnalysis(t_in, t_out, ops, build_fixture_resolver_plan(ops))


def _config(tmp_path, f, engine=EngineType.HELIA_RT, backend="helia"):
    return ProfileConfig(
        model=ModelConfig(
            path=f.model.path, arena_size=262144, arena_location="sram", weights_location="mram"
        ),
        engine=EngineConfig(type=engine, backend=backend),
        target=TargetConfig(
            board="apollo510_evb", toolchain=Toolchain.ATFE, clock=ClockSelection(cpu="lp")
        ),
        profiling=ProfilingConfig(iterations=100, warmup=5),
        work_dir=tmp_path / "build",
    )


def _runner(tmp_path, rt, calls, *, lock_modules, map_lines):
    class Runner:
        def __init__(self, stages):
            calls.append([s.name for s in stages])

        def run(self, config):
            app = tmp_path / "app"
            (app / "src").mkdir(parents=True, exist_ok=True)
            (app / "src" / "main.cc").write_text("main")
            binary = app / "hpx_profiler"
            binary.write_bytes(b"elf")
            binary.with_suffix(".bin").write_bytes(b"bin")
            binary.with_suffix(".map").write_text("\n".join(map_lines))
            module = app / "modules" / "hpx-heliart-runtime"
            module.mkdir(parents=True, exist_ok=True)
            (module / "runtime.a").write_bytes(rt.archive.read())
            (module / "provider-manifest.json").write_bytes(rt.manifest.read())
            modules = "".join(f"      {name}: {{}}\n" for name in lock_modules)
            (app / "nsx.lock").write_text("targets:\n  apollo510_evb:\n    modules:\n" + modules)
            return SimpleNamespace(
                memory_plan=MemoryPlan(engine=config.engine.type),
                engine_artifacts=None,
                run_metadata=RunMetadata(
                    toolchain=ToolchainInfo(compiler="atfe", compiler_version="22.1.0")
                ),
                resolved_firmware_dir=app,
                profile_run=SimpleNamespace(firmware=SimpleNamespace(binary_path=binary)),
            )

    return Runner


GOOD_LOCK = ["nsx-core", "nsx-soc-hal", "hpx-heliart-runtime"]
GOOD_MAP = ["modules/hpx-heliart-runtime/runtime.a(micro_interpreter.cc.obj)"]


def test_heliart_build_links_and_proves_the_prepared_archive(tmp_path, monkeypatch):
    f, analysis = _fixture(tmp_path)
    rt = runtime(tmp_path / "rt")
    calls = []
    monkeypatch.setattr("helia_profiler._fixture_build.analyze_fixture_model", lambda _: analysis)
    monkeypatch.setattr(
        "helia_profiler._fixture_build.PipelineRunner",
        _runner(tmp_path, rt, calls, lock_modules=GOOD_LOCK, map_lines=GOOD_MAP),
    )
    r = build_fixed_fixture(_config(tmp_path, f), f, method=METHOD, runtime=rt)
    assert calls[-1][:5] == [
        "bind_fixed_fixture",
        "resolve_platform",
        "prepare_engine",
        "prepare_heliart_runtime",
        "bind_fixture_render",
    ]
    assert r.built and r.engine is EngineType.HELIA_RT
    assert r.runtime_manifest == rt.manifest
    assert r.capabilities == (("int8", FixtureCapability.SUPPORTED),)


@pytest.mark.parametrize(
    "lock,map_lines,match",
    [
        ([*GOOD_LOCK, "nsx-helia-rt"], GOOD_MAP, "dependency lock"),
        ([*GOOD_LOCK, "nsx-cmsis-nn"], GOOD_MAP, "dependency lock"),
        ([*GOOD_LOCK, "hpx-upstream-runtime"], GOOD_MAP, "dependency lock"),
        (["nsx-core", "nsx-soc-hal"], GOOD_MAP, "dependency lock"),
        (GOOD_LOCK, ["modules/nsx-helia-rt/libhelia-rt.a(x.o)"], "helia-rt provider"),
        (GOOD_LOCK, [*GOOD_MAP, "modules/nsx-cmsis-nn/libcmsis-nn.a(x.o)"], "helia-rt provider"),
        (GOOD_LOCK, [*GOOD_MAP, "modules/nsx-tflite-micro/lib.a(x.o)"], "helia-rt provider"),
        ([*GOOD_LOCK, "helia-rt-source"], GOOD_MAP, "dependency lock"),
        ([*GOOD_LOCK, "arm-cmsis-nn"], GOOD_MAP, "dependency lock"),
        (GOOD_LOCK, [*GOOD_MAP, "/w/modules/helia-rt-source/libhelia.a(x.o)"], "helia-rt provider"),
        (
            GOOD_LOCK,
            [*GOOD_MAP, "/w/app/modules/arm-cmsis-nn/Source/x.c.obj:(.text)"],
            "helia-rt provider",
        ),
    ],
)
def test_heliart_build_refuses_any_other_runtime_provider(
    tmp_path, monkeypatch, lock, map_lines, match
):
    f, analysis = _fixture(tmp_path)
    rt = runtime(tmp_path / "rt")
    monkeypatch.setattr("helia_profiler._fixture_build.analyze_fixture_model", lambda _: analysis)
    monkeypatch.setattr(
        "helia_profiler._fixture_build.PipelineRunner",
        _runner(tmp_path, rt, [], lock_modules=lock, map_lines=map_lines),
    )
    with pytest.raises(ConfigError, match=match):
        build_fixed_fixture(_config(tmp_path, f), f, method=METHOD, runtime=rt)


@pytest.mark.parametrize(
    "extra,refused",
    [
        (None, False),
        ("build/_nsx/helia_rt/libhelia_rt.a(kernel.cc.obj):(.text)", True),
        (
            "modules/hpx-heliart-runtime/cmsis-nn-embedded/modules/utils/lib.a(x.o):(.text)",
            True,
        ),
    ],
)
def test_heliart_proof_reads_inputs_below_the_app_not_above_it(
    tmp_path, monkeypatch, extra, refused
):
    """Directories above the app never count; every directory below it does."""
    f, analysis = _fixture(tmp_path)
    rt = runtime(tmp_path / "rt")
    # A provider-named build tree must pass; refusals use a neutral tree so the
    # extra input alone decides them.
    work = tmp_path / ("plain" if refused else "helia-rt-bench/modules/tflite-micro")
    root = work / "app"
    lines = [
        f"{root}/modules/hpx-heliart-runtime/runtime.a(micro_interpreter.cc.obj):(.text)",
        "_nsx/nsx_core/libnsx_runtime.a(runtime.c.obj):(.text)",
        f"{root}/build/CMakeFiles/hpx_profiler.dir/src/main.cc.obj:(.text)",
        "/opt/ATfE/lib/clang-runtimes/arm-none-eabi/armv8.1m.main_hard_fp/lib/libc.a(x.o):(.text)",
    ]
    if extra:
        lines.append(f"{root}/{extra}")
    monkeypatch.setattr("helia_profiler._fixture_build.analyze_fixture_model", lambda _: analysis)
    monkeypatch.setattr(
        "helia_profiler._fixture_build.PipelineRunner",
        _runner(work, rt, [], lock_modules=GOOD_LOCK, map_lines=lines),
    )
    if refused:
        with pytest.raises(ConfigError, match="helia-rt provider"):
            build_fixed_fixture(_config(tmp_path, f), f, method=METHOD, runtime=rt)
    else:
        assert build_fixed_fixture(_config(tmp_path, f), f, method=METHOD, runtime=rt).built


@pytest.mark.parametrize(
    "engine,backend,schema,match",
    [
        (EngineType.HELIA_RT, "helia", None, "helia-rt prepared runtime and helia backend"),
        (EngineType.HELIA_RT, None, 2, "helia-rt prepared runtime and helia backend"),
        (EngineType.HELIA_RT, "ethos_u", 2, "helia-rt prepared runtime and helia backend"),
        (EngineType.HELIA_RT, "helia", 1, "stack is not helia-rt"),
        (EngineType.TFLM, "cmsis_nn", 2, "stack is not upstream"),
        (EngineType.HELIA_AOT, None, 2, "cannot consume"),
    ],
)
def test_engine_and_prepared_stack_must_agree(
    tmp_path, monkeypatch, engine, backend, schema, match
):
    f, analysis = _fixture(tmp_path)
    monkeypatch.setattr("helia_profiler._fixture_build.analyze_fixture_model", lambda _: analysis)
    monkeypatch.setattr(
        "helia_profiler._fixture_build.PipelineRunner", lambda *_: pytest.fail("pipeline reached")
    )
    rt = None if schema is None else runtime(tmp_path / "rt", schema=schema)
    with pytest.raises(ConfigError, match=match):
        build_fixed_fixture(_config(tmp_path, f, engine, backend), f, method=METHOD, runtime=rt)


def test_upstream_build_still_refuses_a_heliart_module(tmp_path, monkeypatch):
    f, analysis = _fixture(tmp_path)
    rt = runtime(tmp_path / "rt", schema=1)
    monkeypatch.setattr("helia_profiler._fixture_build.analyze_fixture_model", lambda _: analysis)

    class Runner:
        def __init__(self, stages):
            pass

        def run(self, config):
            app = tmp_path / "app"
            (app / "src").mkdir(parents=True, exist_ok=True)
            binary = app / "hpx_profiler"
            binary.write_bytes(b"elf")
            binary.with_suffix(".bin").write_bytes(b"bin")
            binary.with_suffix(".map").write_text("hpx-upstream-runtime/runtime.a(member.o)")
            module = app / "modules" / "hpx-upstream-runtime"
            module.mkdir(parents=True, exist_ok=True)
            (module / "runtime.a").write_bytes(rt.archive.read())
            (module / "provider-manifest.json").write_bytes(rt.manifest.read())
            (app / "nsx.lock").write_text(
                "targets:\n  apollo510_evb:\n    modules:\n"
                "      hpx-upstream-runtime: {}\n      hpx-heliart-runtime: {}\n"
            )
            return SimpleNamespace(
                memory_plan=MemoryPlan(engine=config.engine.type),
                engine_artifacts=None,
                run_metadata=RunMetadata(toolchain=ToolchainInfo("atfe", "22.1.0")),
                resolved_firmware_dir=app,
                profile_run=SimpleNamespace(firmware=SimpleNamespace(binary_path=binary)),
            )

    monkeypatch.setattr("helia_profiler._fixture_build.PipelineRunner", Runner)
    with pytest.raises(ConfigError, match="dependency lock"):
        build_fixed_fixture(
            _config(tmp_path, f, EngineType.TFLM, "cmsis_nn"), f, method=METHOD, runtime=rt
        )


def test_heliart_accepts_float16_models_that_tflm_refuses(tmp_path):
    from helia_profiler._fixture_build import FixtureIO, TypedFixture, _check_typed_fixture
    from helia_profiler.fixture_analysis import FixtureTensor, TypedFixtureModelAnalysis

    tensors = (FixtureTensor("x", 0, "float16", (1, 4), None),)
    outputs = (FixtureTensor("y", 1, "float32", (1, 4), None),)
    fixture = TypedFixture(
        pin(tmp_path, "model.tflite", b"m"),
        (FixtureIO(tensors[0], pin(tmp_path, "x.bin", bytes(8))),),
        (FixtureIO(outputs[0], pin(tmp_path, "y.bin", bytes(16))),),
    )
    ops = ModelAnalysis([LayerOps(0, "ADD")], 0, 0, 0)
    model = TypedFixtureModelAnalysis(tensors, outputs, True, ops, build_fixture_resolver_plan(ops))
    assert _check_typed_fixture(fixture, model, EngineType.HELIA_RT) == (
        ("float16", FixtureCapability.SUPPORTED),
        ("float32", FixtureCapability.SUPPORTED),
    )
    with pytest.raises(ConfigError, match="float16"):
        _check_typed_fixture(fixture, model, EngineType.TFLM)


@pytest.mark.parametrize("sep", ["/", "\\"])
def test_linked_inputs_keep_a_windows_drive_letter(sep):
    """ATfE maps on Windows name inputs as ``C:\\...`` with either separator."""
    from pathlib import PureWindowsPath

    from helia_profiler._fixture_build import _linked_components

    root = sep.join(["C:", "Users", "r", "helia-rt-bench", "modules", "tflite-micro", "app"])
    app = PureWindowsPath(root)
    own = sep.join([root, "modules", "hpx-heliart-runtime", "runtime.a(m.cc.obj):(.text)"])
    main = sep.join([root, "build", "CMakeFiles", "hpx_profiler.dir", "src", "main.cc.obj:(.text)"])
    assert _linked_components(f"{own}\n{main}\n", app) == {
        "modules",
        "hpx-heliart-runtime",
        "runtime",
        "build",
        "CMakeFiles",
        "hpx-profiler.dir",
        "src",
        "main.cc",
    }
    nested = sep.join(
        [root, "modules", "hpx-heliart-runtime", "cmsis-nn-x", "modules", "u", "l.a(x.o):"]
    )
    assert "cmsis-nn-x" in _linked_components(nested, app)


_APPS = {
    "posix": ("/home/First Last/helia-rt bench/modules/tflite-micro/app", "/"),
    "windows": ("C:\\Users\\First Last\\helia-rt-bench\\modules\\tflite-micro\\app", "\\"),
}
_TOOLCHAIN = {
    "posix": "/opt/ATfE 22/lib/clang-runtimes/arm-none-eabi/armv8.1m.main_hard_fp/lib/libc.a",
    "windows": "C:\\Program Files\\ATfE\\lib\\clang-runtimes\\arm-none-eabi\\lib\\libc.a",
}


def _lld(path: str, member: str = "x.c.obj") -> str:
    return f"  41101e   41101e       42     2         {path}({member}):(.text)"


@pytest.mark.parametrize("stack", ["helia-rt", "upstream"])
@pytest.mark.parametrize("style", ["native", "forward", "mixed"])
@pytest.mark.parametrize("host", ["posix", "windows", "windows-lower-drive"])
@pytest.mark.parametrize(
    "case,refused",
    [
        ("clean", False),
        ("nsx-provider", True),
        ("nested-provider", True),
        ("vendored-provider", True),
        ("other-stack-archive", True),
        ("missing-own-archive", True),
    ],
)
def test_link_map_proof_on_posix_and_windows_paths(stack, style, host, case, refused):
    """Every map-scan case under POSIX and Windows paths, spaces and either separator."""
    from pathlib import PurePosixPath, PureWindowsPath

    from helia_profiler._fixture_build import PREPARED_RUNTIME_MODULES, _prove_link_map

    base = host.replace("-lower-drive", "")
    root, native = _APPS[base]
    if stack == "upstream":
        # The upstream proof also refuses any "helia-rt" text; keep the tree neutral there.
        root = root.replace("helia-rt", "neutral")
    app = (PureWindowsPath if base == "windows" else PurePosixPath)(root)
    if host == "windows-lower-drive":
        # The map spells the drive in the other case from the app path.
        root = "c" + root[1:]
    own = PREPARED_RUNTIME_MODULES[stack][0]
    other = next(n for s, (n, _, _) in PREPARED_RUNTIME_MODULES.items() if s != stack)

    def under_app(*parts: str) -> str:
        if style == "native":
            return native.join([root, *parts])
        if style == "forward":
            return "/".join([root.replace("\\", "/"), *parts])
        return root + "/" + "\\".join(parts)

    lines = [
        _lld(under_app("modules", own, "runtime.a"), "micro_interpreter.cc.obj"),
        _lld("_nsx/nsx_core/libnsx_runtime.a", "runtime.c.obj"),
        f"  41101e   41101e       42     2         {under_app('build', 'CMakeFiles', 'hpx_profiler.dir', 'src', 'main.cc.obj')}:(.text)",
        _lld(_TOOLCHAIN[base]),
    ]
    extra = {
        "nsx-provider": _lld(under_app("build", "_nsx", "helia_rt", "libhelia_rt.a")),
        "nested-provider": _lld(
            under_app("modules", own, "cmsis-nn-embedded", "modules", "utils", "lib.a")
        ),
        "vendored-provider": _lld(under_app("modules", "nsx-tflite-micro", "libtflm.a")),
        "other-stack-archive": _lld(under_app("modules", other, "runtime.a")),
    }
    if case == "missing-own-archive":
        lines = lines[1:]
    elif case in extra:
        lines.append(extra[case])
    text = "\n".join(lines) + "\n"
    if refused:
        with pytest.raises(ConfigError, match=f"explicit {stack} provider"):
            _prove_link_map(text, app, stack)
    else:
        _prove_link_map(text, app, stack)
