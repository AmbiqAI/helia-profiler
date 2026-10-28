"""Fixture inputs, method declarations and build records."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

from .._files import FixtureFile
from ..engines import EngineType
from ..engines.base import EngineSource
from ..placement import Placement
from ..results.models import MemoryPlan, ToolchainInfo
from .analysis import FixtureModelAnalysis, FixtureTensor, Int8Tensor, TypedFixtureModelAnalysis
from .io import FixtureCapability
from .power import FixturePowerSync
from .psram import FixturePsramWeights
from .sources import FixtureKernelSource, FixtureRuntimeSource
from .target import FixtureTarget


class FixtureTimingScope(StrEnum):
    """What the timed span covers.

    ``INVOKE_ONLY`` brackets each invocation; ``RESTORE_AND_INVOKE`` brackets
    the timed loop, so restoring the fixed inputs before each invocation is
    inside the span.
    """

    INVOKE_ONLY = "invoke_only"
    RESTORE_AND_INVOKE = "restore_and_invoke"


class FixtureStatePolicy(StrEnum):
    """Internal state behavior; input restoration is independent of this policy."""

    CARRY = "carry"


class FixtureRole(StrEnum):
    """Role of one fixture tensor; outputs are always ``SIGNAL``."""

    SIGNAL = "signal"
    AUX = "aux"


@dataclass(frozen=True)
class FixtureMethod:
    """Select input-restoration timing and, optionally, the internal-state policy.

    Carry initializes the engine once, then retains internal state through
    warmups and measurements. It is how fixtures always run, so a build of a
    model with internal state records it when no policy is given. Fixed external
    inputs are still restored each invocation; no internal reset or
    source-initializer equivalence is implied.
    """

    timing_scope: FixtureTimingScope
    state_policy: FixtureStatePolicy | None = None
    power_sync: FixturePowerSync | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.timing_scope, FixtureTimingScope):
            raise ValueError("Explicit FixtureTimingScope required")
        if self.state_policy is not None and not isinstance(self.state_policy, FixtureStatePolicy):
            raise ValueError("Explicit FixtureStatePolicy required")
        if self.power_sync is not None and (
            not isinstance(self.power_sync, FixturePowerSync)
            or self.timing_scope is not FixtureTimingScope.RESTORE_AND_INVOKE
        ):
            raise ValueError("Rail power requires explicit wiring and restore_and_invoke timing")


@dataclass(frozen=True)
class FixtureRenderSpec:
    fixture: FixedFixture | TypedFixture
    method: FixtureMethod
    model: FixtureModelAnalysis | TypedFixtureModelAnalysis
    #: Paint heliaAOT scratch arenas and report touched bytes after the run.
    observe_aot_arenas: bool = False
    observe_ram: bool = False


@dataclass(frozen=True)
class FixedFixture:
    model: FixtureFile
    input: FixtureFile
    expected: FixtureFile
    input_tensor: Int8Tensor
    output_tensor: Int8Tensor

    def verify(self) -> None:
        self.model.read()
        if (
            len(self.input.read()) != self.input_tensor.size
            or len(self.expected.read()) != self.output_tensor.size
        ):
            raise ValueError("Full fixture tensor byte extent mismatch")

    @property
    def identity(self) -> str:
        value = asdict(self)
        for name in ("model", "input", "expected"):
            value[name].pop("path")
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()


_FIXTURE_ROLES = frozenset(FixtureRole)


@dataclass(frozen=True)
class FixtureIO:
    """One model input with its fixed bytes, or one output with its expected bytes."""

    tensor: FixtureTensor
    data: FixtureFile
    role: FixtureRole = FixtureRole.SIGNAL


@dataclass(frozen=True)
class FixtureOutput:
    """One complete output declaration, without a reference answer."""

    tensor: FixtureTensor


@dataclass(frozen=True)
class TypedFixture:
    """Fixed input bytes and complete ordered outputs for a model.

    Outputs are descriptors, or legacy ``FixtureIO`` references. Reference
    bytes are not needed for measurement; legacy identities remain unchanged.
    """

    model: FixtureFile
    inputs: tuple[FixtureIO, ...]
    outputs: tuple[FixtureIO, ...] | tuple[FixtureOutput, ...]

    def verify(self) -> None:
        self.model.read()
        if not self.inputs or not self.outputs:
            raise ValueError("Typed fixture requires inputs and outputs")
        if not (
            all(isinstance(io, FixtureIO) for io in self.outputs)
            or all(isinstance(io, FixtureOutput) for io in self.outputs)
        ):
            raise ValueError("Mixed reference and descriptor outputs are not supported")
        if any(io.role not in _FIXTURE_ROLES for io in self.inputs) or any(
            isinstance(io, FixtureIO) and io.role != FixtureRole.SIGNAL for io in self.outputs
        ):
            raise ValueError("Invalid fixture tensor role")
        for io in (*self.inputs, *self.outputs):
            if isinstance(io, FixtureIO) and len(io.data.read()) != io.tensor.size_bytes:
                raise ValueError(f"Fixture tensor {io.tensor.name} byte extent mismatch")

    @property
    def expected(self) -> FixtureFile | None:
        first = self.outputs[0]
        return first.data if isinstance(first, FixtureIO) else None

    @property
    def identity(self) -> str:
        value = asdict(self)
        value["model"].pop("path")
        for io in (*value["inputs"], *value["outputs"]):
            if "data" in io:
                io["data"].pop("path")
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()


@dataclass(frozen=True)
class FixtureBuild:
    fixture_identity: str
    app_dir: Path
    binary: FixtureFile | None
    generated_sources: tuple[FixtureFile, ...]
    expected: FixtureFile | None
    built: bool
    runtime_manifest: FixtureFile | None
    flat_binary: FixtureFile | None
    dependency_lock: FixtureFile | None
    link_map: FixtureFile | None
    timing_scope: FixtureTimingScope
    engine: EngineType
    build_identity: str | None
    iterations: int
    warmups: int
    intent_identity: str
    target: FixtureTarget
    toolchain: ToolchainInfo | None
    provider_provenance: str = (
        "manifest-declared; independently audit the pinned build/source record"
    )
    planned_memory: MemoryPlan | None = None
    planned_memory_reason: str | None = "not_recorded"
    #: Expected bytes of every output, in model order (typed fixtures).
    outputs: tuple[FixtureFile, ...] = ()
    #: Producer capability of each IO dtype this build uses.
    capabilities: tuple[tuple[str, FixtureCapability], ...] = ()
    #: heliaAOT scratch arenas painted and scanned: (region id, size in bytes).
    aot_arena_scan: tuple[tuple[int, int], ...] = ()
    #: heliaAOT's ``<prefix>_plan.json`` and ``<prefix>_report.json`` when the
    #: installed heliaAOT writes them, plus explicit mixed-placement region evidence.
    aot_outputs: tuple[FixtureFile, ...] = ()
    #: The engine package that generated the model code, when one did.
    engine_source: EngineSource | None = None
    #: Exact dependency state retained beside the per-build lock snapshot.
    dependency_state: FixtureFile | None = None
    state_policy: FixtureStatePolicy | None = None
    source_initializer_present: bool = False
    #: Image-bound PSRAM constants/model expectations, absent on other placements.
    psram_weights: FixturePsramWeights | None = None
    #: Effective configured banks; legacy receipts without these cannot bind operator timing.
    arena_location: Placement | None = None
    weights_location: Placement | None = None
    #: Complete measurement inputs and outputs; required by persisted builds.
    model: FixtureFile | None = None
    inputs: tuple[FixtureIO, ...] = ()
    output_tensors: tuple[FixtureTensor, ...] = ()
    arena_capacity: int | None = None
    clock_profile: str | None = None
    cpu_hz: int | None = None
    timer_hz: int | None = None
    power_sync: FixturePowerSync | None = None
    observe_ram: bool = False
    #: Compiled builds only: the kernel library linked, as the pinned prepared-runtime
    #: manifest or dependency state states it.
    kernel_source: FixtureKernelSource | None = None
    #: Compiled heliaRT and LiteRT builds only: the prepared runtime archive linked.
    runtime_source: FixtureRuntimeSource | None = None
