"""The resolved platform input retained with a plan or measurement."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import TypeAdapter

from .board import BoardDef
from .linker.model import LinkerInput
from .memory_map import LinkFamily, MemoryRegion
from .soc import MemoryRange, SocDef

if TYPE_CHECKING:
    from .registry import PlatformRegistry


@dataclass(frozen=True)
class CatalogSource:
    """Identity of an explicit source included in a catalog."""

    label: str
    schema_version: int
    sha256: str
    distribution_version: str | None = None


@dataclass(frozen=True)
class CatalogProvenance:
    """Catalog package, schema and data identities recorded at resolution."""

    package_version: str
    schema_version: int
    catalog_digest: str
    sources: tuple[CatalogSource, ...] = ()


@dataclass(frozen=True)
class TargetSelection:
    """Requested target resolution and named HPX compatibility choices."""

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
    """A retained application address range for one linker region."""

    region: MemoryRegion
    extent: MemoryRange


@dataclass(frozen=True)
class PlatformSnapshot:
    """Effective HPX platform views and the catalog selection that supplied their facts."""

    board: BoardDef
    soc: SocDef
    selection: TargetSelection
    link_family: LinkFamily
    linker_profile: str
    app_regions: tuple[RegionExtent, ...]
    load_address: int | None
    expected_linker: LinkerInput | None = None

    def __post_init__(self) -> None:
        layout = self.expected_linker
        if layout is not None and (
            layout.soc_name != self.soc.name
            or layout.link_family is not self.link_family
            or layout.linker_profile != self.linker_profile
            or tuple((r.region, r.app_window) for r in layout.regions)
            != tuple((r.region, r.extent) for r in self.app_regions)
        ):
            raise ValueError("Expected linker input differs from its recorded platform view")

    def to_dict(self) -> dict[str, Any]:
        adapter = TypeAdapter(PlatformSnapshot)
        value = adapter.dump_python(self, mode="json")
        adapter.validate_json(json.dumps(value, allow_nan=False), strict=True)
        return value

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
    from .linker.selection import expected_linker_input
    from .registry import build_platform_registry, get_board, get_soc_for_board

    registry = registry or build_platform_registry()
    board_def = get_board(board, registry=registry)
    soc = get_soc_for_board(board, registry=registry)
    selection = registry.selections.get(board)
    if selection is None:
        selection = TargetSelection("hpx-custom", registry.catalog)
    layout = expected_linker_input(soc, selection, link_family, linker_profile)
    return PlatformSnapshot(
        board_def,
        soc,
        selection,
        link_family,
        linker_profile,
        tuple(RegionExtent(r.region, r.app_window) for r in layout.regions)
        if layout is not None
        else (),
        resolve_app_flash_load_addr(soc),
        layout,
    )
