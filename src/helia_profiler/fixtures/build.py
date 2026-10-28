"""Host-only NSX build of a bounded, hash-bound fixed-input fixture."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, replace

from .._files import FixtureFile
from ..config import ProfileConfig
from ..deps.compatibility import resolve_compatibility
from ..engines import EngineType, get_adapter
from ..engines.base import HeliaAotArtifacts, PreparedRuntimeAdapter
from ..errors import ConfigError
from ..firmware.launcher import _DISABLED_LAUNCHER_VALUES
from ..pipeline import PipelineContext, PipelineRunner, Stage, serialize_config
from ..placement import ArenaRole, Placement
from ..runtime.archive import PreparedUpstreamRuntime, prove_prepared_runtime_build
from ..wire import STIMER_HZ
from .analysis import (
    FixtureModelAnalysis,
    FixtureTensor,
    Int8Tensor,
    PerTensorQuantization,
    TypedFixtureModelAnalysis,
    analyze_fixture_model,
    analyze_typed_fixture_model_for_engine,
)
from .image import MAX_IMAGE, MRAM, bounded, fixture_sink_sizes, inspect_elf
from .io import FIXTURE_CAPABILITIES as FIXTURE_CAPABILITIES
from .io import FIXTURE_READBACK_BUDGET as FIXTURE_READBACK_BUDGET
from .io import FixtureCapability as FixtureCapability
from .io import FixtureDType as FixtureDType
from .io import io_capabilities
from .psram import FixturePsramWeights
from .sources import pinned_sources
from .target import (
    FIXTURE_CLOCK_PROFILE,
    fixture_cpu_hz,
    fixture_target_for_board,
    validate_fixture_placement,
)
from .types import (
    FixedFixture,
    FixtureBuild,
    FixtureIO,
    FixtureMethod,
    FixtureRenderSpec,
    FixtureRole,
    FixtureStatePolicy,
    TypedFixture,
)


def _state_method(model, method: FixtureMethod) -> FixtureMethod:
    """Record that internal state carries between invocations, as the firmware runs it."""
    if model.has_internal_state and method.state_policy is None:
        return replace(method, state_policy=FixtureStatePolicy.CARRY)
    return method


def _check_typed_fixture(
    fixture: TypedFixture, model: TypedFixtureModelAnalysis, engine: EngineType
) -> tuple[tuple[str, FixtureCapability], ...]:
    """Match declared tensors to the model and return each IO dtype's capability."""
    if (
        tuple(io.tensor for io in fixture.inputs) != model.inputs
        or tuple(io.tensor for io in fixture.outputs) != model.outputs
    ):
        raise ConfigError("Fixture tensor declarations differ from analyzed model")
    return io_capabilities(model.inputs, model.outputs, engine)


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
    "HELIART_DIST_PATH",
    "HELIART_SOURCE_PATH",
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
        allow_unsupported_version=config.engine.allow_unsupported_version,
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
        raise ConfigError("Fixture supports LiteRT, heliaRT or heliaAOT only")
    if config.target.custom_socs or config.target.custom_boards:
        raise ConfigError("Fixed fixture does not support custom target declarations")
    try:
        fixture_target_for_board(config.target.board)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    if config.target.clock.cpu != FIXTURE_CLOCK_PROFILE:
        raise ConfigError(f"Fixed fixture supports the {FIXTURE_CLOCK_PROFILE} clock only")
    validate_fixture_placement(
        config.model.arena_location,
        config.model.weights_location,
        config.engine.type,
        board=config.target.board,
    )
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
    observe_ram: bool = False,
) -> FixtureBuild:
    """Render or compile one fixed fixture through profiler's host-only stages."""
    if not isinstance(method, FixtureMethod):
        raise ConfigError("Explicit FixtureMethod required")
    _validate(config, fixture)
    if config.target.board == "apollo510b_evb" and method.power_sync is not None:
        raise ConfigError("AP510B fixture integration is timing-only")
    if type(observe_ram) is not bool or (observe_ram and method.power_sync is not None):
        raise ConfigError("RAM diagnostic requires a boolean option and energy-off method")
    model: FixtureModelAnalysis | TypedFixtureModelAnalysis
    if isinstance(fixture, TypedFixture):
        model = analyze_typed_fixture_model_for_engine(
            fixture.model.path, engine=config.engine.type
        )
        capabilities = _check_typed_fixture(fixture, model, config.engine.type)
    else:
        model = analyze_fixture_model(fixture.model.path, _engine=config.engine.type)
        if (fixture.input_tensor, fixture.output_tensor) != (
            model.input_tensor,
            model.output_tensor,
        ):
            raise ConfigError("Fixture tensor declarations differ from analyzed model")
        capabilities = (("int8", FIXTURE_CAPABILITIES[config.engine.type]["int8"]),)
    method = _state_method(model, method)
    adapter = get_adapter(config.engine.type)
    if isinstance(adapter, PreparedRuntimeAdapter):
        stack, backend = adapter.prepared_runtime_contract
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
    # Placement owns the engine settings it implies; they take precedence over caller values.
    config = adapter.placement_config(config)
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
    if method.power_sync is not None:
        identity["power_sync"] = asdict(method.power_sync)
    if method.state_policy is not None:
        identity["state_policy"] = method.state_policy.value
    if observe_ram:
        identity["observe_ram"] = True
    if observe_aot_arenas:
        identity["observe_aot_arenas"] = True

    # PipelineRunner holds its normal workspace lock; its first stage binds
    # this directory before any generated source can overwrite older intent.
    class IdentityStage:
        name = "bind_fixed_fixture"

        def should_skip(self, ctx):
            return False

        def run(self, ctx):
            if (
                identity_path.exists()
                and json.loads(identity_path.read_text(encoding="utf-8")) != identity
            ):
                raise ConfigError("Work directory belongs to a different fixture")
            identity_path.write_text(json.dumps(identity, sort_keys=True), encoding="utf-8")
            fixture.verify()

    from ..stages import (
        BuildFirmwareStage,
        GenerateFirmwareStage,
        PlanMemoryStage,
        PrepareEngineStage,
        ResolvePlatformStage,
    )

    stages: list[Stage] = [
        IdentityStage(),
        ResolvePlatformStage(),
        PrepareEngineStage(runtime=verified_runtime),
    ]
    stages += [
        _BindFixtureStage(
            FixtureRenderSpec(
                fixture=fixture,
                method=method,
                model=model,
                observe_aot_arenas=observe_aot_arenas,
                observe_ram=observe_ram,
            )
        ),
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
    flat_binary = dependency_lock = dependency_state = link_map = None
    if binary is not None:
        flat_binary = pin(binary.path.with_suffix(".bin"))
        link_map = pin(binary.path.with_suffix(".map"))
        if ctx.dependency_lock_path is None:
            raise ConfigError("Compiled fixture requires its dependency snapshot")
        dependency_lock = pin(ctx.dependency_lock_path)
        dependency_state = pin(ctx.dependency_lock_path.with_name("hpx-dependencies.json"))
    if binary is not None and runtime is not None and verified_runtime is not None:
        assert dependency_lock is not None and link_map is not None
        prove_prepared_runtime_build(
            runtime,
            verified_runtime,
            app,
            config.target.board,
            dependency_lock.read(),
            link_map.read(),
        )
    if flat_binary is not None:
        size = flat_binary.path.stat().st_size
        if not 0 < size <= MAX_IMAGE or not bounded(
            fixture_target_for_board(config.target.board).load_address, size, MRAM
        ):
            raise ConfigError(
                f"Flat image is {size} B; fixture capture accepts at most {MAX_IMAGE} B in MRAM"
            )
    intent_identity = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    toolchain = ctx.run_metadata.toolchain if compile else None
    if compile and (toolchain is None or not toolchain.compiler_version):
        raise ConfigError("Compiled fixture requires recorded toolchain provenance")
    planned_memory = ctx.memory_plan
    artifacts = ctx.engine_artifacts
    if config.engine.type is EngineType.HELIA_AOT and (
        artifacts is None or artifacts.memory_plan is None
    ):
        planned_memory = None
    region_evidence = ()
    if isinstance(artifacts, HeliaAotArtifacts):
        from .placement import validate_fixture_regions

        residence = validate_fixture_regions(ctx, artifacts.aot_arena_regions)
        if residence is not None:
            record = root / "fixture-aot-regions.json"
            record.write_text(json.dumps(residence, sort_keys=True, indent=2), encoding="utf-8")
            region_evidence = (pin(record),)
    report_files = tuple(pin(path) for path in artifacts.report_files) if artifacts else ()
    kernel_source, runtime_source = (
        pinned_sources(runtime.manifest.read() if runtime else None, dependency_state.read())
        if dependency_state is not None
        else (None, None)
    )
    inputs, output_tensors = _measurement_io(fixture)
    result = FixtureBuild(
        fixture_identity=fixture.identity,
        app_dir=app,
        binary=binary,
        generated_sources=sources,
        expected=fixture.expected,
        built=compile,
        runtime_manifest=runtime.manifest if runtime else None,
        flat_binary=flat_binary,
        dependency_lock=dependency_lock,
        link_map=link_map,
        timing_scope=method.timing_scope,
        engine=config.engine.type,
        build_identity=None,
        iterations=config.profiling.iterations,
        warmups=config.profiling.warmup,
        intent_identity=intent_identity,
        target=fixture_target_for_board(config.target.board),
        toolchain=toolchain,
        provider_provenance="manifest-declared; independently audit the pinned build/source record"
        if runtime
        else "normal AOT engine artifacts and resolved dependency lock",
        planned_memory=planned_memory,
        planned_memory_reason=None
        if planned_memory is not None
        else "producer_plan_unavailable_or_unsupported_mapping",
        outputs=tuple(io.data for io in fixture.outputs if isinstance(io, FixtureIO))
        if isinstance(fixture, TypedFixture)
        else (fixture.expected,),
        capabilities=capabilities,
        aot_arena_scan=tuple(
            (r.region_id, r.size)
            for r in (
                artifacts.aot_arena_regions if isinstance(artifacts, HeliaAotArtifacts) else ()
            )
            if observe_aot_arenas and r.role is ArenaRole.SCRATCH
        ),
        aot_outputs=(*report_files, *region_evidence),
        engine_source=artifacts.source if artifacts else None,
        kernel_source=kernel_source,
        runtime_source=runtime_source,
        dependency_state=dependency_state,
        psram_weights=_psram_weights(ctx, flat_binary),
        arena_location=config.model.arena_location,
        weights_location=config.model.weights_location,
        state_policy=method.state_policy,
        source_initializer_present=model.has_initializer,
        model=fixture.model,
        inputs=inputs,
        output_tensors=output_tensors,
        arena_capacity=config.model.arena_size,
        clock_profile=FIXTURE_CLOCK_PROFILE,
        cpu_hz=fixture_cpu_hz(config.target.board),
        timer_hz=STIMER_HZ,
        power_sync=method.power_sync,
        observe_ram=observe_ram,
    )
    if binary is not None:
        from .record import compiled_fixture_identity

        if result.power_sync is not None or result.observe_ram:
            assert flat_binary is not None
            inspect_elf(
                binary.read(),
                flat_binary.read(),
                result.target.load_address,
                fixture_sink_sizes(
                    tuple(tensor.size_bytes for tensor in result.output_tensors),
                    power=result.power_sync is not None,
                    ram=result.observe_ram,
                    memory=result.engine is not EngineType.HELIA_AOT,
                    arena_count=len(result.aot_arena_scan),
                    psram_size=result.psram_weights.sink_size if result.psram_weights else 0,
                ),
            )
        result = replace(result, build_identity=compiled_fixture_identity(result))
    return result


def _measurement_io(fixture: FixedFixture | TypedFixture):
    if isinstance(fixture, TypedFixture):
        return (
            tuple(replace(io, role=FixtureRole(io.role)) for io in fixture.inputs),
            tuple(io.tensor for io in fixture.outputs),
        )

    def tensor(value: Int8Tensor, name: str) -> FixtureTensor:
        return FixtureTensor(
            name,
            value.tensor_index,
            "int8",
            value.shape,
            PerTensorQuantization(value.scale, value.zero_point),
        )

    return (
        (FixtureIO(tensor(fixture.input_tensor, "input"), fixture.input),),
        (tensor(fixture.output_tensor, "output"),),
    )


def _psram_weights(ctx: PipelineContext, image: FixtureFile | None) -> FixturePsramWeights | None:
    if ctx.config.model.weights_location is not Placement.PSRAM:
        return None
    from ..firmware.fixture import fixture_psram_payloads

    regions = (
        ctx.engine_artifacts.aot_arena_regions
        if isinstance(ctx.engine_artifacts, HeliaAotArtifacts)
        else []
    )
    payloads = fixture_psram_payloads(ctx, regions)
    return FixturePsramWeights(
        payloads, image.sha256 if image else None, ctx.config.target.psram.clock_hz
    )
