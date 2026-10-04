"""Typed fixed-fixture build request with a path-free intent identity."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ._fixture_build import (
    FixedFixture,
    FixtureBackend,
    FixtureBuild,
    FixtureMethod,
    TypedFixture,
    _build,
)
from .config import ProfileConfig, load_config
from .engines import EngineType
from .errors import ConfigError
from .fixture_runtime import PreparedUpstreamRuntime
from .fixture_target import FIXTURE_CLOCK_PROFILE, FixtureTarget, supported_fixture_target
from .placement import Placement
from .prepared_runtimes import prepared_runtime

#: Backends the prepared-runtime engines require; heliaAOT takes none.
_ENGINE_BACKENDS = {
    EngineType.TFLM: FixtureBackend.CMSIS_NN,
    EngineType.HELIA_RT: FixtureBackend.HELIA,
    EngineType.HELIA_AOT: None,
}


@dataclass(frozen=True)
class FixturePlacement:
    """Where the tensor arena and the model weights live."""

    arena: Placement = Placement.SRAM
    weights: Placement = Placement.MRAM


@dataclass(frozen=True)
class HeliaAotOptions:
    """heliaAOT settings the profiler passes to the converter.

    ``convert_args_json`` is heliaAOT's own convert-argument object as JSON; it
    is stored in canonical form so equal settings hash equally.
    """

    prefix: str | None = None
    module_name: str | None = None
    linker_profile: str | None = None
    platform_name: str | None = None
    cmsis_nn_requantize_inline_asm: bool = True
    convert_args_json: str = "{}"

    def __post_init__(self) -> None:
        try:
            convert_args = json.loads(self.convert_args_json)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"heliaAOT convert arguments are not JSON: {exc}") from exc
        if not isinstance(convert_args, dict):
            raise ConfigError("heliaAOT convert arguments must be a JSON object")
        canonical = json.dumps(convert_args, sort_keys=True, separators=(",", ":"))
        object.__setattr__(self, "convert_args_json", canonical)

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
    """The build inputs a caller chooses, plus the work directory.

    The installed profiler and engine are not part of the request: the source
    closure pins the profiler, and ``FixtureBuild.engine_source`` records the
    engine package. A heliaRT request without ``runtime`` uses the archive
    ``hpx runtimes prepare helia-rt`` built from the default helia-rt record.
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

    def __post_init__(self) -> None:
        if self.engine not in _ENGINE_BACKENDS:
            raise ConfigError(f"{self.engine.value} has no fixed-fixture build")
        if self.backend != _ENGINE_BACKENDS[self.engine]:
            expected = _ENGINE_BACKENDS[self.engine]
            raise ConfigError(
                f"{self.engine.value} fixtures use backend {expected.value if expected else 'none'}"
            )
        if self.aot is not None and self.engine is not EngineType.HELIA_AOT:
            raise ConfigError("heliaAOT options apply to heliaAOT fixtures only")
        if self.runtime is None and self.engine is EngineType.HELIA_RT:
            object.__setattr__(self, "runtime", prepared_runtime(self.engine.value).runtime)
        try:
            self.target.verify()
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        if self.placement != FixturePlacement():
            raise ConfigError(
                f"Fixture placement not qualified: arena {self.placement.arena.value}, "
                f"weights {self.placement.weights.value}"
            )

    @property
    def intent_identity(self) -> str:
        """sha256 over every build-deciding input, with files as content hashes and no paths."""
        intent = {
            "fixture": self.fixture.identity,
            "method": self.method.timing_scope.value,
            "engine": self.engine.value,
            "backend": self.backend.value if self.backend else None,
            "arena_size": self.arena_size,
            "iterations": self.iterations,
            "warmup": self.warmup,
            "placement": [self.placement.arena.value, self.placement.weights.value],
            "target": asdict(self.target),
            "runtime": self.runtime.manifest.sha256 if self.runtime else None,
            "aot": asdict(self.aot or HeliaAotOptions())
            if self.engine is EngineType.HELIA_AOT
            else None,
            "observe_aot_arenas": self.observe_aot_arenas,
        }
        return hashlib.sha256(json.dumps(intent, sort_keys=True).encode()).hexdigest()

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
    """Render or compile the requested fixture; its intent identity is the request's."""
    return _build(
        request.to_config(),
        request.fixture,
        method=request.method,
        runtime=request.runtime,
        compile=compile,
        observe_aot_arenas=request.observe_aot_arenas,
        intent_identity=request.intent_identity,
    )
