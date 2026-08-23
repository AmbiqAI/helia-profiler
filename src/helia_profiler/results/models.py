"""Typed result models for profiling data.

Structured data flowing between pipeline stages, capture, and reports uses
typed dataclasses. Core measurement records are frozen against field
reassignment, while run metadata is intentionally enriched by pipeline stages.

Nested dynamic collections remain mutable for compatibility and efficient
capture assembly. In particular, PMU counter names, engine extensions, power
samples, and metadata are open-ended. Public consumers should treat returned
collections as read-only; structural deep immutability is not currently part
of the API contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..compatibility import CompatibilityResolution
from ..engines import EngineType
from ..power.base import PowerResult
from ..placement import MemoryRegion

if TYPE_CHECKING:
    from .dependencies import DependencyProvenance
    from .artifacts import OnDevicePowerSummary, PowerObservation, PowerTerminalRecord


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
class PsramInfo:
    """PSRAM state and timing diagnostics reported by ``nsx-psram``."""

    size_bytes: int
    clock_hz: int
    capabilities: int
    state: int
    last_init_status: int
    xip_enabled: bool
    timing_status: int
    rxdqs_delay: int


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
    #: Actual CPU clock (Hz) reported by the firmware's ``SystemCoreClock``.
    #: Ground truth for verifying the host's registry clock assumption.
    system_clock_hz: int | None = None
    profiled_infer_count: int | None = None
    profiled_infer_total_us: int | None = None
    profiled_infer_avg_us: int | None = None
    #: Clean end-to-end inference timing measured with per-layer
    #: instrumentation disabled (warmed caches).  Cycles are DWT core cycles,
    #: directly comparable to the per-layer ``cycles`` sum.
    clean_infer_count: int | None = None
    clean_infer_total_cycles: int | None = None
    clean_infer_avg_cycles: int | None = None
    clean_infer_avg_us: int | None = None
    #: Clean-window iterations whose DWT delta came back as exactly zero
    #: (``HPX_CLEAN_STALLED_ITERS``) -- a frozen cycle counter.  An inference
    #: cannot take zero core cycles, so any non-zero value means the counter
    #: stopped mid-window and the timing above is short by those iterations.
    clean_stalled_iters: int | None = None
    #: Clean-window iterations whose delta was non-zero but below the
    #: firmware's warm-derived floor (``HPX_CLEAN_PARTIAL_ITERS``) -- a counter
    #: that kept advancing far too slowly rather than stopping.  Observed on
    #: Apollo4 at ~0.6% of the expected rate; such deltas pass the zero test,
    #: so they are counted separately.
    clean_partial_iters: int | None = None
    #: The warm per-inference cycle count that floor was derived from
    #: (``HPX_CLEAN_REF_CYCLES``), so the threshold is auditable from the
    #: capture rather than taken on trust.
    #:
    #: For all three: ``None`` means the firmware did not report it -- either a
    #: build whose window is not DWT-timed per-iteration (Apollo5 / STIMER, or
    #: the busy_loop probe), or firmware predating the check.  Absence is
    #: "unknown", never "healthy".  See
    #: ``power.diagnostics.assess_clean_window_stall``.
    clean_ref_cycles: int | None = None
    #: DWT cycles counted across the firmware's ``HPX_CLEAN_DWT_RATE_US``
    #: calibration interval (``HPX_CLEAN_DWT_RATE_CYC``), timed by
    #: nsx_delay_us's BOOTROM cycle loop -- a clock that shares none of DWT's
    #: dependencies.  The only clean-window check whose reference is not itself
    #: DWT, and so the only one that can see a uniform slowdown.
    clean_dwt_rate_cyc: int | None = None
    clean_dwt_rate_us: int | None = None
    #: Microseconds the firmware spent waiting for the host debug probe to
    #: attach before opening the window (``HPX_CLEAN_ATTACH_WAIT_US``).  0 means
    #: the host was already draining RTT; a value at the budget means the wait
    #: timed out and the detectors above are the only cover for that run.
    clean_attach_wait_us: int | None = None
    psram: PsramInfo | None = None
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
class TimingInfo:
    """Host-observed wall-clock timings for a profiling capture."""

    capture_duration_s: float | None = None
    hpx_start_latency_s: float | None = None
    protocol_duration_s: float | None = None
    # Attributed breakdown of the boot/attach window into named phases
    # (e.g. ``reset``, ``sbl_settle``, ``attach``, ``control_block_scan``).
    # Lets the few unavoidable settle floors be inspected and tuned with data
    # instead of guesswork.  Currently populated for the RTT transport.
    phases: dict[str, float] | None = None


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
class EngineInfo:
    """Resolved inference-engine identity and version for one run."""

    type: str = ""
    version: str | None = None


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
    engine: EngineInfo | None = None
    firmware: FirmwareMeta | None = None
    memory_plan: "MemoryPlan | None" = None
    timing: TimingInfo | None = None
    compatibility: CompatibilityResolution | None = None
    dependencies: "DependencyProvenance | None" = None


# ---------------------------------------------------------------------------
# Engine module reference (replaces dict in EngineArtifacts.extra_modules)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BinarySections:
    """ELF binary section sizes (from ``arm-none-eabi-size``).

    ``bss`` counts zero-initialized state the program actually uses --
    including the stack, which is live memory whatever its section says.

    ``reserved`` is separate: the linker's ``.heap`` region, which NSX scripts
    size to whatever remained in the memory region rather than to a
    requirement, purely so ``_sbrk`` has a bounded area. Its size states
    leftover space, not need. ``size``'s Berkeley output folds it into bss,
    which overstated the reported footprint by hundreds of KB on the affected
    boards -- 392 KB of "bss" against 260 bytes of real state in the #24
    reproduction.

    ``total`` keeps the tool's own inclusive sum, so
    ``text + data + bss + reserved`` reconciles against it.
    """

    text: int = 0
    data: int = 0
    bss: int = 0
    total: int = 0
    reserved: int = 0


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
    #: Optional linked-symbol hint for plan-vs-measured reconciliation
    #: (#133 Phase 3). Set where the consumer name does not resemble its
    #: symbol (heliaAOT: consumer ``dtcm_scratch_arena_0`` vs symbol
    #: ``hpx_arena_dtcm_buffer``); the reconciler's name table covers the
    #: rest. Serialized only when present.
    symbol: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ConsumerKind):
            object.__setattr__(self, "kind", ConsumerKind(self.kind))


@dataclass(frozen=True)
class MemoryRegionUsage:
    """PLANNED usage for a single memory region (e.g. DTCM, MRAM).

    Part of the memory-plan DECISION RECORD: ``capacity`` is the SoC's
    declared size and ``used`` sums only what hpx itself placed. Since
    run-summary schema v3 the ``free``/``overflow`` properties are
    plan-time capacity checks (``plan_memory`` raises on oversubscription)
    and are NOT serialized — the measured truth is
    :class:`MeasuredRegion`, read from the linked ELF (#133).
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
# Measured memory regions (#133 Phase 2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MeasuredRegion:
    """One region's MEASURED occupancy, read from the linked ELF.

    The counterpart to :class:`MemoryRegionUsage`: that one is the memory
    PLAN's decision record (what hpx intended, computed before any compiler
    ran); this one is what the linker actually did, computed from the
    binary's section inventory classified into the verified per-SoC windows
    (``platform.memory_map``). ``used``/``free``/``reserved`` live HERE and
    only here — the plan stopped wearing measurement vocabulary at
    run-summary schema v3 (#133).

    ``window_*`` is the classification aperture; ``app_*`` is the link
    family's app extent inside it. ``used`` sums allocated, non-reserved
    sections inside the app extent (gcc's floating ``.stack`` included —
    live memory). ``reserved`` sums the linker's own reservations: the
    fill-to-end/fixed heap sections inside the extent plus allocated
    sections inside the window but outside the extent (armlink's fixed
    heap/stack, apollo3p's STACKMEM). ``load_image`` is the PT_LOAD file
    bytes whose PHYSICAL address lands here — initialized data's flash
    image (summed per segment, never per section: armlink emits one
    aggregate PT_LOAD).

    Attribution is by section START address, all-or-nothing: a section
    straddling an extent boundary is charged entirely to where it begins.
    No NSX script produces one; if a future link does, the per-region
    numbers will show it (negative or inflated free), not hide it.
    """

    region: MemoryRegion
    window_start: int
    window_length: int
    app_start: int
    app_length: int
    used: int
    reserved: int
    load_image: int = 0
    window_provenance: str = "hardware-aperture"
    app_provenance: str = "linker-script"

    def __post_init__(self) -> None:
        if not isinstance(self.region, MemoryRegion):
            object.__setattr__(self, "region", MemoryRegion(str(self.region).upper()))

    @property
    def free(self) -> int:
        """App extent minus measured usage. Deliberately unclamped: a
        negative value means the inventory and the characterized extent
        disagree — surface it, never hide it."""
        return self.app_length - self.used


@dataclass(frozen=True)
class UnattributedSection:
    """An allocated section outside every verified window — the police
    flag: either the binary put bytes somewhere hpx has not characterized,
    or the characterized table is wrong for this part."""

    name: str
    address: int
    size: int


@dataclass(frozen=True)
class MeasuredMemoryRegions:
    """The measured memory truth of one linked binary (#133 Phase 2).

    Absent (None upstream) whenever it cannot be TRUE: unknown SoC or
    linker profile, tool failure, or a partial section inventory
    (``unparsed_rows`` nonzero) — never guessed, per #131. Regions that no
    ELF section can land in (PSRAM) are excluded entirely; the plan owns
    them.
    """

    link_family: str
    linker_profile: str
    regions: tuple[MeasuredRegion, ...]
    unattributed: tuple[UnattributedSection, ...] = ()
    #: PT_LOAD file bytes whose PHYSICAL address classifies to no
    #: attributable window — the segment-side police flag (sections have
    #: ``unattributed``).
    unattributed_load_bytes: int = 0

    def region(self, name: str | MemoryRegion) -> MeasuredRegion | None:
        key = MemoryRegion(str(name).upper()) if not isinstance(name, MemoryRegion) else name
        for r in self.regions:
            if r.region is key:
                return r
        return None


# ---------------------------------------------------------------------------
# Plan-vs-measured reconciliation (#133 Phase 3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConsumerReconciliation:
    """One plan consumer held against the linked binary's symbols.

    ``status``: ``matched`` (symbols found; ``measured_size``/``delta``
    populated — delta is measured minus planned, so positive means the
    firmware reserves MORE than the plan booked), ``missing`` (planned
    but no symbol found — either the plan is wrong or the symbol table
    is), or ``unmatchable`` (structural: no symbol mapping exists for
    this consumer — PSRAM-placed weights are a runtime pointer with no
    sized symbol, armlink's stack is a scatter region, and some staged
    entries have no single symbol)."""

    name: str
    kind: str
    region: str
    planned_size: int
    status: str
    matched_symbols: tuple[str, ...] = ()
    measured_size: int | None = None
    #: The measured region the DOMINANT matched symbol's address falls in
    #: (None when nothing matched or the address is outside every
    #: window). A matched consumer whose measured_region differs from
    #: ``region`` landed somewhere the plan did not intend — the check
    #: that catches wrong-region "clean" matches (#179 review M-6).
    measured_region: str | None = None
    delta: int | None = None


@dataclass(frozen=True)
class RegionReconciliation:
    """Plan ``used`` vs measured ``used`` for one region — the honest
    "the plan missed N bytes here" figure the hpx-owned consumers exist
    to drive toward zero."""

    region: str
    planned_used: int
    measured_used: int

    @property
    def delta(self) -> int:
        return self.measured_used - self.planned_used


@dataclass(frozen=True)
class MemoryReconciliation:
    """The #133 payoff artifact: what the plan intended vs what the
    linker did, by name and by region."""

    consumers: tuple[ConsumerReconciliation, ...] = ()
    regions: tuple[RegionReconciliation, ...] = ()


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
    power: PowerResult | None = None
    power_observation: PowerObservation | None = None
    power_terminal: PowerTerminalRecord | None = None
    on_device_power: OnDevicePowerSummary | None = None
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
