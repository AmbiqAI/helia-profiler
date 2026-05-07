"""Engine adapter protocol and artifact types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

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


@dataclass(frozen=True)
class EngineArtifacts:
    """Outputs produced by an engine adapter's prepare step.

    These are consumed by the firmware template renderer.
    """

    # Identity of the producing engine — single source of truth.  Use
    # this for engine-specific dispatch instead of pulling a string out
    # of ``template_vars``.
    engine_type: EngineType = EngineType.TFLM

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

    # Engine-specific template context (merged into Jinja rendering)
    template_vars: dict[str, Any] = field(default_factory=dict)

    # Optional memory plan built from engine-specific internals (e.g.
    # heliaAOT's ``codegen_ctx.memory_plan``).  If None, ``plan_memory``
    # stage synthesises a conservative plan from ``model.arena_size`` and
    # ``model.model_location``.
    memory_plan: MemoryPlan | None = None


@runtime_checkable
class EngineAdapter(Protocol):
    """Interface that each inference engine adapter must implement."""

    @property
    def name(self) -> str:
        """Human-readable engine name."""
        ...

    def prepare(self, config: ProfileConfig, work_dir: Path) -> EngineArtifacts:
        """Produce engine-specific artifacts needed for the profiler firmware.

        This may involve running an AOT compiler, fetching static libraries,
        generating wrapper source files, etc.
        """
        ...
