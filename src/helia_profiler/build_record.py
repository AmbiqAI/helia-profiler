"""Build once, run later: a frozen plan's compiled firmware as a durable record.

``FrozenRun.build`` compiles a plan for fixed inputs and returns a
:class:`BuildRecord`, which can be saved, verified and loaded on another
host. :meth:`BuildRecord.run` captures from the record without rebuilding and
returns a :class:`RunResult` with the run's identity, its measurements and,
when asked, the comparison of its outputs with reference outputs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from .errors import ConfigError
from .lifecycle import Lifecycle, lifecycle_invocations, require_lifecycle

if TYPE_CHECKING:
    from ._fixture_build import FixtureBuild, FixtureMethod, TypedFixture
    from .expect import Expect, Verification
    from .fixture_capture import FixtureCaptureGuard, FixtureCaptureResult
    from .fixture_runtime import PreparedUpstreamRuntime
    from .frozen_run import FrozenRun
    from .identity import RunIdentity

BUILD_RECORD_SCHEMA = "hpx.build-record"
BUILD_RECORD_SCHEMA_VERSION = 1

_DOCUMENT_KEYS = frozenset({"schema", "schema_version", "plan", "lifecycle", "build"})


@dataclass(frozen=True)
class BuildRecord:
    """A frozen plan and the firmware compiled from it, verifiable on any host."""

    plan: FrozenRun
    build: FixtureBuild
    lifecycle: Lifecycle

    @property
    def identity(self) -> RunIdentity:
        """The record's identity; ``observation_hash`` is ``None`` until it runs."""
        from .fixture_identity import fixture_run_identity

        identity = fixture_run_identity(self.build)
        return replace(identity, run=replace(identity.run, lifecycle=self.lifecycle.value))

    def verify(self) -> None:
        """Re-check the build against its pinned files and its plan; raise on any mismatch."""
        from ._fixture_record import verify_fixture_build
        from .frozen_run import FrozenRun

        try:
            verify_fixture_build(self.build)
        except (ValueError, OSError) as exc:
            raise ConfigError(f"Build record does not verify: {exc}") from exc
        if self.build.model is None or self.build.model.sha256 != self.plan.model.sha256:
            raise ConfigError(
                "Build record's model does not match its plan "
                f"(sha256 {self.plan.model.sha256[:12]}…)"
            )
        FrozenRun.from_dict(self.plan.to_dict())
        try:
            profiling = self.plan.settings["profiling"]
            expected = lifecycle_invocations(
                self.lifecycle, profiling["iterations"], profiling["warmup"]
            )
        except (KeyError, TypeError) as exc:
            raise ConfigError(f"Build record's plan has no profiling counts: {exc}") from exc
        if (self.build.iterations, self.build.warmups) != expected:
            raise ConfigError(
                f"Build record runs {self.build.iterations} iterations and "
                f"{self.build.warmups} warmups; lifecycle {self.lifecycle.value} of its plan "
                f"runs {expected[0]} and {expected[1]}"
            )

    def save(self, path: str | Path, *, roots: Mapping[str, Path]) -> Path:
        """Write the record as JSON with paths relative to *roots* and return its path."""
        from ._fixture_record import dump_fixture_build

        try:
            build = json.loads(dump_fixture_build(self.build, roots=roots))
        except ValueError as exc:
            raise ConfigError(f"Cannot save build record: {exc}") from exc
        document = {
            "schema": BUILD_RECORD_SCHEMA,
            "schema_version": BUILD_RECORD_SCHEMA_VERSION,
            "plan": self.plan.to_dict(),
            "lifecycle": self.lifecycle.value,
            "build": build,
        }
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n"
        with target.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        return target

    @classmethod
    def load(cls, path: str | Path, *, roots: Mapping[str, Path]) -> BuildRecord:
        """Read and verify a record written by :meth:`save`, resolving paths under *roots*."""
        from ._fixture_record import _read_json, load_fixture_build
        from .frozen_run import FrozenRun

        source = Path(path).expanduser()
        try:
            data = _read_json(source.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigError(f"Build record not found: {source}") from exc
        except (OSError, ValueError) as exc:
            raise ConfigError(f"Cannot read build record {source}: {exc}") from exc
        document = _document(data)
        try:
            lifecycle = Lifecycle(document["lifecycle"])
        except (ValueError, TypeError) as exc:
            raise ConfigError(
                f"Malformed build record: unknown lifecycle {document['lifecycle']!r}"
            ) from exc
        plan = FrozenRun.from_dict(document["plan"])
        try:
            build = load_fixture_build(json.dumps(document["build"]), roots=roots)
        except ValueError as exc:
            raise ConfigError(f"Malformed build record: {exc}") from exc
        except OSError as exc:
            raise ConfigError(
                f"Build record file is missing under the given roots: {exc}",
                hint="Pass roots= naming where the record's files now live.",
            ) from exc
        record = cls(plan, build, lifecycle)
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
        """Flash the recorded image, capture one run and compare outputs when *expect* is given."""
        raise NotImplementedError


@dataclass(frozen=True)
class RunResult:
    """One capture from a :class:`BuildRecord`."""

    record: BuildRecord
    capture: FixtureCaptureResult
    identity: RunIdentity
    verification: Verification | None

    @property
    def succeeded(self) -> bool:
        return self.capture.state == "success"


def build_record(
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
) -> BuildRecord:
    """Compile *plan* for *fixture*'s pinned inputs and return the record."""
    from ._fixture_build import build_fixed_fixture

    if fixture.model.sha256 != plan.model.sha256:
        raise ConfigError(
            f"Fixture model {fixture.model.path} does not match the plan's model "
            f"(sha256 {plan.model.sha256[:12]}…)",
            hint="Build the fixture from the model the plan was frozen against.",
        )
    config = plan.config(model=model, host=host)
    require_lifecycle(config.engine.type, lifecycle)
    iterations, warmup = lifecycle_invocations(
        lifecycle, config.profiling.iterations, config.profiling.warmup
    )
    config = replace(
        config, profiling=replace(config.profiling, iterations=iterations, warmup=warmup)
    )
    build = build_fixed_fixture(
        config,
        fixture,
        method=method,
        runtime=runtime,
        compile=True,
        observe_aot_arenas=observe_aot_arenas,
        observe_ram=observe_ram,
    )
    return BuildRecord(plan, build, lifecycle)


def _document(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ConfigError("A build record must be a JSON object.")
    if data.get("schema") != BUILD_RECORD_SCHEMA:
        raise ConfigError(f"Not a build record: schema {data.get('schema')!r}")
    version = data.get("schema_version")
    if type(version) is not int or version != BUILD_RECORD_SCHEMA_VERSION:
        raise ConfigError(f"Unsupported build record version: {version!r}")
    if data.keys() != _DOCUMENT_KEYS:
        raise ConfigError(
            f"Malformed build record: fields {sorted(data)}, expected {sorted(_DOCUMENT_KEYS)}"
        )
    return data
