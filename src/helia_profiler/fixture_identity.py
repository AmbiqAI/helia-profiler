"""The :class:`RunIdentity` of a fixture build, and of a capture made from it.

Every fact comes from the build record and the files it pins, so a saved
record gives the same identity on any host. A fact the record does not state
is :class:`Unknown`, with the reason.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ._identity_hash import build_hash, digest, measured_facts
from ._semantic_hash import semantic_hash
from .deps.compatibility import CMSIS_NN_PROVIDER_MODULES
from .engines import get_adapter
from .fixture_runtime import FixtureFile
from .fixture_sources import FixtureKernelSource, FixtureRuntimeSource, pinned_sources
from .identity import (
    BuildFacts,
    EnergyFacts,
    KernelIdentity,
    RunFacts,
    RunIdentity,
    RuntimeIdentity,
    Unknown,
    canonical_engine,
    canonical_kernel,
)
from .lifecycle import first_invoke_window
from .runtime_records import runtime

if TYPE_CHECKING:
    from ._fixture_build import FixtureBuild
    from .fixture_capture import FixtureCaptureResult

_NOT_COMPILED = Unknown("the build was not compiled")
#: Capture evidence files that hold a measurement rather than host bookkeeping.
_MEASURED_EVIDENCE = frozenset({"ram.json"})


def fixture_run_identity(
    build: FixtureBuild, capture: FixtureCaptureResult | None = None
) -> RunIdentity:
    """The identity of a fixture build, and of *capture* when one is given."""
    workspace = _workspace(build)
    kernel_source, runtime_source = _sources(build)
    archive = runtime_source.archive_sha256 if runtime_source else None
    return RunIdentity(
        runtime=_runtime(build, runtime_source),
        kernels=_kernels(build, kernel_source),
        prepared_archive_sha256=archive,
        build=_build_facts(build),
        baseline_id=_workspace_fact(workspace, "baseline_id"),
        dependency_lock_sha256=_sha(build.dependency_lock),
        model_sha256=_sha(build.model, Unknown("the record predates pinned models")),
        inputs_sha256=_inputs_sha256(build),
        run=_run_facts(build, capture),
        producer=_workspace_fact(_inputs(workspace), "hpx_version"),
        build_hash=_build_hash(build, archive),
        observation_hash=_observation_hash(capture) if capture is not None else None,
    )


def _run_facts(build: FixtureBuild, capture: FixtureCaptureResult | None) -> RunFacts:
    adapter = get_adapter(build.engine)
    boundary = adapter.instance_boundary
    return RunFacts(
        lifecycle=(build.state_policy.value if build.state_policy else "stateless"),
        iterations=build.iterations,
        warmups=build.warmups,
        timing_scope=build.timing_scope.value,
        instance_boundary=boundary.value,
        first_invoke_initializer=adapter.first_invoke_initializer(build.source_initializer_present),
        first_invoke_window=first_invoke_window(boundary, build.warmups),
        timing_clock="stimer",
        timing_clock_hz=(
            build.timer_hz
            if build.timer_hz is not None
            else Unknown("the record predates this fact")
        ),
        # fixed_fixture.cc.j2 reports one tick count over every timed invocation.
        timing_statistic="total",
        energy=_energy(build, capture),
    )


def _energy(build: FixtureBuild, capture: FixtureCaptureResult | None) -> EnergyFacts | None:
    """The rail-energy method of a power build: its wiring fixes all but the rail.

    The rail comes from the measurement; without one, only the power request names it.
    """
    from .fixture_power import GROSS_RAIL_SCOPE, NO_SUBTRACTION

    wiring = build.power_sync
    if wiring is None:
        return None
    measured = capture.rail_energy if capture is not None else None
    rail = (
        measured.rail if measured is not None else Unknown("the run's power request names the rail")
    )
    return EnergyFacts(GROSS_RAIL_SCOPE, rail, NO_SUBTRACTION, wiring.protocol_version)


def _workspace(build: FixtureBuild) -> dict[str, Any] | Unknown:
    if build.dependency_state is None:
        return _NOT_COMPILED
    try:
        state = json.loads(build.dependency_state.read())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return Unknown("the dependency state is not JSON")
    workspace = state.get("workspace") if isinstance(state, dict) else None
    if not isinstance(workspace, dict):
        return Unknown("the dependency state records no workspace")
    return workspace


def _inputs(workspace: dict[str, Any] | Unknown) -> dict[str, Any] | Unknown:
    if isinstance(workspace, Unknown):
        return workspace
    inputs = workspace.get("inputs")
    return inputs if isinstance(inputs, dict) else Unknown("the workspace records no inputs")


def _workspace_fact(source: dict[str, Any] | Unknown, key: str) -> str | Unknown:
    if isinstance(source, Unknown):
        return source
    value = source.get(key)
    return value if isinstance(value, str) and value else Unknown(f"the workspace records no {key}")


def _sources(build: FixtureBuild) -> tuple[FixtureKernelSource | None, FixtureRuntimeSource | None]:
    """The build's sources, re-derived from its pinned files for records that predate them."""
    if build.kernel_source is not None or build.runtime_source is not None:
        return build.kernel_source, build.runtime_source
    manifest = build.runtime_manifest.read() if build.runtime_manifest else None
    state = build.dependency_state.read() if build.dependency_state else None
    try:
        return pinned_sources(manifest, state)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None, None


def _runtime(build: FixtureBuild, source: FixtureRuntimeSource | None) -> RuntimeIdentity:
    engine = canonical_engine(build.engine.value)
    if source is not None:
        version = source.version or Unknown(f"no runtime record matches {source.commit}")
        return RuntimeIdentity(engine, source.url, version, source.commit)
    if build.engine_source is not None:
        package = build.engine_source
        record = runtime(package.package)
        repository = (
            f"https://github.com/{record.source.repo}"
            if record is not None
            else Unknown(f"no runtime record for {package.package}")
        )
        commit = package.commit or Unknown(f"{package.package} {package.version} records no commit")
        return RuntimeIdentity(engine, repository, package.version, commit)
    reason = _NOT_COMPILED if not build.built else Unknown("the record states no runtime source")
    return RuntimeIdentity(engine, reason, reason, reason)


def _kernels(build: FixtureBuild, source: FixtureKernelSource | None) -> KernelIdentity:
    if source is None:
        reason = _no_kernel_reason(build)
        return KernelIdentity(reason, reason, reason, reason)

    def absent(what: str) -> Unknown:
        return Unknown(f"the {source.name} source states no {what}")

    return KernelIdentity(
        canonical_kernel(source.name),
        source.url or absent("repository"),
        source.version or absent("release tag"),
        source.commit or absent("commit"),
    )


def _no_kernel_reason(build: FixtureBuild) -> Unknown:
    if build.dependency_state is None and build.runtime_manifest is None:
        return _NOT_COMPILED
    if build.dependency_state is None:
        return Unknown("the pinned runtime manifest names no kernel library")
    try:
        modules = json.loads(build.dependency_state.read()).get("modules") or []
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return Unknown("the dependency state is not JSON")
    count = sum(
        1 for m in modules if isinstance(m, dict) and m.get("name") in CMSIS_NN_PROVIDER_MODULES
    )
    return Unknown(f"the dependency state names {count} CMSIS-NN modules, not one")


def _build_facts(build: FixtureBuild) -> BuildFacts:
    toolchain = build.toolchain
    unrecorded = Unknown("the record predates this fact")
    no_toolchain = Unknown("the record states no toolchain") if build.built else _NOT_COMPILED
    return BuildFacts(
        toolchain=toolchain.compiler if toolchain and toolchain.compiler else no_toolchain,
        toolchain_version=(
            toolchain.compiler_version if toolchain and toolchain.compiler_version else no_toolchain
        ),
        board=build.target.board,
        cpu_hz=build.cpu_hz if build.cpu_hz is not None else unrecorded,
        clock_profile=build.clock_profile or unrecorded,
        arena_location=build.arena_location.value if build.arena_location else unrecorded,
        weights_location=build.weights_location.value if build.weights_location else unrecorded,
        arena_bytes=build.arena_capacity if build.arena_capacity is not None else unrecorded,
    )


def _sha(file: FixtureFile | None, absent: Unknown = _NOT_COMPILED) -> str | Unknown:
    return file.sha256 if file is not None else absent


def _inputs_sha256(build: FixtureBuild) -> str | Unknown:
    if not build.inputs:
        return Unknown("the record predates pinned inputs")
    return digest([[io.tensor.name, io.data.sha256] for io in build.inputs])


def _build_hash(build: FixtureBuild, archive: str | None) -> str | Unknown:
    pinned = (build.flat_binary, build.dependency_lock, build.model)
    if any(file is None for file in pinned):
        return (
            _NOT_COMPILED if not build.built else Unknown("the record pins no image, lock or model")
        )
    image, lock, model = pinned
    assert image is not None and lock is not None and model is not None
    return build_hash(
        image_sha256=image.sha256,
        lock=lock.read(),
        runtime_archive=archive,
        model_sha256=model.sha256,
    )


def _observation_hash(capture: FixtureCaptureResult) -> str:
    facts = measured_facts(capture, _file_hash)
    # Evidence files record host timings and paths; only the RAM observation is measured.
    facts["artifacts"] = sorted(
        item.sha256 for item in capture.artifacts if item.path.name in _MEASURED_EVIDENCE
    )
    # An error's text can name host paths; the state and status carry the outcome.
    facts["error"] = capture.error is not None
    return semantic_hash("observation", facts)


def _file_hash(value: Any) -> Any:
    return value.sha256 if isinstance(value, FixtureFile) else NotImplemented
