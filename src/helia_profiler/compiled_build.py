"""Build once, run later: a frozen plan's compiled firmware as a durable record.

:func:`build` compiles a plan for fixed inputs and returns a
:class:`CompiledBuild`, which can be saved, verified and loaded on another
host with :func:`load_build`. :func:`run_build` captures from it without
rebuilding and returns a :class:`RunResult` with the run's identity, its
measurements and, when asked, the comparison of its outputs with reference
outputs.

Power takes two routes. A build compiled with a ``power_sync`` method measures
the gross MCU-rail energy of its timed loop when run with a
:class:`~helia_profiler.FixtureRailPowerRequest`; any other build can run its
timing capture through a configured supply with a
:class:`~helia_profiler.FixtureBoardPowerRequest`, which measures no energy.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, TypeAlias

from ._files import write_text_atomically
from ._semantic_hash import require, semantic_hash
from .errors import CaptureError, ConfigError
from .lifecycle import Lifecycle, lifecycle_invocations, require_lifecycle

if TYPE_CHECKING:
    from ._fixture_build import FixtureBuild, FixtureMethod, TypedFixture
    from .expect import Expect, Verification
    from .fixture_capture import FixtureCaptureGuard, FixtureCaptureResult
    from .fixture_power import (
        FixtureBoardPowerRequest,
        FixturePowerGuard,
        FixturePowerSync,
        FixtureRailEnergy,
        FixtureRailPowerRequest,
    )
    from .fixture_runtime import FixtureFile, PreparedUpstreamRuntime
    from .fixture_target import FixtureTarget
    from .frozen_run import FrozenRun
    from .identity import RunIdentity

    _PowerRequest: TypeAlias = FixtureRailPowerRequest | FixtureBoardPowerRequest | None

COMPILED_BUILD_SCHEMA = "hpx.compiled-build"
COMPILED_BUILD_SCHEMA_VERSION = 3

_DOCUMENT_KEYS = frozenset({"schema", "schema_version", "plan", "lifecycle", "binding", "build"})

#: The rail-energy route the run API admits (helia-profiler#558): the gross MCU
#: rail over the timed restore-and-invoke loop of a carried heliaAOT or LiteRT
#: build on the Apollo510 EVB.
_POWER_ENGINES = ("helia-aot", "litert")
_POWER_BOARDS = ("apollo510_evb",)
_POWER_RAIL = "MCU"


@dataclass(frozen=True)
class CompiledBuild:
    """A frozen plan and the firmware compiled from it, verifiable on any host.

    ``binding``, an ``hpx.binding`` semantic hash, ties the build to the plan
    and lifecycle it was compiled from; :func:`build` computes it and
    :meth:`verify` recomputes it.
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

    def _identity(self, identity: RunIdentity, rail: str | None = None) -> RunIdentity:
        """*identity* under this build's lifecycle and plan, naming the requested *rail*."""
        from .identity import EnergyFacts

        run = replace(identity.run, lifecycle=self.lifecycle.value)
        if rail is not None and isinstance(run.energy, EnergyFacts):
            run = replace(run, energy=replace(run.energy, rail=rail))
        return replace(identity, run=run, intent_hash=self.plan.intent_hash)

    def verify(self) -> None:
        """Re-check the build against its pinned files and its plan; raise on any mismatch.

        The build's files must match their hashes, the plan its intent hash,
        and the binding the plan, lifecycle and build. The facts the build
        records must be the ones the plan states: engine, board, model,
        toolchain and its version, arena and weights placement, arena size,
        clock profile and the lifecycle's invocation counts.

        The runtime version and commit and the kernel commit are compared
        wherever both the plan and the build state one; a build or plan
        that states none of a fact is not compared on it. heliaRT and
        heliaAOT compare with the plan's pins. LiteRT compares with this HPX's
        prepared LiteRT record, because the plan pins the modules an ordinary
        profile builds from source, which a fixture build does not link.

        Raises:
            ConfigError: Naming the first fact that does not match.
        """
        from ._fixture_record import verify_fixture_build
        from .frozen_run import FrozenRun

        try:
            verify_fixture_build(self.build)
        except (ValueError, OSError) as exc:
            raise ConfigError(f"Compiled build does not verify: {exc}") from exc
        # Round-trips the plan to recheck its intent hash against its settings.
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
        for name, planned, built in (*self._facts(model), *self._linked_facts()):
            if planned != built:
                raise ConfigError(
                    f"Compiled build's {name} {built!r} differs from its plan's {planned!r}"
                )
        toolchain = self.build.toolchain
        version = self.plan.toolchain.version
        if (
            toolchain is None
            or version is None
            or toolchain.compiler_version not in self.plan.toolchain.lines
        ):
            raise ConfigError(
                "Compiled build's toolchain version "
                f"{toolchain.compiler_version if toolchain else None!r} is not its plan's {version!r}",
                hint="Build with the compiler version the plan was frozen with, or freeze again.",
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

    def _linked_facts(self) -> list[tuple[str, object, object]]:
        """The runtime and kernel facts both the plan and the build state."""
        source = self.build.runtime_source
        expected = _linked_runtime(self.plan, source.version if source is not None else None)
        if expected is None:
            return []
        build = self.build
        runtime_source, engine_source = build.runtime_source, build.engine_source
        built = (
            (runtime_source.version, runtime_source.commit)
            if runtime_source is not None
            else (engine_source.version, engine_source.commit)
            if engine_source is not None
            else (None, None)
        )
        kernel = build.kernel_source.commit if build.kernel_source is not None else None
        facts = [
            ("runtime version", expected.version, built[0]),
            ("runtime commit", expected.commit, built[1]),
            ("kernel commit", expected.kernels_commit, kernel),
        ]
        return [fact for fact in facts if fact[1] is not None and fact[2] is not None]

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
            write_text_atomically(target, text)
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
        try:
            require(document["binding"], "binding")
        except ValueError as exc:
            raise ConfigError(
                f"Malformed compiled build: {exc}",
                hint="The build was saved by another heliaPROFILER or edited; build it again.",
            ) from exc
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
        compiled = cls(plan, build, lifecycle, document["binding"])
        compiled.verify()
        return compiled

    def run(
        self,
        *,
        guard: FixtureCaptureGuard,
        jlink_serial: str,
        evidence_dir: Path,
        settle_seconds: float,
        expect: Expect | None = None,
        expected_duration_s: float | None = None,
        power: FixtureRailPowerRequest | FixtureBoardPowerRequest | None = None,
        power_guard: FixturePowerGuard | None = None,
    ) -> RunResult:
        """Flash the recorded image, capture one run and compare outputs when *expect* is given.

        A failed capture comes back with ``capture.state == "failure"`` and no
        verification; errors raised while capturing propagate.

        *power* selects the instrument: a :class:`FixtureRailPowerRequest`
        measures the MCU rail of a build compiled with the same
        ``power_sync`` wiring, and a :class:`FixtureBoardPowerRequest`
        powers the board through a configured supply for a timing capture
        of a build without power wiring. Either needs *power_guard*, the
        station's authority over that instrument and rail; *guard* keeps
        its authority over the board and probe. Every refusal comes before
        the device is touched.

        Raises:
            ConfigError: The build does not verify, *expect* cannot be
                compared, or *power* does not fit the build or the run API's
                rail-energy route.
            CaptureError: The device reports counts other than the build's.
        """
        from ._fixture_record import fixture_capture_request
        from .expect import check_fixture_expectation, compare_fixture_outputs
        from .fixture_capture import capture_fixture
        from .fixture_identity import fixture_run_identity
        from .fixture_power import FixtureRailPowerRequest

        self.verify()
        self._check_power(power, power_guard, settle_seconds)
        if expect is not None and expect.source == "build" and not self.build.outputs:
            raise ConfigError(
                "This compiled build carries no expected outputs",
                hint="Use Expect.host_litert() or Expect.from_files().",
            )
        if expect is not None:
            check_fixture_expectation(self.build, expect)
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
        rail = power if isinstance(power, FixtureRailPowerRequest) else None
        if rail is not None:
            request = replace(request, rail_power=rail)
        elif power is not None:
            request = replace(request, board_power=power)
        capture = capture_fixture(
            request,
            guard=guard if power_guard is None else _PowerCaptureGuard(guard, power_guard),
        )
        timing = capture.timing
        if capture.state == "success" and (
            timing is None
            or (timing.iterations, timing.warmups) != (self.build.iterations, self.build.warmups)
        ):
            counts = "none" if timing is None else f"{timing.iterations} and {timing.warmups}"
            raise CaptureError(
                f"The device reported {counts} timed iterations and warmups; this build "
                f"runs {self.build.iterations} and {self.build.warmups}",
                hint=f"The capture's evidence is in {evidence_dir}.",
            )
        identity = self._identity(
            fixture_run_identity(self.build, capture), rail.rail if rail is not None else None
        )
        verification = (
            compare_fixture_outputs(self.build, capture, expect)
            if expect is not None and capture.state == "success"
            else None
        )
        return RunResult(self, capture, identity, verification, power)

    def _check_power(
        self,
        power: _PowerRequest,
        power_guard: FixturePowerGuard | None,
        settle_seconds: float,
    ) -> None:
        """Refuse a power request this build or the run API's rail-energy route cannot take."""
        from .fixture_capture import _RAIL_SETTLE_LIMIT_S, _SETTLE_LIMIT_S
        from .fixture_power import FixtureBoardPowerRequest, FixtureRailPowerRequest

        wiring = self.build.power_sync
        if power is None:
            if power_guard is not None:
                raise ConfigError(
                    "A power_guard was given without a power request",
                    hint="Pass power= with the instrument and rail it authorizes, or drop it.",
                )
            if wiring is not None:
                raise ConfigError(
                    "This build is compiled for rail energy and needs a FixtureRailPowerRequest",
                    hint="Its firmware waits for the instrument's GO before its timed loop.",
                )
            return
        if isinstance(power, FixtureBoardPowerRequest):
            if wiring is not None:
                raise ConfigError(
                    "A supply-only FixtureBoardPowerRequest measures no energy; "
                    "this build is compiled for rail energy",
                    hint="Pass a FixtureRailPowerRequest, or build without power_sync.",
                )
        elif isinstance(power, FixtureRailPowerRequest):
            if wiring is None:
                raise ConfigError(
                    "This build has no power wiring to measure rail energy with",
                    hint="Build with FixtureMethod(..., power_sync=FixturePowerSync(...)).",
                )
            requested = power.firmware_wiring()
            if replace(requested, protocol_version=wiring.protocol_version) != wiring:
                raise ConfigError(
                    f"The power request's GPIO pins {_pins(requested)} "
                    f"differ from the build's {_pins(wiring)}",
                    hint="Wire the instrument as the firmware was built, or build again.",
                )
            if requested.protocol_version != wiring.protocol_version:
                raise ConfigError(
                    f"The build's firmware speaks power protocol {wiring.protocol_version}; "
                    f"the run API's rail request speaks protocol {requested.protocol_version}",
                    hint="Build again with build(); it compiles the current protocol.",
                )
            if power.rail != _POWER_RAIL:
                raise ConfigError(
                    f"The run API measures the {_POWER_RAIL} rail only, not {power.rail!r}"
                )
            _require_power_route(self.build.engine.value, self.build.target.board, self.lifecycle)
        else:
            raise ConfigError(
                f"Unknown power request {type(power).__name__}",
                hint="Pass a FixtureRailPowerRequest or a FixtureBoardPowerRequest.",
            )
        limit, kind = (
            (_RAIL_SETTLE_LIMIT_S, "a rail capture")
            if isinstance(power, FixtureRailPowerRequest)
            else (_SETTLE_LIMIT_S, "a supply-only capture")
        )
        if not (type(settle_seconds) in (int, float) and 0 < settle_seconds <= limit):
            raise ConfigError(
                f"settle_seconds must be a number above 0 and at most {limit} for {kind}, "
                f"not {settle_seconds!r}",
                hint="It bounds each wait for READY and for completion.",
            )
        if power_guard is None:
            raise ConfigError(
                "A power request needs a power_guard",
                hint="Pass the station's authority over the instrument and rail as power_guard=.",
            )
        if not all(
            callable(getattr(power_guard, name, None)) for name in ("check", "verify_power_device")
        ):
            raise ConfigError(
                f"power_guard {type(power_guard).__name__} is not a FixturePowerGuard",
                hint="It needs check() and verify_power_device().",
            )


@dataclass(frozen=True)
class _PowerCaptureGuard:
    """The caller's board and probe authority joined with the station's power authority."""

    board: FixtureCaptureGuard
    power: FixturePowerGuard

    def check(self, *, require_free: bool, remaining_s: float) -> None:
        self.board.check(require_free=require_free, remaining_s=remaining_s)
        if self.power is not self.board:
            self.power.check(require_free=require_free, remaining_s=remaining_s)

    def verify_target(self, *, target: FixtureTarget, jlink_serial: str) -> None:
        self.board.verify_target(target=target, jlink_serial=jlink_serial)

    def verify_power_device(self, *, serial: str, rail: str) -> None:
        self.power.verify_power_device(serial=serial, rail=rail)


def _pins(wiring: FixturePowerSync) -> str:
    return f"(sync {wiring.sync_gpio_pin}, state {wiring.state_gpio_pin}, GO {wiring.go_gpio_pin})"


def _require_power_route(engine: str | None, board: str | None, lifecycle: Lifecycle) -> None:
    """Refuse rail energy outside the route the run API admits."""
    if lifecycle is not Lifecycle.CARRY:
        raise ConfigError(
            "The run API measures rail energy over a Lifecycle.CARRY build's timed loop, "
            f"not a Lifecycle.{lifecycle.name} one, which runs the model stateless",
            hint="Build with lifecycle=Lifecycle.CARRY for a power run.",
        )
    if engine not in _POWER_ENGINES:
        raise ConfigError(
            "The run API measures rail energy for heliaAOT and LiteRT builds, "
            + (f"not {engine}" if engine else "and the plan names no engine"),
            hint="Use a heliaAOT or LiteRT plan.",
        )
    if board not in _POWER_BOARDS:
        raise ConfigError(
            "The run API measures rail energy on the apollo510_evb, "
            + (f"not {board}" if board else "and the plan names no board"),
            hint="Build for apollo510_evb.",
        )


@dataclass(frozen=True)
class RunResult:
    """One capture from a :class:`CompiledBuild`.

    ``power`` is the power request the run was captured with, or ``None``.
    """

    compiled: CompiledBuild
    capture: FixtureCaptureResult
    identity: RunIdentity
    verification: Verification | None
    power: FixtureRailPowerRequest | FixtureBoardPowerRequest | None = None

    @property
    def succeeded(self) -> bool:
        """True only for a ``success`` capture."""
        return self.capture.state == "success"

    @property
    def energy(self) -> FixtureRailEnergy | None:
        """The capture's gross MCU-rail energy, or ``None`` when it measured none.

        It holds the measured iterations, duration, mean power and current,
        energy and energy per iteration, the rail and instrument serial, the
        hash-pinned raw instrument trace, and the protocol-2 READY, GO,
        gate and DONE samples in ``completion``. A failed capture measures
        none.
        """
        return self.capture.rail_energy


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
    iterations and warmups the firmware runs. A *method* with ``power_sync``
    compiles the protocol-2 power firmware, which a run measures with a
    matching :class:`~helia_profiler.FixtureRailPowerRequest`; it is admitted
    for a heliaAOT or LiteRT plan on the ``apollo510_evb`` under
    ``Lifecycle.CARRY``, and refused otherwise. A plan frozen with power
    enabled needs such a method. *host* supplies host-local
    settings, such as ``{"work_dir": ...}``, as :meth:`FrozenRun.config` does.
    heliaRT and LiteRT link a prepared runtime: without *runtime*, the one the
    plan names is taken from the HPX cache (``hpx runtimes prepare``), never
    downloaded or built. The result is verified before it is returned.
    """
    from ._fixture_build import build_fixed_fixture

    if fixture.model.sha256 != plan.model.sha256:
        raise ConfigError(
            f"Fixture model {fixture.model.path} does not match the plan's model "
            f"(sha256 {plan.model.sha256[:12]}…)",
            hint="Build the fixture from the model the plan was frozen against.",
        )
    try:
        lifecycle = Lifecycle(lifecycle)
    except ValueError as exc:
        raise ConfigError(
            f"Unknown lifecycle {lifecycle!r}",
            hint="Use Lifecycle.CARRY or Lifecycle.SINGLE.",
        ) from exc
    if method.power_sync is not None:
        target = plan.settings.get("target", {})
        _require_power_route(
            plan.settings.get("engine", {}).get("type"), target.get("board"), lifecycle
        )
    config = plan.config(model=model, host=host)
    if config.power.enabled and method.power_sync is None:
        raise ConfigError(
            "The plan is frozen with power enabled, but the method has no power_sync; "
            "the build would measure timing only",
            hint="Pass FixtureMethod(..., power_sync=FixturePowerSync(...)) to build() "
            "and power= to run_build().",
        )
    require_lifecycle(config.engine.type, lifecycle)
    iterations, warmup = lifecycle_invocations(
        lifecycle, config.profiling.iterations, config.profiling.warmup
    )
    # The method's power_sync compiles the power firmware; a profile's power
    # section has no meaning to a fixture build, which refuses it.
    config = replace(
        config,
        profiling=replace(config.profiling, iterations=iterations, warmup=warmup),
        power=replace(config.power, enabled=False),
    )
    fixture_build = build_fixed_fixture(
        config,
        fixture,
        method=method,
        runtime=runtime if runtime is not None else _prepared_runtime(plan),
        compile=True,
        observe_aot_arenas=observe_aot_arenas,
        observe_ram=observe_ram,
    )
    compiled = CompiledBuild(
        plan, fixture_build, lifecycle, _binding(plan, lifecycle, fixture_build)
    )
    compiled.verify()
    return compiled


@dataclass(frozen=True)
class _LinkedRuntime:
    """The runtime and kernel library a fixture build of a plan links."""

    name: str
    version: str | None
    commit: str | None
    kernels_commit: str | None


def _linked_runtime(plan: FrozenRun, linked_version: str | None = None) -> _LinkedRuntime | None:
    """What a fixture build of *plan* links, or ``None`` when the plan states no runtime.

    heliaRT and heliaAOT link the runtime and kernel commits the plan pins.
    LiteRT does not: the plan pins the tflite-micro and CMSIS-NN modules an
    ordinary profile builds from source, while a fixture build links a
    prepared LiteRT record (``hpx runtimes prepare litert``). A build is
    compared with the record of the version it linked (*linked_version*), so
    a saved build keeps verifying after this HPX's default changes; a new
    build links the default.
    """
    from .runtime_records import runtime

    planned = plan.runtime
    if planned is None:
        return None
    if planned.name != "litert":
        return _LinkedRuntime(
            planned.name, planned.version, planned.source_commit, planned.kernels_commit
        )
    record = runtime("litert", linked_version)
    if record is None:
        raise ConfigError(
            f"This HPX ships no prepared LiteRT runtime record {linked_version or ''}".rstrip()
        )
    return _LinkedRuntime(
        record.name,
        record.version,
        record.source.commit,
        record.kernels.commit if record.kernels is not None else None,
    )


def _prepared_runtime(plan: FrozenRun) -> PreparedUpstreamRuntime | None:
    """The cached prepared runtime a fixture build of *plan* links, for heliaRT and LiteRT."""
    from .engines import EngineType, get_adapter
    from .engines.base import PreparedRuntimeAdapter
    from .prepared_runtimes import prepared_runtime

    engine = EngineType(plan.settings["engine"]["type"])
    if not isinstance(get_adapter(engine), PreparedRuntimeAdapter):
        return None
    linked = _linked_runtime(plan)
    if linked is None:
        raise ConfigError(f"The plan states no {engine.value} runtime to link")
    if linked.version is None:
        reason = plan.runtime.unavailable_reason if plan.runtime else None
        raise ConfigError(
            f"The plan names no prepared {engine.value} release to link"
            + (f": {reason}" if reason else ""),
            hint="Freeze the plan with a recorded runtime release, or pass runtime=.",
        )
    return prepared_runtime(linked.name, linked.version).runtime


def _binding(plan: FrozenRun, lifecycle: Lifecycle, build: FixtureBuild) -> str:
    """The ``hpx.binding`` hash tying *build* to the plan and lifecycle it was compiled from.

    It covers the plan's intent hash, the lifecycle, the timing scope,
    state policy and power wiring the build records, its prepared runtime
    manifest, and what it observes, and no host path.
    """
    payload = {
        "plan": plan.intent_hash,
        "lifecycle": lifecycle.value,
        "timing_scope": build.timing_scope.value,
        "state_policy": _value(build.state_policy),
        "runtime_manifest": build.runtime_manifest.sha256 if build.runtime_manifest else None,
        "observe_ram": build.observe_ram,
        "observe_aot_arenas": [list(arena) for arena in build.aot_arena_scan],
        "power_sync": asdict(build.power_sync) if build.power_sync is not None else None,
    }
    try:
        return semantic_hash("binding", payload)
    except ValueError as exc:
        raise ConfigError("A compiled build cannot record a non-finite number") from exc


def _value(member: StrEnum | None) -> str | None:
    return member.value if member is not None else None


def load_build(path: str | Path, *, roots: Mapping[str, Path]) -> CompiledBuild:
    """Read and verify a compiled build written by :meth:`CompiledBuild.save`."""
    return CompiledBuild.load(path, roots=roots)


def run_build(
    compiled: CompiledBuild,
    *,
    guard: FixtureCaptureGuard,
    jlink_serial: str,
    evidence_dir: Path,
    settle_seconds: float,
    expect: Expect | None = None,
    expected_duration_s: float | None = None,
    power: FixtureRailPowerRequest | FixtureBoardPowerRequest | None = None,
    power_guard: FixturePowerGuard | None = None,
) -> RunResult:
    """Flash *compiled*'s image, capture one run and compare outputs; see :meth:`CompiledBuild.run`."""
    return compiled.run(
        guard=guard,
        jlink_serial=jlink_serial,
        evidence_dir=evidence_dir,
        settle_seconds=settle_seconds,
        expect=expect,
        expected_duration_s=expected_duration_s,
        power=power,
        power_guard=power_guard,
    )


def _document(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ConfigError("A compiled build must be a JSON object.")
    if data.get("schema") != COMPILED_BUILD_SCHEMA:
        raise ConfigError(f"Not a compiled build: schema {data.get('schema')!r}")
    version = data.get("schema_version")
    if type(version) is not int or version != COMPILED_BUILD_SCHEMA_VERSION:
        raise ConfigError(
            f"Unsupported compiled build version: {version!r}",
            hint=f"This heliaPROFILER reads version {COMPILED_BUILD_SCHEMA_VERSION}; "
            "build it again.",
        )
    if data.keys() != _DOCUMENT_KEYS:
        raise ConfigError(
            f"Malformed compiled build: fields {sorted(data)}, expected {sorted(_DOCUMENT_KEYS)}"
        )
    return data
