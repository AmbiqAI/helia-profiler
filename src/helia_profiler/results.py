"""Typed result models for profiling data.

Every piece of structured data that flows between pipeline stages, the
capture layer, and the report module is represented as a frozen dataclass
here.  No ``dict[str, Any]`` at the boundary — consumers get IDE completion,
type-checking, and clear contracts.

The models are intentionally flat and simple.  ``LayerResult.counters`` is the
one deliberate ``dict`` — PMU counter names are dynamic (varies by preset) and
enumerating every possible ARM PMU event as a field would be impractical.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from .engines import EngineType
from .placement import MemoryRegion


class ConsumerKind(StrEnum):
    """Logical role of a :class:`MemoryConsumer` entry."""

    ARENA = "arena"
    WEIGHTS = "weights"
    CODE = "code"
    STACK = "stack"
    OTHER = "other"


# ---------------------------------------------------------------------------
# PMU / layer-level results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LayerResult:
    """Profiling result for a single model layer (averaged across iterations)."""

    id: int | str
    op: str
    counters: dict[str, float] = field(default_factory=dict)
    cycles: float | None = None
    overflow: bool = False


@dataclass(frozen=True)
class PresetResult:
    """Results for a single PMU counter preset (e.g. ``basic_cpu``)."""

    name: str
    header: list[str] = field(default_factory=list)
    iterations: list[list[LayerResult]] = field(default_factory=list)
    layers: list[LayerResult] = field(default_factory=list)


@dataclass(frozen=True)
class FirmwareMeta:
    """Metadata reported by the profiler firmware at startup.

    All fields are optional because older firmware versions may not report
    every field.
    """

    model_size: int | None = None
    arena_size: int | None = None
    allocated_arena: int | None = None
    input_size: int | None = None
    output_size: int | None = None
    num_tensors: int | None = None
    num_inputs: int | None = None
    num_outputs: int | None = None
    num_presets: int | None = None
    presets: tuple[str, ...] = ()


@dataclass(frozen=True)
class PmuResult:
    """Complete PMU profiling result across all presets."""

    meta: FirmwareMeta
    presets: dict[str, PresetResult] = field(default_factory=dict)
    layers: list[LayerResult] = field(default_factory=list)
    overflow_detected: bool = False
    #: Per-compute-unit merged layers.  Keys are group names (``cpu``,
    #: ``mve``, ``memory``, …).  Each value is a list of LayerResult whose
    #: ``counters`` contain all columns for that compute unit, merged
    #: across multiple firmware passes.
    groups: dict[str, list[LayerResult]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Run metadata (enriched progressively by pipeline stages)
# ---------------------------------------------------------------------------


@dataclass
class PlatformInfo:
    """Resolved platform details (populated by stage 1)."""

    board: str = ""
    soc: str = ""
    core: str = ""
    pmu_tier: str = ""
    has_mve: bool = False
    profiling_backends: list[str] = field(default_factory=list)
    profiling_domains: list[str] = field(default_factory=list)
    cpu_clock_name: str = ""  # selected CPU speed name (e.g. "hp")
    cpu_clock_mhz: int = 0  # selected CPU frequency
    cpu_perf_tier: str = ""  # NSX perf_mode symbol (e.g. "NSX_PERF_HIGH")


@dataclass
class ModelInfo:
    """Model file metadata (populated by stage 1)."""

    name: str = ""
    size_bytes: int = 0
    sha256: str = ""


@dataclass
class ToolchainInfo:
    """Build toolchain versions (populated by stage 4)."""

    compiler: str = ""
    compiler_version: str = ""
    cmake_version: str = ""


@dataclass
class RunMetadata:
    """Accumulated run metadata — enriched by stages, consumed by reports."""

    hpx_version: str = ""
    run_id: str = ""
    timestamp: str = ""
    config_snapshot: dict[str, Any] = field(default_factory=dict)
    platform: PlatformInfo | None = None
    model: ModelInfo | None = None
    toolchain: ToolchainInfo | None = None
    firmware: FirmwareMeta | None = None
    memory_plan: "MemoryPlan | None" = None


# ---------------------------------------------------------------------------
# Engine module reference (replaces dict in EngineArtifacts.extra_modules)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BinarySections:
    """ELF binary section sizes (from ``arm-none-eabi-size``)."""

    text: int = 0
    data: int = 0
    bss: int = 0
    total: int = 0


@dataclass(frozen=True)
class NsxModuleRef:
    """Reference to an NSX module needed by the profiler firmware build.

    A module is resolved one of two ways:

    * **Registry** (``local=False``) — NSX clones the module from its
      registered upstream (GitHub). ``project`` is the registry project
      name and ``ref`` optionally pins a tag/branch. ``path`` is unused.
    * **Local** (``local=True``) — hpx vendors the module on disk. ``path``
      is the source directory to copy into the app, and ``project`` (when
      set) selects the registry-derived install location so NSX's
      registry-aware lock can find it.
    """

    name: str
    path: Path
    version: str = ""
    local: bool = True
    project: str = ""
    ref: str = ""


# ---------------------------------------------------------------------------
# Memory plan — engine-agnostic view of what sits in each SoC memory region
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryConsumer:
    """One named thing that consumes bytes in a memory region.

    Examples: model weights, tensor arena, per-DTCM scratch, code/text.
    """

    name: str
    size: int
    kind: ConsumerKind = ConsumerKind.ARENA

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ConsumerKind):
            object.__setattr__(self, "kind", ConsumerKind(self.kind))


@dataclass(frozen=True)
class MemoryRegionUsage:
    """Usage breakdown for a single memory region (e.g. DTCM, MRAM).

    ``capacity`` reflects the SoC's physical size for this region (bytes).
    ``used`` is the sum of ``consumers[i].size`` (what the plan allocates).
    ``free`` is a convenience property.
    """

    region: MemoryRegion
    capacity: int
    used: int
    consumers: tuple[MemoryConsumer, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.region, MemoryRegion):
            object.__setattr__(self, "region", MemoryRegion(str(self.region).upper()))

    @property
    def free(self) -> int:
        return max(0, self.capacity - self.used)

    @property
    def overflow(self) -> bool:
        return self.capacity > 0 and self.used > self.capacity


@dataclass(frozen=True)
class MemoryPlan:
    """Engine-agnostic memory plan for a single profiling run.

    Produced by the ``plan_memory`` stage by combining engine-specific
    knowledge (AOT arena_usages, TFLM single-arena size, weight placement)
    with the SoC's physical memory layout.  Consumed by the report and by
    the firmware template generator for placement macros / linker hints.
    """

    engine: EngineType
    regions: tuple[MemoryRegionUsage, ...] = ()
    # Total model weight bytes (informational — where they go is in regions).
    model_weight_bytes: int = 0
    # True if ANY region is oversubscribed.  A run with overflow will
    # typically fail at build/flash/boot; the stage raises PlatformError
    # before that happens so the user gets a clear hint.
    has_overflow: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.engine, EngineType):
            object.__setattr__(self, "engine", EngineType(self.engine))

    def region(self, name: str | MemoryRegion) -> MemoryRegionUsage | None:
        key = MemoryRegion(str(name).upper()) if not isinstance(name, MemoryRegion) else name
        for r in self.regions:
            if r.region is key:
                return r
        return None


# ---------------------------------------------------------------------------
# Top-level result (public API return type)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProfileResult:
    """Complete profiling result — the public return type of ``hpx.profile()``.

    This is the one object a programmatic user needs.  It carries everything:
    PMU data, optional power data, run metadata, and report file paths.
    """

    pmu: PmuResult
    power: Any | None = None  # PowerResult when power capture is enabled
    metadata: RunMetadata = field(default_factory=RunMetadata)
    report_paths: list[Path] = field(default_factory=list)

    # -- Convenience accessors (progressive disclosure) --------------------

    @property
    def layers(self) -> list[LayerResult]:
        """Merged per-layer results across all PMU presets."""
        return self.pmu.layers

    @property
    def total_cycles(self) -> float:
        """Total CPU cycles across all layers."""
        return sum(layer.cycles or 0 for layer in self.pmu.layers)

    @property
    def layer_count(self) -> int:
        return len(self.pmu.layers)

    @property
    def overflow_detected(self) -> bool:
        return self.pmu.overflow_detected
