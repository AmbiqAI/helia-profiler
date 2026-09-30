"""Host-only NSX build of a bounded, hash-bound fixed-input fixture."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path, PurePath
import re

from .config import ProfileConfig
from .deps.compatibility import resolve_compatibility
from .engines import EngineType
from .engines.base import HeliaAotArtifacts
from .errors import ConfigError
from .firmware.launcher import _DISABLED_LAUNCHER_VALUES
from .pipeline import PipelineContext, PipelineRunner, Stage, serialize_config
from .placement import ArenaRole, Placement
from .fixture_image import MAX_IMAGE, MRAM, bounded
from .fixture_target import FixtureTarget, supported_fixture_target
from .results.models import ToolchainInfo, MemoryPlan


from enum import StrEnum
from .fixture_runtime import (
    PREPARED_RUNTIME_MODULES,
    FixtureFile,
    PreparedUpstreamRuntime,
    _PreparedRuntimeStage,
)
from .fixture_analysis import (
    FixtureModelAnalysis,
    FixtureTensor,
    Int8Tensor,
    PerAxisQuantization,
    TypedFixtureModelAnalysis,
    analyze_fixture_model,
    analyze_typed_fixture_model,
)


class FixtureTimingScope(StrEnum):
    INVOKE_ONLY = "invoke_only"
    RESTORE_AND_INVOKE = "restore_and_invoke"


class FixtureRole(StrEnum):
    """Role of one fixture tensor; outputs are always ``SIGNAL``."""

    SIGNAL = "signal"
    AUX = "aux"


class FixtureDType(StrEnum):
    """IO tensor element types a fixture can declare."""

    INT8 = "int8"
    INT16 = "int16"
    FLOAT16 = "float16"
    FLOAT32 = "float32"


class FixtureBackend(StrEnum):
    """Kernel backend a prepared-runtime fixture engine requires."""

    CMSIS_NN = "cmsis_nn"
    HELIA = "helia"


@dataclass(frozen=True)
class FixtureMethod:
    """Select whether fixed-input restoration is included in the timed interval."""

    timing_scope: FixtureTimingScope

    def __post_init__(self) -> None:
        if not isinstance(self.timing_scope, FixtureTimingScope):
            raise ValueError("Explicit FixtureTimingScope required")


@dataclass(frozen=True)
class FixtureRenderSpec:
    fixture: FixedFixture | TypedFixture
    method: FixtureMethod
    model: FixtureModelAnalysis | TypedFixtureModelAnalysis
    #: Paint heliaAOT scratch arenas and report touched bytes after the run.
    observe_aot_arenas: bool = False


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


class FixtureCapability(StrEnum):
    """Producer-declared status of one engine and IO dtype for fixed fixtures."""

    QUALIFIED = "qualified"
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"


#: Fixture support per engine and IO dtype. ``qualified`` has an exact device
#: pass; ``supported`` builds but has none yet; ``unsupported`` is refused.
FIXTURE_CAPABILITIES: dict[EngineType, dict[str, FixtureCapability]] = {
    EngineType.TFLM: {
        FixtureDType.INT8: FixtureCapability.QUALIFIED,
        FixtureDType.INT16: FixtureCapability.SUPPORTED,
        FixtureDType.FLOAT32: FixtureCapability.SUPPORTED,
        FixtureDType.FLOAT16: FixtureCapability.UNSUPPORTED,
    },
    EngineType.HELIA_AOT: {
        FixtureDType.INT8: FixtureCapability.QUALIFIED,
        FixtureDType.INT16: FixtureCapability.SUPPORTED,
        FixtureDType.FLOAT16: FixtureCapability.SUPPORTED,
        FixtureDType.FLOAT32: FixtureCapability.SUPPORTED,
    },
    EngineType.HELIA_RT: {
        FixtureDType.INT8: FixtureCapability.SUPPORTED,
        FixtureDType.INT16: FixtureCapability.SUPPORTED,
        FixtureDType.FLOAT16: FixtureCapability.SUPPORTED,
        FixtureDType.FLOAT32: FixtureCapability.SUPPORTED,
    },
}
#: Engines that link a prepared runtime archive: (manifest stack, required backend).
PREPARED_RUNTIME_ENGINES = {
    EngineType.TFLM: ("upstream", FixtureBackend.CMSIS_NN),
    EngineType.HELIA_RT: ("helia-rt", FixtureBackend.HELIA),
}
#: Runtime providers a prepared-runtime build must not also resolve or link:
#: any module whose name carries one of these, and the other prepared stacks.
_RUNTIME_PROVIDER = re.compile(r"helia-rt|tflite-micro|cmsis-nn")
_PREPARED_MODULES = frozenset(name for name, _, _ in PREPARED_RUNTIME_MODULES.values())
#: Archives and objects a link map lists as inputs, e.g. ``.../libx.a(y.o):`` or ``.../y.o:``.
_MAP_INPUT = re.compile(r"((?:[A-Za-z]:)?[^\s():]+)\.(?:a|o|obj)(?=[(:])")
#: An LLD map input line: address, load address, size and alignment columns, then the
#: input path, which may contain spaces.
_LLD_INPUT = re.compile(r"\s*(?:[0-9A-Fa-f]+\s+){4}((?:[A-Za-z]:)?[^():]+?)\.(?:a|o|obj)(?=[(:])")


def _linked_components(map_text: str, app: PurePath) -> set[str]:
    """Path components naming each linked input, not the directories above the app.

    Inputs under ``app`` and relative inputs contribute every component; other
    absolute inputs (the toolchain's libraries) contribute the components after
    their first ``modules`` or ``_nsx`` directory, or else their parent directory
    and file stem. Underscores are read as hyphens, the NSX build-directory spelling.
    """
    # Windows maps may spell separators either way, whatever the app path uses.
    root = str(app).replace("\\", "/").rstrip("/")
    names = set()
    for line in map_text.splitlines():
        lld = _LLD_INPUT.match(line)
        for found in [lld.group(1)] if lld else _MAP_INPUT.findall(line):
            names.update(_input_components(found.replace("\\", "/"), root))
    return names


def _input_components(path: str, root: str) -> set[str]:
    """Components of one ``/``-separated input path, per :func:`_linked_components`."""
    prefix = path[: len(root) + 1]
    # A drive-letter root is a Windows path, whose case does not matter.
    windows = re.match(r"[A-Za-z]:", root) is not None
    if prefix == root + "/" or (windows and prefix.lower() == (root + "/").lower()):
        parts = path[len(root) + 1 :].split("/")
    else:
        parts = path.split("/")
        if path.startswith("/") or re.match(r"[A-Za-z]:", path):
            marks = [i for i, part in enumerate(parts) if part in ("modules", "_nsx")]
            parts = parts[marks[0] + 1 :] if marks else parts[-2:]
    return {part.replace("_", "-") for part in parts if part}


def _prove_link_map(map_text: str, app: PurePath, stack: str) -> None:
    """Refuse a link map that lacks the stack's archive or names another runtime provider."""
    name = PREPARED_RUNTIME_MODULES[stack][0]
    others = _PREPARED_MODULES - {name}
    if (
        f"{name}/runtime.a(" not in map_text.replace("\\", "/")
        or any(
            part in others or _RUNTIME_PROVIDER.search(part)
            for part in _linked_components(map_text, app)
        )
        or (stack == "upstream" and ("helia_rt" in map_text or "helia-rt" in map_text))
    ):
        raise ConfigError(f"Link map does not prove the explicit {stack} provider")


#: Largest total output a fixture may expose for full readback.
FIXTURE_READBACK_BUDGET = 128 * 1024
_FIXTURE_ROLES = frozenset(FixtureRole)


@dataclass(frozen=True)
class FixtureIO:
    """One model input with its fixed bytes, or one output with its expected bytes."""

    tensor: FixtureTensor
    data: FixtureFile
    role: FixtureRole = FixtureRole.SIGNAL


@dataclass(frozen=True)
class TypedFixture:
    """Fixed inputs and expected outputs for every IO tensor of a model."""

    model: FixtureFile
    inputs: tuple[FixtureIO, ...]
    outputs: tuple[FixtureIO, ...]

    def verify(self) -> None:
        self.model.read()
        if not self.inputs or not self.outputs:
            raise ValueError("Typed fixture requires inputs and outputs")
        if any(io.role not in _FIXTURE_ROLES for io in self.inputs) or any(
            io.role != FixtureRole.SIGNAL for io in self.outputs
        ):
            raise ValueError("Invalid fixture tensor role")
        for io in (*self.inputs, *self.outputs):
            if len(io.data.read()) != io.tensor.size_bytes:
                raise ValueError(f"Fixture tensor {io.tensor.name} byte extent mismatch")

    @property
    def expected(self) -> FixtureFile:
        return self.outputs[0].data

    @property
    def identity(self) -> str:
        value = asdict(self)
        value["model"].pop("path")
        for io in (*value["inputs"], *value["outputs"]):
            io["data"].pop("path")
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()


def _check_typed_fixture(
    fixture: TypedFixture, model: TypedFixtureModelAnalysis, engine: EngineType
) -> tuple[tuple[str, FixtureCapability], ...]:
    """Match declared tensors to the model and return each IO dtype's capability."""
    if (
        tuple(io.tensor for io in fixture.inputs) != model.inputs
        or tuple(io.tensor for io in fixture.outputs) != model.outputs
    ):
        raise ConfigError("Fixture tensor declarations differ from analyzed model")
    if model.output_bytes > FIXTURE_READBACK_BUDGET:
        raise ConfigError(
            f"Fixture outputs total {model.output_bytes} B, above the "
            f"{FIXTURE_READBACK_BUDGET} B readback budget"
        )
    if engine is EngineType.TFLM and model.has_float16:
        raise ConfigError("The upstream TFLM runtime has no float16 support")
    table = FIXTURE_CAPABILITIES[engine]
    tensors = (*model.inputs, *model.outputs)
    # Device passes so far cover one input and one output with per-tensor
    # quantization, which the firmware also checks on the device.
    single_io = len(model.inputs) == len(model.outputs) == 1 and not any(
        isinstance(t.quantization, PerAxisQuantization) for t in tensors
    )

    def status(dtype: str) -> FixtureCapability:
        if table[dtype] is FixtureCapability.QUALIFIED and not single_io:
            return FixtureCapability.SUPPORTED
        return table[dtype]

    capabilities = tuple(sorted({(t.dtype, status(t.dtype)) for t in tensors}))
    unsupported = [
        dtype for dtype, status in capabilities if status is FixtureCapability.UNSUPPORTED
    ]
    if unsupported:
        raise ConfigError(f"{engine.value} fixtures do not support {', '.join(unsupported)} IO")
    return capabilities


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
    build_identity: str | None
    iterations: int
    warmups: int
    intent_identity: str
    target: FixtureTarget
    toolchain: ToolchainInfo | None
    provider_provenance: str = (
        "manifest-declared; independently audit the pinned build/source record"
    )
    planned_memory: MemoryPlan | None = None
    planned_memory_reason: str | None = "not_recorded"
    #: Expected bytes of every output, in model order (typed fixtures).
    outputs: tuple[FixtureFile, ...] = ()
    #: Producer capability of each IO dtype this build uses.
    capabilities: tuple[tuple[str, FixtureCapability], ...] = ()
    #: heliaAOT scratch arenas painted and scanned: (region id, size in bytes).
    aot_arena_scan: tuple[tuple[int, int], ...] = ()


#: Environment variables outside the compatibility classifier that change what a
#: fixture build compiles or which inputs it accepts: HPX's own source paths,
#: variables CMake and the compilers read, and NSX's check bypasses.
FIXTURE_REFUSED_ENVIRONMENT = (
    "ASM",
    "ASMFLAGS",
    "CC",
    "CCC_OVERRIDE_OPTIONS",
    "CFLAGS",
    "CMAKE_ASM_COMPILER_LAUNCHER",
    "CMAKE_BUILD_TYPE",
    "CMAKE_CXX_COMPILER_LAUNCHER",
    "CMAKE_CXX_LINKER_LAUNCHER",
    "CMAKE_C_COMPILER_LAUNCHER",
    "CMAKE_C_LINKER_LAUNCHER",
    "CMAKE_TOOLCHAIN_FILE",
    "COMPILER_PATH",
    "CPATH",
    "CPLUS_INCLUDE_PATH",
    "CPPFLAGS",
    "CXX",
    "CXXFLAGS",
    "C_INCLUDE_PATH",
    "GCC_EXEC_PREFIX",
    "LDFLAGS",
    "LIBRARY_PATH",
    "NSX_ALLOW_VERSION_MISMATCH",
    "NSX_SKIP_COMPAT_CHECK",
    "SEGGER_RTT_PATH",
)


def _refuse_overrides(config: ProfileConfig) -> None:
    """Refuse any source, module, path, flag or launcher override; fixtures build pinned inputs only."""
    resolution = resolve_compatibility(
        config.compatibility_baseline,
        module_overrides=config.build.nsx_modules,
        engine_config=config.engine.config,
        engine_config_path=config.engine.config_path,
        engine_type=config.engine.type.value,
        engine_backend=config.engine.backend,
    )
    overrides = {*resolution.module_overrides, *resolution.engine_overrides}
    overrides.update(f"env.{name}" for name in FIXTURE_REFUSED_ENVIRONMENT if os.environ.get(name))
    if config.target.segger_rtt_path is not None:
        overrides.add("target.segger_rtt_path")
    launcher, source = os.environ.get("HPX_COMPILER_LAUNCHER"), "env.HPX_COMPILER_LAUNCHER"
    if launcher is None:
        launcher, source = config.build.compiler_launcher, "build.compiler_launcher"
    if launcher.strip().lower() not in {"auto", *_DISABLED_LAUNCHER_VALUES}:
        overrides.add(source)
    if overrides:
        raise ConfigError(
            f"Fixture builds use pinned inputs only; remove: {', '.join(sorted(overrides))}",
            hint="Unset the environment variables and drop the config keys named above.",
        )


def _validate(config: ProfileConfig, fixture: FixedFixture | TypedFixture) -> None:
    fixture.verify()
    _refuse_overrides(config)
    if config.model.path.resolve() != fixture.model.path.resolve():
        raise ConfigError("Profile model differs from the pinned fixture")
    if config.engine.type not in FIXTURE_CAPABILITIES:
        raise ConfigError("Fixture supports upstream TFLM, heliaRT or helia-AOT only")
    if config.target.custom_socs or config.target.custom_boards:
        raise ConfigError("Fixed fixture does not support custom target declarations")
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
    fixture: FixedFixture | TypedFixture,
    *,
    method: FixtureMethod,
    runtime: PreparedUpstreamRuntime | None = None,
    compile: bool = True,
    observe_aot_arenas: bool = False,
) -> FixtureBuild:
    """Render or compile one fixed fixture through profiler's host-only stages."""
    if not isinstance(method, FixtureMethod):
        raise ConfigError("Explicit FixtureMethod required")
    _validate(config, fixture)
    model: FixtureModelAnalysis | TypedFixtureModelAnalysis
    if isinstance(fixture, TypedFixture):
        model = analyze_typed_fixture_model(fixture.model.path)
        capabilities = _check_typed_fixture(fixture, model, config.engine.type)
    else:
        model = analyze_fixture_model(fixture.model.path)
        if (fixture.input_tensor, fixture.output_tensor) != (
            model.input_tensor,
            model.output_tensor,
        ):
            raise ConfigError("Fixture tensor declarations differ from analyzed model")
        capabilities = (("int8", FIXTURE_CAPABILITIES[config.engine.type]["int8"]),)
    if config.engine.type in PREPARED_RUNTIME_ENGINES:
        stack, backend = PREPARED_RUNTIME_ENGINES[config.engine.type]
        if runtime is None or config.engine.backend != backend:
            raise ConfigError(f"Explicit {stack} prepared runtime and {backend} backend required")
        verified_runtime = runtime.verify()
        if verified_runtime.record.stack != stack:
            raise ConfigError(f"Prepared runtime stack is not {stack}")
    else:
        if runtime is not None:
            raise ConfigError("AOT cannot consume an upstream runtime override")
        verified_runtime = None
    if observe_aot_arenas and config.engine.type is not EngineType.HELIA_AOT:
        raise ConfigError("Arena observation applies to heliaAOT fixtures only")
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
    if observe_aot_arenas:
        identity["observe_aot_arenas"] = True

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
        _BindFixtureStage(FixtureRenderSpec(fixture, method, model, observe_aot_arenas)),
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
    if binary is not None and runtime is not None and verified_runtime is not None:
        import yaml

        stack = verified_runtime.record.stack
        name = PREPARED_RUNTIME_MODULES[stack][0]
        others = _PREPARED_MODULES - {name}
        module = app / "modules" / name
        FixtureFile(module / "runtime.a", runtime.archive.sha256).read()
        FixtureFile(module / "provider-manifest.json", runtime.manifest.sha256).read()
        dependency_lock = pin(app / "nsx.lock")
        modules = yaml.safe_load(dependency_lock.read())["targets"][config.target.board]["modules"]
        if name not in modules or any(m in others or _RUNTIME_PROVIDER.search(m) for m in modules):
            raise ConfigError("Unexpected runtime provider in resolved dependency lock")
        flat_binary = pin(binary.path.with_suffix(".bin"))
        link_map = pin(binary.path.with_suffix(".map"))
        _prove_link_map(link_map.read().decode(), app, stack)
    if flat_binary is not None:
        size = flat_binary.path.stat().st_size
        if not 0 < size <= MAX_IMAGE or not bounded(
            supported_fixture_target().load_address, size, MRAM
        ):
            raise ConfigError(
                f"Flat image is {size} B; fixture capture accepts at most {MAX_IMAGE} B in MRAM"
            )
    intent_identity = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    toolchain = ctx.run_metadata.toolchain if compile else None
    if compile and (toolchain is None or not toolchain.compiler_version):
        raise ConfigError("Compiled fixture requires recorded toolchain provenance")
    artifact_identity = None
    if binary is not None:
        artifact_identity = hashlib.sha256(
            json.dumps(
                {
                    "intent": intent_identity,
                    "elf": binary.sha256,
                    "image": flat_binary.sha256 if flat_binary else None,
                    "lock": dependency_lock.sha256 if dependency_lock else None,
                    "map": link_map.sha256 if link_map else None,
                    "sources": [(source.path.name, source.sha256) for source in sources],
                    "toolchain": asdict(toolchain) if toolchain else None,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
    planned_memory = ctx.memory_plan
    if config.engine.type is EngineType.HELIA_AOT and (
        ctx.engine_artifacts is None or ctx.engine_artifacts.memory_plan is None
    ):
        planned_memory = None
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
        artifact_identity,
        config.profiling.iterations,
        config.profiling.warmup,
        intent_identity,
        supported_fixture_target(),
        toolchain,
        "manifest-declared; independently audit the pinned build/source record"
        if runtime
        else "normal AOT engine artifacts and resolved dependency lock",
        planned_memory=planned_memory,
        planned_memory_reason=None
        if planned_memory is not None
        else "producer_plan_unavailable_or_unsupported_mapping",
        outputs=tuple(io.data for io in fixture.outputs)
        if isinstance(fixture, TypedFixture)
        else (fixture.expected,),
        capabilities=capabilities,
        aot_arena_scan=tuple(
            (r.region_id, r.size)
            for r in (
                ctx.engine_artifacts.aot_arena_regions
                if isinstance(ctx.engine_artifacts, HeliaAotArtifacts)
                else ()
            )
            if observe_aot_arenas and r.role is ArenaRole.SCRATCH
        ),
    )
