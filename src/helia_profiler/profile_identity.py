"""The :class:`RunIdentity` of a profile run.

Facts come from what the run recorded: the dependency provenance taken from
the lock, the build images, the resolved platform and placement, and the
configuration. The kernel library is the CMSIS-NN module the lock resolved,
never the backend label. A fact the run does not state is :class:`Unknown`,
with the reason.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._identity_hash import build_hash, digest, measured_facts
from .config import CleanWindowProbe
from .deps.compatibility import CMSIS_NN_PROVIDER_MODULES
from .engines import EngineType
from .identity import (
    BuildFacts,
    CanonicalKernel,
    KernelIdentity,
    RunFacts,
    RunIdentity,
    RuntimeIdentity,
    Unknown,
    canonical_engine,
    canonical_kernel,
)
from .results.dependencies import DependencyModule, DependencyProvenance
from .runtime_records import runtime

if TYPE_CHECKING:
    from .pipeline import PipelineContext

#: Profile firmware initializes the engine once and keeps its state across invocations.
PROFILE_LIFECYCLE = "carry"

_NO_PROVENANCE = Unknown("the run recorded no dependency provenance")

#: The NSX module that is each interpreter engine's runtime.
_RUNTIME_MODULES = {
    EngineType.TFLM: "nsx-tflite-micro",
    EngineType.HELIA_RT: "nsx-helia-rt",
}


def profile_run_identity(ctx: PipelineContext) -> RunIdentity:
    """The identity of the profile run *ctx* describes, and of its PMU capture."""
    provenance = ctx.run_metadata.dependencies
    runtime_identity = _runtime(ctx, provenance)
    model = ctx.run_metadata.model
    model_sha256 = model.sha256 if model and model.sha256 else Unknown("the run recorded no model")
    return RunIdentity(
        runtime=runtime_identity,
        kernels=_kernels(ctx, provenance, runtime_identity),
        prepared_archive_sha256=None,
        build=_build_facts(ctx),
        baseline_id=provenance.workspace.baseline_id if provenance else _NO_PROVENANCE,
        dependency_lock_sha256=provenance.lock.sha256.value if provenance else _NO_PROVENANCE,
        model_sha256=model_sha256,
        inputs_sha256=Unknown("profile firmware feeds zero-filled inputs and pins no input data"),
        run=RunFacts(
            lifecycle=PROFILE_LIFECYCLE,
            iterations=ctx.config.profiling.iterations,
            warmups=ctx.config.profiling.warmup,
            timing_scope=_timing_scope(ctx),
        ),
        producer=_producer(provenance),
        build_hash=_build_hash(ctx, model_sha256),
        observation_hash=(
            digest(measured_facts(ctx.pmu_result)) if ctx.pmu_result is not None else None
        ),
    )


def _module(provenance: DependencyProvenance, name: str) -> DependencyModule | None:
    return next((m for m in provenance.modules if m.name == name), None)


def _runtime(ctx: PipelineContext, provenance: DependencyProvenance | None) -> RuntimeIdentity:
    engine_type = ctx.config.engine.type
    engine = canonical_engine(engine_type.value)
    if engine_type is EngineType.HELIA_AOT:
        return _installed_runtime(ctx, provenance)
    if engine_type is EngineType.EXECUTORCH:
        # The adapter refuses a checkout at any other commit or version.
        record = runtime("executorch")
        if record is None:
            reason = Unknown("this heliaPROFILER ships no executorch runtime record")
            return RuntimeIdentity(engine, reason, reason, reason)
        return RuntimeIdentity(
            engine, f"https://github.com/{record.source.repo}", record.version, record.source.commit
        )
    if provenance is None:
        return RuntimeIdentity(engine, _NO_PROVENANCE, _NO_PROVENANCE, _NO_PROVENANCE)
    name = _RUNTIME_MODULES[engine_type]
    module = _module(provenance, name)
    if module is None:
        reason = Unknown(f"the dependency lock names no {name} module")
        return RuntimeIdentity(engine, reason, reason, reason)
    local = Unknown(f"the {name} module is a local copy; the lock records no source")
    engine_info = ctx.run_metadata.engine
    version = (
        (engine_info.version if engine_info else None)
        or module.requested_tag
        or Unknown(f"neither the engine nor the lock states a {name} version")
    )
    return RuntimeIdentity(engine, module.url or local, version, module.peeled_commit or local)


def _installed_runtime(
    ctx: PipelineContext, provenance: DependencyProvenance | None
) -> RuntimeIdentity:
    """heliaAOT is the installed compiler package the workspace recorded."""
    engine = canonical_engine(EngineType.HELIA_AOT.value)
    record = runtime("helia-aot")
    repository = (
        f"https://github.com/{record.source.repo}"
        if record is not None
        else Unknown("this heliaPROFILER ships no helia-aot runtime record")
    )
    engine_inputs = provenance.workspace.inputs.get("engine") if provenance else None
    source = engine_inputs.get("source") if isinstance(engine_inputs, dict) else None
    if not isinstance(source, dict):
        reason = (
            _NO_PROVENANCE
            if provenance is None
            else Unknown("the workspace records no installed helia-aot package")
        )
        engine_info = ctx.run_metadata.engine
        version = engine_info.version if engine_info and engine_info.version else reason
        return RuntimeIdentity(engine, repository, version, reason)
    version = source.get("version") or Unknown("the installed helia-aot records no version")
    commit = source.get("commit") or Unknown(
        f"helia-aot {source.get('version')} is not a VCS install; it records no commit"
    )
    return RuntimeIdentity(engine, repository, version, commit)


def _kernels(
    ctx: PipelineContext, provenance: DependencyProvenance | None, runtime_id: RuntimeIdentity
) -> KernelIdentity:
    if provenance is None:
        return KernelIdentity(_NO_PROVENANCE, _NO_PROVENANCE, _NO_PROVENANCE, _NO_PROVENANCE)
    found = [m for m in provenance.modules if m.name in CMSIS_NN_PROVIDER_MODULES]
    if len(found) == 1:
        module = found[0]

        def absent(what: str) -> Unknown:
            return Unknown(f"the lock states no {what} for {module.name}")

        return KernelIdentity(
            canonical_kernel(module.project),
            module.url or absent("repository"),
            module.requested_tag or absent("release tag"),
            module.peeled_commit or absent("commit"),
        )
    if not found and _links_reference_kernels(ctx):
        # TFLM's reference kernels are compiled from the runtime's own tree.
        return KernelIdentity(
            CanonicalKernel.REFERENCE, runtime_id.repository, runtime_id.version, runtime_id.commit
        )
    reason = Unknown(f"the dependency lock names {len(found)} CMSIS-NN modules, not one")
    return KernelIdentity(reason, reason, reason, reason)


def _links_reference_kernels(ctx: PipelineContext) -> bool:
    config = ctx.config.engine
    return config.type is EngineType.TFLM and (config.backend or "reference") == "reference"


def _build_facts(ctx: PipelineContext) -> BuildFacts:
    toolchain = ctx.run_metadata.toolchain
    platform = ctx.run_metadata.platform
    no_toolchain = Unknown("the run recorded no toolchain")
    no_clock = Unknown("the run resolved no CPU clock")
    return BuildFacts(
        toolchain=toolchain.compiler if toolchain and toolchain.compiler else no_toolchain,
        toolchain_version=(
            toolchain.compiler_version
            if toolchain and toolchain.compiler_version
            else Unknown("the compiler reported no version")
            if toolchain
            else no_toolchain
        ),
        board=ctx.config.target.board,
        cpu_hz=platform.cpu_clock_mhz * 1_000_000
        if platform and platform.cpu_clock_mhz
        else no_clock,
        clock_profile=platform.cpu_clock_name if platform and platform.cpu_clock_name else no_clock,
        arena_location=(
            ctx.arena_region.value
            if ctx.arena_region is not None
            else Unknown("the memory plan resolved no arena placement")
        ),
        weights_location=(
            ctx.weights_region.value
            if ctx.weights_region is not None
            else Unknown("the memory plan resolved no weights placement")
        ),
        arena_bytes=_arena_bytes(ctx),
    )


def _arena_bytes(ctx: PipelineContext) -> int | Unknown:
    if ctx.config.model.arena_size is not None:
        return ctx.config.model.arena_size
    reported = ctx.pmu_result.meta.arena_size if ctx.pmu_result is not None else None
    if isinstance(reported, int) and not isinstance(reported, bool):
        return reported
    return Unknown("neither the configuration nor the firmware states an arena size")


def _timing_scope(ctx: PipelineContext) -> str:
    # Mirrors main_aot.cc.j2: heliaAOT restores its inputs inside the timed clean window.
    restores = (
        ctx.config.engine.type is EngineType.HELIA_AOT
        and ctx.config.profiling.clean_window_probe is CleanWindowProbe.INFER
    )
    return "restore_and_invoke" if restores else "invoke_only"


def _producer(provenance: DependencyProvenance | None) -> str | Unknown:
    if provenance is None:
        return _NO_PROVENANCE
    version = provenance.workspace.inputs.get("hpx_version")
    return (
        version
        if isinstance(version, str) and version
        else Unknown("the workspace records no hpx_version")
    )


def _build_hash(ctx: PipelineContext, model_sha256: str | Unknown) -> str | Unknown:
    image = next((i for i in ctx.run_metadata.build_images if i.role == "profile"), None)
    if image is None:
        return Unknown("the run recorded no profile image")
    if not image.flash_sha256:
        return Unknown("the build left no flat image to hash")
    if isinstance(model_sha256, Unknown):
        return model_sha256
    lock = _lock_bytes(ctx)
    if isinstance(lock, Unknown):
        return lock
    return build_hash(
        image_sha256=image.flash_sha256, lock=lock, runtime_archive=None, model_sha256=model_sha256
    )


def _lock_bytes(ctx: PipelineContext) -> bytes | Unknown:
    if ctx.dependency_lock_path is None:
        return Unknown("the run kept no dependency lock")
    try:
        return ctx.dependency_lock_path.read_bytes()
    except OSError:
        return Unknown("the run's dependency lock is unreadable")
