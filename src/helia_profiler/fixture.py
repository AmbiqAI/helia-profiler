"""Host-only NSX build of a bounded, hash-bound fixed-input fixture."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path

from .config import ProfileConfig
from .engines import EngineType
from .errors import ConfigError
from .pipeline import PipelineContext, PipelineRunner, Stage, serialize_config
from .placement import Placement


from enum import StrEnum
from .fixture_runtime import FixtureFile, PreparedUpstreamRuntime, _PreparedRuntimeStage
from .fixture_analysis import Int8Tensor, FixtureModelAnalysis, analyze_fixture_model


class FixtureTimingScope(StrEnum):
    INVOKE_ONLY = "invoke_only"
    RESTORE_AND_INVOKE = "restore_and_invoke"


@dataclass(frozen=True)
class FixtureMethod:
    """Select whether fixed-input restoration is included in the timed interval."""

    timing_scope: FixtureTimingScope

    def __post_init__(self) -> None:
        if not isinstance(self.timing_scope, FixtureTimingScope):
            raise ValueError("Explicit FixtureTimingScope required")


@dataclass(frozen=True)
class FixtureRenderSpec:
    fixture: FixedFixture
    method: FixtureMethod
    model: FixtureModelAnalysis


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
    runtime_manifest: FixtureFile | None
    flat_binary: FixtureFile | None
    dependency_lock: FixtureFile | None
    link_map: FixtureFile | None
    timing_scope: FixtureTimingScope
    engine: EngineType
    build_identity: str
    iterations: int
    warmups: int
    provider_provenance: str = (
        "manifest-declared; independently audit the pinned build/source record"
    )


def _validate(config: ProfileConfig, fixture: FixedFixture) -> None:
    fixture.verify()
    if config.model.path.resolve() != fixture.model.path.resolve():
        raise ConfigError("Profile model differs from the pinned fixture")
    if config.engine.type not in (EngineType.TFLM, EngineType.HELIA_AOT):
        raise ConfigError("Fixture supports upstream TFLM or helia-AOT only")
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
    if not 0 <= config.profiling.warmup <= 10000 or not 1 <= config.profiling.iterations <= 100000:
        raise ConfigError("Fixture requires bounded configured warmup and iteration counts")


class _BindFixtureStage:
    name = "bind_fixture_render"

    def __init__(self, spec: FixtureRenderSpec):
        self.spec = spec

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        self.spec.fixture.verify()
        ctx.fixture = self.spec
        ctx.model_analysis = self.spec.model.analysis


def build_fixed_fixture(
    config: ProfileConfig,
    fixture: FixedFixture,
    *,
    method: FixtureMethod,
    runtime: PreparedUpstreamRuntime | None = None,
    compile: bool = True,
) -> FixtureBuild:
    """Render or compile one fixed fixture through profiler's host-only stages."""
    if not isinstance(method, FixtureMethod):
        raise ConfigError("Explicit FixtureMethod required")
    _validate(config, fixture)
    model = analyze_fixture_model(fixture.model.path)
    if (fixture.input_tensor, fixture.output_tensor) != (model.input_tensor, model.output_tensor):
        raise ConfigError("Fixture tensor declarations differ from analyzed model")
    if config.engine.type is EngineType.TFLM:
        if runtime is None or config.engine.backend != "cmsis_nn":
            raise ConfigError("Explicit upstream prepared runtime and cmsis_nn backend required")
        verified_runtime = runtime.verify()
    else:
        if runtime is not None:
            raise ConfigError("AOT cannot consume an upstream runtime override")
        verified_runtime = None
    if config.target.toolchain != "atfe":
        raise ConfigError("Prepared runtime requires its matching ATfE toolchain")
    if config.work_dir is None:
        raise ConfigError("Explicit work directory required")
    root = config.work_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    identity_path = root / "fixed-fixture-identity.json"
    identity = {
        "runtime": runtime.manifest.sha256 if runtime else None,
        "timing_scope": method.timing_scope.value,
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
        GenerateFirmwareStage,
        PlanMemoryStage,
        BuildFirmwareStage,
    )

    stages: list[Stage] = [
        IdentityStage(),
        ResolvePlatformStage(),
        PrepareEngineStage(),
    ]
    if verified_runtime is not None:
        stages.append(_PreparedRuntimeStage(verified_runtime))
    stages += [
        _BindFixtureStage(FixtureRenderSpec(fixture, method, model)),
        PlanMemoryStage(),
        GenerateFirmwareStage(),
    ]
    if compile:
        stages.append(BuildFirmwareStage())
    ctx = PipelineRunner(stages).run(config)
    app = ctx.resolved_firmware_dir

    def pin(path):
        return FixtureFile(path, hashlib.sha256(path.read_bytes()).hexdigest())

    sources = tuple(pin(path) for path in sorted((app / "src").glob("*")) if path.is_file())
    if compile and ctx.profile_run is None:
        raise ConfigError("Build stage did not produce a firmware result")
    binary = (
        pin(ctx.profile_run.firmware.binary_path)
        if ctx.profile_run is not None and compile
        else None
    )
    flat_binary = dependency_lock = link_map = None
    if binary is not None:
        flat_binary = pin(binary.path.with_suffix(".bin"))
        link_map = pin(binary.path.with_suffix(".map"))
        dependency_lock = pin(app / "nsx.lock")
    if binary is not None and runtime is not None:
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
        runtime.manifest if runtime else None,
        flat_binary,
        dependency_lock,
        link_map,
        method.timing_scope,
        config.engine.type,
        hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
        config.profiling.iterations,
        config.profiling.warmup,
        "manifest-declared; independently audit the pinned build/source record"
        if runtime
        else "normal AOT engine artifacts and resolved dependency lock",
    )
