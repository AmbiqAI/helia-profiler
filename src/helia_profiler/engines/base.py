"""Engine adapter protocol and artifact types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..config import ProfileConfig
from ..results import NsxModuleRef


@dataclass(frozen=True)
class EngineArtifacts:
    """Outputs produced by an engine adapter's prepare step.

    These are consumed by the firmware template renderer.
    """

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
