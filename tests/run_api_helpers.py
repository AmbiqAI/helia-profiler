"""A fixture build as an ATfE build records it, and the frozen plan it was built from."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import helia_profiler as hpx
from helia_profiler._fixture_record import compiled_fixture_identity
from helia_profiler.compiled_build import CompiledBuild, _binding
from helia_profiler.fixture import FixtureBuild
from helia_profiler.frozen_run import FrozenRun
from helia_profiler.lifecycle import Lifecycle
from helia_profiler.results.models import ToolchainInfo

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
) -> FrozenRun:
    """The plan *build* was compiled from, frozen with its model copied to *model*."""
    assert build.model is not None and build.arena_location and build.weights_location
    model.write_bytes(build.model.read())
    return (
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
        .freeze()
    )


def compiled(plan: FrozenRun, build: FixtureBuild, lifecycle: Lifecycle) -> CompiledBuild:
    """*build* bound to *plan* and *lifecycle*, as :func:`helia_profiler.build` binds it."""
    return CompiledBuild(plan, build, lifecycle, _binding(plan, lifecycle, build))
