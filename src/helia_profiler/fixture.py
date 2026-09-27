"""Host-only NSX build of a bounded, hash-bound fixed-input fixture."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math
from pathlib import Path
import re

from .config import ProfileConfig
from .engines import EngineType
from .errors import ConfigError
from .pipeline import PipelineContext, PipelineRunner, Stage, serialize_config
from .placement import Placement


@dataclass(frozen=True)
class FixtureFile:
    path: Path
    sha256: str

    def read(self) -> bytes:
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("Expected a SHA256 fixture identity")
        data = self.path.read_bytes()
        if hashlib.sha256(data).hexdigest() != self.sha256:
            raise ValueError(f"Fixture hash mismatch: {self.path}")
        return data


@dataclass(frozen=True)
class PreparedUpstreamRuntime:
    """Explicit prebuilt provider with a pinned, vendored header closure."""

    archive: FixtureFile
    header_root: Path
    manifest: FixtureFile

    def verify(self) -> dict:
        self.archive.read()
        data = json.loads(self.manifest.read())
        if data.get("schema_version") != 1 or data.get("archive_sha256") != self.archive.sha256:
            raise ValueError("Runtime manifest/archive identity mismatch")
        if data.get("providers", {}).keys() != {"tflite-micro", "cmsis-nn"}:
            raise ValueError("Explicit upstream TFLM and Arm CMSIS-NN provenance required")
        for name, url in (
            ("tflite-micro", "https://github.com/tensorflow/tflite-micro"),
            ("cmsis-nn", "https://github.com/ARM-software/CMSIS-NN"),
        ):
            provider = data["providers"][name]
            if provider.get("url") != url or not re.fullmatch(
                r"[0-9a-f]{40}", provider.get("revision", "")
            ):
                raise ValueError("Invalid upstream provider source identity")
        if data.get("abi") != {
            "toolchain": "atfe",
            "cpu": "cortex-m55",
            "float_abi": "hard",
            "short_enums": True,
        }:
            raise ValueError("Unsupported prepared runtime ABI")
        if not data.get("headers") or not data.get("include_dirs"):
            raise ValueError("Pinned header closure required")
        for name, digest in data["headers"].items():
            path = self._path(name)
            FixtureFile(path, digest).read()
        for name in data["include_dirs"]:
            if not self._path(name).is_dir():
                raise ValueError("Missing runtime include directory")
        return data

    def _path(self, name: str) -> Path:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_./+-]+", name):
            raise ValueError("Unsafe runtime relative path")
        path = (self.header_root / name).resolve()
        if Path(name).is_absolute() or not path.is_relative_to(self.header_root.resolve()):
            raise ValueError("Runtime path escapes header root")
        return path


class _PreparedRuntimeStage:
    name = "prepare_upstream_runtime"

    def __init__(self, runtime: PreparedUpstreamRuntime):
        self.runtime = runtime

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        from .results import NsxModuleRef

        data = self.runtime.verify()
        module = ctx.work_dir / "prepared-upstream-runtime" / self.runtime.manifest.sha256
        module.mkdir(parents=True, exist_ok=True)
        (module / "runtime.a").write_bytes(self.runtime.archive.read())
        for name, digest in data["headers"].items():
            target = module / "include" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(FixtureFile(self.runtime._path(name), digest).read())
        (module / "provider-manifest.json").write_bytes(self.runtime.manifest.read())
        (module / "nsx-module.yaml").write_text(
            "schema_version: 1\nmodule:\n  name: hpx-upstream-runtime\n  type: runtime\n  version: '0.1.0'\n"
            "support:\n  ambiqsuite: true\n  zephyr: false\n"
            "build:\n  cmake:\n    targets: [nsx::tflite_micro]\n"
            "depends:\n  required: [nsx-core, nsx-soc-hal]\n"
        )
        includes = "\n".join(
            '  "${CMAKE_CURRENT_LIST_DIR}/include/' + name + '"' for name in data["include_dirs"]
        )
        (module / "CMakeLists.txt").write_text(
            "add_library(hpx_upstream_runtime STATIC IMPORTED GLOBAL)\n"
            'set_target_properties(hpx_upstream_runtime PROPERTIES IMPORTED_LOCATION "${CMAKE_CURRENT_LIST_DIR}/runtime.a")\n'
            "target_include_directories(hpx_upstream_runtime INTERFACE\n" + includes + "\n)\n"
            "target_compile_definitions(hpx_upstream_runtime INTERFACE TF_LITE_STATIC_MEMORY CMSIS_NN ARM_NN_ENABLE_F16=0 ARM_NN_ENABLE_F32=0)\n"
            "add_library(nsx::tflite_micro ALIAS hpx_upstream_runtime)\n"
        )
        if ctx.engine_artifacts is None:
            raise ConfigError("Engine preparation did not produce artifacts")
        ctx.engine_artifacts = replace(
            ctx.engine_artifacts,
            extra_modules=[NsxModuleRef(name="hpx-upstream-runtime", path=module, local=True)],
            cmake_vars={},
        )


@dataclass(frozen=True)
class Int8Tensor:
    shape: tuple[int, ...]
    scale: float
    zero_point: int
    tensor_index: int

    def __post_init__(self) -> None:
        if not self.shape or any(type(n) is not int or n <= 0 for n in self.shape):
            raise ValueError("Positive fixed tensor dimensions required")
        if not math.isfinite(self.scale) or self.scale <= 0:
            raise ValueError("Positive finite quantization scale required")
        if type(self.zero_point) is not int or not -128 <= self.zero_point <= 127:
            raise ValueError("INT8 zero point required")
        if type(self.tensor_index) is not int or self.tensor_index < 0:
            raise ValueError("Nonnegative tensor index required")

    @property
    def size(self) -> int:
        return math.prod(self.shape)


@dataclass(frozen=True)
class FixedFixture:
    model: FixtureFile
    input: FixtureFile
    expected: FixtureFile
    input_tensor: Int8Tensor
    output_tensor: Int8Tensor

    def verify(self) -> None:
        self.model.read()
        if (
            len(self.input.read()) != self.input_tensor.size
            or len(self.expected.read()) != self.output_tensor.size
        ):
            raise ValueError("Full fixture tensor byte extent mismatch")
        if self.input_tensor.shape != (1, 240, 14) or self.output_tensor.shape != (1, 240, 2):
            raise ValueError("This adapter supports the retained single-input INT8 TCN signature")

    @property
    def identity(self) -> str:
        value = asdict(self)
        for name in ("model", "input", "expected"):
            value[name].pop("path")
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()


@dataclass(frozen=True)
class FixtureBuild:
    fixture_identity: str
    app_dir: Path
    binary: FixtureFile | None
    generated_sources: tuple[FixtureFile, ...]
    expected: FixtureFile
    built: bool
    runtime_manifest: FixtureFile
    flat_binary: FixtureFile | None
    dependency_lock: FixtureFile | None
    link_map: FixtureFile | None
    provider_provenance: str = (
        "manifest-declared; independently audit the pinned build/source record"
    )


def _validate(config: ProfileConfig, fixture: FixedFixture) -> None:
    fixture.verify()
    if config.model.path.resolve() != fixture.model.path.resolve():
        raise ConfigError("Profile model differs from the pinned fixture")
    if config.engine.type != EngineType.TFLM or config.engine.backend != "cmsis_nn":
        raise ConfigError("Fixed fixture requires explicit upstream TFLM with Arm CMSIS-NN")
    if config.target.board != "apollo510_evb" or config.target.clock.cpu != "lp":
        raise ConfigError("Fixed fixture supports Apollo510 EVB LP clock only")
    if (
        config.model.arena_location != Placement.SRAM
        or config.model.weights_location != Placement.MRAM
    ):
        raise ConfigError("Fixed fixture requires explicit SRAM arena and MRAM model")
    if config.model.arena_size is None or not 0 < config.model.arena_size <= 3 * 1024 * 1024:
        raise ConfigError("Explicit bounded arena capacity required")
    if config.power.enabled or config.target.ensure_board_powered:
        raise ConfigError("Fixture build cannot enable power or instrument operations")
    if config.work_dir is None or config.clean:
        raise ConfigError("Fixture build requires an explicit preserved work directory")
    if config.profiling.warmup != 5 or config.profiling.iterations != 100:
        raise ConfigError("Fixed fixture requires the retained five-warmup/100-call method")


class _GenerateFixtureStage:
    name = "generate_fixed_fixture"

    def __init__(self, fixture: FixedFixture):
        self.fixture = fixture

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        from .stages.generate_firmware import GenerateFirmwareStage
        from .deps.dependencies import workspace_mutex
        from .firmware.render import _jinja_env

        GenerateFirmwareStage().run(ctx)
        fixture = self.fixture
        fixture.verify()
        with workspace_mutex(ctx.resolved_workspace):
            root = ctx.resolved_firmware_dir / "src"
            source = _jinja_env.get_template("fixed_fixture_tflm.cc.j2").render(
                input_tensor=fixture.input_tensor,
                output_tensor=fixture.output_tensor,
                input_values=",".join(str(b) for b in fixture.input.read()),
                arena_size=ctx.config.model.arena_size,
            )
            (root / "main.cc").write_text(source)
            (root / "fixed_fixture_memory.h").write_text(
                _jinja_env.get_template("fixed_fixture_memory.h.j2").render()
            )
            (root / "fixed_fixture_clock.h").write_text(
                '#pragma once\n#include "apollo510.h"\n#include "nsx_core.h"\n#include "am_hal_status.h"\n#include "am_hal_stimer.h"\n'
                + _jinja_env.get_template("_stimer_init.j2").render()
            )


def build_fixed_fixture(
    config: ProfileConfig,
    fixture: FixedFixture,
    *,
    runtime: PreparedUpstreamRuntime,
    compile: bool = True,
) -> FixtureBuild:
    """Render or compile one fixed fixture through profiler's host-only stages."""
    _validate(config, fixture)
    runtime.verify()
    if config.target.toolchain != "atfe":
        raise ConfigError("Prepared runtime requires its matching ATfE toolchain")
    if config.work_dir is None:
        raise ConfigError("Explicit work directory required")
    root = config.work_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    identity_path = root / "fixed-fixture-identity.json"
    identity = {
        "runtime": runtime.manifest.sha256,
        "fixture": fixture.identity,
        "profile": hashlib.sha256(
            json.dumps(serialize_config(config), sort_keys=True).encode()
        ).hexdigest(),
    }

    # PipelineRunner holds its normal workspace lock; its first stage binds
    # this directory before any generated source can overwrite older intent.
    class IdentityStage:
        name = "bind_fixed_fixture"

        def should_skip(self, ctx):
            return False

        def run(self, ctx):
            if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
                raise ConfigError("Work directory belongs to a different fixture")
            identity_path.write_text(json.dumps(identity, sort_keys=True))
            fixture.verify()

    from .stages import (
        ResolvePlatformStage,
        PrepareEngineStage,
        AnalyzeModelStage,
        PlanMemoryStage,
        BuildFirmwareStage,
    )

    stages: list[Stage] = [
        IdentityStage(),
        ResolvePlatformStage(),
        PrepareEngineStage(),
        _PreparedRuntimeStage(runtime),
        AnalyzeModelStage(),
        PlanMemoryStage(),
        _GenerateFixtureStage(fixture),
    ]
    if compile:
        stages.append(BuildFirmwareStage())
    ctx = PipelineRunner(stages).run(config)
    app = ctx.resolved_firmware_dir

    def pin(path):
        return FixtureFile(path, hashlib.sha256(path.read_bytes()).hexdigest())

    sources = tuple(
        pin(app / "src" / name)
        for name in ("main.cc", "model_data.h", "fixed_fixture_memory.h", "fixed_fixture_clock.h")
    )
    if compile and ctx.profile_run is None:
        raise ConfigError("Build stage did not produce a firmware result")
    binary = (
        pin(ctx.profile_run.firmware.binary_path)
        if ctx.profile_run is not None and compile
        else None
    )
    flat_binary = dependency_lock = link_map = None
    if binary is not None:
        import yaml

        module = app / "modules" / "hpx-upstream-runtime"
        FixtureFile(module / "runtime.a", runtime.archive.sha256).read()
        FixtureFile(module / "provider-manifest.json", runtime.manifest.sha256).read()
        dependency_lock = pin(app / "nsx.lock")
        modules = yaml.safe_load(dependency_lock.read())["targets"][config.target.board]["modules"]
        if "hpx-upstream-runtime" not in modules or any(
            "helia-rt" in name or name in {"nsx-tflite-micro", "arm-cmsis-nn"} for name in modules
        ):
            raise ConfigError("Unexpected runtime provider in resolved dependency lock")
        flat_binary = pin(binary.path.with_suffix(".bin"))
        link_map = pin(binary.path.with_suffix(".map"))
        map_text = link_map.read().decode()
        if (
            "hpx-upstream-runtime/runtime.a(" not in map_text
            or "helia_rt" in map_text
            or "helia-rt" in map_text
        ):
            raise ConfigError("Link map does not prove the explicit upstream provider")
    return FixtureBuild(
        fixture.identity,
        app,
        binary,
        sources,
        fixture.expected,
        compile,
        runtime.manifest,
        flat_binary,
        dependency_lock,
        link_map,
    )
