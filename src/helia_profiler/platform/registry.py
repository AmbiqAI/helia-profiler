"""Platform registry construction and public lookup helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from .board import (
    DEFAULT_GO_GPIO_PIN,
    DEFAULT_STATE_GPIO_PIN,
    DEFAULT_SYNC_GPIO_PIN,
    BoardDef,
)
from .provenance import CatalogProvenance, OverlaySource, TargetSelection
from .soc import SocDef


@dataclass(frozen=True)
class PlatformRegistry:
    """Resolved platform registry used for one config/run."""

    socs: Mapping[str, SocDef]
    boards: Mapping[str, BoardDef]
    selections: Mapping[str, TargetSelection] = field(default_factory=lambda: MappingProxyType({}))
    catalog: CatalogProvenance | None = None
    board_socs: Mapping[str, SocDef] = field(default_factory=lambda: MappingProxyType({}))
    overlay_source: OverlaySource | None = None


def _freeze_registry(
    socs: Mapping[str, SocDef],
    boards: Mapping[str, BoardDef],
    selections: Mapping[str, TargetSelection],
    catalog: CatalogProvenance | None,
    board_socs: Mapping[str, SocDef],
    overlay_source: OverlaySource | None = None,
) -> PlatformRegistry:
    soc_map = dict(socs)
    board_map = dict(boards)
    for board in board_map.values():
        if board.soc not in soc_map:
            raise ValueError(f"Board '{board.name}' references unknown SoC '{board.soc}'.")
        if board.starter_profile_board is not None and board.starter_profile_board not in board_map:
            raise ValueError(
                f"Board '{board.name}' references unknown starter-profile board "
                f"'{board.starter_profile_board}'."
            )
    return PlatformRegistry(
        socs=MappingProxyType(soc_map),
        boards=MappingProxyType(board_map),
        selections=MappingProxyType(dict(selections)),
        catalog=catalog,
        board_socs=MappingProxyType(dict(board_socs)),
        overlay_source=overlay_source,
    )


def build_platform_registry(
    *,
    base: PlatformRegistry | None = None,
    socs: Mapping[str, SocDef] | None = None,
    boards: Mapping[str, BoardDef] | None = None,
) -> PlatformRegistry:
    """Return a frozen platform registry for one config/run."""
    if base is None:
        from .catalog import catalog_views

        builtin_socs, builtin_boards, builtin_board_socs, builtin_selections, catalog = (
            catalog_views()
        )
        merged_socs = dict(builtin_socs)
        merged_boards = dict(builtin_boards)
        board_socs = dict(builtin_board_socs)
        selections = dict(builtin_selections)
    else:
        merged_socs = dict(base.socs)
        merged_boards = dict(base.boards)
        selections = dict(base.selections)
        catalog = base.catalog
        board_socs = dict(base.board_socs)
    if socs:
        merged_socs.update(socs)
    previous_boards = dict(merged_boards)
    if boards:
        merged_boards.update(boards)
    for name, board in merged_boards.items():
        if (socs and board.soc in socs) or (boards and name in boards):
            selections[name] = TargetSelection("hpx-custom", catalog)
            previous = previous_boards.get(name)
            if (socs and board.soc in socs) or previous is None or previous.soc != board.soc:
                board_socs.pop(name, None)
    return _freeze_registry(
        merged_socs,
        merged_boards,
        selections,
        catalog,
        board_socs,
        base.overlay_source if base is not None else None,
    )


def get_soc(name: str, *, registry: PlatformRegistry | None = None) -> SocDef:
    """Look up a SoC definition by name."""
    active = registry or build_platform_registry()
    if name not in active.socs:
        known = ", ".join(sorted(active.socs))
        raise ValueError(f"Unknown SoC '{name}'. Known SoCs: {known}")
    return active.socs[name]


def get_board(name: str, *, registry: PlatformRegistry | None = None) -> BoardDef:
    active = registry or build_platform_registry()
    if name not in active.boards:
        known = ", ".join(sorted(active.boards))
        raise ValueError(f"Unknown board '{name}'. Known boards: {known}")
    return active.boards[name]


def get_soc_for_board(board_name: str, *, registry: PlatformRegistry | None = None) -> SocDef:
    """Resolve the SoC definition for a given board."""
    registry = registry or build_platform_registry()
    board = get_board(board_name, registry=registry)
    soc = registry.board_socs.get(board_name) or get_soc(board.soc, registry=registry)
    if board.psram_kb is None:
        return soc
    return replace(
        soc,
        memory=replace(soc.memory, psram_kb=board.psram_kb),
    )


def get_default_sync_gpio_pin(
    board_name: str,
    fallback: int = DEFAULT_SYNC_GPIO_PIN,
    *,
    registry: PlatformRegistry | None = None,
) -> int:
    """Return the board's default sync GPIO pin, or *fallback* if unknown."""
    active = registry or build_platform_registry()
    board = active.boards.get(board_name)
    if board is None:
        return fallback
    return board.default_sync_gpio_pin


def get_default_state_gpio_pin(
    board_name: str,
    fallback: int = DEFAULT_STATE_GPIO_PIN,
    *,
    registry: PlatformRegistry | None = None,
) -> int:
    """Return the board's default state/error GPIO pin, or *fallback* if unknown."""
    active = registry or build_platform_registry()
    board = active.boards.get(board_name)
    if board is None:
        return fallback
    return board.default_state_gpio_pin


def get_default_go_gpio_pin(
    board_name: str,
    fallback: int = DEFAULT_GO_GPIO_PIN,
    *,
    registry: PlatformRegistry | None = None,
) -> int:
    """Return the board's default go GPIO pin, or *fallback* if unknown."""
    active = registry or build_platform_registry()
    board = active.boards.get(board_name)
    if board is None:
        return fallback
    return board.default_go_gpio_pin


def list_boards(*, registry: PlatformRegistry | None = None) -> list[BoardDef]:
    active = registry or build_platform_registry()
    return list(active.boards.values())


def list_socs(*, registry: PlatformRegistry | None = None) -> list[SocDef]:
    active = registry or build_platform_registry()
    return list(active.socs.values())
