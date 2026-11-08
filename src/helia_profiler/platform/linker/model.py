"""Recorded linker inputs and per-executable source verification."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import TypeAdapter

from ..memory_map import LinkFamily
from ..placement import MemoryRegion
from ..soc import MemoryRange


@dataclass(frozen=True)
class LinkerFile:
    """One relative SDK input, identified by its UTF-8 source with LF line endings."""

    path: str
    sha256: str

    def __post_init__(self) -> None:
        if PurePosixPath(self.path).is_absolute() or ".." in PurePosixPath(self.path).parts:
            raise ValueError("A linker input path must be relative to its SDK source root")
        if len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256):
            raise ValueError("A linker input requires a lowercase SHA256 source digest")


@dataclass(frozen=True)
class LinkerRegion:
    """One script region and its separately derived application intervals.

    ``allowed=None`` means hardware admission is unavailable. An empty tuple
    means the inputs are known and leave no space. Fixed stack reservations
    lie outside ``app_window`` and are accounted separately from app usage.
    """

    region: MemoryRegion
    window: MemoryRange
    app_window: MemoryRange
    allowed: tuple[MemoryRange, ...] | None
    window_provenance: str
    fixed_stack_bytes: int = 0
    section_attributable: bool = True

    def __post_init__(self) -> None:
        if self.window.length < 0 or self.window.start < 0:
            raise ValueError("Invalid linker classification window")
        if (
            self.app_window.start < self.window.start
            or self.app_window.length < 0
            or self.app_window.end > self.window.end
            or self.fixed_stack_bytes < 0
        ):
            raise ValueError("Application extent or reservation is outside its linker window")
        if self.allowed is not None:
            object.__setattr__(self, "allowed", tuple(self.allowed))
            end = self.app_window.start
            for extent in self.allowed:
                if extent.start < end or extent.length <= 0 or extent.end > self.app_window.end:
                    raise ValueError(
                        "Application intervals must be disjoint and inside the script extent"
                    )
                end = extent.end

    @property
    def capacity(self) -> int | None:
        """Conservative contiguous admission capacity; ``None`` remains unavailable."""
        if self.allowed is None:
            return None
        return max((extent.length for extent in self.allowed), default=0)


@dataclass(frozen=True)
class ApplicationRegion:
    """Catalog application intervals translated into one core's address space."""

    name: str
    region: MemoryRegion
    intervals: tuple[MemoryRange, ...] | None


@dataclass(frozen=True)
class LinkerInput:
    """Expected SDK/script input and its source-backed effective region data."""

    sdk_commit: str
    soc_name: str
    link_family: LinkFamily
    linker_profile: str
    script: LinkerFile
    files: tuple[LinkerFile, ...]
    definition_sha256: str
    admission_source: Literal["catalog-and-linker", "sdk-descriptor", "unavailable"]
    regions: tuple[LinkerRegion, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", tuple(self.files))
        object.__setattr__(self, "regions", tuple(self.regions))
        names = [entry.region for entry in self.regions]
        if len(names) != len(set(names)):
            raise ValueError("A linker input must not repeat a logical region")


@dataclass(frozen=True)
class LinkerVerification:
    """Actual applicability decision for one executable at one build boundary."""

    role: Literal["profile", "power"]
    target: str
    layout: LinkerInput | None
    reason: str | None = None

    def __post_init__(self) -> None:
        targets = {"profile": "hpx_profiler", "power": "hpx_profiler_power"}
        if self.target != targets.get(self.role):
            raise ValueError("Linker verification role and executable target disagree")
        if (self.layout is None) != bool(self.reason):
            raise ValueError(
                "An unavailable linker decision needs a reason; verified data has none"
            )

    @property
    def verified(self) -> bool:
        """Whether actual SDK/script applicability was verified, independently of fit."""
        return self.layout is not None

    def to_dict(self) -> dict[str, Any]:
        return TypeAdapter(LinkerVerification).dump_python(self, mode="json")

    @classmethod
    def from_dict(cls, value: object) -> LinkerVerification:
        if not isinstance(value, dict):
            raise ValueError("Recorded linker verification must be an object")
        parsed = TypeAdapter(cls).validate_json(json.dumps(value, allow_nan=False), strict=True)
        if parsed.to_dict() != value:
            raise ValueError("Recorded linker verification has missing or unexpected fields")
        return parsed
