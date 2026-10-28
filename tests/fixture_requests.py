"""Build fixed fixtures in tests from a few chosen inputs instead of a full profile config."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from helia_profiler.config import ProfileConfig, load_config
from helia_profiler.engines import EngineType, get_adapter
from helia_profiler.engines.base import PreparedRuntimeAdapter
from helia_profiler.errors import ConfigError
from helia_profiler.fixtures.build import FIXTURE_CAPABILITIES, build_fixed_fixture
from helia_profiler.fixtures.target import (
    FIXTURE_CLOCK_PROFILE,
    FixtureTarget,
    supported_fixture_target,
    validate_fixture_placement,
)
from helia_profiler.fixtures.types import FixedFixture, FixtureBuild, FixtureMethod, TypedFixture
from helia_profiler.placement import Placement
from helia_profiler.runtime.archive import PreparedUpstreamRuntime
from helia_profiler.runtime.prepare import prepared_runtime


class FixtureBackend(StrEnum):
    """Kernel backend a prepared-runtime fixture engine requires."""

    CMSIS_NN = "cmsis_nn"
    HELIA = "helia"


@dataclass(frozen=True)
class FixturePlacement:
    """Where the tensor arena and the model weights live."""

    arena: Placement = Placement.SRAM
    weights: Placement = Placement.MRAM


@dataclass(frozen=True)
class HeliaAotOptions:
    """heliaAOT settings passed to the converter; ``convert_args_json`` is its argument object."""

    prefix: str | None = None
    module_name: str | None = None
    linker_profile: str | None = None
    platform_name: str | None = None
    cmsis_nn_requantize_inline_asm: bool = True
    convert_args_json: str = "{}"

    def engine_config(self) -> dict[str, Any]:
        """The ``engine.config`` mapping the heliaAOT adapter reads."""
        values: dict[str, Any] = {
            "prefix": self.prefix,
            "module_name": self.module_name,
            "linker_profile": self.linker_profile,
            "platform_name": self.platform_name,
        }
        config = {key: value for key, value in values.items() if value is not None}
        config["cmsis_nn_requantize_inline_asm"] = self.cmsis_nn_requantize_inline_asm
        convert_args = json.loads(self.convert_args_json)
        if convert_args:
            config["aot_args"] = convert_args
        return config


@dataclass(frozen=True)
class FixtureBuildRequest:
    """The build inputs a test chooses, plus the work directory.

    A LiteRT or heliaRT request without ``runtime`` uses the archive
    ``hpx runtimes prepare`` built from the engine's default record.
    """

    fixture: FixedFixture | TypedFixture
    method: FixtureMethod
    work_dir: Path
    engine: EngineType
    arena_size: int
    iterations: int
    warmup: int
    backend: FixtureBackend | None = None
    placement: FixturePlacement = field(default_factory=FixturePlacement)
    target: FixtureTarget = field(default_factory=supported_fixture_target)
    runtime: PreparedUpstreamRuntime | None = None
    aot: HeliaAotOptions | None = None
    observe_aot_arenas: bool = False
    observe_ram: bool = False

    def __post_init__(self) -> None:
        if self.engine not in FIXTURE_CAPABILITIES:
            raise ConfigError(f"{self.engine.value} has no fixed-fixture build")
        adapter = get_adapter(self.engine)
        expected = (
            adapter.prepared_runtime_contract[1]
            if isinstance(adapter, PreparedRuntimeAdapter)
            else None
        )
        if self.backend != expected:
            raise ConfigError(f"{self.engine.value} fixtures use backend {expected or 'none'}")
        try:
            self.target.verify()
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        validate_fixture_placement(
            self.placement.arena, self.placement.weights, self.engine, board=self.target.board
        )

    def to_config(self) -> ProfileConfig:
        """The profile configuration this request builds with."""
        engine: dict[str, Any] = {"type": self.engine.value}
        if self.backend is not None:
            engine["backend"] = self.backend.value
        if self.aot is not None:
            engine["config"] = self.aot.engine_config()
        return load_config(
            None,
            {
                "model": {
                    "path": str(self.fixture.model.path),
                    "arena_size": self.arena_size,
                    "arena_location": self.placement.arena.value,
                    "weights_location": self.placement.weights.value,
                },
                "engine": engine,
                "profiling": {"iterations": self.iterations, "warmup": self.warmup},
                "target": {
                    "toolchain": "atfe",
                    "board": self.target.board,
                    "clock": {"cpu": FIXTURE_CLOCK_PROFILE},
                },
                "work_dir": str(self.work_dir),
            },
        )


def build_fixture(request: FixtureBuildRequest, *, compile: bool = True) -> FixtureBuild:
    """Resolve the engine's cached prepared runtime when none is given, then build."""
    if request.runtime is None and isinstance(get_adapter(request.engine), PreparedRuntimeAdapter):
        request = replace(request, runtime=prepared_runtime(request.engine.value).runtime)
    return build_fixed_fixture(
        request.to_config(),
        request.fixture,
        method=request.method,
        runtime=request.runtime,
        compile=compile,
        observe_aot_arenas=request.observe_aot_arenas,
        observe_ram=request.observe_ram,
    )
