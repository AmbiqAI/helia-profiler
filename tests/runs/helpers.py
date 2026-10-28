"""A fixture build as an ATfE build records it, and the frozen plan it was built from."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import helia_profiler as hpx
from helia_profiler import FixtureBuild
from helia_profiler.fixtures.record import compiled_fixture_identity
from helia_profiler.lifecycle import Lifecycle
from helia_profiler.results.models import ToolchainInfo
from helia_profiler.runs.compiled import CompiledBuild, _binding
from helia_profiler.runs.plan import FrozenRun

ATFE_VERSION = "clang version 22.1.0"
#: What ``clang --version`` prints for the ATfE the plans are frozen against.
ATFE_OUTPUT = f"{ATFE_VERSION}\nTarget: arm-none-eabi\nThread model: single\n"


def reseal(build: FixtureBuild, **change: object) -> FixtureBuild:
    changed = replace(build, **change)
    return replace(changed, build_identity=compiled_fixture_identity(changed))


def atfe_build(build: FixtureBuild, **change: object) -> FixtureBuild:
    """*build* with the toolchain facts an ATfE build records."""
    return reseal(build, toolchain=ToolchainInfo("atfe", ATFE_VERSION, "3.31"), **change)


def plan_for(
    build: FixtureBuild,
    model: Path,
    *,
    engine: str | None = None,
    board: str | None = None,
    clock: str = "lp",
    iterations: int = 100,
    warmup: int = 5,
    power: bool = False,
) -> FrozenRun:
    """The plan *build* was compiled from, frozen with its model copied to *model*."""
    assert build.model is not None and build.arena_location and build.weights_location
    model.write_bytes(build.model.read())
    session = (
        hpx.Session()
        .with_model(
            model,
            arena_size=build.arena_capacity,
            arena_location=build.arena_location.value,
            weights_location=build.weights_location.value,
        )
        .with_engine(engine or build.engine.value)
        .with_target(board=board or build.target.board, toolchain="atfe", clock={"cpu": clock})
        .with_profiling(iterations=iterations, warmup=warmup)
    )
    return (session.with_power(enabled=True) if power else session).freeze()


def compiled(plan: FrozenRun, build: FixtureBuild, lifecycle: Lifecycle) -> CompiledBuild:
    """*build* bound to *plan* and *lifecycle*, as :func:`helia_profiler.build` binds it."""
    return CompiledBuild(plan, build, lifecycle, _binding(plan, lifecycle, build))


def linking(
    build: FixtureBuild,
    root: Path,
    engine: str,
    runtime: str,
    kernels: str,
    *,
    aot_commit: str | None = None,
) -> FixtureBuild:
    """*build* as an *engine* build linking *runtime* and kernels at commit *kernels*.

    *runtime* is the runtime commit, or for heliaAOT the installed version
    (with *aot_commit* when it was a VCS install).
    """
    import json
    from dataclasses import asdict

    from helia_profiler.engines import EngineType
    from helia_profiler.engines.base import EngineSource
    from helia_profiler.fixtures.build import FIXTURE_CAPABILITIES
    from helia_profiler.fixtures.sources import pinned_sources
    from helia_profiler.results.dependencies import ContentDigest, DependencyModule
    from tests.fixtures_api.test_fixture_record import _pin
    from tests.fixtures_api.test_heliart_fixture import runtime as prepared

    selected = EngineType(engine)
    change: dict[str, object] = {
        "engine": selected,
        "capabilities": (("int8", FIXTURE_CAPABILITIES[selected]["int8"]),),
    }
    if selected is EngineType.HELIA_AOT:
        assert build.dependency_lock is not None
        state = {
            "lock": {"sha256": {"value": build.dependency_lock.sha256}},
            "modules": [
                asdict(
                    DependencyModule(
                        name="nsx-cmsis-nn",
                        project="ns-cmsis-nn",
                        kind="git",
                        requested_ref=kernels,
                        requested_tag="v7.47.0",
                        peeled_commit=kernels,
                        content_hash=ContentDigest("sha256", "f" * 64),
                        url="https://github.com/AmbiqAI/ns-cmsis-nn.git",
                        vendored_at="modules/ns-cmsis-nn",
                    )
                )
            ],
        }
        pinned = _pin(root / "evidence" / "linked-state.json", json.dumps(state).encode())
        kernel, _ = pinned_sources(None, pinned.read())
        change.update(
            dependency_state=pinned,
            kernel_source=kernel,
            engine_source=EngineSource("helia-aot", runtime, aot_commit),
        )
    else:
        names = (
            ("helia-rt", "ns-cmsis-nn")
            if selected is EngineType.HELIA_RT
            else ("tflite-micro", "cmsis-nn")
        )
        providers = {
            name: {"url": f"https://example.invalid/{name}", "revision": revision}
            for name, revision in zip(names, (runtime, kernels), strict=True)
        }
        archive = prepared(
            root / "runtime",
            schema=2 if selected is EngineType.HELIA_RT else 1,
            providers=providers,
        )
        kernel, source = pinned_sources(archive.manifest.read(), None)
        change.update(
            runtime_manifest=archive.manifest, kernel_source=kernel, runtime_source=source
        )
    return reseal(build, **change)
