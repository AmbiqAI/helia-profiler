"""Build once, run later: a frozen plan's compiled firmware as a durable record.

:func:`build` compiles a plan for fixed inputs and returns a
:class:`CompiledBuild`, which can be saved, verified and loaded on another
host with :func:`load_build`. :func:`run_build` captures from it without
rebuilding and returns a :class:`RunResult` with the run's identity, its
measurements and, when asked, the comparison of its outputs with reference
outputs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from .errors import ConfigError
from .lifecycle import Lifecycle, lifecycle_invocations, require_lifecycle

if TYPE_CHECKING:
    from ._fixture_build import FixtureBuild, FixtureMethod, TypedFixture
    from .expect import Expect, Verification
    from .fixture_capture import FixtureCaptureGuard, FixtureCaptureResult
    from .fixture_runtime import FixtureFile, PreparedUpstreamRuntime
    from .frozen_run import FrozenRun
    from .identity import RunIdentity

COMPILED_BUILD_SCHEMA = "hpx.compiled-build"
COMPILED_BUILD_SCHEMA_VERSION = 2

_DOCUMENT_KEYS = frozenset({"schema", "schema_version", "plan", "lifecycle", "binding", "build"})


@dataclass(frozen=True)
class CompiledBuild:
    """A frozen plan and the firmware compiled from it, verifiable on any host.

    ``binding`` ties the build to the plan and lifecycle it was compiled
    from; :func:`build` computes it and :meth:`verify` recomputes it.
    """

    plan: FrozenRun
    build: FixtureBuild
    lifecycle: Lifecycle
    binding: str

    @property
    def identity(self) -> RunIdentity:
        """The build's identity, with its plan's ``intent_hash``; ``observation_hash`` is ``None`` until it runs."""
        from .fixture_identity import fixture_run_identity

        return self._identity(fixture_run_identity(self.build))

    def _identity(self, identity: RunIdentity) -> RunIdentity:
        return replace(
            identity,
            run=replace(identity.run, lifecycle=self.lifecycle.value),
            intent_hash=self.plan.intent_hash,
        )

    def verify(self) -> None:
        """Re-check the build against its pinned files and its plan; raise on any mismatch.

        The build's files must match their hashes, the plan its intent hash,
        and the binding the plan, lifecycle and build. The facts the build
        records must be the ones the plan states: engine, board, model,
        toolchain and its version, arena and weights placement, arena size,
        clock profile and the lifecycle's invocation counts.

        Raises:
            ConfigError: Naming the first fact that does not match.
        """
        from ._fixture_record import verify_fixture_build
        from .frozen_run import FrozenRun

        try:
            verify_fixture_build(self.build)
        except (ValueError, OSError) as exc:
            raise ConfigError(f"Compiled build does not verify: {exc}") from exc
        FrozenRun.from_dict(self.plan.to_dict())
        if self.binding != _binding(self.plan, self.lifecycle, self.build):
            raise ConfigError(
                "Compiled build's binding does not match its plan and lifecycle",
                hint="A compiled build runs only with the plan it was built from; build it again.",
            )
        model = self.build.model
        if model is None or model.sha256 != self.plan.model.sha256:
            raise ConfigError(
                "Compiled build's model does not match its plan "
                f"(sha256 {self.plan.model.sha256[:12]}…)"
            )
        for name, planned, built in self._facts(model):
            if planned != built:
                raise ConfigError(
                    f"Compiled build's {name} {built!r} differs from its plan's {planned!r}"
                )
        toolchain = self.build.toolchain
        version = self.plan.toolchain.version
        if (
            toolchain is None
            or version is None
            or toolchain.compiler_version not in version.split(" | ")
        ):
            raise ConfigError(
                "Compiled build's toolchain version "
                f"{toolchain.compiler_version if toolchain else None!r} is not its plan's {version!r}"
            )
        try:
            profiling = self.plan.settings["profiling"]
            expected = lifecycle_invocations(
                self.lifecycle, profiling["iterations"], profiling["warmup"]
            )
        except (KeyError, TypeError) as exc:
            raise ConfigError(f"Compiled build's plan has no profiling counts: {exc}") from exc
        if (self.build.iterations, self.build.warmups) != expected:
            raise ConfigError(
                f"Compiled build runs {self.build.iterations} iterations and "
                f"{self.build.warmups} warmups; lifecycle {self.lifecycle.value} of its plan "
                f"runs {expected[0]} and {expected[1]}"
            )

    def _facts(self, model: FixtureFile) -> list[tuple[str, object, object]]:
        """Each fact as (name, what the plan states, what the build records)."""
        settings = self.plan.settings
        target = settings.get("target", {})
        planned_model = settings.get("model", {})
        build = self.build
        return [
            ("engine", settings.get("engine", {}).get("type"), build.engine.value),
            ("board", target.get("board"), build.target.board),
            ("toolchain", target.get("toolchain"), build.toolchain and build.toolchain.compiler),
            ("arena location", planned_model.get("arena_location"), _value(build.arena_location)),
            (
                "weights location",
                planned_model.get("weights_location"),
                _value(build.weights_location),
            ),
            ("arena size", planned_model.get("arena_size"), build.arena_capacity),
            ("clock profile", target.get("clock", {}).get("cpu"), build.clock_profile),
            ("model size", self.plan.model.size_bytes, model.path.stat().st_size),
        ]

    def save(self, path: str | Path, *, roots: Mapping[str, Path]) -> Path:
        """Write the build as JSON with paths relative to *roots* and return its path."""
        from ._fixture_record import dump_fixture_build

        self.verify()
        try:
            build = json.loads(dump_fixture_build(self.build, roots=roots))
        except ValueError as exc:
            raise ConfigError(f"Cannot save compiled build: {exc}") from exc
        document = {
            "schema": COMPILED_BUILD_SCHEMA,
            "schema_version": COMPILED_BUILD_SCHEMA_VERSION,
            "plan": self.plan.to_dict(),
            "lifecycle": self.lifecycle.value,
            "binding": self.binding,
            "build": build,
        }
        target = Path(path).expanduser()
        text = json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
        except OSError as exc:
            raise ConfigError(f"Cannot save compiled build to {target}: {exc}") from exc
        return target

    @classmethod
    def load(cls, path: str | Path, *, roots: Mapping[str, Path]) -> CompiledBuild:
        """Read and verify a build written by :meth:`save`, resolving paths under *roots*."""
        from ._fixture_record import _read_json, load_fixture_build
        from .frozen_run import FrozenRun

        source = Path(path).expanduser()
        try:
            data = _read_json(source.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigError(f"Compiled build not found: {source}") from exc
        except (OSError, ValueError, RecursionError) as exc:
            raise ConfigError(f"Cannot read compiled build {source}: {exc}") from exc
        document = _document(data)
        try:
            lifecycle = Lifecycle(document["lifecycle"])
        except (ValueError, TypeError) as exc:
            raise ConfigError(
                f"Malformed compiled build: unknown lifecycle {document['lifecycle']!r}"
            ) from exc
        if not isinstance(document["binding"], str):
            raise ConfigError("Malformed compiled build: binding is not a string")
        plan = FrozenRun.from_dict(document["plan"])
        try:
            build = load_fixture_build(json.dumps(document["build"]), roots=roots)
        except ValueError as exc:
            raise ConfigError(f"Malformed compiled build: {exc}") from exc
        except OSError as exc:
            raise ConfigError(
                f"Compiled build file is missing under the given roots: {exc}",
                hint="Pass roots= naming where the record's files now live.",
            ) from exc
        record = cls(plan, build, lifecycle, document["binding"])
        record.verify()
        return record

    def run(
        self,
        *,
        guard: FixtureCaptureGuard,
        jlink_serial: str,
        evidence_dir: Path,
        settle_seconds: float,
        expect: Expect | None = None,
        expected_duration_s: float | None = None,
    ) -> RunResult:
        """Flash the recorded image, capture one run and compare outputs when *expect* is given.

        A failed capture comes back with ``capture.state == "failure"`` and no
        verification; errors raised while capturing propagate.
        """
        from ._fixture_record import fixture_capture_request
        from .expect import compare_fixture_outputs
        from .fixture_capture import capture_fixture
        from .fixture_identity import fixture_run_identity

        self.verify()
        if expect is not None and expect.source == "build" and not self.build.outputs:
            raise ConfigError(
                "This compiled build carries no expected outputs",
                hint="Use Expect.host_litert() or Expect.from_files().",
            )
        try:
            request = fixture_capture_request(
                self.build,
                jlink_serial=jlink_serial,
                evidence_dir=evidence_dir,
                settle_seconds=settle_seconds,
                expected_duration_s=expected_duration_s,
            )
        except ValueError as exc:
            raise ConfigError(f"Cannot capture from this compiled build: {exc}") from exc
        capture = capture_fixture(request, guard=guard)
        identity = self._identity(fixture_run_identity(self.build, capture))
        verification = (
            compare_fixture_outputs(self.build, capture, expect)
            if expect is not None and capture.state == "success"
            else None
        )
        return RunResult(self, capture, identity, verification)


@dataclass(frozen=True)
class RunResult:
    """One capture from a :class:`CompiledBuild`."""

    record: CompiledBuild
    capture: FixtureCaptureResult
    identity: RunIdentity
    verification: Verification | None

    @property
    def succeeded(self) -> bool:
        return self.capture.state == "success"


def build(
    plan: FrozenRun,
    fixture: TypedFixture,
    *,
    method: FixtureMethod,
    lifecycle: Lifecycle = Lifecycle.CARRY,
    runtime: PreparedUpstreamRuntime | None = None,
    model: str | Path | None = None,
    host: Mapping[str, object] | None = None,
    observe_aot_arenas: bool = False,
    observe_ram: bool = False,
) -> CompiledBuild:
    """Compile *plan* for *fixture*'s pinned inputs and return the compiled build.

    *lifecycle* must be one the plan's engine implements; it sets the timed
    iterations and warmups the firmware runs. *host* supplies host-local
    settings, such as ``{"work_dir": ...}``, as :meth:`FrozenRun.config` does.
    """
    from ._fixture_build import build_fixed_fixture

    if fixture.model.sha256 != plan.model.sha256:
        raise ConfigError(
            f"Fixture model {fixture.model.path} does not match the plan's model "
            f"(sha256 {plan.model.sha256[:12]}…)",
            hint="Build the fixture from the model the plan was frozen against.",
        )
    lifecycle = Lifecycle(lifecycle)
    if method.power_sync is not None:
        if lifecycle is Lifecycle.SINGLE:
            raise ConfigError(
                "A single invocation is too short for a power capture",
                hint="Use Lifecycle.CARRY for a power run.",
            )
        raise ConfigError(
            "run_build() takes no power request yet",
            hint="Build and capture power fixtures with helia_profiler.fixture for now.",
        )
    config = plan.config(model=model, host=host)
    require_lifecycle(config.engine.type, lifecycle)
    iterations, warmup = lifecycle_invocations(
        lifecycle, config.profiling.iterations, config.profiling.warmup
    )
    config = replace(
        config, profiling=replace(config.profiling, iterations=iterations, warmup=warmup)
    )
    fixture_build = build_fixed_fixture(
        config,
        fixture,
        method=method,
        runtime=runtime,
        compile=True,
        observe_aot_arenas=observe_aot_arenas,
        observe_ram=observe_ram,
    )
    return CompiledBuild(plan, fixture_build, lifecycle, _binding(plan, lifecycle, fixture_build))


def _binding(plan: FrozenRun, lifecycle: Lifecycle, build: FixtureBuild) -> str:
    """SHA-256 tying *build* to the plan and lifecycle it was compiled from.

    It covers the plan's intent hash, the lifecycle and the build's content:
    every fact and pinned file hash the build records (image, dependency
    lock, model, inputs, runtime manifest, method and observations), but not
    its work directory or any other host path.
    """
    from ._fixture_record import fixture_build_content

    payload = {
        "plan": plan.intent_hash,
        "lifecycle": lifecycle.value,
        "build": fixture_build_content(build),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _value(member: StrEnum | None) -> str | None:
    return member.value if member is not None else None


def load_build(path: str | Path, *, roots: Mapping[str, Path]) -> CompiledBuild:
    """Read and verify a compiled build written by :meth:`CompiledBuild.save`."""
    return CompiledBuild.load(path, roots=roots)


def run_build(
    record: CompiledBuild,
    *,
    guard: FixtureCaptureGuard,
    jlink_serial: str,
    evidence_dir: Path,
    settle_seconds: float,
    expect: Expect | None = None,
    expected_duration_s: float | None = None,
) -> RunResult:
    """Flash *record*'s image, capture one run and compare outputs; see :meth:`CompiledBuild.run`."""
    return record.run(
        guard=guard,
        jlink_serial=jlink_serial,
        evidence_dir=evidence_dir,
        settle_seconds=settle_seconds,
        expect=expect,
        expected_duration_s=expected_duration_s,
    )


def _document(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ConfigError("A compiled build must be a JSON object.")
    if data.get("schema") != COMPILED_BUILD_SCHEMA:
        raise ConfigError(f"Not a compiled build: schema {data.get('schema')!r}")
    version = data.get("schema_version")
    if type(version) is not int or version != COMPILED_BUILD_SCHEMA_VERSION:
        raise ConfigError(f"Unsupported compiled build version: {version!r}")
    if data.keys() != _DOCUMENT_KEYS:
        raise ConfigError(
            f"Malformed compiled build: fields {sorted(data)}, expected {sorted(_DOCUMENT_KEYS)}"
        )
    return data
