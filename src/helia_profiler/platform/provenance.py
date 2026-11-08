"""The resolved platform input retained with a plan or measurement."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import TypeAdapter

from .board import BoardDef
from .memory_map import LinkFamily, MemoryRegion, linked_memory_map
from .soc import MemoryRange, SocDef

if TYPE_CHECKING:
    from .registry import PlatformRegistry


@dataclass(frozen=True)
class CatalogSource:
    label: str
    schema_version: int
    sha256: str
    distribution_version: str | None = None


@dataclass(frozen=True)
class CatalogProvenance:
    package_version: str
    schema_version: int
    catalog_digest: str
    sources: tuple[CatalogSource, ...] = ()


@dataclass(frozen=True)
class TargetSelection:
    origin: Literal["catalog", "hpx-custom", "sdk-descriptor"]
    catalog: CatalogProvenance | None
    target_id: str | None = None
    core: str | None = None
    boot: str | None = None
    implementation: str | None = None
    addons: tuple[str, ...] = ()
    compatibility: tuple[str, ...] = ()


@dataclass(frozen=True)
class RegionExtent:
    region: MemoryRegion
    extent: MemoryRange


@dataclass(frozen=True)
class PlatformSnapshot:
    board: BoardDef
    soc: SocDef
    selection: TargetSelection
    link_family: LinkFamily
    linker_profile: str
    app_regions: tuple[RegionExtent, ...]
    load_address: int | None

    def to_dict(self) -> dict[str, Any]:
        return TypeAdapter(PlatformSnapshot).dump_python(self, mode="json")

    @classmethod
    def from_dict(cls, value: object) -> PlatformSnapshot:
        if not isinstance(value, dict):
            raise ValueError("Recorded platform must be an object")
        parsed = TypeAdapter(cls).validate_json(json.dumps(value, allow_nan=False), strict=True)
        if parsed.to_dict() != value:
            raise ValueError("Recorded platform has missing or unexpected fields")
        return parsed

    def verify_current(self) -> None:
        current = platform_snapshot(
            self.board.name, link_family=self.link_family, linker_profile=self.linker_profile
        )
        if self != current:
            raise ValueError(
                "Recorded platform differs from the current catalog or HPX target settings; "
                "use the matching HPX/catalog environment or freeze and build again"
            )


def platform_snapshot(
    board: str,
    *,
    registry: PlatformRegistry | None = None,
    link_family: LinkFamily = LinkFamily.GNU,
    linker_profile: str = "default",
) -> PlatformSnapshot:
    from .capabilities import resolve_app_flash_load_addr
    from .registry import build_platform_registry, get_board, get_soc_for_board

    registry = registry or build_platform_registry()
    board_def = get_board(board, registry=registry)
    soc = get_soc_for_board(board, registry=registry)
    selection = registry.selections.get(board)
    if selection is None:
        selection = TargetSelection("hpx-custom", registry.catalog)
    return PlatformSnapshot(
        board_def,
        soc,
        selection,
        link_family,
        linker_profile,
        tuple(
            RegionExtent(window.region, window.app_window[link_family])
            for window in linked_memory_map(soc, linker_profile=linker_profile)
        ),
        resolve_app_flash_load_addr(soc),
    )
