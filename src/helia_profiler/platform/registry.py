"""Platform registry construction and public lookup helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType

from .board import (
    DEFAULT_GO_GPIO_PIN,
    DEFAULT_STATE_GPIO_PIN,
    DEFAULT_SYNC_GPIO_PIN,
    BoardDef,
    _BOARDS,
)
from .soc import SocDef, _SOCS

@dataclass(frozen=True)
class PlatformRegistry:
    """Resolved platform registry used for one config/run."""

    socs: Mapping[str, SocDef]
    boards: Mapping[str, BoardDef]


def _freeze_registry(socs: Mapping[str, SocDef], boards: Mapping[str, BoardDef]) -> PlatformRegistry:
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
    )


# ---------------------------------------------------------------------------
# Public lookup API
# ---------------------------------------------------------------------------


def build_platform_registry(
    *,
    base: PlatformRegistry | None = None,
    socs: Mapping[str, SocDef] | None = None,
    boards: Mapping[str, BoardDef] | None = None,
) -> PlatformRegistry:
    """Return a frozen platform registry for one config/run."""
    if base is None:
        merged_socs = dict(_SOCS)
        merged_boards = dict(_BOARDS)
    else:
        merged_socs = dict(base.socs)
        merged_boards = dict(base.boards)
    if socs:
        merged_socs.update(socs)
    if boards:
        merged_boards.update(boards)
    return _freeze_registry(merged_socs, merged_boards)


def get_soc(name: str, *, registry: PlatformRegistry | None = None) -> SocDef:
    """Look up a SoC definition by name."""
    active = registry or build_platform_registry()
    if name not in active.socs:
        known = ", ".join(sorted(active.socs))
        raise ValueError(f"Unknown SoC '{name}'. Known SoCs: {known}")
    return active.socs[name]


def get_board(name: str, *, registry: PlatformRegistry | None = None) -> BoardDef:
    """Look up a board definition by name."""
    active = registry or build_platform_registry()
    if name not in active.boards:
        known = ", ".join(sorted(active.boards))
        raise ValueError(f"Unknown board '{name}'. Known boards: {known}")
    return active.boards[name]


def get_soc_for_board(board_name: str, *, registry: PlatformRegistry | None = None) -> SocDef:
    """Resolve the SoC definition for a given board."""
    board = get_board(board_name, registry=registry)
    soc = get_soc(board.soc, registry=registry)
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
    """Return all registered boards."""
    active = registry or build_platform_registry()
    return list(active.boards.values())


def list_socs(*, registry: PlatformRegistry | None = None) -> list[SocDef]:
    """Return all registered SoCs."""
    active = registry or build_platform_registry()
    return list(active.socs.values())
