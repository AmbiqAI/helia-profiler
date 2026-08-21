"""Engine adapter protocol and artifact types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Protocol, runtime_checkable

from ..config import ProfileConfig
from ..placement import ArenaRole, Placement
from ..results import NsxModuleRef, MemoryPlan
from . import EngineType


@dataclass(frozen=True)
class ArenaRegion:
    """One arena buffer emitted by an engine adapter.

    AOT-style engines (e.g. heliaAOT) split the working set into multiple
    arenas — typically scratch / persistent / constant — each with its
    own size, alignment, and target memory.  The firmware template binds
    each region individually via ``bind_arena()``.

    Replaces the previous ``list[dict[str, Any]]`` shuttle so producers
    and consumers share a typed contract.

    Attributes
    ----------
    region_id:
        Stable AOT region index — also the enum value passed to
        ``bind_arena()`` from firmware.
    name:
        Human-readable region name (used in firmware logs / report tables).
    enum_name:
        C symbol name of the corresponding region enum value.
    size:
        Byte size of the backing buffer.
    alignment:
        Required alignment of the backing buffer.
    role:
        Region role — drives firmware-level placement overrides.
    memory:
        Original physical memory name from the AOT planner (e.g.
        ``"dtcm"``, ``"itcm"``, ``"sram"``).  Used in symbol names and
        diagnostics; placement decisions should consult :attr:`placement`
        instead.
    placement:
        Logical placement region — the single vocabulary used by the
        firmware Jinja templates and the rest of the pipeline.
    """

    region_id: int
    name: str
    enum_name: str
    size: int
    alignment: int
    role: ArenaRole
    memory: str
    placement: Placement
    blob_filename: str | None = None


@dataclass(frozen=True, kw_only=True)
class EngineArtifacts:
    """Common core of the outputs produced by an engine adapter's prepare step.

    Every adapter returns a per-engine subclass (:class:`TflmArtifacts`,
    :class:`HeliaRtArtifacts`, :class:`HeliaAotArtifacts`,
    :class:`ExecutorchArtifacts`).  This base carries only what *every*
    engine produces; engine-specific outputs live on the subclass, so a
    wrong-engine read is an ``AttributeError`` at the access site instead
    of a silently-defaulted ``None``.

    Consumers that need engine-specific fields narrow with ``isinstance``
    at their existing ``engine_type`` branch points.  The four
    ``resolved_*`` identity properties stay on the base so the workspace
    fingerprint (``dependencies.py``) remains engine-agnostic.
    """

    #: Identity of the producing engine — single source of truth.  Use
    #: this for engine-specific dispatch instead of branching on a string.
    #: Each subclass pins it; a mismatched value raises ``ValueError``.
    engine_type: EngineType

    #: C/C++ header included by the firmware main template to pull in the
    #: engine's public API.  Every adapter states its own — there is no
    #: cross-engine default for a new adapter to inherit by accident.
    engine_header: str

    # Additional NSX modules the profiler app needs (e.g. a local heliaRT wrapper)
    extra_modules: list[NsxModuleRef] = field(default_factory=list)

    # Additional CMake variables to pass during configure
    cmake_vars: dict[str, str] = field(default_factory=dict)

    # Paths to engine-specific source files to include in the build
    source_files: list[Path] = field(default_factory=list)

    # Paths to engine-specific include directories
    include_dirs: list[Path] = field(default_factory=list)

    # Paths to static libraries to link
    static_libs: list[Path] = field(default_factory=list)

    # Optional memory plan built from engine-specific internals (e.g.
    # heliaAOT's ``codegen_ctx.memory_plan``).  If None, ``plan_memory``
    # stage synthesises a conservative plan from ``model.arena_size`` and
    # the resolved split placement.
    memory_plan: MemoryPlan | None = None

    #: Engine type this class is pinned to — ``None`` on the base, which
    #: accepts any.  Read by :meth:`__post_init__`.
    _PINNED_ENGINE_TYPE: ClassVar[EngineType | None] = None

    def __post_init__(self) -> None:
        pinned = type(self)._PINNED_ENGINE_TYPE
        if pinned is not None and self.engine_type != pinned:
            raise ValueError(
                f"{type(self).__name__} is pinned to engine_type {pinned.value!r}, "
                f"got {self.engine_type!r}"
            )

    # -- Resolved engine identity (workspace fingerprint inputs) ------------
    #
    # ``dependencies.py`` records the identity the adapter actually
    # resolved.  These four properties keep that call site engine-agnostic:
    # the base answers ``None`` and each subclass routes to its own fields.

    @property
    def resolved_backend(self) -> str | None:
        """Engine backend the adapter resolved (None when the engine has none)."""
        return None

    @property
    def resolved_version(self) -> str | None:
        """Engine version the adapter resolved (None when the engine has none)."""
        return None

    @property
    def resolved_variant(self) -> str | None:
        """Engine build variant the adapter resolved (None when the engine has none)."""
        return None

    @property
    def resolved_toolchain_tag(self) -> str | None:
        """Engine toolchain tag the adapter resolved (None when the engine has none)."""
        return None


@dataclass(frozen=True, kw_only=True)
class TflmArtifacts(EngineArtifacts):
    """Stock TFLM adapter outputs — the common core, nothing more."""

    engine_type: EngineType = EngineType.TFLM

    _PINNED_ENGINE_TYPE: ClassVar[EngineType | None] = EngineType.TFLM


@dataclass(frozen=True, kw_only=True)
class HeliaRtArtifacts(EngineArtifacts):
    """heliaRT adapter outputs.

    All four identity fields are required: both construction paths in
    ``engines/helia_rt/adapter.py`` — the NSX-registry release and the
    local/custom module (source build or prebuilt distribution) — set every
    one of them unconditionally.  They are surfaced in report metadata,
    used by version-compat assertions, and recorded in the workspace
    fingerprint.
    """

    engine_type: EngineType = EngineType.HELIA_RT

    engine_backend: str
    heliart_version: str
    heliart_variant: str
    heliart_toolchain_tag: str

    _PINNED_ENGINE_TYPE: ClassVar[EngineType | None] = EngineType.HELIA_RT

    @property
    def resolved_backend(self) -> str | None:
        return self.engine_backend

    @property
    def resolved_version(self) -> str | None:
        return self.heliart_version

    @property
    def resolved_variant(self) -> str | None:
        return self.heliart_variant

    @property
    def resolved_toolchain_tag(self) -> str | None:
        return self.heliart_toolchain_tag


@dataclass(frozen=True, kw_only=True)
class HeliaAotArtifacts(EngineArtifacts):
    """heliaAOT adapter outputs — generated module, arenas, operator manifest."""

    engine_type: EngineType = EngineType.HELIA_AOT

    #: Symbol prefix of the generated AOT module.  Required: the firmware
    #: template names every generated entry point through it (the consumer
    #: used to assert it non-None).
    aot_prefix: str
    #: NSX module name of the generated AOT module.
    aot_module_name: str
    #: CMake target the app links against (``nsx::<module>``).
    aot_cmake_target: str
    #: Version of the installed ``helia-aot`` compiler that produced this.
    helia_aot_version: str

    #: False when the AOT module expects externally bound arenas.
    aot_allocate_arenas: bool = True
    #: Arena buffers the firmware binds individually via ``bind_arena()``.
    aot_arena_regions: list[ArenaRegion] = field(default_factory=list)

    #: AOT operator manifest — ordered list of post-codegen operator
    #: descriptors (idx, id, op_type, name, inputs, outputs).  Consumed by
    #: the firmware template (``main_aot.cc.j2``) for the per-op callback
    #: table and by the report stage.  ``None`` when the manifest could not
    #: be extracted from the codegen context.
    aot_op_manifest: list[dict[str, Any]] | None = None

    _PINNED_ENGINE_TYPE: ClassVar[EngineType | None] = EngineType.HELIA_AOT

    @property
    def resolved_version(self) -> str | None:
        return self.helia_aot_version


@dataclass(frozen=True, kw_only=True)
class ExecutorchArtifacts(EngineArtifacts):
    """ExecuTorch adapter outputs — the explicit PTE runtime contract.

    ExecuTorch keeps the buffer sizes explicit because a PTE does not
    expose a stable host-side sizing API.  All five are required: the
    adapter resolves each through ``_positive_int``, which either returns
    a positive ``int`` or raises.
    """

    engine_type: EngineType = EngineType.EXECUTORCH

    executorch_method_arena_size: int
    executorch_planned_arena_size: int
    executorch_temporary_arena_size: int
    executorch_input_size: int
    executorch_output_size: int

    #: Per-buffer memory regions ("tcm" | "sram"); None = follow the run's
    #: resolved arena region (model.arena_location or the planner's choice).
    executorch_planned_arena_region: str | None = None
    executorch_method_arena_region: str | None = None
    executorch_temporary_arena_region: str | None = None
    executorch_io_region: str | None = None

    _PINNED_ENGINE_TYPE: ClassVar[EngineType | None] = EngineType.EXECUTORCH


@runtime_checkable
class EngineAdapter(Protocol):
    """Interface that each inference engine adapter must implement.

    Adapters own all engine-specific behaviour.  Shared pipeline stages
    (preflight, plan_memory, firmware) call the methods defined here
    instead of branching on :class:`EngineType`.

    Adapters are cheap to instantiate — preflight (stage 0) constructs
    one via :func:`get_adapter` to query capabilities before
    ``prepare()`` runs in stage 2.
    """

    @property
    def name(self) -> str:
        """Human-readable engine name."""
        ...

    @property
    def engine_type(self) -> EngineType:
        """Identity of this adapter (single source of truth)."""
        ...

    def prepare(self, config: ProfileConfig, work_dir: Path) -> EngineArtifacts:
        """Produce engine-specific artifacts needed for the profiler firmware.

        This may involve running an AOT compiler, fetching static libraries,
        generating wrapper source files, etc.
        """
        ...

    # -- Capability hooks (called by shared stages) --

    def default_auto_placement(
        self, *, tcm_cap: int, sram_cap: int
    ) -> tuple[Placement, Placement] | None:
        """Engine-specific default when split placement fields are omitted.

        Returns a ``(arena, weights)`` pair, or ``None`` to fall through
        to the shared greedy fastest-fit policy in ``plan_memory``.
        """
        ...

    def apply_arena_placement_override(
        self,
        regions: list["ArenaRegion"],
        target: Placement,
    ) -> list["ArenaRegion"]:
        """Apply firmware-level arena placement override.

        Called after ``prepare()`` produced :attr:`EngineArtifacts.aot_arena_regions`.
        Default impl is identity (no override).
        AOT-style engines move *scratch* regions to ``target``.
        """
        ...
