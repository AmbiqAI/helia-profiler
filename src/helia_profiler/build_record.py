"""Build once, run later: a frozen plan's compiled firmware as a durable record.

``FrozenRun.build`` compiles a plan for fixed inputs and returns a
:class:`BuildRecord`, which can be saved, verified and loaded on another
host. :meth:`BuildRecord.run` captures from the record without rebuilding and
returns a :class:`RunResult` with the run's identity, its measurements and,
when asked, the comparison of its outputs with reference outputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

from .lifecycle import Lifecycle

if TYPE_CHECKING:
    from ._fixture_build import FixtureBuild, FixtureMethod, TypedFixture
    from .expect import Expect, Verification
    from .fixture_capture import FixtureCaptureGuard, FixtureCaptureResult
    from .fixture_runtime import PreparedUpstreamRuntime
    from .frozen_run import FrozenRun
    from .identity import RunIdentity

BUILD_RECORD_SCHEMA = "hpx.build-record"
BUILD_RECORD_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class BuildRecord:
    """A frozen plan and the firmware compiled from it, verifiable on any host."""

    plan: FrozenRun
    build: FixtureBuild
    lifecycle: Lifecycle

    @property
    def identity(self) -> RunIdentity:
        """The record's identity; ``observation_hash`` is ``None`` until it runs."""
        raise NotImplementedError

    def verify(self) -> None:
        """Re-check the build against its pinned files and its plan; raise on any mismatch."""
        raise NotImplementedError

    def save(self, path: str | Path, *, roots: Mapping[str, Path]) -> Path:
        """Write the record as JSON with paths relative to *roots* and return its path."""
        raise NotImplementedError

    @classmethod
    def load(cls, path: str | Path, *, roots: Mapping[str, Path]) -> BuildRecord:
        """Read and verify a record written by :meth:`save`, resolving paths under *roots*."""
        raise NotImplementedError

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
        from dataclasses import replace

        from ._fixture_record import fixture_capture_request
        from .expect import compare_fixture_outputs
        from .fixture_capture import capture_fixture
        from .fixture_identity import fixture_run_identity

        request = fixture_capture_request(
            self.build,
            jlink_serial=jlink_serial,
            evidence_dir=evidence_dir,
            settle_seconds=settle_seconds,
            expected_duration_s=expected_duration_s,
        )
        capture = capture_fixture(request, guard=guard)
        identity = fixture_run_identity(self.build, capture)
        identity = replace(identity, run=replace(identity.run, lifecycle=self.lifecycle.value))
        verification = (
            compare_fixture_outputs(self.build, capture, expect)
            if expect is not None and capture.state == "success"
            else None
        )
        return RunResult(self, capture, identity, verification)


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
    raise NotImplementedError
